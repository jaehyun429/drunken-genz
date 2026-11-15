from app.questions import filters
from app.constants import QUESTION_MAX

OTHERS = {"e2", "e3"}


def q(text, type="try_next", axis="did", source="한 줄", done=None):
    return {"type": type, "axis": axis, "text": text, "source": source, "done_experience_id": done}


def test_keeps_good_question_and_order():
    kept, dropped = filters.apply([q("다음 회의 때 첫 5분 안에 한마디만 꺼내볼까요?", axis="method"),
                                   q("그때 역할을 먼저 정했다면 어땠을까요?", type="imagine", axis="situation")], OTHERS)
    assert [k.text[:5] for k in kept] == ["다음 회의", "그때 역할"]
    assert dropped == []


def test_drops_without_source():
    kept, dropped = filters.apply([q("아무 질문", source="")], OTHERS)
    assert kept == [] and dropped[0][1] == "source 없음"


def test_drops_axis_mismatch():
    kept, dropped = filters.apply([q("상황을 바꾼 해볼 것?", type="try_next", axis="situation")], OTHERS)
    assert kept == [] and "축 불일치" in dropped[0][1]


def test_drops_reversed_good_deed():
    for text in ["단골 이름 외우기 시작하지 않았다면 어땠을까요?", "인스타에 릴스를 올리지 않았다면 어땠을까요?",
                 "부스 홍보를 하지 않았다면 어땠을까요?"]:
        kept, dropped = filters.apply([q(text, type="imagine", axis="situation")], OTHERS)
        assert kept == [] and dropped[0][1] == "잘한 일을 뒤집기만 한 질문", text


# 11/14 실측에서 잘못 버려졌던 문장과, 그와 같은 꼴. 패턴을 고칠 때 여기가 깨지면 안 된다
PASS = [
    ("시간이 부족하지 않았다면, 타로 부스 동선 기획에 더 많은 시간을 썼을 때 어떤 변화가 있었을까요?", "imagine", "situation"),   # Qwen
    ("손님이랑 얘기하는 게 좋다면, 주문 받을 때 짧은 말을 먼저 건네는 방식으로 바꿔볼까요?", "try_next", "method"),          # Haiku
    ("시간이 부족했던 상황이 아니었다면, 설문 문항을 어떻게 다르게 짰을까요?", "imagine", "situation"),
    ("방식이 복잡하지 않았다면 어디에 시간을 더 썼을까요?", "imagine", "situation"),
    ("손님이랑 얘기하는 게 좋았다면, 그 시간이 더 긴 자리에서 일해볼까요?", "try_next", "field"),
    ("팀원이 아니라 팀장으로 들어갔다면 어땠을까요?", "imagine", "situation"),
    ("팀원이 한 명 더 있었다면 동선을 어떻게 나눴을까요?", "imagine", "external"),
    ("멘토가 없었다면 수업 준비를 어떻게 했을까요?", "imagine", "external"),
    ("교수님이 정한 주제가 아니라 직접 고른 주제였다면 어땠을까요?", "imagine", "external"),
    ("내가 팀장이었다면 회의를 어떻게 시작했을까요?", "imagine", "situation"),
    ("야간 근무 중 처리한 고객 응대 건수를 평소보다 더 늘려봤다면 어땠을까요?", "try_next", "did"),            # Qwen v2
    ("지금보다 더 작은 규모의 팀에서 같은 일을 해볼까요?", "try_next", "scale"),
    ("첫 만남들에서 먼저 말을 걸어보는 방식으로 바꿔볼까요?", "try_next", "method"),
]


def test_passes_questions_that_only_look_banned():
    for text, type_, axis in PASS:
        kept, dropped = filters.apply([q(text, type=type_, axis=axis)], OTHERS)
        assert len(kept) == 1 and dropped == [], (text, dropped)


