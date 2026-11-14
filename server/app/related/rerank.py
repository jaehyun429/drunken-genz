"""후보 카드 → LLM 재정렬 + "왜 관련 있는지" 한 줄 (CLAUDE.local.md 5장 2번).
   LLM 에 넣는 건 질문, 질문 주인의 출처 경험, 남의 **공개 카드 스냅샷 글**뿐. Q&A·반응·개수는 넣지 않는다 (POL-07)."""
from __future__ import annotations
import re
from dataclasses import dataclass

from ..constants import RELATED_LLM_ATTEMPTS, RELATED_MAX, RELATED_REASON_MAX
from ..llm import client as llm

PROMPT_NAME = "related_v1"

RERANK_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["items"],
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["n", "reason"],
        "properties": {"n": {"type": "integer"},
                       "reason": {"anyOf": [{"type": "string"}, {"type": "null"}]}}}}},
}

_WS = re.compile(r"\s+")
# reason 에 연락처·평가하는 말이 섞이면 reason 만 버린다 (카드는 남김)
_REASON_BAD = re.compile(r"\d{7,}|\d{2,4}-\d{3,4}-\d{4}|@|https?://|당신|더 나은|더 좋은|성공한|해야 ")


@dataclass
class Ranked:
    index: int            # 후보 목록에서의 위치 (0부터)
    reason: str | None


def clean_reason(reason) -> str | None:
    if not isinstance(reason, str):
        return None
    r = _WS.sub(" ", reason).strip()
    if not r or len(r) > RELATED_REASON_MAX or _REASON_BAD.search(r):
        return None
    return r


def parse(data: dict, n_candidates: int) -> list[Ranked]:
    """LLM 출력 → 순서 있는 목록. 범위 밖 번호·중복은 버리고 최대 RELATED_MAX 개."""
    out: list[Ranked] = []
    seen: set[int] = set()
    for it in data.get("items") or []:
        if not isinstance(it, dict):
            continue
        n = it.get("n")
        if not isinstance(n, int) or isinstance(n, bool) or not (1 <= n <= n_candidates) or n in seen:
            continue
        seen.add(n)
        out.append(Ranked(index=n - 1, reason=clean_reason(it.get("reason"))))
        if len(out) >= RELATED_MAX:
            break
    return out


def build_messages(question: str, source_text: str, candidate_texts: list[str]) -> tuple[str, str, str]:
    """(prompt_version, system, user)."""
    version, system = llm.load_prompt(PROMPT_NAME)
    system = system.replace("{max}", str(RELATED_MAX)).replace("{reason_max}", str(RELATED_REASON_MAX))
    cands = "\n".join(f"[{i}] {t}" for i, t in enumerate(candidate_texts, 1))
    user = f"## 질문\n{question}\n\n## 출처 경험\n{source_text or '(삭제된 기록)'}\n\n## 후보\n{cands}"
    return version, system, user


async def rerank(question: str, source_text: str, candidate_texts: list[str]) -> tuple[list[Ranked], str]:
    """(순서 있는 결과, 모델 이름). 실패는 LLMConnectionError / LLMModelError 로 올라간다."""
    _, system, user = build_messages(question, source_text, candidate_texts)
    res = await llm.complete_json(system, user, RERANK_SCHEMA, max_attempts=RELATED_LLM_ATTEMPTS)
    return parse(res.data, len(candidate_texts)), res.model
