"""응답 후 걸러내기 (CLAUDE.md 4장 '프롬프트에 이 목록을 넣고, 응답을 받은 뒤에도 한 번 걸러낸다').
규칙으로 잡히는 것만 잡는다. 애매한 건 통과시키고 사람이 본다."""
from __future__ import annotations
import re
from .schemas import ExperienceInput, GeneratedQuestion, AXES_TRY, AXES_IMAGINE
from ..constants import QUESTION_TEXT_MAX, QUESTION_MAX, QUESTION_DEDUPE_SIMILARITY, QUESTION_ENDING

# 패턴 → 이유. 질문 본문에 걸리면 버림.
# 11/14~15 실측(Haiku·Qwen)에서 나온 오탐은 전부 여기 패턴이 넓어서였다 ("부족하지 않았다면", "얘기하는 게 좋다면", "평소보다 더").
# 패턴을 넓히기 전에 tests/test_filters.py 의 PASS 목록에 '통과해야 하는 문장'을 먼저 넣는다.
BANNED: list[tuple[re.Pattern, str]] = [
    # "시간이 부족했다"는 상황 칩 문구라 평가가 아니다
    (re.compile(r"잘못|(?<!시간이 )부족했|실패했|못했|미흡"), "평가하는 말"),
    # 견주는 상대가 사람일 때만. "평소보다 더·지금보다 더"는 내 평소와 견준 것이라 통과 (11/15 Qwen 오탐)
    (re.compile(r"다른 사람들|(?<![가-힣])남들|(?<![가-힣])남(처럼|보다)|"
                r"(다른 사람|다른 팀|다른 조|동기|친구|또래|동료|선배|후배|팀원|조원|지원자|참가자)(들)?(보다|에 비해)"), "남과 비교"),
    (re.compile(r"(불안|지치|막막|답답|아쉬)[^가-힣]{0,2}(하지|지|해하지) ?(않|말)"), "감정을 고치라는 말"),
    # 권하는 말로 끝날 때만. "얘기하는 게 좋다면 …"처럼 사용자의 말을 받아 조건으로 이어가면 통과
    (re.compile(r"해야 (한다|합니다|해|돼|하지)|하는 게 (좋|낫)(?!다면|았|다는|다고)|하는 게 나[아을]|필요해요?\?"), "조언·훈계"),
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

# 말투 (11/15 재현 결정: 존댓말). Qwen 은 "…궁금해.", "…했을지?"처럼 묻다 만 꼴이나 반말을 섞어 낸다.
# 끝맺음만 본다. 문장 가운데의 말투는 프롬프트에 맡긴다.
TONE = "존댓말 물음 꼴이 아님"
_ENDING = re.compile(QUESTION_ENDING)


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


# 같은 사실 하나를 축만 바꿔 두 번 묻는 것 (프롬프트 10번). 글자 겹침으로는 안 잡힌다 (11/15 Qwen f05: 유사도 0.12).
# 규칙으로 가를 수 있는 한 가지 꼴만 본다: 조건 값이 상황 칩에도 적혀 있고(규모=혼자 ↔ "혼자 결정해야 했다"),
# 두 질문이 다른 축에서 그 낱말을 "~가 아니라·말고·대신"으로 바꾼 경우.
# 더 넓은 규칙(바꾸는 구절에 같은 낱말)은 저장된 Haiku 296쌍에서 7쌍을 잡았는데 5쌍이 서로 다른 질문이었다.
# 둘 중 무엇을 남길지는 _keep_new 참고.
SAME_FACT = "같은 사실을 두 번 물음"
_NEGATE = re.compile(r"아니라|아닌|아니었|말고|대신")


def same_fact_words(e: ExperienceInput) -> frozenset[str]:
    """조건 값 가운데 상황 칩(회차 기록 포함) 안에도 나오는 낱말."""
    chips = list(e.situation_chips)
    for lg in e.logs:
        chips += lg.situation_chips
    words = set()
    for v in e.conditions.values():
        for x in (v if isinstance(v, list) else [v]):
            w = (x or "").strip()
            if len(w) >= 2 and any(w in c for c in chips):
                words.add(w)
    return frozenset(words)


def _negates(word: str, text: str) -> bool:
    m = _NEGATE.search(text)
    return bool(m) and word in text[:m.start()]


def same_fact(text: str, axis: str, other: GeneratedQuestion, words: frozenset[str]) -> bool:
    return axis != other.axis and any(_negates(w, text) and _negates(w, other.text) for w in words)


def _keep_new(qtype: str, old: GeneratedQuestion, kept: list[GeneratedQuestion]) -> bool:
    """같은 사실로 겹친 둘 가운데 새로 온 질문을 남길지 (11/15 재현 결정).
       종류가 같으면 앞의 것을 남긴다. 종류가 다르면 그 종류의 질문이 더 적은 쪽, 곧 버리면 그 종류가 없어지는 쪽을 남긴다.
       둘 다 하나씩이면 상상해보기를 남긴다: 상상해보기는 상황 칩에서만 나와 다시 받기 어렵고, 해볼 것은 재료가 많다
       (Qwen f05 는 뒤에 온 상상해보기를 버려서 해볼 것만 2개가 됐다).
       TODO(기획): 두 종류가 꼭 하나씩 있어야 하는지는 미정. 여기서는 겹쳤을 때 고르는 기준으로만 쓴다."""
    if qtype == old.type:
        return False
    n_new = sum(k.type == qtype for k in kept) + 1
    n_old = sum(k.type == old.type for k in kept)
    return n_new < n_old if n_new != n_old else qtype == "imagine"


# 다시 물었을 때 버린 질문을 말만 바꿔 다시 내는 것 (11/15 Qwen f02 강제 확인. 다시 묻기 프롬프트에 적어도 Qwen 은 안 지킨다).
# 형식 때문에 버린 것(출처 없음, 축 불일치, 길이, 말투)은 고쳐서 다시 내는 게 맞으니 견주지 않는다.
REPEATED = "버린 질문을 다시 냄"
_FIXABLE = re.compile(rf"^(source 없음|type 이상|축 불일치|{re.escape(TONE)})|자 초과$")


def split_repeats(raw: list[dict], dropped: list[tuple[str, str]]) -> tuple[list[dict], list[tuple[str, str]]]:
    """다시 받은 응답 → (새 질문, 버린 질문과 닮아서 버릴 것). 닮음 기준은 겹치는 질문과 같다."""
    rejected = [t for t, why in dropped if not _FIXABLE.search(why)]
    fresh, repeats = [], []
    for item in raw:
        text = (item.get("text") or "").strip()
        if text and any(similarity(text, t) >= QUESTION_DEDUPE_SIMILARITY for t in rejected):
            repeats.append((text, REPEATED))
        else:
            fresh.append(item)
    return fresh, repeats


def apply(raw: list[dict], other_ids: set[str],
          fact_words: frozenset[str] = frozenset()) -> tuple[list[GeneratedQuestion], list[tuple[str, str]]]:
    """모델 출력 → (살린 질문, 버린 것과 이유). 순서는 모델이 준 순서(우선순위) 유지.
       같은 사실 겹침에서 뒤의 것을 남길 때는 앞의 것이 있던 자리에 넣는다.
       fact_words 는 same_fact_words(경험). 비우면 같은 사실 겹침은 보지 않는다."""
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
        if not _ENDING.search(text):   # 금지 규칙 다음에 본다. 둘 다 걸리면 고쳐 내도 못 받는 쪽의 이유를 남기려고
            dropped.append((text, TONE)); continue
        if any(similarity(text, k.text) >= QUESTION_DEDUPE_SIMILARITY for k in kept):
            dropped.append((text, "겹치는 질문")); continue
        clash = [i for i, k in enumerate(kept) if same_fact(text, axis, k, fact_words)]
        if clash and not (len(clash) == 1 and _keep_new(qtype, kept[clash[0]], kept)):
            dropped.append((text, SAME_FACT)); continue
        done = item.get("done_experience_id")
        if done and done not in other_ids:
            done = None  # 목록에 없는 id 를 지어낸 것
        new = GeneratedQuestion(type=qtype, axis=axis, text=text, source=source, done_experience_id=done)
        if clash:
            dropped.append((kept[clash[0]].text, SAME_FACT))
            kept[clash[0]] = new
            continue
        kept.append(new)
        if len(kept) >= QUESTION_MAX:
            break
    return kept, dropped
