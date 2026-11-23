"""분석 서비스 (CLAUDE.local.md 6장, API 명세 POST /analysis). 모든 DB 접근은 여기.
   읽는 테이블: experience, experience_log, question(담은 것), analysis_run, analysis_pattern. 전부 본인 행만.
   qa·reaction 은 절대 읽지 않는다 (POL-07)."""
from __future__ import annotations
import logging
import uuid

import asyncpg

from ..constants import (ANALYSIS_EDIT_MAX, ANALYSIS_INPUT_MAX_RECORDS, ANALYSIS_MIN_RECORDS, ANALYSIS_PATTERN_MAX,
                         ANALYSIS_RESURFACE_MIN_RECORDS, ANALYSIS_RESURFACE_NEW_EVIDENCE, PATTERN_MIN_EVIDENCE,
                         QUESTION_SAVE_MAX)
from ..errors import AppError
from ..llm.client import LLMConnectionError, LLMModelError
from .generate import find_patterns
from .prompt import experience_text, log_text, records_hash
from .schemas import Record

log = logging.getLogger("analysis")

FEEDBACK = ("yes", "edit", "no", "later")   # 맞아요 / 수정할게요 / 아니에요 / 아직 모르겠어요
# 다시 분석해도 남기는 패턴. later 는 '확인 중'으로 남긴다 (기능명세서 15장). 응답이 없는 것은 지우고 새로 만든다
KEEP_ON_RECOMPUTE = ("yes", "edit", "later")
REASK_MORE = "more_evidence"                # 확인 중이던 패턴의 근거가 늘었다: '기록이 늘었어요. 다시 확인해볼까요?'
REASK_REPEATED = "repeated"                 # 아니에요였던 패턴이 새 기록에서 또 보인다: '새 기록 N개에서도 이런 선택이 보여요'


# ---------- 읽기 ----------

async def load_records(conn: asyncpg.Connection, user_id: uuid.UUID) -> list[Record]:
    """본인의 경험과 회차 기록. 오래된 것부터, 경험 바로 뒤에 그 회차 기록."""
    exps = await conn.fetch("select * from experience where user_id = $1 order by created_at, id", user_id)
    logs = await conn.fetch(
        "select id, experience_id, log_date, situation_chips, emotion_chips, one_liner, created_at from experience_log "
        "where user_id = $1 order by log_date, created_at, id", user_id)
    by_exp: dict[uuid.UUID, list[asyncpg.Record]] = {}
    for lg in logs:
        by_exp.setdefault(lg["experience_id"], []).append(lg)
    out: list[Record] = []
    for i, e in enumerate(exps, 1):
        out.append(Record(key=f"e{i}", experience_id=str(e["id"]), activity_name=e["activity_name"],
                          text=experience_text(e), created_at=e["created_at"]))
        for j, lg in enumerate(by_exp.get(e["id"], []), 1):
            out.append(Record(key=f"e{i}.l{j}", experience_id=str(e["id"]), activity_name=e["activity_name"],
                              text=log_text(e["activity_name"], lg), log_id=str(lg["id"]), log_date=str(lg["log_date"]),
                              created_at=lg["created_at"]))
    return out


async def saved_questions(conn: asyncpg.Connection, user_id: uuid.UUID) -> list[dict]:
    """담은 질문. 컬럼 이름은 question 테이블 그대로 (앱은 다른 화면에서 이 테이블을 직접 읽는다)."""
    rows = await conn.fetch(
        "select id, experience_id, source_activity_name, type, axis, text, source, memo, is_done, done_dismissed, "
        " linked_experience_id, related_new, created_at from question "
        "where user_id = $1 and saved order by created_at desc limit $2", user_id, QUESTION_SAVE_MAX)
    return [{"id": str(r["id"]), "experience_id": str(r["experience_id"]) if r["experience_id"] else None,
             "source_activity_name": r["source_activity_name"], "type": r["type"], "axis": r["axis"], "text": r["text"],
             "source": r["source"], "memo": r["memo"], "is_done": r["is_done"], "done_dismissed": r["done_dismissed"],
             "linked_experience_id": str(r["linked_experience_id"]) if r["linked_experience_id"] else None,
             "related_new": r["related_new"], "created_at": r["created_at"].isoformat()} for r in rows]


