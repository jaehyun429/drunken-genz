"""질문 생성 서비스: batch 만들기 → 생성 → 저장. 모든 DB 접근은 여기."""
from __future__ import annotations
import logging
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import asyncpg
from pydantic import ValidationError

from .. import db
from ..constants import QUESTION_MIN, QUESTION_REGEN_PER_DAY, TZ
from ..errors import AppError
from ..llm.client import LLMConnectionError, LLMModelError
from .generate import generate
from .schemas import ExperienceInput, OtherExperience, LogInput

log = logging.getLogger("questions")


# ---------- 읽기 ----------

async def load_experience(conn: asyncpg.Connection, experience_id: uuid.UUID) -> tuple[asyncpg.Record, ExperienceInput]:
    row = await conn.fetchrow("select * from experience where id = $1", experience_id)
    if row is None:
        raise AppError("NOT_FOUND", "경험 없음")
    logs = []
    if row["status"] == "in_progress":
        for lg in await conn.fetch(
            "select log_date, situation_chips, emotion_chips, one_liner from experience_log "
            "where experience_id = $1 order by log_date", experience_id):
            logs.append(LogInput(log_date=str(lg["log_date"]), situation_chips=list(lg["situation_chips"] or []),
                                 emotion_chips=list(lg["emotion_chips"] or []), one_liner=lg["one_liner"]))
    e = ExperienceInput(
        id=str(row["id"]), status=row["status"], activity_name=row["activity_name"], genre=row["genre"],
        role=row["role"], period_start=row["period_start"], period_end=row["period_end"], place=row["place"],
        did_chips=list(row["did_chips"] or []), did_notes=row["did_notes"] or {},
        custom_chips=list(row["custom_chips"] or []), conditions=row["conditions"] or {},
        situation_chips=list(row["situation_chips"] or []), situation_notes=row["situation_notes"] or {},
        emotion_chips=list(row["emotion_chips"] or []), one_liner=row["one_liner"],
        ai_tags=list(row["ai_tags"] or []), logs=logs)
    return row, e


async def load_others(conn: asyncpg.Connection, user_id: uuid.UUID, exclude: uuid.UUID) -> list[OtherExperience]:
    """본인의 다른 기록만 (CLAUDE.md 7장). qa·reaction 은 절대 읽지 않는다."""
    rows = await conn.fetch(
        "select id, activity_name, genre, role, did_chips from experience "
        "where user_id = $1 and id <> $2 order by created_at desc limit 50", user_id, exclude)
    return [OtherExperience(id=str(r["id"]), activity_name=r["activity_name"], genre=r["genre"], role=r["role"],
                            did_chips=list(r["did_chips"] or [])) for r in rows]


# ---------- batch ----------

def _kst_today() -> datetime:
    return datetime.now(ZoneInfo(TZ)).replace(hour=0, minute=0, second=0, microsecond=0)


async def start_batch(conn: asyncpg.Connection, experience_id: uuid.UUID, user_id: uuid.UUID, trigger: str) -> asyncpg.Record:
    """pending 행 하나 만들기. 재생성(user)이면 하루 1회 검사."""
    if trigger == "user":
        n = await conn.fetchval(
            "select count(*) from question_batch where experience_id = $1 and trigger = 'user' and created_at >= $2",
            experience_id, _kst_today())
        if n >= QUESTION_REGEN_PER_DAY:
            raise AppError("QUESTION_REGEN_LIMIT", "재생성은 하루 1회", {"limit": QUESTION_REGEN_PER_DAY})
    gen = await conn.fetchval("select coalesce(max(generation), 0) + 1 from question_batch where experience_id = $1",
                              experience_id)
    return await conn.fetchrow(
        "insert into question_batch (experience_id, user_id, generation, trigger, input_hash) "
        "values ($1, $2, $3, $4, '') returning *", experience_id, user_id, gen, trigger)


async def run_batch(batch_id: uuid.UUID) -> None:
    """백그라운드. 실패해도 예외를 밖으로 던지지 않고 batch 에 기록한다.
       _run_batch 가 처리하지 못한 예외(저장 중 DB 오류 등)도 여기서 failed 로 남긴다. pending 으로 남으면
       앱이 '준비 중'을 계속 띄우고, 주기 점검에서는 뒤에 줄 선 batch 까지 못 돈다.
       예외 글에는 사용자 글이 섞일 수 있어 (pydantic 은 입력값을, Postgres 는 실패한 행을 적는다) 종류 이름만 남긴다."""
    try:
        await _run_batch(batch_id)
    except Exception as err:  # noqa: BLE001
        log.error("batch %s 예상 못 한 예외: %s", batch_id, type(err).__name__)
        try:
            async with db.pool().acquire() as conn:
                await _fail(conn, batch_id, "model", type(err).__name__)
        except Exception as err2:  # noqa: BLE001
            log.error("batch %s 실패 기록도 못 함: %s", batch_id, type(err2).__name__)


