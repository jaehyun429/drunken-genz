"""PublicCard — 탐색 목록·카드 상세·관련 경험이 같이 쓰는 모양 하나 (API 명세 0번).
   읽는 건 소윤의 experience_public 뷰뿐이다. 원본 experience 는 읽지 않는다 (CLAUDE.local.md 5장).
   뷰에 없으면(비공개·삭제) 결과에 안 나오고, 차단 관계면 blocked=True 로 표시해 부르는 쪽이 뺀다."""
from __future__ import annotations
import json
import uuid

import asyncpg

# 스냅샷 안의 키 이름은 experience 컬럼과 같고, 글자는 이미 가려진 값이다 (소윤 publish/snapshot.py, 11/20 확정).
# masked 는 가린 칸의 이름 목록뿐이라 카드에는 쓰지 않는다.

_qa_table = False


async def has_qa_table(conn: asyncpg.Connection) -> bool:
    """qa 는 소윤 테이블인데 아직 로컬 임시 테이블뿐이다 (Supabase 에는 없을 수 있다).
       없으면 Q&A 개수는 0, 목록은 빈 것으로 준다. 생기면 다음 요청부터 읽는다 (있다고 확인되면 다시 안 묻는다)."""
    global _qa_table
    if not _qa_table:
        _qa_table = await conn.fetchval("select to_regclass('qa') is not null")
    return _qa_table


def qa_count_sql(alias: str = "p") -> str:
    # TODO(소윤): 접힌(no_answer)·숨긴 Q&A 를 세는지. 지금은 전부 센다
    return f"(select count(*) from qa where qa.experience_id = {alias}.experience_id)"


BLOCKED_SQL = ("exists (select 1 from block b where (b.user_id = $2 and b.blocked_id = p.user_id) "
               "or (b.user_id = p.user_id and b.blocked_id = $2))")


def snapshot_of(row) -> dict | None:
    """뷰 행 → 스냅샷 dict. 비어 있거나 활동명이 없으면 None (카드로 못 만듦)."""
    snap = row["published_snapshot"]
    if isinstance(snap, str):          # jsonb 코덱이 없는 연결
        snap = json.loads(snap)
    if not isinstance(snap, dict) or not snap.get("activity_name"):
        return None
    return snap


async def fetch_public_rows(conn: asyncpg.Connection, ids: list[uuid.UUID], viewer: uuid.UUID) -> dict[uuid.UUID, asyncpg.Record]:
    """experience_public 에서 id 들을 읽는다. 키 = experience_id. 뷰에 없는 id 는 빠진다.
       qa_count 는 개수만 센다 — Q&A 내용은 읽지 않고, 이 값은 LLM 입력에 넣지 않는다 (POL-07)."""
    if not ids:
        return {}
    qa_count = qa_count_sql() if await has_qa_table(conn) else "0"
    rows = await conn.fetch(
        "select p.experience_id, p.user_id, p.published_snapshot, p.published_at, p.accept_questions, "
        f"  {qa_count} as qa_count, {BLOCKED_SQL} as blocked "
        "from experience_public p where p.experience_id = any($1::uuid[])", ids, viewer)
    return {r["experience_id"]: r for r in rows}


def to_public_card(row, viewer: uuid.UUID) -> dict | None:
    """뷰 행 → PublicCard. 스냅샷이 비었으면 None."""
    snap = snapshot_of(row)
    if snap is None:
        return None
    official = snap.get("official_activity")
    if not (isinstance(official, dict) and official.get("name")):
        official = None
    else:
        official = {"name": official["name"], "verified": bool(official.get("verified", False))}
    return {
        "experience_id": str(row["experience_id"]),
        "activity_name": snap.get("activity_name"),
        "genre": snap.get("genre"),
        "role": snap.get("role"),
        "did_chips": list(snap.get("did_chips") or []),
        "one_liner": snap.get("one_liner"),
        "official_activity": official,
        "official_period": snap.get("official_period") if official else None,   # 기수 1~99. 가렸으면 null (소윤 11/20)
        "published_at": row["published_at"].isoformat() if row["published_at"] else None,
        "accept_questions": bool(row["accept_questions"]),
        "qa_count": int(row["qa_count"]),
        "is_mine": row["user_id"] == viewer,
    }
