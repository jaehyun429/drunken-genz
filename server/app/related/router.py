"""POST /related — 질문 하나에 붙는 남의 공개 경험 (API 명세 2부). 처음 열 때 계산하고 캐시."""
from __future__ import annotations
import uuid
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import db
from ..auth import current_user_id
from . import service

router = APIRouter(tags=["related"])


class RelatedIn(BaseModel):
    question_id: uuid.UUID
    filter: str = "all"      # all / same_role / different_role / genre:{장르}


@router.post("/related")
async def related(body: RelatedIn, user_id: uuid.UUID = Depends(current_user_id)):
    """items 는 최대 10개, 관련 큰 순서. 맞는 게 없으면 빈 목록. reason 은 null 일 수 있다. 페이지 없음."""
    async with db.pool().acquire() as conn:
        items = await service.get_related(conn, user_id, body.question_id, body.filter)
    return {"items": items}
