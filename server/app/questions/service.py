"""질문 생성 서비스: batch 만들기 → 생성 → 저장. 모든 DB 접근은 여기."""
from __future__ import annotations
import datetime
import logging
import uuid

import asyncpg
from pydantic import ValidationError

from .. import db
from ..constants import QUESTION_REGEN_PER_WINDOW, QUESTION_REGEN_WINDOW_H, QUESTION_ANSWER_MAX
from ..errors import AppError
from ..llm.client import LLMConnectionError, LLMModelError
from .generate import generate
from .schemas import ExperienceInput, OtherExperience, LogInput, AnswerInput

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
    # 더 적어보기에 답한 것은 기록 원문(보강)으로 친다 (기능명세서 14.1). 본인 질문의 본인 답만 읽는다
    answers = [AnswerInput(slot=a["axis"], question=a["text"], answer=a["answer"]) for a in await conn.fetch(
        "select axis, text, answer from question where experience_id = $1 and type = 'write_more' "
        "and answer is not null order by answered_at", experience_id)]
    e = ExperienceInput(
        id=str(row["id"]), status=row["status"], activity_name=row["activity_name"], genre=row["genre"],
        role=row["role"], period_start=row["period_start"], period_end=row["period_end"], place=row["place"],
        did_chips=list(row["did_chips"] or []), did_notes=row["did_notes"] or {},
        custom_chips=list(row["custom_chips"] or []), conditions=row["conditions"] or {},
        situation_chips=list(row["situation_chips"] or []), situation_notes=row["situation_notes"] or {},
        emotion_chips=list(row["emotion_chips"] or []), one_liner=row["one_liner"],
        ai_tags=list(row["ai_tags"] or []), logs=logs, answers=answers)
    return row, e


async def load_others(conn: asyncpg.Connection, user_id: uuid.UUID, exclude: uuid.UUID) -> list[OtherExperience]:
    """본인의 다른 기록만 (CLAUDE.md 7장). qa·reaction 은 절대 읽지 않는다."""
    rows = await conn.fetch(
        "select id, activity_name, genre, role, did_chips from experience "
        "where user_id = $1 and id <> $2 order by created_at desc limit 50", user_id, exclude)
    return [OtherExperience(id=str(r["id"]), activity_name=r["activity_name"], genre=r["genre"], role=r["role"],
                            did_chips=list(r["did_chips"] or [])) for r in rows]


# ---------- batch ----------

async def start_batch(conn: asyncpg.Connection, experience_id: uuid.UUID, user_id: uuid.UUID, trigger: str) -> asyncpg.Record:
    """pending 행 하나 만들기. 재생성(user)이면 횟수 검사 (PRD 7.2 재생성, 11/14):
       기록당 24시간에 1회 + 더 적어보기에 답할 때마다 1회.
       TODO(기획): 답으로 생긴 1회가 언제까지 유효한지는 명세에 없다. 답한 뒤 24시간으로 뒀다.
       TODO(기획): '새 질문 없음'이나 모델 실패로 끝난 시도도 센다."""
    if trigger == "user":
        window = datetime.timedelta(hours=QUESTION_REGEN_WINDOW_H)
        used = await conn.fetchval(
            "select count(*) from question_batch where experience_id = $1 and trigger = 'user' "
            "and created_at >= now() - $2::interval", experience_id, window)
        answered = await conn.fetchval(
            "select count(*) from question where experience_id = $1 and type = 'write_more' "
            "and answered_at >= now() - $2::interval", experience_id, window)
        limit = QUESTION_REGEN_PER_WINDOW + answered
        if used >= limit:
            raise AppError("QUESTION_REGEN_LIMIT", "재생성은 24시간에 1회, 더 적어보기에 답하면 1회 추가",
                           {"limit": limit, "window_hours": QUESTION_REGEN_WINDOW_H})
    gen = await conn.fetchval("select coalesce(max(generation), 0) + 1 from question_batch where experience_id = $1",
                              experience_id)
    return await conn.fetchrow(
        "insert into question_batch (experience_id, user_id, generation, trigger, input_hash) "
        "values ($1, $2, $3, $4, '') returning *", experience_id, user_id, gen, trigger)


async def load_previous(conn: asyncpg.Connection, experience_id: uuid.UUID) -> list[str]:
    """이전에 보여준 해볼 것·상상해보기 본문. 지난 생성들이 남긴 shown_texts 와 지금 남아 있는 질문(담은 것 포함)."""
    out: list[str] = []
    for r in await conn.fetch(
            "select shown_texts from question_batch where experience_id = $1 and status = 'done' order by generation",
            experience_id):
        out += [t for t in (r["shown_texts"] or []) if isinstance(t, str)]
    out += [r["text"] for r in await conn.fetch(
        "select text from question where experience_id = $1 and type <> 'write_more' order by created_at", experience_id)]
    return list(dict.fromkeys(out))


