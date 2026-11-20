"""관련 경험: 후보 뽑기 → LLM 재정렬 → related_cache (CLAUDE.local.md 5장).
   남의 카드는 experience_public 뷰와 match_public_cards() 로만 읽는다. 질문 주인의 출처 경험은 본인 것이라 원본을 읽는다.
   qa·reaction 내용은 읽지 않는다 (POL-07)."""
from __future__ import annotations
import logging
import re
import uuid

import asyncpg

from ..cards.public import fetch_public_rows, snapshot_of, to_public_card
from ..constants import (RELATED_CANDIDATES, RELATED_FILTER_CANDIDATES, RELATED_MAX, RELATED_MIN_SIMILARITY,
                         RELATED_NOTIFY_RECHECK_LIMIT, RELATED_QUERY_WITH_CONTEXT)
from ..embedding import client as embed_client
from ..embedding.text import card_text
from ..errors import AppError
from ..llm.client import LLMConnectionError, LLMModelError
from . import rerank as rr

log = logging.getLogger("related")

_FILTER_RE = re.compile(r"^(all|same_role|different_role|genre:\S.{0,19})$")
_Q_COLS = "id, user_id, experience_id, text, source_activity_name, type"


def valid_filter(f: str) -> bool:
    return bool(_FILTER_RE.match(f))


def _norm(s) -> str:
    return "".join(str(s or "").split()).lower()


def passes_filter(filter_: str, snap: dict, source: dict) -> bool:
    """filter 를 후보 하나에 적용. 역할은 자유 입력이라 공백·대소문자만 무시하고 같은지 본다.
       TODO(기획): '같은 역할'을 글자 일치로 볼지 비슷한 역할까지 묶을지 명세에 없음. 지금은 글자 일치."""
    if filter_ == "all":
        return True
    if filter_.startswith("genre:"):
        return _norm(snap.get("genre")) == _norm(filter_[6:])
    my_role = _norm(source.get("role"))
    if not my_role:                       # 출처 경험이 지워져 역할을 모름 → 같은 역할은 없음, 다른 역할은 전부
        return filter_ == "different_role"
    same = _norm(snap.get("role")) == my_role
    return same if filter_ == "same_role" else not same


async def load_source(conn: asyncpg.Connection, q) -> dict:
    """질문이 나온 경험(질문 주인 본인 것). 지워졌으면 질문에 남겨둔 활동명만."""
    if q["experience_id"] is not None:
        row = await conn.fetchrow(
            "select activity_name, genre, role, did_chips, custom_chips from experience where id = $1 and user_id = $2",
            q["experience_id"], q["user_id"])
        if row is not None:
            return dict(row)
    return {"activity_name": q["source_activity_name"]}


def query_text(source: dict, question: str) -> str:
    """검색어 = 출처 경험 맥락(활동·장르·역할·한 일) + 질문. 질문만 넣으면 맞는 카드도 0.5대에 머문다 (11/14 실측).
       카드 임베딩과 같은 card_text() 꼴로 맥락을 만들어 문단 모양을 맞춘다."""
    q = " ".join(question.split())
    if not RELATED_QUERY_WITH_CONTEXT:
        return q
    ctx = card_text({k: source.get(k) for k in ("activity_name", "genre", "role", "did_chips", "custom_chips")})
    return f"{ctx}. {q}" if ctx else q


# ---------- 계산 ----------