def _live_evidence(evidence, records: list[Record]) -> list[dict]:
    """저장된 근거 중 지금도 있는 기록만. 활동명은 지금 이름으로 바꾼다."""
    exp_name = {r.experience_id: r.activity_name for r in records}
    log_ids = {r.log_id for r in records if r.log_id}
    out = []
    for ev in evidence or []:
        eid, lid = ev.get("experience_id"), ev.get("log_id")
        if eid not in exp_name or (lid and lid not in log_ids):
            continue
        out.append({"experience_id": eid, "activity_name": exp_name[eid], "log_id": lid, "log_date": ev.get("log_date")})
    return out


def _pattern_out(row, evidence: list[dict]) -> dict:
    return {"id": str(row["id"]), "text": row["text"], "evidence": evidence, "feedback": row["feedback"],
            "edited_text": row["edited_text"], "reask": row["reask"], "reask_new_count": row["reask_new_count"]}


def _waiting(row) -> bool:
    """'확인 중'이고 아직 다시 물을 때가 아닌 패턴. 다른 패턴 뒤로 밀리고 새 패턴의 자리를 차지하지 않는다 (15장)."""
    return row["feedback"] == "later" and row["reask"] is None


def _visible(rows, records: list[Record]) -> list[dict]:
    """내려줄 패턴. 근거 수 많은 순으로 최대 ANALYSIS_PATTERN_MAX 개, 확인 중 패턴은 그 뒤에서 자리가 남을 때만.
       같은 근거 수면 먼저 만든 것이 앞 (한 번에 만든 것끼리는 모델이 낸 순서)."""
    items = []
    for r in rows:
        ev = _live_evidence(r["evidence"], records)
        if len(ev) >= PATTERN_MIN_EVIDENCE:      # 근거 기록이 지워져 모자라진 패턴은 보여주지 않는다
            items.append((r, ev))
    items.sort(key=lambda x: (_waiting(x[0]), -len(x[1]), x[0]["created_at"], str(x[0]["id"])))
    return [_pattern_out(r, ev) for r, ev in items[:ANALYSIS_PATTERN_MAX]]


def _merge(evidence: list[dict], keys: list[str], by_key: dict[str, Record], after=None) -> tuple[list[dict], int]:
    """근거에 모델이 다시 찾은 기록을 더한다. (합친 근거, 새로 붙은 수). 있던 근거는 모델이 빼먹어도 그대로 둔다.
       after 가 있으면 그 시각 뒤에 쓴 기록만 더한다."""
    have = {(ev["experience_id"], ev["log_id"]) for ev in evidence}
    merged, added = list(evidence), 0
    for k in keys:
        r = by_key.get(k)
        if r is None or (r.experience_id, r.log_id) in have:
            continue
        if after is not None and (r.created_at is None or r.created_at <= after):
            continue
        have.add((r.experience_id, r.log_id))
        merged.append({"experience_id": r.experience_id, "activity_name": r.activity_name,
                       "log_id": r.log_id, "log_date": r.log_date})
        added += 1
    return merged, added


# ---------- 분석 ----------

async def analyze(conn: asyncpg.Connection, user_id: uuid.UUID, force: bool) -> dict:
    """저장된 결과를 돌려주고, 처음이거나 (force 이고 기록이 바뀌었으면) 새로 계산한다.
       기록이 그대로면 force 여도 LLM 을 부르지 않는다 (6장 '새 기록이 없으면 저장된 결과')."""
    async with conn.transaction():
        # 같은 사용자의 요청이 겹치면 한 번만 계산하게 줄을 세운다 (뒤 요청은 저장된 결과를 받는다)
        await conn.execute("select pg_advisory_xact_lock(hashtextextended($1, 0))", f"analysis:{user_id}")
        records = await load_records(conn, user_id)
        saved = await saved_questions(conn, user_id)
        if len(records) < ANALYSIS_MIN_RECORDS:
            return {"locked": True, "record_count": len(records), "patterns": [], "criteria": _criteria(None),
                    "saved_questions": saved, "last_updated": None, "has_new_records": False, "reask_count": 0}
        h = records_hash(records)
        run = await conn.fetchrow("select * from analysis_run where user_id = $1", user_id)
        if run is None or (force and run["input_hash"] != h):
            await _recompute(conn, user_id, records, h)
            run = await conn.fetchrow("select * from analysis_run where user_id = $1", user_id)
        rows = await conn.fetch("select * from analysis_pattern where user_id = $1 and not hidden", user_id)
        patterns = _visible(rows, records)
        return {"locked": False, "record_count": len(records), "patterns": patterns, "criteria": _criteria(run["criteria"]),
                "saved_questions": saved, "last_updated": run["last_updated"].isoformat(),
                "has_new_records": run["input_hash"] != h,
                "reask_count": sum(1 for p in patterns if p["reask"])}   # 홈 '다시 확인할 패턴이 N개 있어요'


