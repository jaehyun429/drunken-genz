"""분석의 순수 부분: 필터, 프롬프트, find_patterns. LLM 은 가짜."""
import asyncio
import pathlib
import re
import pytest

import app.analysis.generate as gen
from app.analysis import filters
from app.analysis.prompt import build_messages, records_hash
from app.analysis.schemas import Record
from app.constants import ANALYSIS_AVOID_PROMPT_MAX, ANALYSIS_PATTERN_MAX, ANALYSIS_TEXT_MAX
from app.llm.client import LLMResult, LLMConnectionError

R = [Record(key="e1", experience_id="a", activity_name="카페 알바", text="활동: 카페 알바 (알바, 진행 중)\n감정: 지침"),
     Record(key="e1.l1", experience_id="a", activity_name="카페 알바", text="카페 알바 회차 기록 2026-10-20 · 한 줄: 매출 얘기",
            log_id="l", log_date="2026-10-20"),
     Record(key="e2", experience_id="b", activity_name="편의점 야간 알바", text="활동: 편의점 야간 알바 (알바, 끝남)\n감정: 지침")]
KEYS = {r.key for r in R}

PASS = [
    "사람을 직접 만나는 일을 여러 번 골랐어요.",
    "혼자 결정해야 했던 기록마다 지쳤다고 적었어요.",
    "시간이 부족했다고 적은 기록이 두 개 있어요.",          # 상황 칩 문구라 평가가 아님
    "잘하고 싶다고 적은 기록이 두 개 있어요.",              # 기록을 옮긴 말
    "메뉴 추천을 맡은 기록이 두 개 있어요.",                # 내가 한 일
    "평소보다 늦게까지 남았다고 적은 기록이 두 개 있어요.",  # 내 평소와 견준 것
    "손님이 많았던 날에 재미를 골랐어요.",
]
DROP = [
    ("사람을 대하는 일을 잘하는 편이에요.", "평가하는 말"),
    ("꼼꼼한 성격이 여러 기록에 보여요.", "사람을 규정하는 말"),
    ("혼자 일하는 걸 좋아하는 사람이에요.", "사람을 규정하는 말"),
    ("다음에는 팀원 역할도 해보세요.", "조언·훈계"),
    ("기획 일에 잘 맞을 거예요.", "앞날 단정"),
    ("동기들보다 리더를 자주 맡았어요.", "남과 비교"),
    ("리더 역할을 두 번 고르셨어요.", "읽는 사람을 부르거나 높임"),
    ("사람을 직접 만나는 일을 자주 골랐나요?", filters.TONE),
    ("사람을 직접 만나는 일을 자주 고름", filters.TONE),
    ("사람을 직접 만나는 일을 자주 골랐다.", filters.TONE),
    ("가" * (ANALYSIS_TEXT_MAX + 1) + "요.", filters.TOO_LONG),
]


@pytest.mark.parametrize("text", PASS)
def test_sentences_that_must_pass(text):
    assert filters.text_reason(text) is None


@pytest.mark.parametrize("text,why", DROP)
def test_sentences_that_must_drop(text, why):
    assert filters.text_reason(text) == why


def p(text, *keys):
    return {"text": text, "evidence": list(keys)}


def test_clean_adds_missing_period_only_after_yo():
    assert filters.clean("  오프라인  활동을 골랐어요 ") == "오프라인 활동을 골랐어요."
    assert filters.clean("오프라인 활동을 골랐다") == "오프라인 활동을 골랐다"
    assert filters.clean(None) == ""


def test_evidence_needs_two_distinct_known_records():
    kept, dropped = filters.apply([
        p("오프라인으로 한 활동이 두 개 있어요.", "e1", "e1"),          # 같은 기록 두 번
        p("알바를 두 번 했어요.", "e1", "e9"),                         # 없는 번호
        p("지쳤다고 고른 기록이 두 개 있어요.", "[e1]", " e2 "),        # 괄호·공백은 떼고 받는다
    ], KEYS, [], [], 5)
    assert [k.text for k in kept] == ["지쳤다고 고른 기록이 두 개 있어요."] and kept[0].keys == ["e1", "e2"]
    assert [why for _, why in dropped] == [filters.EVIDENCE, filters.EVIDENCE]