async def compute(conn: asyncpg.Connection, q, filter_: str, *, source: dict | None = None,
                  query_vec: list[float] | None = None) -> tuple[list[dict], str | None, bool]:
    """(items, model, cacheable). items = [{experience_id, reason, score}] 관련 큰 순서, 최대 RELATED_MAX.
       cacheable=False 는 LLM 재정렬이 실패해 유사도 순으로 대신 준 경우 — 저장하지 않고 다음에 다시 계산한다."""
    source = source if source is not None else await load_source(conn, q)
    if query_vec is None:
        try:
            query_vec = await embed_client.embed_one(query_text(source, q["text"]))
        except embed_client.EmbedError as e:
            log.warning("관련 경험 검색어 임베딩 실패 question=%s (%s)", q["id"], e)
            raise AppError("AI_UNAVAILABLE", "임베딩 서버가 응답하지 않음") from e

    k = RELATED_CANDIDATES if filter_ == "all" else RELATED_FILTER_CANDIDATES
    rows = await conn.fetch(
        "select m.experience_id, m.similarity, p.published_snapshot "
        "from match_public_cards($1::vector, $2, $3, $4) m "
        "join experience_public p on p.experience_id = m.experience_id "
        "order by m.similarity desc", embed_client.to_pg(query_vec), q["user_id"], k, RELATED_MIN_SIMILARITY)
    cands = []
    for r in rows:
        snap = snapshot_of(r)
        if snap is not None and passes_filter(filter_, snap, source):
            cands.append((r["experience_id"], float(r["similarity"]), snap))
    cands = cands[:RELATED_CANDIDATES]
    if not cands:
        return [], None, True          # 임계값 위 후보가 없음 → 빈 목록. 억지로 채우지 않는다

    source_text = card_text(source)
    try:
        ranked, model = await rr.rerank(q["text"], source_text, [card_text(s) for _, _, s in cands])
    except (LLMConnectionError, LLMModelError) as e:
        log.warning("관련 경험 재정렬 실패 question=%s (%s) — 유사도 순으로 대신 줌", q["id"], type(e).__name__)
        return ([{"experience_id": str(eid), "reason": None, "score": round(sim, 4)} for eid, sim, _ in cands[:RELATED_MAX]],
                None, False)
    items = [{"experience_id": str(cands[x.index][0]), "reason": x.reason, "score": round(cands[x.index][1], 4)}
             for x in ranked]
    return items, model, True


async def _store(conn: asyncpg.Connection, question_id: uuid.UUID, filter_: str, items: list[dict], model: str | None):
    await conn.execute(
        "insert into related_cache (question_id, filter, items, model) values ($1, $2, $3, $4) "
        "on conflict (question_id, filter) do update set items = excluded.items, model = excluded.model, computed_at = now()",
        question_id, filter_, items, model)


async def _resolve(conn: asyncpg.Connection, items: list[dict], viewer: uuid.UUID) -> tuple[list[dict], bool]:
    """저장된 items → 응답 모양. 꺼낼 때마다 다시 거른다: 뷰에 없는 것(비공개·삭제), 내 것, 차단 관계.
       (결과, stale). stale = 담아둔 카드가 뷰에서 사라짐 → 부르는 쪽이 다시 계산한다."""
    ids = [uuid.UUID(it["experience_id"]) for it in items]
    rows = await fetch_public_rows(conn, ids, viewer)
    out, stale = [], False
    for it, eid in zip(items, ids):
        row = rows.get(eid)
        if row is None:
            stale = True
            continue
        if row["blocked"] or row["user_id"] == viewer:
            continue                    # 차단은 읽을 때 빼기만 (API 명세 /related). 내 경험은 내 질문에 안 붙인다
        card = to_public_card(row, viewer)
        if card is not None:
            out.append({"card": card, "reason": it.get("reason")})
    return out[:RELATED_MAX], stale


# ---------- 조회 (POST /related) ----------

