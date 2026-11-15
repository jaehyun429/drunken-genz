"""generate() 의 다시 묻기·출처 맞추기. LLM 은 가짜."""
import asyncio
import pytest
import app.questions.generate as gen
from app.constants import QUESTION_MIN
from app.llm.client import LLMResult, LLMConnectionError, LLMModelError
from app.questions.schemas import ExperienceInput
from app.questions import filters

E = ExperienceInput(id="e1", activity_name="카페 알바", genre="알바", role="바리스타", period_start="2026-01",
                    did_chips=["고객 응대", "판매"], situation_chips=["결과가 숫자로 평가됐다"],
                    one_liner="사장님이 매출 얘기만 해서 좀 지치지만 손님이랑 얘기하는 건 좋다.")


def q(text, type="try_next", axis="did", source="고객 응대"):
    return {"type": type, "axis": axis, "text": text, "source": source, "done_experience_id": None}


GOOD1 = q("매출 얘기 대신, 손님과 나눈 대화 내용을 기록해볼까요?")
GOOD2 = q("결과가 숫자로 평가되지 않았다면 어디에 시간을 더 썼을까요?", type="imagine", axis="situation", source="결과가 숫자로 평가됬다")
GOOD3 = q("판매 말고 메뉴를 짜는 일을 맡아본다면 어떨까요?", source="판매")
BAD = q("사장님이 매출이 아닌 다른 주제를 꺼냈다면, 지치는 느낌이 덜했을까요?", type="imagine", axis="external", source="한 줄")


class Fake:
    def __init__(self, *answers):
        self.answers = list(answers); self.users = []

    async def __call__(self, system, user, schema, **kw):
        self.users.append(user)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return LLMResult(data={"questions": a}, model="fake", prompt_tokens=1, completion_tokens=1, attempts=1)


def run(monkeypatch, *answers):
    fake = Fake(*answers)
    monkeypatch.setattr(gen, "complete_json", fake)
    return fake, asyncio.run(gen.generate(E, []))


def test_enough_questions_means_one_call(monkeypatch):
    fake, out = run(monkeypatch, [GOOD1, GOOD2])
    assert len(fake.users) == 1 and out.reasks == 0 and len(out.questions) == 2


def test_reasks_once_with_dropped_reason_and_fills_to_minimum(monkeypatch):
    fake, out = run(monkeypatch, [GOOD1, BAD], [GOOD2, GOOD3])
    assert len(fake.users) == 2 and out.reasks == 1 and out.attempts == 2
    assert BAD["text"] in fake.users[1] and "다른 사람의 행동" in fake.users[1] and GOOD1["text"] in fake.users[1]
    assert [x.text for x in out.questions] == [GOOD1["text"], GOOD2["text"]], "다시 물어 얻은 건 최소 개수까지만"
    assert len(out.questions) == QUESTION_MIN
    assert out.dropped == [(BAD["text"], "다른 사람의 행동")]


def test_reask_answer_that_repeats_or_breaks_rules_is_dropped(monkeypatch):
    fake, out = run(monkeypatch, [GOOD1, BAD], [GOOD1, BAD])
    assert len(fake.users) == 2 and [x.text for x in out.questions] == [GOOD1["text"]]
    assert [d[1] for d in out.dropped] == ["다른 사람의 행동", "버린 질문을 다시 냄", "겹치는 질문"]


def test_reask_answer_that_rewords_a_dropped_question_is_dropped(monkeypatch):
    """규칙에 걸려 버린 질문을 규칙만 피한 꼴로 다시 내면 받지 않는다 (11/15 Qwen f02)."""
    reworded = q("매출이 아닌 다른 주제가 오가는 가게였다면, 지치는 느낌이 덜했을까요?", type="imagine", axis="external", source="한 줄")
    assert filters.apply([reworded], set())[1] == [], "이 문장만 보면 규칙에 안 걸린다"
    fake, out = run(monkeypatch, [GOOD1, BAD], [reworded])
    assert [x.text for x in out.questions] == [GOOD1["text"]] and out.reasks == 1
    assert out.dropped[-1] == (reworded["text"], "버린 질문을 다시 냄")


def test_reask_may_fix_a_question_dropped_for_format(monkeypatch):
    no_source = {**GOOD2, "source": ""}
    fake, out = run(monkeypatch, [GOOD1, no_source], [GOOD2])
    assert [x.text for x in out.questions] == [GOOD1["text"], GOOD2["text"]]


def test_reask_may_fix_a_question_dropped_for_tone(monkeypatch):
    """반말이나 묻다 만 꼴로 버린 질문은 존댓말로 고쳐 다시 내면 받는다 (11/15 Qwen f05 "…궁금해.")."""
    casual = {**GOOD2, "text": "결과가 숫자로 평가되지 않았다면 어디에 시간을 더 썼을지 궁금해."}
    fake, out = run(monkeypatch, [GOOD1, casual], [GOOD2])
    assert filters.TONE in fake.users[1]
    assert [x.text for x in out.questions] == [GOOD1["text"], GOOD2["text"]]
    assert out.dropped == [(casual["text"], filters.TONE)]


def test_reask_failure_keeps_what_we_have(monkeypatch):
    fake, out = run(monkeypatch, [GOOD1, BAD], LLMConnectionError("끊김"))
    assert [x.text for x in out.questions] == [GOOD1["text"]] and out.reasks == 1


def test_zero_questions_reasks_then_fails(monkeypatch):
    with pytest.raises(LLMModelError):
        run(monkeypatch, [BAD], [])
    with pytest.raises(LLMConnectionError):
        run(monkeypatch, [BAD], LLMConnectionError("끊김"))


def test_zero_then_reask_recovers(monkeypatch):
    fake, out = run(monkeypatch, [BAD], [GOOD1, GOOD3])
    assert [x.text for x in out.questions] == [GOOD1["text"], GOOD3["text"]]


def test_source_is_matched_to_the_record(monkeypatch):
    fake, out = run(monkeypatch, [GOOD1, GOOD2])
    assert out.questions[1].source == "결과가 숫자로 평가됐다"


def test_same_fact_drop_triggers_a_reask(monkeypatch):
    """같은 사실을 두 번 물어 하나가 빠지면 2개가 안 되니 다시 묻는다 (11/15 Qwen f05 꼴)."""
    e = ExperienceInput(id="e5", activity_name="편의점 야간 알바", genre="알바", role="야간 근무", period_start="2025-06",
                        did_chips=["판매", "고객 응대"], conditions={"규모": "혼자"}, situation_chips=["혼자 결정해야 했다"],
                        one_liner="새벽에 취객 상대하는 법을 배웠다.")
    first = [q("혼자 근무가 아니라 두 명이 함께였다면, 취객 응대 방식이 달라졌을까요?", axis="scale", source="규모=혼자"),
             q("혼자 결정해야 하는 상황이 아니라면, 취객 상대할 때 어떤 선택을 했을까요?", type="imagine", axis="situation",
               source="혼자 결정해야 했다")]
    second = [q("판매 말고 발주를 맡아본다면 어떨까요?", source="판매")]
    fake = Fake(first, second)
    monkeypatch.setattr(gen, "complete_json", fake)
    out = asyncio.run(gen.generate(e, []))
    assert out.reasks == 1 and "같은 사실을 두 번 물음" in fake.users[1]
    # 둘 다 그 종류의 하나뿐이라 상상해보기를 남기고, 해볼 것을 다시 받는다
    assert [(x.type, x.text) for x in out.questions] == [("imagine", first[1]["text"]), ("try_next", second[0]["text"])]
    assert out.dropped == [(first[0]["text"], "같은 사실을 두 번 물음")]
