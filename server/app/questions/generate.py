"""경험 하나 → 질문 2~4개. DB 를 모르는 순수 부분. 데모 스크립트와 서비스가 같이 쓴다."""
from __future__ import annotations
from .schemas import ExperienceInput, OtherExperience, GenerationOutcome, OUTPUT_SCHEMA
from .prompt import build_messages, input_hash
from . import filters
from ..llm.client import complete_json, LLMModelError
from ..constants import QUESTION_MIN


async def generate(e: ExperienceInput, others: list[OtherExperience]) -> GenerationOutcome:
    """LLM 호출 + 걸러내기. 연결/모델 예외는 그대로 올린다 (서비스가 batch 에 기록)."""
    version, system, user = build_messages(e, others)
    result = await complete_json(system, user, OUTPUT_SCHEMA)
    raw = result.data.get("questions") or []
    kept, dropped = filters.apply(raw, {o.id for o in others})
    if len(kept) == 0:
        raise LLMModelError(f"걸러낸 뒤 남은 질문 0개 (버림 {len(dropped)}개)")
    # 1개만 남은 건 저장은 하되 표시. TODO(기획): 글이 적을 때 보완 질문을 더 낼지 검토 중
    return GenerationOutcome(questions=kept, dropped=dropped, input_hash=input_hash(e), model=result.model,
                             prompt_version=version, attempts=result.attempts)


def too_few(outcome: GenerationOutcome) -> bool:
    return len(outcome.questions) < QUESTION_MIN