async def get_related(conn: asyncpg.Connection, user_id: uuid.UUID, question_id: uuid.UUID, filter_: str) -> list[dict]:
    if not valid_filter(filter_):
        raise AppError("VALIDATION_FAILED", "filter 는 all / same_role / different_role / genre:{장르}", {"filter": filter_})
    q = await conn.fetchrow(f"select {_Q_COLS} from question where id = $1", question_id)
    if q is None or q["user_id"] != user_id:
        raise AppError("NOT_FOUND", "질문 없음")
    if q["type"] == "write_more":   # 더 적어보기는 관련 경험을 붙이지 않는다 (기능명세서 14.1)
        raise AppError("VALIDATION_FAILED", "더 적어보기 질문에는 관련 경험이 없음", {"field": "question_id"})

    cached = await conn.fetchval("select items from related_cache where question_id = $1 and filter = $2", question_id, filter_)
    if cached is not None:
        out, stale = await _resolve(conn, cached, user_id)
        if not stale:
            return out
        # 담아둔 카드가 비공개·삭제로 사라졌다 → 읽을 때 거르기만 하지 않고 다시 계산 (지은 11/14)

    items, model, cacheable = await compute(conn, q, filter_)
    if cacheable:
        await _store(conn, question_id, filter_, items, model)
    out, _ = await _resolve(conn, items, user_id)
    return out


# ---------- 새 공개 카드가 생겼을 때 (임베딩 직후) ----------

async def on_new_card(conn: asyncpg.Connection, experience_id: uuid.UUID, owner_id: uuid.UUID) -> int:
    """notify_on_related 가 켜져 있고 관련 경험이 아직 0개인 질문만 새 카드와 맞춰본다 (지은 11/14).
       새 카드가 임계값을 넘는 질문만 다시 계산하고, 결과가 생기면 캐시를 갱신하고 related_new 를 켠다. 켠 질문 수 반환.
       알림은 여기서 보내지 않는다 — 하루 한 번 오전 9시에 묶어서 (CLAUDE.local.md 9장)."""
    qs = await conn.fetch(
        f"select {', '.join('q.' + c for c in _Q_COLS.split(', '))} from question q "
        "where q.notify_on_related and q.type <> 'write_more' and q.user_id <> $1 "
        "  and not exists (select 1 from related_cache c where c.question_id = q.id and c.filter = 'all' "
        "                  and jsonb_array_length(c.items) > 0) "
        "  and not exists (select 1 from block b where (b.user_id = q.user_id and b.blocked_id = $1) "
        "                                         or (b.user_id = $1 and b.blocked_id = q.user_id)) "
        "order by q.created_at desc limit $2", owner_id, RELATED_NOTIFY_RECHECK_LIMIT)
    if not qs:
        return 0
    sources = [await load_source(conn, q) for q in qs]
    try:
        vecs = await embed_client.embed([query_text(s, q["text"]) for s, q in zip(sources, qs)])
    except embed_client.EmbedError as e:
        log.warning("새 카드 %s 맞춰보기: 검색어 임베딩 실패 (%s)", experience_id, e)
        return 0

    hit = 0
    for q, source, vec in zip(qs, sources, vecs):
        sim = await conn.fetchval("select 1 - (embedding <=> $1::vector) from experience where id = $2 and embedding is not null",
                                  embed_client.to_pg(vec), experience_id)
        if sim is None or sim < RELATED_MIN_SIMILARITY:
            continue
        try:
            items, model, cacheable = await compute(conn, q, "all", source=source, query_vec=vec)
        except AppError:
            continue
        if not cacheable or not items:
            continue                    # LLM 이 죽었거나 재정렬에서 다 빠짐 → 다음 새 카드 때 다시 본다
        async with conn.transaction():
            await _store(conn, q["id"], "all", items, model)
            # 다른 filter 캐시는 지워서 다음에 열 때 새 카드를 넣고 다시 계산하게 한다
            await conn.execute("delete from related_cache where question_id = $1 and filter <> 'all'", q["id"])
            await conn.execute("update question set related_new = true where id = $1", q["id"])
        hit += 1
        # TODO(6단계 알림): related_found 는 오전 9시 작업이 related_new 인 질문을 사용자별로 모아 하나로 보낸다
    if hit:
        log.info("새 카드 %s → 관련 경험이 새로 붙은 질문 %d개", experience_id, hit)
    return hit
