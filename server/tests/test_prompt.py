from app.constants import QUESTION_DECISION_CHIPS
from app.questions.prompt import record_text, input_hash, build_messages, has_raw_text, is_decision, reask_text
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
    assert version == "v5"
    # v2~v4 에서 넣은 것 가운데 남긴 것 (11/14~15 Qwen 실측)
    assert "다른 사람이 다르게 행동했다면" in system and "그 활동에서 있을 수 없는 조건" in system
    assert '"-시-"를 넣지 않는다' in system
    # v5, 11/14 기획 변경 (PRD 7.2): 40자, 문장 틀, 성장·보완형, 4개 구성, 더 적어보기, 다시 만들 때
    assert "40자 이내" in system and "{text_max}" not in system
    assert "## 문장 틀" in system and "다음엔 ~해보면 어떨까요?" in system and "남은 회차에서 ~해보면 어떨까요?" in system
    assert "만약 ~했다면 어땠을까요?" in system and "다음엔 ~에 지원해보면 어떨까요?" in system
    assert "한 행동을 빼거나 뒤집는 가정" in system and "나쁜 상황을 없애는 가정은 보완형이라 쓸 수 있다" in system
    assert "상황을 반대말로 뒤집기만 한 질문" not in system, "v4 14번은 '나쁜 상황을 없애는 가정 허용'과 어긋나 뺐다"
    assert "## 더 적어보기 (write_more)" in system and "gap_action" in system and "## 다시 만들 때" in system
    assert "내가 내린 결정(decision)" in system
    assert "[e2] 편의점" in user and "## 경험 기록" in user
    assert "## 더 적어보기 판단" in user and "이전에 보여준 질문" not in user


def test_in_progress_logs_rendered():
    t = record_text(exp(status="in_progress", logs=[{"log_date": "2026-11-01", "situation_chips": ["시간이 부족했다"],
                                                    "one_liner": "첫 회의"}]))
    assert "회차 기록:" in t and "2026-11-01 · 상황 시간이 부족했다 · 첫 회의" in t
    assert "(알바, 진행 중)" in t, "문장 틀이 상태에 따라 달라 첫 줄에 상태를 적는다"


# --- 11/14 기획 변경 --------------------------------------------------------

def test_decision_chips_go_to_their_own_line():
    """내가 내린 결정 칩은 해볼 것 재료, 그 밖의 상황 칩은 상상해보기 재료 (PRD 6.6)."""
    assert len(QUESTION_DECISION_CHIPS) == 6 and is_decision("범위  축소") and not is_decision("시간이 부족했다")
    t = record_text(exp(situation_chips=["시간이 부족했다", "범위 축소"],
                        situation_notes={"범위 축소": "부스를 두 개로 줄였다", "시간이 부족했다": "준비가 사흘뿐이었다"}))
    lines = t.split("\n")
    d = lines.index("내가 내린 결정(고를 수 있었던 것): 범위 축소")
    s = lines.index("상황(고를 수 없었던 것): 시간이 부족했다")
    assert lines[d + 1] == "  - 범위 축소: 부스를 두 개로 줄였다" and lines[s + 1] == "  - 시간이 부족했다: 준비가 사흘뿐이었다"
    only = record_text(exp(situation_chips=["범위 축소"]))
    assert "상황(고를 수 없었던 것)" not in only


def test_write_more_judgement_says_whether_there_is_raw_text():
    chips_only = exp(did_notes={}, one_liner=None)
    assert not has_raw_text(chips_only) and has_raw_text(exp())
    _, _, user = build_messages(chips_only, [])
    assert "- 원문: 없음 (칩만 있음). 세 칸 모두 빈 칸" in user and "- 이미 답한 칸: (없음)" in user
    _, _, user = build_messages(exp(), [])
    assert "- 원문: 있음." in user


def test_answers_are_part_of_the_record_and_change_the_hash():
    answered = exp(did_notes={}, one_liner=None,
                   answers=[{"slot": "gap_result", "question": "그 뒤로 어떻게 됐나요?", "answer": "단골이  늘었다."}])
    t = record_text(answered)
    assert "더 적어본 것:\n  - [결과·배움] 그 뒤로 어떻게 됐나요? → 단골이 늘었다." in t
    assert has_raw_text(answered), "답도 원문이다"
    assert input_hash(answered) != input_hash(exp(did_notes={}, one_liner=None))
    _, _, user = build_messages(answered, [])
    assert "- 이미 답한 칸: 결과·배움(gap_result)" in user and "- 원문: 있음." in user


def test_previous_questions_are_listed_only_when_regenerating():
    _, _, user = build_messages(exp(), [], previous=["다음엔 메뉴를 짜는 일도 맡아보면 어떨까요?", "만약 손님이 적었다면 어땠을까요?"])
    tail = user.split("## 이전에 보여준 질문 (다시 내지 않는다)\n")[1]
    assert tail == "- 다음엔 메뉴를 짜는 일도 맡아보면 어떨까요?\n- 만약 손님이 적었다면 어땠을까요?"
    assert "이전에 보여준 질문" not in build_messages(exp(), [], previous=[])[2]


def test_reask_text_names_what_is_missing():
    t = reask_text(["다음엔 메뉴를 짜는 일도 맡아보면 어떨까요?"], [("버린 질문요?", "조언·훈계")], {"try_next": 1, "imagine": 2})
    assert "규칙에 맞는 질문이 1개뿐이다. try_next(해볼 것) 1개, imagine(상상해보기) 2개 더 필요하다." in t
    assert "- 버린 질문요? (조언·훈계)" in t or "버린 질문요?" in t and "조언·훈계" in t
    assert "write_more 는 내지 않는다" in t and "{" not in t
