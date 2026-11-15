from app.questions.prompt import record_text, input_hash, build_messages
from app.questions.schemas import ExperienceInput, OtherExperience


def exp(**kw):
    base = dict(id="e1", activity_name="카페 알바", genre="알바", role="바리스타", period_start="2026-03",
                did_chips=["고객 응대"], did_notes={"고객 응대": "단골 이름 외우기"}, emotion_chips=["재미"],
                one_liner="손님이랑 얘기하는 건 좋다.")
    base.update(kw)
    return ExperienceInput(**base)


def test_record_text_marks_emotion_as_reference_only():
    t = record_text(exp())
    assert "감정(참고만, 질문 재료 아님): 재미" in t
    assert "한 줄: 손님이랑" in t


def test_hash_ignores_whitespace_only_changes():
    a, b = exp(one_liner="손님이랑  얘기하는 건 좋다."), exp(one_liner="손님이랑 얘기하는 건 좋다. ")
    assert input_hash(a) == input_hash(b)
    assert input_hash(a) != input_hash(exp(one_liner="사장님이 매출 얘기만 한다."))


def test_build_messages_has_rules_and_others():
    version, system, user = build_messages(exp(), [OtherExperience(id="e2", activity_name="편의점", genre="알바", role="야간")])
    assert version == "v3"
    assert "잘한 일을 그냥 뒤집은 질문" in system and "80자" in system
    # v2 에서 넣은 것: 재료만큼 만들기, 남의 행동 금지, 상황 뒤집기만 금지 (11/14 Qwen 실측)
    assert "재료가 셋 이상 있는데 2개만 내면" in system
    assert "다른 사람이 다르게 행동했다면" in system and "상황을 반대말로 뒤집기만 한 질문" in system
    # v3 에서 넣은 것: 그 활동에서 있을 수 없는 조건으로 바꾸지 않기 (11/15 Qwen f05 "온라인으로 고객 응대")
    assert "그 활동에서 있을 수 없는 조건" in system
    assert "{text_max}" not in system
    assert "[e2] 편의점" in user and "## 경험 기록" in user


def test_in_progress_logs_rendered():
    t = record_text(exp(status="in_progress", logs=[{"log_date": "2026-11-01", "situation_chips": ["시간이 부족했다"],
                                                    "one_liner": "첫 회의"}]))
    assert "회차 기록:" in t and "2026-11-01 · 상황 시간이 부족했다 · 첫 회의" in t
