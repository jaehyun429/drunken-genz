"""GET /cards · GET /cards/{id} · POST /reactions (API 명세 2부 탐색·카드)."""
from __future__ import annotations
import uuid
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import db
from ..auth import current_user_id
from . import service

router = APIRouter(tags=["cards"])


class ReactionIn(BaseModel):
    experience_id: uuid.UUID
    chip: str
    note: str | None = None


@router.get("/cards")
async def cards(genre: str | None = None, role_filter: str | None = None, cursor: str | None = None,
                user_id: uuid.UUID = Depends(current_user_id)):
    """공개 카드 목록. 최신 공개 순 20개씩. next_cursor 가 null 이면 끝."""
    async with db.pool().acquire() as conn:
        return await service.list_cards(conn, user_id, genre=genre, role_filter=role_filter, cursor=cursor)


@router.get("/cards/{experience_id}")
async def card(experience_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user_id)):
    """카드 상세. 없음·비공개·차단은 404 NOT_FOUND."""
    async with db.pool().acquire() as conn:
        return await service.card_detail(conn, user_id, experience_id)


@router.post("/reactions")
async def reactions(body: ReactionIn, user_id: uuid.UUID = Depends(current_user_id)):
    """반응 남기기. 같은 카드에 다시 보내면 교체."""
    async with db.pool().acquire() as conn:
        return await service.react(conn, user_id, body.experience_id, body.chip, body.note)
