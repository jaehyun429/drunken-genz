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
    """LLM 호출 + 걸러내기. 0개도 정상 결과다 (억지로 채우지 않는다). 연결/모델 예외는 그대로 올린다.
       existing(p1…)·blocked(n1…) 문장마다 그 내용이 적힌 기록 번호를 다시 받아 rechecked 에 담는다."""
    version, system, user = build_messages(records, existing, blocked, need)
    result = await complete_json(system, user, OUTPUT_SCHEMA, max_attempts=ANALYSIS_LLM_ATTEMPTS)
    raw = result.data.get("patterns")
    keys = {r.key for r in records}
    again = result.data.get("existing")
    # 번호가 붙은 문장의 근거 대목 (출력에 quotes 가 있는 프롬프트에서만). 같은 대목에 기댄 새 문장은 버린다
    taken = [(filters.quote_set(it.get("quotes")), filters.BLOCKED if str(it.get("id", "")).strip("[] ").startswith("n") else filters.DUPLICATE)
             for it in (again if isinstance(again, list) else []) if isinstance(it, dict)]
    kept, dropped = filters.apply(raw if isinstance(raw, list) else [], keys, existing, blocked, need, taken)
    labels = {f"p{i}" for i in range(1, len(existing) + 1)} | {f"n{i}" for i in range(1, len(blocked) + 1)}
    rechecked = filters.recheck(again if isinstance(again, list) else [], keys, labels)
    # 사용자 글이 섞일 수 있어 개수만 남긴다
    log.info("분석: 기록 %d개 → 패턴 %d개 (버림 %d개)", len(records), len(kept), len(dropped))
    return Outcome(patterns=kept, dropped=dropped, model=result.model, prompt_version=version, attempts=result.attempts,
                   rechecked=rechecked)