def _criteria(stored) -> dict:
    # TODO(기획): criteria.private / criteria.public 이 무엇인지 명세·CLAUDE 어디에도 없다. 정해질 때까지 만들지 않고 null 로 준다
    stored = stored if isinstance(stored, dict) else {}
    return {"private": stored.get("private"), "public": stored.get("public")}


async def _recompute(conn: asyncpg.Connection, user_id: uuid.UUID, records: list[Record], h: str) -> None:
    """분석 업데이트 (기능명세서 15장: 확인 중·숨긴 패턴까지 포함해 전체를 다시 본다).
       남길 패턴은 근거를 다시 세고, 모자란 만큼만 새로 만든다. 호출하는 쪽의 트랜잭션 안에서 돈다 (LLM 이 실패하면 전부 되돌아감)."""
    rows = await conn.fetch("select * from analysis_pattern where user_id = $1 order by created_at, id", user_id)
    hidden = [r for r in rows if r["hidden"]]                   # "아니에요". 남겨두고 닮은 패턴을 막는 데 쓴다
    kept: list[tuple[asyncpg.Record, list[dict]]] = []
    for r in rows:
        if r["hidden"]:
            continue
        ev = _live_evidence(r["evidence"], records)
        again = r["resurfaced"] and r["feedback"] is None       # 다시 보여줬는데 아직 응답이 없는 패턴
        if len(ev) >= PATTERN_MIN_EVIDENCE and (r["feedback"] in KEEP_ON_RECOMPUTE or again):
            kept.append((r, ev))
        elif again:
            # 근거가 지워져 못 보여주게 됐다. 지우면 막는 목록에서도 빠지니 "아니에요"로 되돌려 둔다
            await conn.execute("update analysis_pattern set hidden = true, feedback = 'no', reask = null, "
                               "reask_new_count = null, updated_at = now() where id = $1", r["id"])
            hidden.append(r)
        else:
            await conn.execute("delete from analysis_pattern where id = $1", r["id"])

    existing = [r["text"] for r, _ in kept]    # 원래 문장만. 사용자가 고친 문장은 입력에 넣지 않는다 (6장)
    blocked = [r["text"] for r in hidden]
    # 확인 중 패턴은 자리를 차지하지 않는다. 더 뚜렷한 패턴이 생기면 한도 밖으로 밀린다 (15장)
    need = max(0, ANALYSIS_PATTERN_MAX - sum(1 for r, _ in kept if not _waiting(r)))
    window = records[-ANALYSIS_INPUT_MAX_RECORDS:]
    by_key = {r.key: r for r in window}
    try:
        outcome = await find_patterns(window, existing, blocked, need)
    except (LLMConnectionError, LLMModelError) as err:
        log.warning("분석 실패 (%s)", type(err).__name__)
        raise AppError("AI_UNAVAILABLE", "분석을 만들지 못함",
                       {"reason": "connection" if isinstance(err, LLMConnectionError) else "model"}) from err

    # 남긴 패턴: 근거를 다시 센다. 확인 중이던 패턴은 응답할 때보다 근거가 늘었으면 다시 묻는다
    # TODO(기획): "근거가 늘었다"를 다시 분석할 때 모델이 새 기록을 그 패턴의 근거로 붙였을 때로 읽었다
    for i, (r, ev) in enumerate(kept, 1):
        merged, added = _merge(ev, outcome.rechecked.get(f"p{i}", []), by_key)
        reask = r["reask"]
        base = r["feedback_evidence_count"] if r["feedback_evidence_count"] is not None else len(ev)
        if r["feedback"] == "later" and added and len(merged) > base:
            reask = REASK_MORE
        if merged != r["evidence"] or reask != r["reask"]:
            await conn.execute("update analysis_pattern set evidence = $2::jsonb, reask = $3, updated_at = now() "
                               "where id = $1", r["id"], merged, reask)   # 풀 연결의 jsonb 코덱이 list 를 받는다

    # 숨긴 패턴: 기록이 충분히 쌓였고 "아니에요" 뒤에 쓴 기록에서 또 보이면 한 번만 다시 보여준다 (15장)
    if len(records) >= ANALYSIS_RESURFACE_MIN_RECORDS:
        for i, r in enumerate(hidden, 1):
            if r["excluded"] or r["resurfaced"] or r["feedback_at"] is None:
                continue
            ev = _live_evidence(r["evidence"], records)
            merged, added = _merge(ev, outcome.rechecked.get(f"n{i}", []), by_key, after=r["feedback_at"])
            if added >= ANALYSIS_RESURFACE_NEW_EVIDENCE and len(merged) >= PATTERN_MIN_EVIDENCE:
                await conn.execute(
                    "update analysis_pattern set hidden = false, resurfaced = true, feedback = null, reask = $2, "
                    " reask_new_count = $3, evidence = $4::jsonb, updated_at = now() where id = $1",
                    r["id"], REASK_REPEATED, added, merged)

    for p in outcome.patterns:
        ev = [{"experience_id": by_key[k].experience_id, "activity_name": by_key[k].activity_name,
               "log_id": by_key[k].log_id, "log_date": by_key[k].log_date} for k in p.keys]
        # now() 는 트랜잭션 시작 시각이라 한 번에 넣은 패턴이 전부 같은 시각이 된다. 모델이 낸 순서를 지키려고 clock_timestamp()
        await conn.execute("insert into analysis_pattern (user_id, text, evidence, created_at) "
                           "values ($1, $2, $3::jsonb, clock_timestamp())", user_id, p.text, ev)
    await conn.execute(
        "insert into analysis_run (user_id, record_count, criteria, input_hash, model, last_updated) "
        "values ($1, $2, null, $3, $4, now()) "
        "on conflict (user_id) do update set record_count = excluded.record_count, input_hash = excluded.input_hash, "
        " model = coalesce(excluded.model, analysis_run.model), last_updated = now()",
        user_id, len(records), h, outcome.model)


