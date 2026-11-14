"""응답 후 걸러내기 (CLAUDE.md 4장 '프롬프트에 이 목록을 넣고, 응답을 받은 뒤에도 한 번 걸러낸다').
규칙으로 잡히는 것만 잡는다. 애매한 건 통과시키고 사람이 본다."""
from __future__ import annotations
import re
from .schemas import GeneratedQuestion, AXES_TRY, AXES_IMAGINE
from ..constants import QUESTION_TEXT_MAX, QUESTION_MAX, QUESTION_DEDUPE_SIMILARITY

# 패턴 → 이유. 질문 본문에 걸리면 버림
BANNED: list[tuple[re.Pattern, str]] = [
    (re.compile(r"잘못|부족했|실패했|못했|미흡"), "평가하는 말"),
    (re.compile(r"다른 사람들|남들|남처럼|보다 더|보다 잘"), "남과 비교"),
    (re.compile(r"(불안|지치|막막|답답|아쉬)[^가-힣]{0,2}(하지|지|해하지) ?(않|말)"), "감정을 고치라는 말"),
    (re.compile(r"해야 (한다|해|돼|하지)|하는 게 (좋|낫)|필요해\?"), "조언·훈계"),
    (re.compile(r"(성공|합격|통과)했을 (텐데|거야|것이다)|분명히|틀림없이"), "결과 단정"),
    (re.compile(r"(기한|마감)[을를]? 연장|AI ?없이|시간이 (두|세) ?배|무한"), "할 수 없는 가정"),
    (re.compile(r"(하지|시작하지|외우지|쓰지|만들지|올리지) ?않았(다면|더라면|으면)"), "잘한 일을 뒤집기만 한 질문"),
    (re.compile(r"[가-힣]{2,3}(씨|님|이가|에게)\b.*(선생|팀장|사장)"), "사람 이름 의심"),
]


def _bigrams(s: str) -> set[str]:
    s = re.sub(r"[\s\W_]+", "", s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def similarity(a: str, b: str) -> float:
    A, B = _bigrams(a), _bigrams(b)
    return len(A & B) / len(A | B) if A and B else 0.0


def apply(raw: list[dict], other_ids: set[str]) -> tuple[list[GeneratedQuestion], list[tuple[str, str]]]:
    """모델 출력 → (살린 질문, 버린 것과 이유). 순서는 모델이 준 순서(우선순위) 유지."""
    kept: list[GeneratedQuestion] = []
    dropped: list[tuple[str, str]] = []
    for item in raw:
        text = (item.get("text") or "").strip()
        source = (item.get("source") or "").strip()
        qtype = item.get("type")
        axis = item.get("axis") or ""
        if not text:
            continue
        if not source:
            dropped.append((text, "source 없음")); continue
        if qtype not in ("try_next", "imagine"):
            dropped.append((text, "type 이상")); continue
        if (qtype == "try_next" and axis not in AXES_TRY) or (qtype == "imagine" and axis not in AXES_IMAGINE):
            dropped.append((text, f"축 불일치 {qtype}/{axis}")); continue
        if len(text) > QUESTION_TEXT_MAX:
            dropped.append((text, f"{QUESTION_TEXT_MAX}자 초과")); continue
        reason = next((why for pat, why in BANNED if pat.search(text)), None)
        if reason:
            dropped.append((text, reason)); continue
        if any(similarity(text, k.text) >= QUESTION_DEDUPE_SIMILARITY for k in kept):
            dropped.append((text, "겹치는 질문")); continue
        done = item.get("done_experience_id")
        if done and done not in other_ids:
            done = None  # 목록에 없는 id 를 지어낸 것
        kept.append(GeneratedQuestion(type=qtype, axis=axis, text=text, source=source, done_experience_id=done))
        if len(kept) >= QUESTION_MAX:
            break
    return kept, dropped