def test_drops_advice_only_when_it_recommends():
    for text in ["미리 말하는 게 좋아", "미리 말하는 게 좋아요?", "기록을 미리 해야 해요?", "먼저 말하는 게 낫지 않을까요?", "다음엔 일찍 준비하는 게 좋겠어", "혼자 하는 게 나을까요?"]:
        kept, dropped = filters.apply([q(text)], OTHERS)
        assert kept == [] and dropped[0][1] == "조언·훈계", text


def test_drops_other_peoples_actions():
    # 앞의 둘은 11/14 Qwen 이 실제로 낸 질문
    for text in ["사장님이 매출이 아니라 손님 반응으로 피드백을 주셨다면 어땠을까요?",
                 "혼자 결정해야 했던 상황이 아니라, 상사가 옆에서 지시를 내렸다면 어땠을까요?",
                 "팀장이 먼저 방향을 정리했다면 어땠을까요?",
                 "사장님이 매출을 묻지 않았다면 어땠을까요?",
                 "팀원들이 회의에 빠지지 않았다면 어땠을까요?"]:
        kept, dropped = filters.apply([q(text, type="imagine", axis="external")], OTHERS)
        assert kept == [] and dropped[0][1] == "다른 사람의 행동", (text, dropped)


def test_drops_impossible_assumption():
    kept, dropped = filters.apply([q("시간 부족을 알리고 기한을 연장 요청했다면 어땠을까요?", type="imagine", axis="external")], OTHERS)
    assert kept == [] and dropped[0][1] == "할 수 없는 가정"


def test_drops_comparison_and_lecture():
    kept, dropped = filters.apply([q("다른 사람들은 어떻게 했을까요?"), q("기록을 미리 해야 해?")], OTHERS)
    assert kept == []
    assert {d[1] for d in dropped} == {"남과 비교", "조언·훈계"}


def test_drops_questions_not_ending_in_polite_question():
    """v4 부터 존댓말. 끝이 "요?"가 아니면 버린다 (11/15 Qwen: "…궁금해.", "…했을지?")."""
    for text in ["주말에도 손님과 대화할 시간이 있었다면 덜 지쳤을지 궁금해.", "다음 알바에서 응대 순서를 직접 적어볼까?",
                 "결과가 숫자로 평가되지 않았다면 어디에 시간을 더 썼을지?", "판매 말고 메뉴를 짜는 일을 맡아보는 것은 어떨까요"]:
        kept, dropped = filters.apply([q(text)], OTHERS)
        assert kept == [] and dropped == [(text, filters.TONE)], text
    for text in ["판매 말고 메뉴를 짜는 일을 맡아보는 것은 어떨까요?", "그때 역할을 먼저 정했다면 어땠을까요?",
                 "주제가 미리 정해져 있었다면, 자료 조사에 시간을 더 썼을까요?"]:
        kept, dropped = filters.apply([q(text, type="imagine", axis="situation")], OTHERS)
        assert [k.text for k in kept] == [text] and dropped == [], text


def test_content_reason_wins_over_tone():
    """반말이면서 금지 규칙에도 걸리면 금지 이유를 남긴다. 말투만 고쳐 다시 내도 받지 않게."""
    kept, dropped = filters.apply([q("사장님이 먼저 얘기를 꺼냈다면 덜 지쳤을까?", type="imagine", axis="external")], OTHERS)
    assert kept == [] and dropped[0][1] == "다른 사람의 행동"
    assert filters.split_repeats([q("사장님이 먼저 얘기를 꺼냈다면 덜 지쳤을까요?")], dropped)[0] == []


def test_drops_comparison_with_people_only():
    for text in ("동기들보다 더 빨리 끝내려면 뭘 바꿔볼까요?", "남들보다 잘 해내려면 어떤 방식을 골라볼까요?",
                 "다른 팀보다 준비를 일찍 시작했다면 어땠을까요?", "남처럼 말을 많이 하는 역할을 맡아볼까요?"):
        kept, dropped = filters.apply([q(text)], OTHERS)
        assert kept == [] and dropped[0][1] == "남과 비교", text


