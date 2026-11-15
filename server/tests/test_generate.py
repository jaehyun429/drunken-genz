"""generate() 의 다시 묻기·출처 맞추기. LLM 은 가짜."""
import asyncio
import pytest
import app.questions.generate as gen
from app.constants import QUESTION_MIN
from app.llm.client import LLMResult, LLMConnectionError, LLMModelError
from app.questions.schemas import ExperienceInput

E = ExperienceInput(id="e1", activity_name="카페 알바", genre="알바", role="바리스타", period_start="2026-01",
                    did_chips=["고객 응대", "판매"], situation_chips=["결과가 숫자로 평가됐다"],
                    one_liner="사장님이 매출 얘기만 해서 좀 지치지만 손님이랑 얘기하는 건 좋다.")


def q(text, type="try_next", axis="did", source="고객 응대"):
    return {"type": type, "axis": axis, "text": text, "source": source, "done_experience_id": None}


GOOD1 = q("매출 얘기 대신, 손님과 나눈 대화 내용을 기록해볼까?")
GOOD2 = q("결과가 숫자로 평가되지 않았다면 어디에 시간을 더 썼을까?", type="imagine", axis="situation", source="결과가 숫자로 평가됬다")
GOOD3 = q("판매 말고 메뉴를 짜는 일을 맡아본다면 어떨까?", source="판매")
BAD = q("사장님이 매출이 아닌 다른 주제를 꺼냈다면, 지치는 느낌이 덜했을지?", type="imagine", axis="external", source="한 줄")


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
    assert [d[1] for d in out.dropped] == ["다른 사람의 행동", "겹치는 질문", "다른 사람의 행동"]


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
