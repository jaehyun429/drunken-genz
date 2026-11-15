"""기록들 → 패턴. DB 를 모르는 순수 부분. 데모 스크립트와 서비스가 같이 쓴다."""
from __future__ import annotations
import logging

from ..constants import ANALYSIS_LLM_ATTEMPTS
from ..llm.client import complete_json
from . import filters
from .prompt import build_messages
from .schemas import OUTPUT_SCHEMA, Outcome, Record

log = logging.getLogger("analysis")


async def find_patterns(records: list[Record], existing: list[str], blocked: list[str], need: int) -> Outcome:
    """LLM 호출 + 걸러내기. 0개도 정상 결과다 (억지로 채우지 않는다). 연결/모델 예외는 그대로 올린다."""
    version, system, user = build_messages(records, existing, blocked, need)
    result = await complete_json(system, user, OUTPUT_SCHEMA, max_attempts=ANALYSIS_LLM_ATTEMPTS)
    raw = result.data.get("patterns")
    kept, dropped = filters.apply(raw if isinstance(raw, list) else [], {r.key for r in records}, existing, blocked, need)
    # 사용자 글이 섞일 수 있어 개수만 남긴다
    log.info("분석: 기록 %d개 → 패턴 %d개 (버림 %d개)", len(records), len(kept), len(dropped))
    return Outcome(patterns=kept, dropped=dropped, model=result.model, prompt_version=version, attempts=result.attempts)