def test_dedupes_similar_questions():
    kept, dropped = filters.apply([q("완성 대신 초안만 저장하는 습관을 만들어볼까요?"),
                                   q("완성 대신 초안으로 만족했다면 어땠을까요?", type="imagine", axis="situation"),
                                   q("완성 대신 초안만 저장하는 습관을 만들어 볼까요?")], OTHERS)
    assert len(kept) == 2  # 1·2 는 다른 말, 3 은 1 과 같음
    assert dropped[0][1] == "겹치는 질문"


def test_caps_at_max_and_validates_done_id():
    texts = ["다음 회의 때 첫 5분 안에 한마디만 꺼내볼까요?", "조원 한 명과 1:1로 짧은 커피 시간을 잡아볼까요?",
             "그때 역할을 먼저 정했다면 어땠을까요?", "혼자가 아니라 둘이서 맡았다면 어땠을까요?",
             "온라인으로 진행했다면 어땠을까요?", "기간을 한 달로 잡았다면 어땠을까요?"]
    raw = [q(t, type="imagine" if i >= 2 else "try_next", axis="situation" if i >= 2 else "method",
             done="e9" if i == 0 else "e2") for i, t in enumerate(texts)]
    kept, _ = filters.apply(raw, OTHERS)
    assert len(kept) == QUESTION_MAX
    assert kept[0].done_experience_id is None   # 목록에 없는 id 는 버림
    assert kept[1].done_experience_id == "e2"


# --- 같은 사실을 축만 바꿔 두 번 묻는 것 ------------------------------------
from app.questions.schemas import ExperienceInput

ALONE = ExperienceInput(id="e1", activity_name="편의점 야간 알바", genre="알바", role="야간 근무", period_start="2025-06",
                        did_chips=["판매"], conditions={"규모": "혼자", "방식": "오프라인", "내 역할": "팀원"},
                        situation_chips=["혼자 결정해야 했다"])


def test_same_fact_words_are_condition_values_found_in_situation_chips():
    assert filters.same_fact_words(ALONE) == {"혼자"}
    logged = ExperienceInput(**{**ALONE.model_dump(), "situation_chips": [],
                                "logs": [{"log_date": "2025-07-01", "situation_chips": ["혼자 결정해야 했다"]}]})
    assert filters.same_fact_words(logged) == {"혼자"}, "회차 기록의 상황 칩도 본다"
    assert filters.same_fact_words(ExperienceInput(**{**ALONE.model_dump(), "situation_chips": ["시간이 부족했다"]})) == set()


SCALE_ALONE = q("혼자 근무가 아니라 두 명이 함께였다면, 취객 응대 방식이 달라졌을까요?", axis="scale")
SITUATION_ALONE = q("혼자 결정해야 하는 상황이 아니라면, 취객 상대할 때 어떤 선택을 했을까요?", type="imagine", axis="situation")


def test_same_fact_on_another_axis_keeps_only_one_and_prefers_imagine_on_a_tie():
    # 11/15 Qwen f05 실제 출력. 글자 유사도는 0.12 라 겹침 규칙으로는 안 잡힌다.
    # 둘 다 그 종류의 하나뿐이면 상상해보기를 남긴다 (해볼 것은 다시 받기 쉽다). 순서가 바뀌어도 같다
    words = filters.same_fact_words(ALONE)
    for raw in ([SCALE_ALONE, SITUATION_ALONE], [SITUATION_ALONE, SCALE_ALONE]):
        kept, dropped = filters.apply(raw, OTHERS, words)
        assert [k.axis for k in kept] == ["situation"] and dropped == [(SCALE_ALONE["text"], filters.SAME_FACT)]
    kept, dropped = filters.apply([SCALE_ALONE, SITUATION_ALONE], OTHERS)
    assert len(kept) == 2, "낱말을 안 주면 보지 않는다"


