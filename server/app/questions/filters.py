"""응답 후 걸러내기 (CLAUDE.md 4장 '프롬프트에 이 목록을 넣고, 응답을 받은 뒤에도 한 번 걸러낸다').
규칙으로 잡히는 것만 잡는다. 애매한 건 통과시키고 사람이 본다."""
from __future__ import annotations
import re
from .schemas import GeneratedQuestion, AXES_TRY, AXES_IMAGINE
from ..constants import QUESTION_TEXT_MAX, QUESTION_MAX, QUESTION_DEDUPE_SIMILARITY

# 패턴 → 이유. 질문 본문에 걸리면 버림.
# 11/14 실측(Haiku·Qwen)에서 2개 미만이 나온 건 전부 여기 패턴이 넓어서였다 ("부족하지 않았다면", "얘기하는 게 좋다면").
# 패턴을 넓히기 전에 tests/test_filters.py 의 PASS 목록에 '통과해야 하는 문장'을 먼저 넣는다.
BANNED: list[tuple[re.Pattern, str]] = [
    # "시간이 부족했다"는 상황 칩 문구라 평가가 아니다
    (re.compile(r"잘못|(?<!시간이 )부족했|실패했|못했|미흡"), "평가하는 말"),
    (re.compile(r"다른 사람들|남들|남처럼|보다 더|보다 잘"), "남과 비교"),
    (re.compile(r"(불안|지치|막막|답답|아쉬)[^가-힣]{0,2}(하지|지|해하지) ?(않|말)"), "감정을 고치라는 말"),
    # 권하는 말로 끝날 때만. "얘기하는 게 좋다면 …"처럼 사용자의 말을 받아 조건으로 이어가면 통과
    (re.compile(r"해야 (한다|해|돼|하지)|하는 게 (좋|낫)(?!다면|았|다는|다고)|하는 게 나[아을]|필요해\?"), "조언·훈계"),
    (re.compile(r"(성공|합격|통과)했을 (텐데|거야|것이다)|분명히|틀림없이"), "결과 단정"),
    (re.compile(r"(기한|마감)[을를]? 연장|AI ?없이|시간이 (두|세) ?배|무한"), "할 수 없는 가정"),
    # 내가 한 행동을 안 했다고 뒤집는 것만. "하지" 단독은 "부족하지·불편하지 않았다면"(상황을 바꾼 상상해보기)까지 잡아서
    # "~을/를 하지"일 때만 본다
    (re.compile(r"(시작하지|외우지|쓰지|만들지|올리지|[을를] ?하지) ?않았(다면|더라면|으면)"), "잘한 일을 뒤집기만 한 질문"),
    (re.compile(r"[가-힣]{2,3}(씨|님|이가|에게)\b.*(선생|팀장|사장)"), "사람 이름 의심"),
]

# 다른 사람의 행동을 바꾸는 질문 (CLAUDE.md 4장 '감정과 다른 사람의 행동은 질문 재료로 쓰지 않는다')
# ① 높임 가정("주셨다면") — 높임은 남에게만 쓴다
# ② 사람(역할) + 이/가 + … + 가정. 단 그 사람이 '있고 없고·많고 적고·되고 아니고'는 조건이지 행동이 아니라 통과,
#    "교수님이 정한 주제가 …"처럼 뒤 명사를 꾸미는 절도 통과
_PERSON = r"(?:사장|점장|매니저|상사|사수|선배|팀장|조장|교수|선생|멘토|담당자|대표|부장|동료|팀원|조원)(?:님들|님|들)?"
_OTHERS_HONORIFIC = re.compile(r"셨(다면|더라면|으면)")
_OTHERS_SUBJECT = re.compile(_PERSON + r"(?:이|가|께서) (\S+)[^,.?!]*?(?:다면|더라면|으면)")
_STATE_PREDICATE = re.compile(r"^(아니|아닌|되|돼|됐|있|없|많|적|더$|한$|두$|세$|몇$)")
OTHERS_ACTION = "다른 사람의 행동"


def _adnominal(token: str) -> bool:
    """'정한·몰리는·맡을'처럼 뒤 명사를 꾸미는 꼴인지. 끝 글자 받침이 ㄴ 이거나 ㄹ(목적격 을/를 제외)."""
    ch = token[-1]
    if not "가" <= ch <= "힣":
        return False
    jong = (ord(ch) - 0xAC00) % 28
    return jong == 4 or (jong == 8 and ch not in "을를")


def others_action(text: str) -> bool:
    if _OTHERS_HONORIFIC.search(text):
        return True
    for m in _OTHERS_SUBJECT.finditer(text):
        first = m.group(1)
        if not _STATE_PREDICATE.match(first) and not _adnominal(first):
            return True
    return False


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
        if reason is None and others_action(text):
            reason = OTHERS_ACTION
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
