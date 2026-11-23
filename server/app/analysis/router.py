"""POST /analysis, POST /analysis/feedback (API 명세 2부). 앱이 기다리는 호출이라 그 자리에서 계산한다."""
from __future__ import annotations
import uuid
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import db
from ..auth import current_user_id
from . import service

router = APIRouter(tags=["analysis"])


class AnalysisIn(BaseModel):
    force: bool = False       # true 면 기록이 바뀌었을 때 다시 계산. 기록이 그대로면 저장된 결과


class FeedbackIn(BaseModel):
    pattern_id: uuid.UUID
    feedback: str             # yes / edit / no / later
    edited_text: str | None = None


@router.post("/analysis")
async def analysis(body: AnalysisIn | None = None, user_id: uuid.UUID = Depends(current_user_id)):
    """기록(경험 + 회차 기록) 3개 미만이면 locked. 패턴은 기록 2개 이상에서 되풀이된 것만, 근거와 같이."""
    async with db.pool().acquire() as conn:
        return await service.analyze(conn, user_id, bool(body and body.force))


@router.post("/analysis/feedback")
async def feedback(body: FeedbackIn, user_id: uuid.UUID = Depends(current_user_id)):
    """맞아요 / 수정할게요 / 아니에요 / 아직 모르겠어요. '아니에요'는 숨기고 다시 만들지 않는다."""
    async with db.pool().acquire() as conn:
        return {"pattern": await service.set_feedback(conn, user_id, body.pattern_id, body.feedback, body.edited_text)}