def test_a_log_counts_as_its_own_record():
    kept, _ = filters.apply([p("매출 이야기를 두 번 적었어요.", "e1", "e1.l1")], KEYS, [], [], 5)
    assert len(kept) == 1


def test_drops_duplicates_of_existing_and_of_each_other():
    kept, dropped = filters.apply([
        p("지쳤다고 고른 기록이 두 개 있어요.", "e1", "e2"),
        p("지쳤다고 고른 기록이 두 개나 있어요.", "e1", "e2"),
        p("오프라인으로 한 활동을 자주 골랐어요.", "e1", "e2"),
    ], KEYS, ["오프라인으로 한 활동을 여러 번 골랐어요."], [], 5)
    assert [k.text for k in kept] == ["지쳤다고 고른 기록이 두 개 있어요."]
    assert [why for _, why in dropped] == [filters.DUPLICATE, filters.DUPLICATE]


@pytest.mark.parametrize("a,b", [
    ("지쳤다고 고른 기록이 두 개 있어요.", "재미를 고른 기록이 두 개 있어요."),
    ("기획하는 일을 여러 번 골랐어요.", "가르치는 일을 여러 번 골랐어요."),
    ("리더 역할을 맡은 기록이 두 개 있어요.", "팀원 역할을 맡은 기록이 세 개 있어요."),
    ("시간이 부족했다고 적은 기록이 두 개 있어요.", "처음 해보는 일이었다고 적은 기록이 두 개 있어요."),
])
def test_same_template_different_content_is_not_a_duplicate(a, b):
    assert filters.alike(a, b) < 0.5


@pytest.mark.parametrize("a,b", [
    ("지쳤다고 고른 기록이 두 개 있어요.", "지쳤다고 고른 기록이 세 개나 있어요."),
    ("사람을 직접 만나는 일을 여러 번 골랐어요.", "사람을 직접 만나는 일을 자주 골랐어요."),
])
def test_same_content_reworded_template_is_a_duplicate(a, b):
    assert filters.alike(a, b) >= 0.5


def test_blocked_patterns_do_not_come_back():
    kept, dropped = filters.apply([p("알바에서 지쳤다고 고른 기록이 세 개 있어요.", "e1", "e2")], KEYS, [],
                                  ["알바에서 지쳤다고 고른 기록이 두 개 있어요."], 5)
    assert kept == [] and dropped[0][1] == filters.BLOCKED


def test_keeps_at_most_need():
    raw = [p(f"{w} 일을 여러 번 골랐어요.", "e1", "e2") for w in ("기획하는", "가르치는", "파는", "만드는")]
    kept, dropped = filters.apply(raw, KEYS, [], [], 2)
    assert len(kept) == 2 and dropped == []


def test_garbage_items_are_ignored():
    kept, dropped = filters.apply(["문자열", None, {"text": 3, "evidence": "e1"}, {"evidence": ["e1", "e2"]}], KEYS, [], [], 5)
    assert kept == [] and dropped == []


def test_hash_ignores_keys_and_order_but_not_content():
    a = records_hash(R)
    renumbered = [Record(key=f"x{i}", experience_id=r.experience_id, activity_name=r.activity_name, text=r.text,
                         log_id=r.log_id, log_date=r.log_date) for i, r in enumerate(reversed(R))]
    assert records_hash(renumbered) == a
    assert records_hash(R[:2]) != a
    changed = [R[0], R[1], Record(key="e2", experience_id="b", activity_name="편의점 야간 알바", text=R[2].text + " 재미")]
    assert records_hash(changed) != a


