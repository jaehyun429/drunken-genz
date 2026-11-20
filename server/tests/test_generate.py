"""generate() 의 4개 고르기·다시 묻기·다시 만들기·출처 맞추기. LLM 은 가짜.
   11/14 기획 변경 (PRD 7.2·7.2.1, 기능명세서 14장): 4개 고정, 더 적어보기, 이전에 보여준 질문과 안 겹치기."""
import asyncio
import pytest
import app.questions.generate as gen
from app.constants import QUESTION_COUNT, QUESTION_TEXT_MAX
from app.llm.client import LLMResult, LLMConnectionError, LLMModelError
from app.questions.schemas import ExperienceInput, GeneratedQuestion
from app.questions import filters

E = ExperienceInput(id="e1", activity_name="카페 알바", genre="알바", role="바리스타", period_start="2026-01",
                    did_chips=["고객 응대", "판매"], situation_chips=["결과가 숫자로 평가됐다"],
                    one_liner="사장님이 매출 얘기만 해서 좀 지치지만 손님이랑 얘기하는 건 좋다.")


def q(text, type="try_next", axis="did", source="고객 응대"):
    return {"type": type, "axis": axis, "text": text, "source": source, "done_experience_id": None}


T1 = q("다음엔 손님과 나눈 대화도 적어보면 어떨까요?")
T2 = q("다음엔 메뉴를 짜는 일도 맡아보면 어떨까요?", source="판매")
T3 = q("다음엔 여럿이 일하는 매장에서 해보면 어떨까요?", axis="scale", source="바리스타")
I1 = q("만약 결과가 숫자로 평가되지 않았다면 어땠을까요?", type="imagine", axis="situation", source="결과가 숫자로 평가됬다")
I2 = q("만약 손님이 적은 시간대였다면 어땠을까요?", type="imagine", axis="external", source="한 줄")
BAD = q("사장님이 매출이 아닌 다른 주제를 꺼냈다면, 지치는 느낌이 덜했을까요?", type="imagine", axis="external", source="한 줄")
W_ACTION = q("그때 어떤 일을 직접 했나요?", type="write_more", axis="gap_action", source="")
W_RESULT = q("그 뒤로 어떻게 됐나요?", type="write_more", axis="gap_result", source="아무 말")
W_SITUATION = q("그때 어떤 상황이었나요?", type="write_more", axis="gap_situation", source="")


def test_fixture_sentences_fit_the_length_limit():
    for item in (T1, T2, T3, I1, I2, BAD, W_ACTION, W_RESULT, W_SITUATION):
        assert len(item["text"]) <= QUESTION_TEXT_MAX, item["text"]


class Fake:
    def __init__(self, *answers):
        self.answers = list(answers); self.users = []

    async def __call__(self, system, user, schema, **kw):
        self.users.append(user)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return LLMResult(data={"questions": a}, model="fake", prompt_tokens=1, completion_tokens=1, attempts=1)


def run(monkeypatch, *answers, e=E, previous=None):
    fake = Fake(*answers)
    monkeypatch.setattr(gen, "complete_json", fake)
    return fake, asyncio.run(gen.generate(e, [], previous))


def texts(out):
    return [x.text for x in out.questions]


# --- 4개 고르기 (PRD 7.2 구성) ---------------------------------------------

def G(type, axis, n):
    return GeneratedQuestion(type=type, axis=axis, text=f"{type}{n}요?", source="s")


def test_compose_takes_two_and_two_when_there_is_no_write_more():
    cands = [G("try_next", "role", 1), G("try_next", "did", 2), G("try_next", "scale", 3),
             G("imagine", "situation", 1), G("imagine", "external", 2), G("imagine", "external", 3)]
    out = gen.compose(cands)
    assert [(x.type, x.axis) for x in out] == [("try_next", "role"), ("try_next", "did"),
                                               ("imagine", "situation"), ("imagine", "external")]


