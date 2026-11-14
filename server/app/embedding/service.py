"""공개 확정 → 임베딩 채우기, 비공개 → 비우기 + 캐시 정리, 빈 곳 주기 점검 (CLAUDE.local.md 8장).
   experience 원본을 읽긴 하지만 '공개된 행'만, 그리고 스냅샷 컬럼만 쓴다. qa·reaction 은 읽지 않는다."""
from __future__ import annotations
import logging
import uuid
from datetime import timedelta

import asyncpg

from .. import db
from ..constants import EMBED_SWEEP_BATCH_LIMIT, EMBED_SWEEP_MIN_AGE_S
from .client import EmbedError, embed_one, to_pg
from .text import card_text, pick_source

log = logging.getLogger("embed")

_PUBLIC_COLS = ("id, user_id, visibility, published_snapshot, activity_name, genre, role, did_chips, did_notes, "
                "custom_chips, situation_chips, emotion_chips, one_liner, ai_tags")


async def embed_experience(conn: asyncpg.Connection, experience_id: uuid.UUID) -> bool:
    """공개된 경험 하나의 embedding 을 채운다. 비공개·없음이면 False. 임베딩 실패는 EmbedError."""
    row = await conn.fetchrow(f"select {_PUBLIC_COLS} from experience where id = $1", experience_id)
    if row is None or row["visibility"] == "private":
        return False
    text = card_text(pick_source(dict(row)))
    if not text:
        log.warning("임베딩 입력이 비어 건너뜀 experience=%s", experience_id)
        return False
    vec = await embed_one(text)
    await conn.execute("update experience set embedding = $1::vector where id = $2", to_pg(vec), experience_id)
    await after_embedded(conn, experience_id, row["user_id"])
    return True


async def after_embedded(conn: asyncpg.Connection, experience_id: uuid.UUID, owner_id: uuid.UUID) -> None:
    """새 공개 카드가 생긴 뒤: notify_on_related 켜진 질문과 맞춰보기 (CLAUDE.local.md 8장).
       여기서 실패해도 임베딩은 이미 저장됐으니 밖으로 던지지 않는다."""
    from ..related.service import on_new_card   # related 가 embedding.client 를 쓰므로 여기서 import
    try:
        await on_new_card(conn, experience_id, owner_id)
    except Exception:  # noqa: BLE001
        log.exception("새 카드 %s 관련 경험 맞춰보기 실패", experience_id)


async def unpublish_experience(conn: asyncpg.Connection, experience_id: uuid.UUID) -> int:
    """비공개로 내려감: embedding 비우고, 이 카드를 담고 있던 related_cache 를 지운다 (다음 조회 때 다시 계산).
       읽을 때 거르기만 하지 않는다 (지은 11/14). 지운 캐시 행 수 반환."""
    await conn.execute("update experience set embedding = null where id = $1", experience_id)
    # 풀 연결은 jsonb 코덱이 dict/list 를 받는다 (문자열을 주면 JSON 문자열로 들어가 매칭이 안 됨)
    deleted = await conn.execute(
        "delete from related_cache where items @> $1::jsonb", [{"experience_id": str(experience_id)}])
    n = int(deleted.split()[-1]) if deleted else 0
    if n:
        log.info("비공개 카드 %s 를 담은 related_cache %d건 삭제", experience_id, n)
    return n


async def handle_published_change(experience_id: uuid.UUID, visibility: str) -> None:
    """웹훅·sweep 공용. 백그라운드에서 돈다. 실패해도 예외를 밖으로 안 보내고 sweep 에 맡긴다."""
    async with db.pool().acquire() as conn:
        try:
            if visibility == "private":
                await unpublish_experience(conn, experience_id)
            else:
                await embed_experience(conn, experience_id)
        except EmbedError as e:
            log.warning("임베딩 실패 experience=%s (%s) — sweep 이 다시 시도", experience_id, e)


async def sweep_missing_embeddings() -> int:
    """공개됐는데 embedding 이 비어 있는 경험 → 채운다. 처리(성공)한 개수 반환."""
    pool = db.pool()
    done = 0
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "select id from experience where visibility <> 'private' and embedding is null "
            "  and coalesce(published_at, updated_at) < now() - $1::interval "
            "order by coalesce(published_at, updated_at) limit $2",
            timedelta(seconds=EMBED_SWEEP_MIN_AGE_S), EMBED_SWEEP_BATCH_LIMIT)
        for r in rows:
            try:
                if await embed_experience(conn, r["id"]):
                    done += 1
            except EmbedError as e:
                log.warning("sweep 임베딩 실패 experience=%s (%s)", r["id"], e)
                break   # 임베딩 서버가 죽은 거면 나머지도 안 될 테니 다음 주기로
    if done:
        log.info("sweep: 임베딩 비어 있던 공개 경험 %d개 채움", done)
    return done
