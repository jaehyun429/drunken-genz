"""경험 하나 → 질문 4개 (더 적어보기 0~2 + 해볼 것·상상해보기). DB 를 모르는 순수 부분. 데모 스크립트와 서비스가 같이 쓴다.
11/14 기획 변경 (PRD 7.2·7.2.1, 기능명세서 14장): 모델은 후보를 내고, 4개를 고르는 건 compose 가 한다."""
from __future__ import annotations
import logging
from .schemas import ExperienceInput, OtherExperience, GeneratedQuestion, GenerationOutcome, AXES_WRITE_MORE, think_schema
from .prompt import build_messages, input_hash, reask_text
from .source import fix_source
from . import filters
from ..llm.client import complete_json, LLMConnectionError, LLMModelError
from ..constants import QUESTION_COUNT, QUESTION_WRITE_MORE_MAX, QUESTION_CANDIDATE_MAX, QUESTION_REASK_MAX

log = logging.getLogger("questions")

OUTPUT_SCHEMA = think_schema()   # 프롬프트 v7: 질문마다 anchor·change 를 먼저 적게 한다. 저장은 안 한다. v6 로 되돌리면 schemas.OUTPUT_SCHEMA 로


def compose(cands: list[GeneratedQuestion]) -> list[GeneratedQuestion]:
    """걸러낸 후보 → 화면에 나갈 4개 (PRD 7.2 구성).
       더 적어보기는 빈 칸 우선순위대로 2개까지. 나머지 자리는 해볼 것과 상상해보기가 나눠 갖는다:
       4자리면 2+2, 3자리면 해볼 것 2 + 상상해보기 1, 2자리면 1+1. 한쪽이 모자라면 다른 쪽으로 채운다.
       TODO(기획): 3자리일 때 어느 쪽이 2개인지는 명세에 없다. 재료가 많은 해볼 것을 2개로 뒀다.
       순서는 더 적어보기 먼저 ('기록을 풍성하게 만드는 질문을 먼저 보여줌'), 그 뒤는 모델이 준 순서."""
    more = sorted((c for c in cands if c.type == "write_more"), key=lambda c: AXES_WRITE_MORE.index(c.axis))
    more = more[:QUESTION_WRITE_MORE_MAX]
    tries = [c for c in cands if c.type == "try_next"]
    imagines = [c for c in cands if c.type == "imagine"]
    rest = QUESTION_COUNT - len(more)
    n_imagine = min(len(imagines), rest // 2)
    n_try = min(len(tries), rest - n_imagine)
    n_imagine = min(len(imagines), rest - n_try)
    chosen = {id(c) for c in tries[:n_try] + imagines[:n_imagine]}
    return more + [c for c in cands if id(c) in chosen]


def shortage(final: list[GeneratedQuestion]) -> dict[str, int]:
    """고른 4개에서 모자란 것. 해볼 것·상상해보기는 각각 1개 이상, 전체는 4개. 더 적어보기는 모자라다고 보지 않는다 (빈 칸이 없으면 0개)."""
    have = {t: sum(q.type == t for q in final) for t in ("try_next", "imagine")}
    need = {t: 1 if have[t] == 0 else 0 for t in have}
    for _ in range(QUESTION_COUNT - len(final) - sum(need.values())):
        t = min(have, key=lambda k: have[k] + need[k])   # 적은 쪽부터 채운다. 같으면 해볼 것
        need[t] += 1
    return {t: n for t, n in need.items() if n}


async def generate(e: ExperienceInput, others: list[OtherExperience],
                   previous: list[str] | None = None) -> GenerationOutcome:
    """LLM 호출 + 걸러내기 + 4개 고르기. 연결/모델 예외는 그대로 올린다 (서비스가 batch 에 기록).
       previous 는 다시 만들기에서만 준다: 이전에 보여준 질문 본문. 겹치는 해볼 것·상상해보기는 버리고,
       새 것이 하나도 없으면 no_new 로 돌려준다 ("새로 만들 질문이 없어요").
       걸러낸 뒤 모자라면 버린 이유를 알려주고 한 번 더 묻는다. 그래도 모자라면 있는 만큼만 낸다."""
    version, system, user = build_messages(e, others, previous)
    other_ids = {o.id for o in others}
    opts = dict(answered=frozenset(a.slot for a in e.answers), previous=tuple(previous or ()))
    fact_words = filters.same_fact_words(e)
    result = await complete_json(system, user, OUTPUT_SCHEMA)
    raw = result.data.get("questions") or []
    cands, dropped = filters.apply(raw, other_ids, fact_words, **opts)
    final = compose(cands)
    attempts, reasks = result.attempts, 0
    while (need := shortage(final)) and reasks < QUESTION_REASK_MAX:
        reasks += 1
        try:
            again = await complete_json(system, user + "\n\n" + reask_text([c.text for c in cands], dropped, need), OUTPUT_SCHEMA)
        except (LLMConnectionError, LLMModelError) as err:
            if not final:
                raise                      # 하나도 없으면 실패로 남긴다 (연결 문제면 앱이 '다시 시도')
            log.warning("다시 묻기 실패, 있는 %d개로 저장 (%s)", len(final), type(err).__name__)
            break
        attempts += again.attempts
        # 버린 질문을 말만 바꿔 다시 낸 것부터 뺀다. 그다음 통과한 후보를 앞에 두고 같이 걸러서, 새 질문이 앞의 것과 겹치면 빠지게 한다
        fresh, repeats = filters.split_repeats(again.data.get("questions") or [], dropped)
        raw += fresh
        cands, more = filters.apply([c.model_dump() for c in cands] + fresh, other_ids, fact_words,
                                    cap=2 * QUESTION_CANDIDATE_MAX, **opts)
        dropped += repeats + more
        final = compose(cands)
    no_new = False
    if previous is not None and not any(q.type != "write_more" for q in final):
        # 다시 만들기인데 새 해볼 것·상상해보기가 없다. 모델이 안 냈거나 이전 질문과 겹쳐 버린 것이면 '새 질문 없음',
        # 금지 규칙으로만 다 버렸으면 모델 문제로 남긴다
        gave = any(item.get("type") in ("try_next", "imagine") for item in raw)
        if gave and not any(why == filters.SHOWN for _, why in dropped):
            raise LLMModelError(f"걸러낸 뒤 남은 새 질문 0개 (버림 {len(dropped)}개)")
        no_new = True
    elif not final:
        raise LLMModelError(f"걸러낸 뒤 남은 질문 0개 (버림 {len(dropped)}개)")
    final = [q if q.type == "write_more" else q.model_copy(update={"source": fix_source(q.source, e)}) for q in final]
    return GenerationOutcome(questions=final, dropped=dropped, input_hash=input_hash(e), model=result.model,
                             prompt_version=version, attempts=attempts, reasks=reasks, no_new=no_new)


def too_few(outcome: GenerationOutcome) -> bool:
    return len(outcome.questions) < QUESTION_COUNT