async def _run_batch(batch_id: uuid.UUID) -> None:
    pool = db.pool()
    async with pool.acquire() as conn:
        batch = await conn.fetchrow("select * from question_batch where id = $1", batch_id)
        if batch is None or batch["status"] != "pending":
            return
        try:
            _, e = await load_experience(conn, batch["experience_id"])
            others = await load_others(conn, batch["user_id"], batch["experience_id"])
        except AppError as err:
            await _fail(conn, batch_id, "model", f"입력 없음: {err.code}")
            return
        except ValidationError as err:
            # 기록 모양이 모델 입력과 안 맞는다. DB 는 conditions 가 객체인지만 봐서 {"규모": 3} 같은 값이 들어올 수 있다.
            # 다시 눌러도 같은 결과라 'model'. 값은 사용자 글이니 어느 칸인지만 남긴다
            fields = sorted({str(x["loc"][0]) for x in err.errors() if x.get("loc")})
            await _fail(conn, batch_id, "model", "입력 모양 오류: " + ", ".join(fields))
            return

        # 같은 입력 해시로 성공한 적이 있으면 LLM 안 부르고 복사 (CLAUDE.md 4장 '출력').
        # 프롬프트 버전이 올라갔으면 예전 버전으로 만든 질문은 재사용하지 않는다
        from .prompt import input_hash, prompt_version
        h = input_hash(e)
        prev = await conn.fetchrow(
            "select id, generation, model, prompt_version from question_batch "
            "where experience_id = $1 and status = 'done' and input_hash = $2 and id <> $3 and prompt_version = $4 "
            "order by generation desc limit 1", batch["experience_id"], h, batch_id, prompt_version())
        if prev is not None:
            rows = await conn.fetch(
                "select type, axis, text, source, done_experience_id from question "
                "where experience_id = $1 and generation = $2 order by created_at", batch["experience_id"], prev["generation"])
            if len(rows) >= QUESTION_MIN:   # 모자라게 끝난 batch 는 복사하지 않고 다시 만든다
                await _store(conn, batch, e, [dict(r) for r in rows], h, prev["model"], prev["prompt_version"], attempts=0)
                return

        try:
            outcome = await generate(e, others)
        except LLMConnectionError as err:
            await _fail(conn, batch_id, "connection", str(err)[:300]); return
        except LLMModelError as err:
            await _fail(conn, batch_id, "model", str(err)[:300]); return
        except Exception as err:  # noqa: BLE001
            log.exception("질문 생성 예외")
            await _fail(conn, batch_id, "model", type(err).__name__); return

        qs = [{"type": q.type, "axis": q.axis, "text": q.text, "source": q.source,
               "done_experience_id": q.done_experience_id} for q in outcome.questions]
        await _store(conn, batch, e, qs, outcome.input_hash, outcome.model, outcome.prompt_version, outcome.attempts)


async def _store(conn, batch, e: ExperienceInput, qs: list[dict], h: str, model, prompt_version, attempts: int):
    """담은 질문은 남기고 나머지 삭제 → 새 질문 삽입 → batch done. 한 트랜잭션."""
    async with conn.transaction():
        # 안 담은 이전 질문 삭제. related_cache 는 FK cascade 로 같이 지워짐
        await conn.execute("delete from question where experience_id = $1 and not saved", batch["experience_id"])
        for q in qs:
            done_id = q.get("done_experience_id")
            await conn.execute(
                "insert into question (experience_id, user_id, source_activity_name, type, axis, text, source, "
                " is_done, done_experience_id, generation) values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
                batch["experience_id"], batch["user_id"], e.activity_name, q["type"], q.get("axis"), q["text"],
                q["source"], done_id is not None, uuid.UUID(done_id) if done_id else None, batch["generation"])
        await conn.execute(
            "update question_batch set status = 'done', input_hash = $2, model = $3, prompt_version = $4, "
            "attempts = $5, finished_at = now() where id = $1",
            batch["id"], h, model, prompt_version, attempts)


async def _fail(conn, batch_id: uuid.UUID, reason: str, detail: str):
    await conn.execute(
        "update question_batch set status = 'failed', failure_reason = $2, error_detail = $3, finished_at = now() "
        "where id = $1 and status = 'pending'", batch_id, reason, detail)
    log.warning("batch %s 실패 (%s)", batch_id, reason)
