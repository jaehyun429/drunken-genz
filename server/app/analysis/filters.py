"""패턴 문장 걸러내기. 프롬프트에 규칙을 넣고 응답을 받은 뒤에도 한 번 거른다 (질문 생성과 같은 방식).
   규칙으로 잡히는 것만 잡는다. 애매한 건 통과시키고 사람이 본다."""
from __future__ import annotations
import re

from ..constants import (ANALYSIS_BLOCK_SIMILARITY, ANALYSIS_DEDUPE_SIMILARITY, ANALYSIS_ENDING, ANALYSIS_TEXT_MAX,
                         PATTERN_MIN_EVIDENCE)
from ..questions.filters import BANNED as _QUESTION_BANNED, similarity
from .schemas import Found

EMPTY = "빈 문장"
TOO_LONG = f"{ANALYSIS_TEXT_MAX}자 초과"
TONE = "존댓말 평서문이 아님"
EVIDENCE = f"근거 기록이 {PATTERN_MIN_EVIDENCE}개 미만"
DUPLICATE = "이미 있는 패턴과 겹침"
BLOCKED = "아니에요를 받은 패턴과 닮음"

# 남과 비교는 질문 생성과 같은 규칙을 쓴다 (견주는 상대가 사람일 때만)
_COMPARE = next(p for p, why in _QUESTION_BANNED if why == "남과 비교")

# "시간이 부족했다"는 상황 칩 문구라 평가가 아니다. "잘하고 싶다고 적었어요"처럼 기록을 옮긴 말도 통과시키려고
# 사람을 평가하는 끝맺음 꼴만 본다
BANNED: list[tuple[re.Pattern, str]] = [
    (re.compile(r"잘했어요|잘해요|잘하는 편|잘 해내|못해요|못하는 편|뛰어나|훌륭|대단|강점|약점|장점|단점|재능|소질|"
                r"능력이 (있|좋|뛰어)|(?<!시간이 )부족해요"), "평가하는 말"),
    (re.compile(r"(사람|타입|유형|스타일|체질)(이에요|이네요|입니다|인 것|인 편)|성격|성향|기질"), "사람을 규정하는 말"),
    (re.compile(r"해 ?보세요|하세요\.|해 ?보면 좋|하는 (게|것이) 좋|해야 (해요|합니다|돼요)|필요해요|필요가 있|추천(해요|드려요|합니다)|권해(요|드려요)"), "조언·훈계"),
    (re.compile(r"[가-힣] (거예요|겁니다|것입니다|것이에요)|분명히|틀림없이"), "앞날 단정"),
    (_COMPARE, "남과 비교"),
    (re.compile(r"\d{7,}|\d{2,4}-\d{3,4}-\d{4}|@|https?://"), "연락처"),
    (re.compile(r"당신|셨어요|셨네요|시는 편"), "읽는 사람을 부르거나 높임"),
]
_ENDING = re.compile(ANALYSIS_ENDING)
_WS = re.compile(r"\s+")

# 패턴 문장은 "~한 기록이 두 개 있어요", "~를 여러 번 골랐어요"처럼 틀이 같아서 문장째 견주면 내용이 달라도 닮게 나온다
# (11/15 손으로 만든 13쌍: 다른 내용 0.37~0.60, 같은 내용 0.21~0.80 으로 안 갈림). 틀 낱말(횟수, 기록·활동, 끝맺음)을 빼고 견준다
# (다른 내용 0.00~0.33, 같은 내용 0.38~1.00). 낱말이 다른 바꿔 말하기("지쳤다고" ↔ "지침을")는 못 잡는다. 그건 프롬프트에 맡긴다.
_TEMPLATE = re.compile(
    r"(한|두|세|네|다섯|여섯|여러|몇|\d+) ?(개|번|건|곳|가지)(나|이나|씩)?|자주|매번|모두|전부|마다|"
    r"(기록|활동|경험)(들)?(이|을|에|에서|에서는|마다)?(?![가-힣])|"
    r"(있|골랐|고른|적었|적은|썼|쓴|했|한|맡았|맡은|남겼|남긴|나와|나왔|보여)(어요|아요|요)?(?=[\s.]|$)")


def alike(a: str, b: str) -> float:
    """틀 낱말을 뺀 나머지의 문자 2-gram 자카드."""
    return similarity(_TEMPLATE.sub(" ", a), _TEMPLATE.sub(" ", b))


_QUOTE_KEY = re.compile(r"^\[?(e\d+(?:\.l\d+)?)\]?\s*[:：]\s*")
_QUOTE_JUNK = re.compile(r"[\s.,!?~\"'“”‘’「」]+")


