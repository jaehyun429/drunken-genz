"""공개 확정 웹훅. 소윤 쪽 DB 웹훅(experience UPDATE, published_at·visibility 바뀔 때)이 부른다.
   다시 공개·비공개 내림도 같은 주소로 오니 visibility 로 분기 (CLAUDE.local.md 8장).
   공개된 경험을 삭제했을 때도 같은 주소로 type=DELETE 가 온다 (소윤 11/15, PR #2). 비공개 내림과 같게 처리."""
from __future__ import annotations
import uuid
from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel

from ..auth import verify_webhook_secret
from . import service

router = APIRouter(tags=["embedding"])


class ExperiencePublishedWebhook(BaseModel):
    """Supabase Database Webhook 모양 (experience 의 UPDATE·DELETE). DELETE 는 record 가 null 이고 old_record 에 지워진 행이 온다."""
    type: str
    table: str
    record: dict | None = None
    old_record: dict | None = None


@router.post("/internal/experience-published", status_code=202, dependencies=[Depends(verify_webhook_secret)])
async def experience_published(payload: ExperiencePublishedWebhook, bg: BackgroundTasks):
    if payload.table != "experience":
        return {"ignored": True}
    if payload.type == "DELETE":
        # 행은 이미 없다. 이 카드를 담은 related_cache 만 지우면 된다 (읽을 때도 거르지만 미리 치운다)
        old_id = (payload.old_record or {}).get("id")
        if not old_id:
            return {"ignored": True}
        experience_id = uuid.UUID(old_id)
        bg.add_task(service.handle_published_change, experience_id, "private")
        return {"experience_id": str(experience_id), "action": "delete"}
    if payload.type not in ("UPDATE", "INSERT") or not payload.record or not payload.record.get("id"):
        return {"ignored": True}
    experience_id = uuid.UUID(payload.record["id"])
    visibility = payload.record.get("visibility") or "private"
    bg.add_task(service.handle_published_change, experience_id, visibility)
    return {"experience_id": str(experience_id), "action": "unpublish" if visibility == "private" else "embed"}
