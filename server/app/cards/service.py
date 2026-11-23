"""탐색·카드·반응 (API 명세 2부 탐색·카드). 남의 카드는 experience_public 뷰로만 읽는다.
   여기서 읽는 Q&A·반응은 화면에 보여주려는 것이고 LLM 입력에는 넣지 않는다 (POL-07)."""
from __future__ import annotations
import base64
import uuid
from datetime import datetime

import asyncpg

from ..constants import CARDS_GENRE_MAX, CARDS_PAGE_SIZE, REACTION_CHIPS, REACTION_NOTE_MAX
from ..errors import AppError
from .public import BLOCKED_SQL, fetch_public_rows, has_qa_table, qa_count_sql, snapshot_of, to_public_card

ROLE_FILTERS = ("same", "different")
# 역할은 자유 입력이라 띄어쓰기·대소문자만 무시하고 같은지 본다 (related.service._norm 과 같은 기준)
_ROLE_SQL = r"lower(regexp_replace(coalesce(p.published_snapshot->>'role', ''), '\s', '', 'g'))"


def _norm(s) -> str:
    return "".join(str(s or "").split()).lower()


# ---------- cursor ----------

def encode_cursor(published_at: datetime, experience_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{published_at.isoformat()}|{experience_id}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        at, eid = raw.split("|")
        at = datetime.fromisoformat(at)
        if at.tzinfo is None:
            raise ValueError("시간대 없음")
        return at, uuid.UUID(eid)
    except Exception as e:   # 앱이 받은 값을 그대로 돌려주는 것이라 깨진 값은 전부 같은 400
        raise AppError("VALIDATION_FAILED", "cursor 가 올바르지 않음", {"field": "cursor"}) from e


# ---------- GET /cards ----------

async def list_cards(conn: asyncpg.Connection, viewer: uuid.UUID, *, genre: str | None, role_filter: str | None,
                     cursor: str | None) -> dict:
    """공개 카드 목록. 최신 공개 순, 20개씩. 내 카드와 차단 관계는 뺀다.
       role_filter 는 내 가장 최근 경험의 역할 기준 (명세). 그 역할을 모르면 same 은 빈 목록, different 는 전부."""
    genre = (genre or "").strip() or None
    role_filter = (role_filter or "").strip() or None
    if genre is not None and len(genre) > CARDS_GENRE_MAX:
        raise AppError("VALIDATION_FAILED", "genre 가 너무 김", {"field": "genre"})
    if role_filter is not None and role_filter not in ROLE_FILTERS:
        raise AppError("VALIDATION_FAILED", "role_filter 는 same / different", {"field": "role_filter"})
    after = decode_cursor(cursor) if cursor else None

    where = ["p.user_id <> $2", f"not {BLOCKED_SQL}", "p.published_at is not null",
             "coalesce(p.published_snapshot->>'activity_name', '') <> ''"]
    args: list = [CARDS_PAGE_SIZE + 1, viewer]
    if genre is not None:
        args.append(genre)
        where.append(f"p.published_snapshot->>'genre' = ${len(args)}")
    if role_filter is not None:
        # TODO(기획): '내 최근 경험'을 가장 최근에 만든 기록으로 읽었다. '같은 역할'은 글자 일치 (related 와 같은 미결)
        mine = _norm(await conn.fetchval(
            "select role from experience where user_id = $1 order by created_at desc limit 1", viewer))
        if not mine:
            if role_filter == "same":
                return {"items": [], "next_cursor": None}
        else:
            args.append(mine)
            where.append(f"{_ROLE_SQL} {'=' if role_filter == 'same' else '<>'} ${len(args)}")
    if after is not None:
        args += [after[0], after[1]]
        where.append(f"(p.published_at, p.experience_id) < (${len(args) - 1}::timestamptz, ${len(args)}::uuid)")

    qa_count = qa_count_sql() if await has_qa_table(conn) else "0"
    rows = await conn.fetch(
        "select p.experience_id, p.user_id, p.published_snapshot, p.published_at, p.accept_questions, "
        f"  {qa_count} as qa_count "
        f"from experience_public p where {' and '.join(where)} "
        "order by p.published_at desc, p.experience_id desc limit $1", *args)
    page = rows[:CARDS_PAGE_SIZE]
    items = [c for c in (to_public_card(r, viewer) for r in page) if c is not None]
    more = len(rows) > CARDS_PAGE_SIZE
    return {"items": items,
            "next_cursor": encode_cursor(page[-1]["published_at"], page[-1]["experience_id"]) if more else None}


# ---------- GET /cards/{id} ----------

async def _visible_row(conn: asyncpg.Connection, viewer: uuid.UUID, experience_id: uuid.UUID):
    """보이는 카드의 뷰 행. 없음·비공개·차단 관계는 전부 같은 404 (차단 티가 안 나게, 명세 0번)."""
    row = (await fetch_public_rows(conn, [experience_id], viewer)).get(experience_id)
    if row is None or row["blocked"] or snapshot_of(row) is None:
        raise AppError("NOT_FOUND", "카드 없음")
    return row


def _detail(snap: dict) -> dict:
    """카드 상세에만 나가는 칸. 전부 공개 스냅샷(가려진 값)에서. flow_summary 는 스냅샷에 없어 늘 null (소윤 흐름 요약이 생기면)."""
    return {
        "situation_chips": list(snap.get("situation_chips") or []),
        "situation_notes": snap.get("situation_notes"),
        "emotion_chips": list(snap.get("emotion_chips") or []),
        "conditions": snap.get("conditions") or {},
        "did_notes": snap.get("did_notes") or {},
        "flow_summary": snap.get("flow_summary"),
        "status": snap.get("status"),
        "type": snap.get("type"),
        "period_start": snap.get("period_start"),
        "period_end": snap.get("period_end"),
        "place": snap.get("place"),
    }


async def _qa_list(conn: asyncpg.Connection, viewer: uuid.UUID, experience_id: uuid.UUID) -> list[dict]:
    """카드에 달린 Q&A. 질문자는 익명이라 asker_id 는 내려주지 않고 is_mine 만 준다. 차단 관계인 질문자의 것은 뺀다.
       TODO(소윤): qa.status(no_answer)·helpful_reason 테이블이 생기면 여기서 읽는다. 지금은 답이 있으면 answered, 없으면 waiting"""
    if not await has_qa_table(conn):
        return []
    rows = await conn.fetch(
        "select q.id, q.type, q.question, q.answer, q.answered_at, q.asker_id from qa q "
        "where q.experience_id = $1 and not exists (select 1 from block b "
        "  where (b.user_id = $2 and b.blocked_id = q.asker_id) or (b.user_id = q.asker_id and b.blocked_id = $2)) "
        "order by q.created_at, q.id", experience_id, viewer)
    return [{"id": str(r["id"]), "type": r["type"], "question": r["question"], "answer": r["answer"],
             "status": "answered" if r["answer"] is not None else "waiting",
             "answered_at": r["answered_at"].isoformat() if r["answered_at"] else None,
             "is_mine": r["asker_id"] == viewer, "helpful_reasons": []} for r in rows]


async def card_detail(conn: asyncpg.Connection, viewer: uuid.UUID, experience_id: uuid.UUID) -> dict:
    """카드 + 상세 + Q&A + 내 반응. 내 카드도 볼 수 있다 (card.is_mine = true → 앱이 '내 카드예요', EXP-02-⑦)."""
    row = await _visible_row(conn, viewer, experience_id)
    my = await conn.fetchrow("select chip, note from reaction where experience_id = $1 and user_id = $2", experience_id, viewer)
    return {"card": to_public_card(row, viewer), "detail": _detail(snapshot_of(row)),
            "qa": await _qa_list(conn, viewer, experience_id),
            "my_reaction": my["chip"] if my else None, "my_reaction_note": my["note"] if my else None}


# ---------- POST /reactions ----------

async def react(conn: asyncpg.Connection, viewer: uuid.UUID, experience_id: uuid.UUID, chip: str, note: str | None) -> dict:
    """반응 남기기. 같은 카드에 다시 보내면 교체. 작성자에게 알리지 않는다."""
    if chip not in REACTION_CHIPS:
        raise AppError("VALIDATION_FAILED", "chip 은 " + " / ".join(REACTION_CHIPS), {"field": "chip"})
    note = (note or "").strip() or None
    if note is not None and len(note) > REACTION_NOTE_MAX:
        raise AppError("VALIDATION_FAILED", f"note 는 {REACTION_NOTE_MAX}자까지", {"field": "note", "max": REACTION_NOTE_MAX})
    row = await _visible_row(conn, viewer, experience_id)
    if row["user_id"] == viewer:
        # TODO(기획): 내 카드에 반응할 수 있는지 명세에 없다. 추천에만 쓰는 값이라 막았다
        raise AppError("VALIDATION_FAILED", "내 카드에는 반응을 남길 수 없음", {"field": "experience_id"})
    await conn.execute(
        "insert into reaction (experience_id, user_id, chip, note) values ($1, $2, $3, $4) "
        "on conflict (experience_id, user_id) do update set chip = excluded.chip, note = excluded.note, updated_at = now()",
        experience_id, viewer, chip, note)
    return {"experience_id": str(experience_id), "chip": chip, "note": note}