# ---------- 피드백 ----------

async def set_feedback(conn: asyncpg.Connection, user_id: uuid.UUID, pattern_id: uuid.UUID, feedback: str,
                       edited_text: str | None) -> dict:
    if feedback not in FEEDBACK:
        raise AppError("VALIDATION_FAILED", "feedback 값이 아님", {"field": "feedback", "allowed": list(FEEDBACK)})
    edited = None
    if feedback == "edit":
        edited = " ".join((edited_text or "").split())
        if not edited or len(edited) > ANALYSIS_EDIT_MAX:
            raise AppError("VALIDATION_FAILED", f"고친 문장은 1~{ANALYSIS_EDIT_MAX}자", {"field": "edited_text", "max": ANALYSIS_EDIT_MAX})
    # 남의 패턴도 없는 것과 똑같이 404
    row = await conn.fetchrow(
        "update analysis_pattern set feedback = $3::text, hidden = ($3::text = 'no'), "
        " excluded = ($3::text = 'no' and resurfaced), "      # 다시 보여준 패턴에 또 아니에요 → 영구 제외 (15장)
        " edited_text = case when $3::text = 'edit' then $4::text else edited_text end, "
        " feedback_at = now(), feedback_evidence_count = jsonb_array_length(evidence), "
        " reask = null, reask_new_count = null, updated_at = now() "
        "where id = $1 and user_id = $2 returning *", pattern_id, user_id, feedback, edited)
    if row is None:
        raise AppError("NOT_FOUND", "패턴 없음")
    out = _pattern_out(row, row["evidence"])
    out["hidden"] = row["hidden"]
    return out