async def run_batch(batch_id: uuid.UUID) -> None:
    """백그라운드. 실패해도 예외를 밖으로 던지지 않고 batch 에 기록한다.
       11/14 부터 같은 입력 해시여도 저장된 질문을 복사하지 않는다 (재생성은 이전 질문과 안 겹쳐야 한다).
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

        # 사용자가 누른 다시 만들기만 이전 질문을 피한다. 저장 직후 생성과 그 실패 재시도는 처음 만드는 것이다
        previous = await load_previous(conn, batch["experience_id"]) if batch["trigger"] == "user" else None

        try:
            outcome = await generate(e, others, previous)
        except LLMConnectionError as err:
            await _fail(conn, batch_id, "connection", str(err)[:300]); return
        except LLMModelError as err:
            await _fail(conn, batch_id, "model", str(err)[:300]); return
        except Exception as err:  # noqa: BLE001
            log.exception("질문 생성 예외")
            await _fail(conn, batch_id, "model", type(err).__name__); return

        if outcome.no_new:
            # 새로 만들 질문이 없다 → 아무것도 지우지 않는다. 앱이 no_new 를 보고 "새로 만들 질문이 없어요"
            await conn.execute(
                "update question_batch set status = 'done', no_new = true, input_hash = $2, model = $3, "
                "prompt_version = $4, attempts = $5, finished_at = now() where id = $1",
                batch["id"], outcome.input_hash, outcome.model, outcome.prompt_version, outcome.attempts)
            return
        qs = [{"type": q.type, "axis": q.axis, "text": q.text, "source": q.source,
               "done_experience_id": q.done_experience_id} for q in outcome.questions]
        await _store(conn, batch, e, qs, outcome.input_hash, outcome.model, outcome.prompt_version, outcome.attempts)


async def _store(conn, batch, e: ExperienceInput, qs: list[dict], h: str, model, prompt_version, attempts: int):
    """담은 질문과 답한 더 적어보기는 남기고 나머지 삭제 → 새 질문 삽입 → batch done. 한 트랜잭션.
       더 적어보기가 앞에 오도록 qs 순서대로 넣는다. 앱은 created_at 순으로 읽으니, 한 트랜잭션 안에서도
       시각이 달라지게 clock_timestamp() 를 쓴다 (now() 는 트랜잭션 시작 시각이라 전부 같다)."""
    async with conn.transaction():
        # 안 담은 이전 질문 삭제. related_cache 는 FK cascade 로 같이 지워짐. 답한 더 적어보기는 기록 원문이라 남긴다
        await conn.execute("delete from question where experience_id = $1 and not saved and answer is null",
                           batch["experience_id"])
        for q in qs:
            done_id = q.get("done_experience_id")
            await conn.execute(
                "insert into question (experience_id, user_id, source_activity_name, type, axis, text, source, "
                " is_done, done_experience_id, generation, created_at) "
                "values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10, clock_timestamp())",
                batch["experience_id"], batch["user_id"], e.activity_name, q["type"], q.get("axis"), q["text"],
                q["source"], done_id is not None, uuid.UUID(done_id) if done_id else None, batch["generation"])
        await conn.execute(
            "update question_batch set status = 'done', input_hash = $2, model = $3, prompt_version = $4, "
            "attempts = $5, shown_texts = $6, finished_at = now() where id = $1",
            batch["id"], h, model, prompt_version, attempts, [q["text"] for q in qs if q["type"] != "write_more"])


async def _fail(conn, batch_id: uuid.UUID, reason: str, detail: str):
    await conn.execute(
        "update question_batch set status = 'failed', failure_reason = $2, error_detail = $3, finished_at = now() "
        "where id = $1 and status = 'pending'", batch_id, reason, detail)
    log.warning("batch %s 실패 (%s)", batch_id, reason)


# ---------- 더 적어보기 답 (POST /questions/{id}/answer) ----------

async def answer_question(conn: asyncpg.Connection, user_id: uuid.UUID, question_id: uuid.UUID, text: str) -> dict:
    """더 적어보기에 쓴 한 줄을 저장한다 (기능명세서 14.1 '저장'). 질문을 자동으로 다시 만들지 않는다. 재생성 1회가 생길 뿐이다.
       답 원문은 로그에 남기지 않는다.
       TODO(기획): 답을 기록의 어느 칸에 넣을지 정해지면 옮긴다. 지금은 question.answer (정리 확인 follow_up 과 같은 문제)."""
    text = (text or "").strip()
    q = await conn.fetchrow("select user_id, type, answer, answered_at from question where id = $1", question_id)
    if q is None or q["user_id"] != user_id:
        raise AppError("NOT_FOUND", "질문 없음")
    if q["type"] != "write_more":
        raise AppError("VALIDATION_FAILED", "더 적어보기 질문만 답할 수 있음", {"field": "id"})
    if not 1 <= len(text) <= QUESTION_ANSWER_MAX:
        raise AppError("VALIDATION_FAILED", f"답은 1~{QUESTION_ANSWER_MAX}자", {"field": "text", "max": QUESTION_ANSWER_MAX})
    at = await conn.fetchval(
        "update question set answer = $2, answered_at = now() where id = $1 and answer is null returning answered_at",
        question_id, text)
    if at is None:   # 이미 답했다. 같은 글이면 재전송으로 보고 그대로 돌려준다
        q = await conn.fetchrow("select answer, answered_at from question where id = $1", question_id)
        if q is None:
            raise AppError("NOT_FOUND", "질문 없음")
        if q["answer"] != text:
            raise AppError("VALIDATION_FAILED", "이미 답한 질문", {"field": "answer"})
        at = q["answered_at"]
    return {"id": str(question_id), "answered_at": at.isoformat()}