def test_compose_puts_write_more_first_in_slot_priority_and_caps_it_at_two():
    cands = [G("try_next", "role", 1), G("write_more", "gap_situation", 1), G("imagine", "situation", 1),
             G("write_more", "gap_result", 2), G("try_next", "did", 2), G("write_more", "gap_action", 3), G("imagine", "external", 2)]
    out = gen.compose(cands)
    assert [(x.type, x.axis) for x in out] == [("write_more", "gap_action"), ("write_more", "gap_result"),
                                               ("try_next", "role"), ("imagine", "situation")]


def test_compose_with_one_write_more_gives_try_two_and_imagine_one():
    cands = [G("write_more", "gap_result", 1), G("imagine", "situation", 1), G("imagine", "external", 2),
             G("try_next", "role", 1), G("try_next", "did", 2)]
    out = gen.compose(cands)
    assert [x.type for x in out] == ["write_more", "imagine", "try_next", "try_next"], "나머지 3자리는 해볼 것 2 + 상상해보기 1, 모델이 준 순서"


def test_compose_fills_with_the_other_type_when_one_runs_short():
    cands = [G("try_next", "role", 1), G("try_next", "did", 2), G("try_next", "scale", 3), G("imagine", "situation", 1)]
    assert [x.type for x in gen.compose(cands)] == ["try_next", "try_next", "try_next", "imagine"]
    assert [x.type for x in gen.compose(cands[:3])] == ["try_next"] * 3


def test_shortage_asks_for_each_missing_type_then_fills_the_smaller_side():
    assert gen.shortage([]) == {"try_next": 2, "imagine": 2}
    assert gen.shortage([G("try_next", "role", 1), G("try_next", "did", 2), G("try_next", "scale", 3)]) == {"imagine": 1}
    assert gen.shortage([G("try_next", "role", 1), G("imagine", "situation", 1)]) == {"try_next": 1, "imagine": 1}
    assert gen.shortage([G("write_more", "gap_action", 1), G("write_more", "gap_result", 2)]) == {"try_next": 1, "imagine": 1}
    full = [G("try_next", "role", 1), G("try_next", "did", 2), G("imagine", "situation", 1), G("imagine", "external", 2)]
    assert gen.shortage(full) == {}
    # 4개가 찼어도 한 종류가 없으면 그 종류를 받는다
    assert gen.shortage(full[:2] + [G("try_next", "scale", 3), G("try_next", "field", 4)]) == {"imagine": 1}


# --- generate ----------------------------------------------------------------

def test_four_good_candidates_means_one_call(monkeypatch):
    fake, out = run(monkeypatch, [T1, I1, T2, I2, T3])
    assert len(fake.users) == 1 and out.reasks == 0 and not out.no_new
    assert texts(out) == [T1["text"], I1["text"], T2["text"], I2["text"]] and len(out.questions) == QUESTION_COUNT
    assert "## 더 적어보기 판단" in fake.users[0] and "이전에 보여준 질문" not in fake.users[0]


def test_write_more_comes_first_with_the_slot_name_as_source(monkeypatch):
    fake, out = run(monkeypatch, [T1, I1, T2, I2, W_RESULT, W_ACTION, W_SITUATION])
    assert [(x.type, x.axis, x.source) for x in out.questions[:2]] == [
        ("write_more", "gap_action", "내가 한 일·이유"), ("write_more", "gap_result", "결과·배움")]
    assert texts(out)[2:] == [T1["text"], I1["text"]] and out.reasks == 0
    assert all(x.done_experience_id is None for x in out.questions[:2])


def test_answered_slot_is_not_asked_again(monkeypatch):
    e = ExperienceInput(**{**E.model_dump(), "answers": [{"slot": "gap_result", "question": "그 뒤로 어떻게 됐나요?", "answer": "단골이 늘었다."}]})
    fake, out = run(monkeypatch, [W_RESULT, T1, I1, T2, I2], e=e)
    assert all(x.type != "write_more" for x in out.questions) and len(out.questions) == 4
    assert out.dropped == [(W_RESULT["text"], filters.ANSWERED_SLOT)]
    assert "더 적어본 것:" in fake.users[0] and "단골이 늘었다." in fake.users[0]
    assert "이미 답한 칸: 결과·배움(gap_result)" in fake.users[0]


