"""질문의 source 를 기록 원문에 맞춘다.
모델이 항목 이름을 붙이거나("한 줄: …", "역할 칩 '바리스타'") 칩을 옮겨 적다 틀리는 경우가 있다 (11/15 Qwen: "방향이가 자주 바뀌었다").
앱은 source 를 '어느 기록에서 나왔는지'로 보여주니 기록에 있는 글자 그대로여야 한다.
기록에서 못 찾은 출처는 손대지 않고 그대로 둔다 (질문을 버리지 않는다. 얼마나 나오는지 먼저 본다)."""
from __future__ import annotations
import re
from .schemas import ExperienceInput
from .prompt import record_text, record_fragments
from .filters import similarity
from ..constants import QUESTION_SOURCE_SNAP_SIMILARITY

_WS = re.compile(r"\s+")
_NAMES = r"(?:활동|역할|기간|장소|주로 한 일|조건(?:\([^)]*\))?|내가 내린 결정(?:\([^)]*\))?|상황(?:\([^)]*\))?|감정(?:\([^)]*\))?|한 줄|AI 태그|회차 기록|더 적어본 것)"
_LABEL = re.compile(rf"^{_NAMES}\s*[:：]\s*")                  # record_text 가 붙이는 꼴 그대로
_LOOSE_LABEL = re.compile(rf"^{_NAMES}(?:\s*칩)?\s*[:：\-–·]?\s*")  # 모델이 바꿔 적은 꼴 ("역할 칩 …", "주로 한 일 - …")
_SPLIT = re.compile(r"\s*[,;]\s+|\s+/\s+")       # 출처 둘을 이어 쓴 경우. "대면/비대면"처럼 붙여 쓴 / 는 안 자른다
_QUOTES = "\"'“”‘’「」`"
_QUOTED = re.compile(r"[\"'“‘「]([^\"'“”‘’「」]{2,})[\"'”’」]")


def _direct(text: str, record: str, fragments: list[str]) -> str | None:
    """항목 이름만 떼면 기록에 그대로 있는 경우. 칩·문장 안에 있는지를 먼저 본다
       (회차 기록 줄은 "상황 결과가 …"처럼 이름이 붙은 채 기록 글에 있어서, 기록 글부터 보면 이름이 남는다)."""
    strict = _LABEL.sub("", text).strip()
    loose = _LOOSE_LABEL.sub("", text).strip(_QUOTES + " ")
    for cand in (strict, loose):
        if len(cand) >= 2 and any(cand in f for f in fragments):
            return cand
    for cand in (strict, loose):              # "칩: 보강글" 꼴은 조각이 아니라 기록 글에만 있다
        if len(cand) >= 2 and cand in record and not _LOOSE_LABEL.fullmatch(cand):
            return cand
    return None


def _resolve(part: str, record: str, fragments: list[str]) -> list[str]:
    """출처 조각 하나 → 기록에 있는 글자들. 못 찾으면 빈 목록."""
    part = part.strip().strip(_QUOTES).strip()
    if _LOOSE_LABEL.fullmatch(part):          # 항목 이름만 남은 조각 ("한 줄"). 기록 글에도 있는 글자라 따로 막는다
        return []
    direct = _direct(part, record, fragments)
    if direct:
        return [direct]
    # 기록의 칩·문장이 통째로 들어 있으면 그것들 (긴 것부터, 겹치지 않게, 나온 순서로)
    found: list[tuple[int, str]] = []
    rest = part
    for f in sorted(set(fragments), key=len, reverse=True):
        i = rest.find(f)
        if len(f) >= 2 and i >= 0:
            found.append((i, f))
            rest = rest[:i] + "\0" * len(f) + rest[i + len(f):]
    if found:
        # 칩 뒤에 보강글 일부만 옮긴 경우 ("교육·멘토링: 한 명은 끝까지 마음을 안 열었음"). 남은 글자가 기록에 있으면 같이 둔다
        for m in re.finditer(r"[^\0]+", rest):
            left = m.group().strip(" :：,-–·" + _QUOTES)
            if len(left) >= 4 and left in record and not _LOOSE_LABEL.fullmatch(left):
                found.append((m.start(), left))
        return [f for _, f in sorted(found)]
    # 오타: 가장 닮은 칩·문장으로
    cand = _LOOSE_LABEL.sub("", part)
    best = max(fragments, key=lambda f: similarity(cand, f), default=None)
    if best is not None and similarity(cand, best) >= QUESTION_SOURCE_SNAP_SIMILARITY:
        return [best]
    return []


def fix_source(source: str, e: ExperienceInput) -> str:
    record = _WS.sub(" ", record_text(e))
    fragments = record_fragments(e)
    whole = _WS.sub(" ", source).strip()
    plain = _LABEL.sub("", whole).strip()
    direct = _direct(whole, record, fragments)
    if direct:
        return direct
    out: list[str] = []
    # 따옴표로 인용한 곳은 그 안을 한 덩어리로, 바깥은 바깥대로 본다 ("바리스타 / 주로 한 일 '판매'")
    units = [u for u in _QUOTED.split(whole) if u.strip(_QUOTES + " ,;/")] if _QUOTED.search(whole) else [plain]
    for unit in units:
        got: list[str] = []
        for part in _SPLIT.split(unit):
            got += _resolve(part, record, fragments)
        if not got:
            got = _resolve(unit, record, fragments)      # 쉼표가 든 문장을 틀리게 옮긴 경우
        out += [g for g in got if g not in out]
    return ", ".join(out) if out else whole
