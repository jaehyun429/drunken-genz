"""엔드포인트. 생성은 전부 백그라운드 → 앱은 question_batch.status 로 '준비 중'을 안다."""
from __future__ import annotations
import uuid
from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel

from .. import db
from ..auth import current_user_id, verify_webhook_secret
from ..errors import AppError
from . import service

router = APIRouter(tags=["questions"])


class ExperienceSavedWebhook(BaseModel):
    """Supabase Database Webhook 모양 (INSERT on experience)."""
    type: str
    table: str
    record: dict
    old_record: dict | None = None


@router.post("/internal/experience-saved", status_code=202, dependencies=[Depends(verify_webhook_secret)])
async def experience_saved(payload: ExperienceSavedWebhook, bg: BackgroundTasks):
    """경험이 저장되면 소윤 쪽 DB 웹훅이 여기를 부른다. 앱은 부르지 않는다 (CLAUDE.md 4장)."""
    if payload.table != "experience" or payload.type != "INSERT":
        return {"ignored": True}
    experience_id = uuid.UUID(payload.record["id"])
    user_id = uuid.UUID(payload.record["user_id"])
    async with db.pool().acquire() as conn:
        exists = await conn.fetchval("select 1 from question_batch where experience_id = $1 limit 1", experience_id)
        if exists:  # 웹훅 재전송 대비
            return {"ignored": True, "reason": "already started"}
        batch = await service.start_batch(conn, experience_id, user_id, trigger="save")
    bg.add_task(service.run_batch, batch["id"])
    return {"batch_id": str(batch["id"]), "generation": batch["generation"], "status": "pending"}


class RegenerateIn(BaseModel):
    experience_id: uuid.UUID


@router.post("/questions/regenerate", status_code=202)
async def regenerate(body: RegenerateIn, bg: BackgroundTasks, user_id: uuid.UUID = Depends(current_user_id)):
    """사용자 재생성 버튼. 하루 1회(KST). saved 질문은 남는다."""
    async with db.pool().acquire() as conn:
        owner = await conn.fetchval("select user_id from experience where id = $1", body.experience_id)
        if owner is None or owner != user_id:
            raise AppError("NOT_FOUND", "경험 없음")   # 남의 것도 404 (차단 티 안 나게 하는 원칙과 같게)
        pending = await conn.fetchval(
            "select id from question_batch where experience_id = $1 and status = 'pending'", body.experience_id)
        if pending:
            return {"batch_id": str(pending), "status": "pending", "note": "이미 생성 중"}
        batch = await service.start_batch(conn, body.experience_id, user_id, trigger="user")
    bg.add_task(service.run_batch, batch["id"])
    return {"batch_id": str(batch["id"]), "generation": batch["generation"], "status": "pending"}


@router.post("/questions/retry", status_code=202)
async def retry(body: RegenerateIn, bg: BackgroundTasks, user_id: uuid.UUID = Depends(current_user_id)):
    """실패(connection)했을 때 앱의 '다시 시도' 버튼. 하루 횟수에 안 셈. 마지막 batch 가 connection 실패여야 함."""
    async with db.pool().acquire() as conn:
        last = await conn.fetchrow(
            "select * from question_batch where experience_id = $1 and user_id = $2 order by generation desc limit 1",
            body.experience_id, user_id)
        if last is None:
            raise AppError("NOT_FOUND", "생성 기록 없음")
        if last["status"] != "failed" or last["failure_reason"] != "connection":
            raise AppError("VALIDATION_FAILED", "다시 시도할 수 있는 상태가 아님", {"status": last["status"],
                                                                      "failure_reason": last["failure_reason"]})
        await conn.execute("update question_batch set status = 'pending', failure_reason = null, error_detail = null, "
                           "finished_at = null where id = $1", last["id"])
    bg.add_task(service.run_batch, last["id"])
    return {"batch_id": str(last["id"]), "generation": last["generation"], "status": "pending"}
