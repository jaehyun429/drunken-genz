"""경험 하나 → 질문 2~4개. DB 를 모르는 순수 부분. 데모 스크립트와 서비스가 같이 쓴다."""
from __future__ import annotations
import logging
from .schemas import ExperienceInput, OtherExperience, GenerationOutcome, OUTPUT_SCHEMA
from .prompt import build_messages, input_hash, reask_text
from .source import fix_source
from . import filters
from ..llm.client import complete_json, LLMConnectionError, LLMModelError
from ..constants import QUESTION_MIN, QUESTION_REASK_MAX

log = logging.getLogger("questions")


async def generate(e: ExperienceInput, others: list[OtherExperience]) -> GenerationOutcome:
    """LLM 호출 + 걸러내기. 연결/모델 예외는 그대로 올린다 (서비스가 batch 에 기록).
       걸러낸 뒤 QUESTION_MIN 미만이면 버린 이유를 알려주고 한 번 더 묻는다. 다시 물어 얻은 질문은 최소 개수를 채울 때까지만 쓴다."""
    version, system, user = build_messages(e, others)
    other_ids = {o.id for o in others}
    result = await complete_json(system, user, OUTPUT_SCHEMA)
    kept, dropped = filters.apply(result.data.get("questions") or [], other_ids)
    attempts, reasks = result.attempts, 0
    while len(kept) < QUESTION_MIN and reasks < QUESTION_REASK_MAX:
        reasks += 1
        need = QUESTION_MIN - len(kept)
        try:
            again = await complete_json(system, user + "\n\n" + reask_text([k.text for k in kept], dropped, need), OUTPUT_SCHEMA)
        except (LLMConnectionError, LLMModelError) as err:
            if not kept:
                raise                      # 하나도 없으면 실패로 남긴다 (연결 문제면 앱이 '다시 시도')
            log.warning("다시 묻기 실패, 있는 %d개로 저장 (%s)", len(kept), type(err).__name__)
            break
        attempts += again.attempts
        # 통과한 질문을 앞에 두고 같이 걸러서, 새 질문이 앞의 것과 겹치면 빠지게 한다
        merged, more = filters.apply([k.model_dump() for k in kept] + (again.data.get("questions") or []), other_ids)
        kept = merged[:QUESTION_MIN]
        dropped += more
    if len(kept) == 0:
        raise LLMModelError(f"걸러낸 뒤 남은 질문 0개 (버림 {len(dropped)}개)")
    # 그래도 1개면 저장은 하되 표시. TODO(기획): 글이 적을 때 보완 질문을 더 낼지 검토 중
    kept = [k.model_copy(update={"source": fix_source(k.source, e)}) for k in kept]
    return GenerationOutcome(questions=kept, dropped=dropped, input_hash=input_hash(e), model=result.model,
                             prompt_version=version, attempts=attempts, reasks=reasks)


def too_few(outcome: GenerationOutcome) -> bool:
    return len(outcome.questions) < QUESTION_MIN