def test_prompt_has_rules_and_only_what_it_is_given():
    version, system, user = build_messages(R, ["이미 있는 문장이에요."], ["막힌 문장이에요."], 3)
    assert version == "v3" and "{" not in re.sub(r'\{"patterns".*', "", system)   # 자리표시자가 남지 않음
    assert "최대 3개" in system and f"{ANALYSIS_TEXT_MAX}자" in system and "기록 **2개 이상**" in system
    assert "[e1] 활동: 카페 알바" in user and "[e1.l1] 카페 알바 회차 기록" in user
    assert "## 이미 있는 문장\n- [p1] 이미 있는 문장이에요." in user and "## 만들지 않을 문장\n- [n1] 막힌 문장이에요." in user
    _, _, bare = build_messages(R, [], [], ANALYSIS_PATTERN_MAX)
    assert "이미 있는 문장" not in bare and "만들지 않을 문장" not in bare
    assert "최대 0개" in build_messages(R, ["이미 있는 문장이에요."], [], -1)[1], "자리가 없으면 근거만 다시 센다"
    # 막는 문장이 많으면 최근 것만 보내지만 번호는 전체 목록에서의 자리 그대로다
    many = [f"막힌 문장 {i}이에요." for i in range(1, ANALYSIS_AVOID_PROMPT_MAX + 3)]
    _, _, user = build_messages(R, [], many, 1)
    assert "[n1] " not in user and "[n2] " not in user and f"- [n3] {many[2]}" in user
    assert f"- [n{len(many)}] {many[-1]}" in user


class Fake:
    def __init__(self, answer):
        self.answer, self.calls, self.kw = answer, 0, None

    async def __call__(self, system, user, schema, **kw):
        self.calls += 1; self.kw = kw
        if isinstance(self.answer, Exception):
            raise self.answer
        return LLMResult(data=self.answer, model="fake", prompt_tokens=1, completion_tokens=1, attempts=1)


def test_find_patterns_filters_and_reports(monkeypatch):
    fake = Fake({"patterns": [p("지쳤다고 고른 기록이 두 개 있어요.", "e1", "e2"), p("기획 일에 잘 맞을 거예요.", "e1", "e2")]})
    monkeypatch.setattr(gen, "complete_json", fake)
    out = asyncio.run(gen.find_patterns(R, [], [], 5))
    assert [f.text for f in out.patterns] == ["지쳤다고 고른 기록이 두 개 있어요."]
    assert out.dropped == [("기획 일에 잘 맞을 거예요.", "앞날 단정")] and out.model == "fake" and out.prompt_version == "v3"
    assert fake.kw == {"max_attempts": 2} and out.rechecked == {}


def test_find_patterns_reads_evidence_for_numbered_sentences(monkeypatch):
    """남긴 문장(p)과 숨긴 문장(n)의 근거를 다시 받는다. 보낸 번호와 입력에 있는 기록 번호만 남긴다."""
    fake = Fake({"patterns": [], "existing": [
        {"id": "p1", "evidence": ["e1", "[e2]", "e9", "e1"]}, {"id": "[n2]", "evidence": ["e1.l1"]},
        {"id": "p2", "evidence": ["e1"]}, {"id": "n3", "evidence": ["e1"]}, {"id": "n1", "evidence": []},
        "문자열", {"id": 3, "evidence": ["e1"]}, {"id": "p1", "evidence": ["e1.l1"]}]})
    monkeypatch.setattr(gen, "complete_json", fake)
    out = asyncio.run(gen.find_patterns(R, ["남긴 문장이에요."], ["숨긴 하나예요.", "숨긴 둘이에요."], 0))
    assert out.patterns == [] and out.rechecked == {"p1": ["e1", "e2", "e1.l1"], "n2": ["e1.l1"], "n1": []}
    assert filters.recheck([], KEYS, {"p1"}) == {}


def test_zero_patterns_is_a_valid_result(monkeypatch):
    for answer in ({"patterns": []}, {"patterns": None}, {}):
        monkeypatch.setattr(gen, "complete_json", Fake(answer))
        assert asyncio.run(gen.find_patterns(R, [], [], 5)).patterns == []


def test_llm_errors_propagate(monkeypatch):
    monkeypatch.setattr(gen, "complete_json", Fake(LLMConnectionError("down")))
    with pytest.raises(LLMConnectionError):
        asyncio.run(gen.find_patterns(R, [], [], 5))