def test_reasks_once_for_the_missing_kinds_and_fills_to_four(monkeypatch):
    fake, out = run(monkeypatch, [T1, BAD], [I1, T2, I2])
    assert len(fake.users) == 2 and out.reasks == 1 and out.attempts == 2
    assert BAD["text"] in fake.users[1] and "다른 사람의 행동" in fake.users[1] and T1["text"] in fake.users[1]
    assert "try_next(해볼 것) 1개, imagine(상상해보기) 2개" in fake.users[1]
    assert texts(out) == [T1["text"], I1["text"], T2["text"], I2["text"]]
    assert out.dropped == [(BAD["text"], "다른 사람의 행동")]


def test_reasks_when_four_candidates_have_no_imagine(monkeypatch):
    more_try = q("다음엔 발표를 맡는 역할에 지원해보면 어떨까요?", axis="role", source="바리스타")
    fake, out = run(monkeypatch, [T1, T2, T3, more_try], [I1])
    assert out.reasks == 1 and "imagine(상상해보기) 1개" in fake.users[1] and "try_next" not in fake.users[1].split("## 다시 요청")[1].split("###")[0]
    assert [x.type for x in out.questions] == ["try_next", "try_next", "try_next", "imagine"]


def test_reask_answer_that_repeats_or_breaks_rules_is_dropped(monkeypatch):
    fake, out = run(monkeypatch, [T1, BAD], [T1, BAD])
    assert len(fake.users) == 2 and texts(out) == [T1["text"]]
    assert [d[1] for d in out.dropped] == ["다른 사람의 행동", "버린 질문을 다시 냄", "겹치는 질문"]


def test_reask_answer_that_rewords_a_dropped_question_is_dropped(monkeypatch):
    """규칙에 걸려 버린 질문을 규칙만 피한 꼴로 다시 내면 받지 않는다 (11/15 Qwen f02)."""
    reworded = q("매출이 아닌 다른 주제가 오가는 가게였다면, 지치는 느낌이 덜했을까요?", type="imagine", axis="external", source="한 줄")
    assert filters.apply([reworded], set())[1] == [], "이 문장만 보면 규칙에 안 걸린다"
    fake, out = run(monkeypatch, [T1, BAD], [reworded])
    assert texts(out) == [T1["text"]] and out.reasks == 1
    assert out.dropped[-1] == (reworded["text"], "버린 질문을 다시 냄")


def test_reask_may_fix_a_question_dropped_for_format_tone_or_length(monkeypatch):
    no_source = {**I1, "source": ""}
    casual = {**I2, "text": "만약 손님이 적은 시간대였다면 어땠을지 궁금해."}
    long = {**T2, "text": "다음엔 판매만 하지 말고 계절마다 바뀌는 메뉴를 짜는 일도 한번 직접 맡아보면 어떨까요?"}
    assert len(long["text"]) > QUESTION_TEXT_MAX
    fake, out = run(monkeypatch, [T1, no_source, casual, long], [I1, I2, T2])
    assert filters.TONE in fake.users[1] and f"{QUESTION_TEXT_MAX}자 초과" in fake.users[1]
    assert texts(out) == [T1["text"], I1["text"], I2["text"], T2["text"]]
    assert [d[1] for d in out.dropped] == ["source 없음", filters.TONE, f"{QUESTION_TEXT_MAX}자 초과"]


def test_reask_failure_keeps_what_we_have(monkeypatch):
    fake, out = run(monkeypatch, [T1, BAD], LLMConnectionError("끊김"))
    assert texts(out) == [T1["text"]] and out.reasks == 1 and gen.too_few(out)


def test_still_short_after_one_reask_stores_what_exists(monkeypatch):
    fake, out = run(monkeypatch, [T1, I1], [])
    assert len(fake.users) == 2 and texts(out) == [T1["text"], I1["text"]] and gen.too_few(out)


