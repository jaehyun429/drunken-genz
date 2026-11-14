"""PublicCard — 탐색 목록·카드 상세·관련 경험이 같이 쓰는 모양 하나 (API 명세 0번).
   읽는 건 소윤의 experience_public 뷰뿐이다. 원본 experience 는 읽지 않는다 (CLAUDE.local.md 5장).
   뷰에 없으면(비공개·삭제) 결과에 안 나오고, 차단 관계면 blocked=True 로 표시해 부르는 쪽이 뺀다."""
from __future__ import annotations
import json
import uuid

import asyncpg

# 스냅샷 안의 키 이름은 experience 컬럼과 같다고 가정 (embedding/text.py 와 같은 가정).
# TODO(소윤): published_snapshot 모양이 확정되면 여기와 embedding/text.py 두 곳만 맞춘다.
# TODO(소윤): 가림 값이 스냅샷에 이미 반영돼 있는지, masked 컬럼에 따로 있는지 확인. 지금은 스냅샷이 가림본이라고 본다.


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
    rows = await conn.fetch(
        "select p.experience_id, p.user_id, p.published_snapshot, p.published_at, p.accept_questions, "
        "  (select count(*) from qa where qa.experience_id = p.experience_id) as qa_count, "   # TODO(소윤): 접힌(no_answer)·숨긴 Q&A 를 세는지
        "  exists (select 1 from block b where (b.user_id = $2 and b.blocked_id = p.user_id) "
        "                                  or (b.user_id = p.user_id and b.blocked_id = $2)) as blocked "
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
        "published_at": row["published_at"].isoformat() if row["published_at"] else None,
        "accept_questions": bool(row["accept_questions"]),
        "qa_count": int(row["qa_count"]),
        "is_mine": row["user_id"] == viewer,
    }
