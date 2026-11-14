"""공개 확정 웹훅. 소윤 쪽 DB 웹훅(experience UPDATE, published_at·visibility 바뀔 때)이 부른다.
   다시 공개·비공개 내림도 같은 주소로 오니 visibility 로 분기 (CLAUDE.local.md 8장)."""
from __future__ import annotations
import uuid
from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel

from ..auth import verify_webhook_secret
from . import service

router = APIRouter(tags=["embedding"])


class ExperiencePublishedWebhook(BaseModel):
    """Supabase Database Webhook 모양 (UPDATE on experience)."""
    type: str
    table: str
    record: dict
    old_record: dict | None = None


@router.post("/internal/experience-published", status_code=202, dependencies=[Depends(verify_webhook_secret)])
async def experience_published(payload: ExperiencePublishedWebhook, bg: BackgroundTasks):
    if payload.table != "experience" or payload.type not in ("UPDATE", "INSERT"):
        return {"ignored": True}
    experience_id = uuid.UUID(payload.record["id"])
    visibility = payload.record.get("visibility") or "private"
    bg.add_task(service.handle_published_change, experience_id, visibility)
    return {"experience_id": str(experience_id), "action": "unpublish" if visibility == "private" else "embed"}