def test_zero_questions_reasks_then_fails(monkeypatch):
    with pytest.raises(LLMModelError):
        run(monkeypatch, [BAD], [])
    with pytest.raises(LLMConnectionError):
        run(monkeypatch, [BAD], LLMConnectionError("끊김"))


def test_zero_then_reask_recovers(monkeypatch):
    fake, out = run(monkeypatch, [BAD], [T1, I1])
    assert texts(out) == [T1["text"], I1["text"]]


def test_source_is_matched_to_the_record(monkeypatch):
    fake, out = run(monkeypatch, [T1, I1, T2, I2])
    assert out.questions[1].source == "결과가 숫자로 평가됐다"


# --- 다시 만들기 (PRD 7.2 재생성) ------------------------------------------

def test_regeneration_drops_questions_shown_before(monkeypatch):
    shown_again = q("다음엔 손님과 나눈 대화도 꼭 적어보면 어떨까요?")
    fake, out = run(monkeypatch, [shown_again, I1, T2, I2, T3], previous=[T1["text"]])
    assert "## 이전에 보여준 질문 (다시 내지 않는다)" in fake.users[0] and f"- {T1['text']}" in fake.users[0]
    assert texts(out) == [I1["text"], T2["text"], I2["text"], T3["text"]] and not out.no_new and out.reasks == 0
    assert out.dropped == [(shown_again["text"], filters.SHOWN)]


def test_regeneration_with_nothing_new_reports_no_new(monkeypatch):
    previous = [T1["text"], I1["text"]]
    fake, out = run(monkeypatch, [T1, I1], [T1], previous=previous)
    assert out.no_new and out.questions == [] and out.reasks == 1
    assert [d[1] for d in out.dropped] == [filters.SHOWN, filters.SHOWN, filters.REPEATED]
    # 모델이 빈 목록을 내도 같다
    fake, out = run(monkeypatch, [], [], previous=previous)
    assert out.no_new and out.questions == []
    # 더 적어보기만 새로 나온 것도 '새 질문 없음'이다. 넘긴 더 적어보기는 그대로 다시 보여주는 것이라서
    fake, out = run(monkeypatch, [W_ACTION], [], previous=previous)
    assert out.no_new and [x.type for x in out.questions] == ["write_more"]


def test_regeneration_that_only_breaks_rules_is_a_model_failure(monkeypatch):
    with pytest.raises(LLMModelError):
        run(monkeypatch, [BAD], [], previous=[T1["text"]])


def test_first_generation_ignores_shown_check(monkeypatch):
    fake, out = run(monkeypatch, [T1, I1, T2, I2])
    assert not out.no_new and len(out.questions) == 4


def test_same_fact_drop_triggers_a_reask(monkeypatch):
    """같은 사실을 두 번 물어 하나가 빠지면 모자라니 다시 묻는다 (11/15 Qwen f05 꼴)."""
    e = ExperienceInput(id="e5", activity_name="편의점 야간 알바", genre="알바", role="야간 근무", period_start="2025-06",
                        did_chips=["판매", "고객 응대"], conditions={"규모": "혼자"}, situation_chips=["혼자 결정해야 했다"],
                        one_liner="새벽에 취객 상대하는 법을 배웠다.")
    first = [q("혼자 근무가 아니라 둘이 함께였다면 응대가 달라졌을까요?", axis="scale", source="규모=혼자"),
             q("만약 혼자 결정해야 하는 상황이 아니라면 어땠을까요?", type="imagine", axis="situation", source="혼자 결정해야 했다")]
    second = [q("다음엔 발주도 맡아보면 어떨까요?", source="판매")]
    fake, out = run(monkeypatch, first, second, e=e)
    assert out.reasks == 1 and "같은 사실을 두 번 물음" in fake.users[1]
    # 둘 다 그 종류의 하나뿐이라 상상해보기를 남기고, 해볼 것을 다시 받는다
    assert [(x.type, x.text) for x in out.questions] == [("imagine", first[1]["text"]), ("try_next", second[0]["text"])]
    assert out.dropped == [(first[0]["text"], "같은 사실을 두 번 물음")]