def test_same_fact_keeps_the_question_whose_type_would_otherwise_disappear():
    words = filters.same_fact_words(ALONE)
    other_imagine = q("새벽에 손님이 몰리지 않았다면, 판매 말고 어떤 일에 시간을 썼을까요?", type="imagine", axis="external")
    other_try = q("판매 말고 발주를 맡아본다면 어떨까요?", axis="did")
    # 상상해보기가 이미 둘이면 하나뿐인 해볼 것을 남긴다. 남긴 질문은 버린 질문이 있던 자리에 들어간다
    kept, dropped = filters.apply([SITUATION_ALONE, other_imagine, SCALE_ALONE], OTHERS, words)
    assert [k.axis for k in kept] == ["scale", "external"] and dropped == [(SITUATION_ALONE["text"], filters.SAME_FACT)]
    # 해볼 것이 이미 둘이면 하나뿐인 상상해보기를 남긴다
    kept, dropped = filters.apply([SCALE_ALONE, other_try, SITUATION_ALONE], OTHERS, words)
    assert [k.axis for k in kept] == ["situation", "did"] and dropped == [(SCALE_ALONE["text"], filters.SAME_FACT)]
    # 종류가 같으면 앞의 것을 남긴다
    method = q("혼자 말고 둘이 번갈아 계산대를 보는 방식이었다면 어땠을까요?", axis="method")
    kept, dropped = filters.apply([SCALE_ALONE, method], OTHERS, words)
    assert [k.axis for k in kept] == ["scale"] and dropped == [(method["text"], filters.SAME_FACT)]


def test_split_repeats_drops_reworded_rejects_but_lets_format_fixes_through():
    # 11/15 Qwen f02: 뺀 질문을 출처만 바꿔 다시 냈다
    rejected = "매출 숫자보다 고객 반응이 더 중요하게 평가받는 환경이었다면, 사장님과의 대화에서 내가 더 편안했을지 궁금해볼까요?"
    reworded = q("주말 매출 숫자보다 단골 아저씨 같은 고객 반응이 더 중요하게 평가받는 환경이었다면, 내가 더 편안했을지 궁금해볼까요?",
                 type="imagine", axis="external")
    other = q("판매 말고 메뉴를 짜는 일을 맡아본다면 어떨까요?")
    fresh, repeats = filters.split_repeats([reworded, other], [(rejected, "남과 비교")])
    assert fresh == [other] and repeats == [(reworded["text"], filters.REPEATED)]
    # 형식 때문에 버린 질문은 고쳐서 다시 내도 된다
    for why in ("source 없음", "type 이상", "축 불일치 try_next/situation", "80자 초과"):
        fresh, repeats = filters.split_repeats([reworded], [(rejected, why)])
        assert fresh == [reworded] and repeats == [], why


def test_same_fact_rule_leaves_different_questions_alone():
    words = filters.same_fact_words(ALONE)
    pairs = [
        # 한쪽만 그 낱말을 바꾼다 (11/15 Qwen f04 꼴)
        (q("혼자 결정해야 했던 상황이 아니라면, 아이에게 다른 접근을 했을까요?", type="imagine", axis="situation"),
         q("2~5명이 아닌, 1명만 집중해서 멘토링을 해볼까요?", axis="scale")),
        # 낱말은 둘 다 있지만 한쪽은 그걸 바꾸지 않는다
        (q("혼자 근무가 아니라 두 명이 함께였다면 어땠을까요?", axis="scale"),
         q("혼자 있는 새벽에 손님이 몰리지 않았다면 어땠을까요?", type="imagine", axis="situation")),
        # 같은 축이면 글자 겹침 규칙에 맡긴다
        (q("혼자 말고 둘이서 근무해보면 어떨까요?", axis="scale"),
         q("혼자 대신 여섯 명 넘는 매장에서 일해보면 어떨까요?", axis="scale")),
    ]
    for a, b in pairs:
        kept, dropped = filters.apply([a, b], OTHERS, words)
        assert len(kept) == 2, (a["text"], b["text"], dropped)
