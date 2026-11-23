"""공식 활동 검색 (소윤, REC-01). 결정: docs/결정_01_공식활동.md
   - 공식(verified) 활동만 검색된다. 목록은 소윤이 직접 넣고, 사용자 등록·사용자 입력 묶기는 안 함 (11/18)
   - 검색: 이름+별칭을 소문자·띄어쓰기 없앤 글자에서 포함 검색 → 앞부분 일치, 비슷한 정도(pg_trgm) 순
   - 초성: 검색어에 ㄱ~ㅎ 이 있으면 초성끼리 비교 (줄임말은 별칭으로)
   - 기수는 목록에 없다. 활동마다 한 줄이고, 사용자가 기록할 때 experience.official_period(1~99)로 고른다 (11/20)"""
from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends

from . import db
from .auth import current_user_id
from .constants import ACTIVITY_RECORD_COUNT_MIN, ACTIVITY_SEARCH_LIMIT, ACTIVITY_SEARCH_MIN_CHARS

router = APIRouter(tags=["activities"])


def search_text(s: str) -> str:
    """db 함수 activity_search_text 와 같은 규칙 (소문자, 띄어쓰기 없앰)."""
    return re.sub(r"\s", "", s).lower()


def has_initials(s: str) -> bool:
    """검색어에 초성(ㄱ~ㅎ)이 섞여 있으면 초성 검색. "ㅊㅇㅂㄹㅎ", 입력 중인 "취ㅇ" 둘 다."""
    return re.search(r"[ㄱ-ㅎ]", s) is not None


# $1 검색 글자(정리됨), $2 초성 검색인지, $3 개수
# 초성 검색이면 검색어·활동 이름 둘 다 hangul_initials 로 바꿔서 비교 ("취ㅇ" → "ㅊㅇ")
# ponytail: 포함 여부를 strpos 로 봐서 trigram 인덱스를 안 탐. 공식 활동 수백 개라 괜찮음. 수만 개면 인덱스 쓰는 like 로
_SEARCH = r"""
with p as (
  select case when $2 then hangul_initials($1) else $1 end as q
), keyed as (
  select a.*, case when $2 then hangul_initials(activity_search_text(a.name, a.aliases))
                   else activity_search_text(a.name, a.aliases) end as k
  from official_activity a
  where a.status = 'verified'
)
select h.id, h.name, h.organizer, h.genre, h.type,
       (select count(*) from experience e where e.official_activity_id = h.id) as record_count
from keyed h, p
where strpos(h.k, p.q) > 0
order by strpos(h.k, p.q) = 1 desc, similarity(h.k, p.q) desc, h.name
limit $3
"""


def _item(r) -> dict:
    n = r["record_count"]
    return {"id": str(r["id"]), "name": r["name"], "organizer": r["organizer"],
            "record_count": n if n >= ACTIVITY_RECORD_COUNT_MIN else None,
            # 아래는 명세 1.8 에 더한 것 (필드 추가는 호환 규칙상 자유)
            "genre": r["genre"], "type": r["type"]}


@router.get("/activities/search")
async def search(q: str = "", _: uuid.UUID = Depends(current_user_id)):
    t = search_text(q)
    if len(t) < ACTIVITY_SEARCH_MIN_CHARS:
        return {"items": []}       # 앱은 2글자부터 부르지만, 와도 에러 대신 빈 목록
    async with db.pool().acquire() as conn:
        rows = await conn.fetch(_SEARCH, t, has_initials(t), ACTIVITY_SEARCH_LIMIT)
    return {"items": [_item(r) for r in rows]}