def quote_set(raw) -> set[tuple[str, str]]:
    """모델이 옮겨 적은 대목들 ("e1: 옮긴 글") → {(기록 번호, 글)}. 띄어쓰기·문장부호는 뺀다. 번호가 없거나 글이 비면 버린다."""
    out = set()
    for q in raw if isinstance(raw, list) else []:
        m = _QUOTE_KEY.match(q.strip()) if isinstance(q, str) else None
        text = _QUOTE_JUNK.sub("", q.strip()[m.end():]) if m else ""
        if text:
            out.add((m.group(1), text))
    return out


def same_quotes(a: set[tuple[str, str]], b: set[tuple[str, str]]) -> bool:
    """두 패턴이 같은 대목에 기대고 있는가. 같은 기록의 같은 글(한쪽이 다른 쪽을 품어도 같다)이 둘 중 적은 쪽의 절반 이상이면 같다고 본다.
       문장을 바꿔 말해도 근거 대목은 그대로라서, 낱말 닮음(alike)이 못 잡는 겹침을 잡는다 (11/28 Qwen)."""
    if not a or not b:
        return False
    hit = sum(1 for k, t in a if any(k == k2 and (t in t2 or t2 in t) for k2, t2 in b))
    return hit * 2 >= min(len(a), len(b))


def clean(text) -> str:
    """공백 정리. "~요"로 끝나고 마침표만 빠졌으면 붙인다."""
    t = _WS.sub(" ", text.strip()) if isinstance(text, str) else ""
    return t + "." if t.endswith("요") else t


def text_reason(text: str) -> str | None:
    """문장만 보고 버릴 이유. 없으면 None. 금지 규칙을 먼저 보고 말투를 본다 (질문 필터와 같은 순서)."""
    if not text:
        return EMPTY
    if len(text) > ANALYSIS_TEXT_MAX:
        return TOO_LONG
    for pat, why in BANNED:
        if pat.search(text):
            return why
    if not _ENDING.search(text):
        return TONE
    return None


def apply(raw: list, valid_keys: set[str], existing: list[str], blocked: list[str], need: int,
          taken: list[tuple[set, str]] | None = None) -> tuple[list[Found], list[tuple[str, str]]]:
    """LLM 출력 → (남긴 패턴, 버린 것과 이유). existing 은 지금 보여주는 패턴, blocked 는 "아니에요"를 받은 패턴.
       taken 은 번호가 붙은 문장(p1, n1 …)의 옮겨 적은 대목과 그때 붙일 이유. 출력에 quotes 가 있을 때만 쓰인다."""
    kept: list[Found] = []
    seen: list[tuple[set, str]] = list(taken or [])
    dropped: list[tuple[str, str]] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        text = clean(it.get("text"))
        why = text_reason(text)
        keys: list[str] = []
        for k in it.get("evidence") or []:
            k = k.strip().strip("[]") if isinstance(k, str) else ""
            if k in valid_keys and k not in keys:
                keys.append(k)
        if why is None and len(keys) < PATTERN_MIN_EVIDENCE:
            why = EVIDENCE
        if why is None and any(alike(text, b) >= ANALYSIS_BLOCK_SIMILARITY for b in blocked):
            why = BLOCKED
        if why is None and any(alike(text, x) >= ANALYSIS_DEDUPE_SIMILARITY
                               for x in existing + [k.text for k in kept]):
            why = DUPLICATE
        quotes = quote_set(it.get("quotes"))
        if why is None:
            why = next((reason for qs, reason in seen if same_quotes(quotes, qs)), None)
        if why is not None:
            if text:
                dropped.append((text, why))
            continue
        if len(kept) < need:
            kept.append(Found(text=text, keys=keys))
            seen.append((quotes, DUPLICATE))
    return kept, dropped


def recheck(raw: list, valid_keys: set[str], labels: set[str]) -> dict[str, list[str]]:
    """LLM 출력의 existing → {문장 번호: 기록 번호들}. 우리가 보낸 문장 번호와 입력에 있는 기록 번호만 남긴다."""
    out: dict[str, list[str]] = {}
    for it in raw:
        if not isinstance(it, dict) or not isinstance(it.get("id"), str):
            continue
        label = it["id"].strip().strip("[]")
        if label not in labels:
            continue
        keys = out.setdefault(label, [])
        for k in it.get("evidence") or []:
            k = k.strip().strip("[]") if isinstance(k, str) else ""
            if k in valid_keys and k not in keys:
                keys.append(k)
    return out