def test_analysis_code_never_touches_qa_or_reaction():
    """POL-07: 분석 코드는 qa·reaction 테이블을 읽지 않는다."""
    src = "".join(f.read_text(encoding="utf-8") for f in pathlib.Path(gen.__file__).parent.glob("*.py"))
    sql = " ".join(re.findall(r'"([^"]*)"', src))
    assert not re.search(r"\b(from|join|into|update)\s+(qa|reaction|helpful_reason)\b", sql)


# ── 옮겨 적은 대목(quotes)으로 겹침 잡기 (11/28, 이슈 #24. 출력에 quotes 가 있는 프롬프트에서만 쓰인다) ──

def test_quote_set_reads_key_and_ignores_punctuation():
    assert filters.quote_set(["e1: 뭐가 통한 건지 모르겠다.", "[e2.l1] : 주말 매출 얘기 또 들음", "번호 없음", 3, "e3: "]) == {
        ("e1", "뭐가통한건지모르겠다"), ("e2.l1", "주말매출얘기또들음")}


def test_reworded_pattern_on_the_same_quotes_is_dropped():
    """11/28 Qwen 실제 출력: '맞아요' 한 문장을 말만 바꿔 다시 냈다. 낱말 닮음은 0.28 이라 못 잡는다."""
    q = ["e1: 뭐가 통한 건지 모르겠다", "e2: 왜 떨어졌는지 피드백이 없다"]
    new = {"quotes": q, "text": "노력한 결과에 대해 왜 그런지 이유를 알 수 없었다고 두 번 적었어요.", "evidence": ["e1", "e2"]}
    other = {"quotes": ["e1: 시간이 부족했다", "e2: 시간이 부족했다"], "text": "시간이 부족했다는 상황을 두 번 적었어요.", "evidence": ["e1", "e2"]}
    old = ["결과가 나오지 않거나 떨어졌을 때 이유를 알 수 없었다고 적었어요."]
    kept, dropped = filters.apply([new, other], KEYS, old, [], 5, [(filters.quote_set(q), filters.DUPLICATE)])
    assert [k.text for k in kept] == [other["text"]]            # 근거 기록이 같아도 대목이 다르면 남는다
    assert dropped == [(new["text"], filters.DUPLICATE)]
    kept, dropped = filters.apply([new], KEYS, [], old, 5, [(filters.quote_set(q), filters.BLOCKED)])
    assert not kept and dropped[0][1] == filters.BLOCKED


def test_two_new_patterns_on_the_same_quotes_keep_the_first():
    q = ["e1: 주말 매출 얘기 또 들음", "e2: 매출 얘기만 해서"]
    a = {"quotes": q, "text": "매출 이야기를 두 번 적었어요.", "evidence": ["e1", "e2"]}
    b = {"quotes": q, "text": "숫자로 평가받는 일을 되풀이해서 겪었어요.", "evidence": ["e1", "e2"]}
    kept, dropped = filters.apply([a, b], KEYS, [], [], 5)
    assert [k.text for k in kept] == [a["text"]] and dropped[0][1] == filters.DUPLICATE


def test_patterns_without_quotes_are_untouched():
    kept, _ = filters.apply([p("시간이 부족했다는 상황을 두 번 적었어요.", "e1", "e2"), p("지침을 두 번 골랐어요.", "e1", "e2")], KEYS, [], [], 5)
    assert len(kept) == 2


def test_output_shape_puts_quotes_before_text():
    """v3: 모델이 문장을 쓰기 전에 근거 대목부터 옮겨 적는다. 번호 붙은 문장에도 quotes 가 있어야 겹침을 가린다."""
    from app.analysis import generate
    props = generate.OUTPUT_SCHEMA["properties"]
    assert list(props["patterns"]["items"]["properties"]) == ["quotes", "text", "evidence"]
    assert list(props["existing"]["items"]["properties"]) == ["id", "quotes", "evidence"]
