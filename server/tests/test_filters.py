from app.questions import filters
from app.constants import QUESTION_MAX

OTHERS = {"e2", "e3"}


def q(text, type="try_next", axis="did", source="한 줄", done=None):
    return {"type": type, "axis": axis, "text": text, "source": source, "done_experience_id": done}


def test_keeps_good_question_and_order():
    kept, dropped = filters.apply([q("다음 회의 때 첫 5분 안에 한마디만 꺼내볼까?", axis="method"),
                                   q("그때 역할을 먼저 정했다면 어땠을까?", type="imagine", axis="situation")], OTHERS)
    assert [k.text[:5] for k in kept] == ["다음 회의", "그때 역할"]
    assert dropped == []


def test_drops_without_source():
    kept, dropped = filters.apply([q("아무 질문", source="")], OTHERS)
    assert kept == [] and dropped[0][1] == "source 없음"


def test_drops_axis_mismatch():
    kept, dropped = filters.apply([q("상황을 바꾼 해볼 것?", type="try_next", axis="situation")], OTHERS)
    assert kept == [] and "축 불일치" in dropped[0][1]


def test_drops_reversed_good_deed():
    for text in ["단골 이름 외우기 시작하지 않았다면 어땠을까?", "인스타에 릴스를 올리지 않았다면 어땠을까?",
                 "부스 홍보를 하지 않았다면 어땠을까?"]:
        kept, dropped = filters.apply([q(text, type="imagine", axis="situation")], OTHERS)
        assert kept == [] and dropped[0][1] == "잘한 일을 뒤집기만 한 질문", text


# 11/14 실측에서 잘못 버려졌던 문장과, 그와 같은 꼴. 패턴을 고칠 때 여기가 깨지면 안 된다
PASS = [
    ("시간이 부족하지 않았다면, 타로 부스 동선 기획에 더 많은 시간을 썼을 때 어떤 변화가 있었을까?", "imagine", "situation"),   # Qwen
    ("손님이랑 얘기하는 게 좋다면, 주문 받을 때 짧은 말을 먼저 건네는 방식으로 바꿔볼까?", "try_next", "method"),          # Haiku
    ("시간이 부족했던 상황이 아니었다면, 설문 문항을 어떻게 다르게 짰을까?", "imagine", "situation"),
    ("방식이 복잡하지 않았다면 어디에 시간을 더 썼을까?", "imagine", "situation"),
    ("손님이랑 얘기하는 게 좋았다면, 그 시간이 더 긴 자리에서 일해볼까?", "try_next", "field"),
    ("팀원이 아니라 팀장으로 들어갔다면 어땠을까?", "imagine", "situation"),
    ("팀원이 한 명 더 있었다면 동선을 어떻게 나눴을까?", "imagine", "external"),
    ("멘토가 없었다면 수업 준비를 어떻게 했을까?", "imagine", "external"),
    ("교수님이 정한 주제가 아니라 직접 고른 주제였다면 어땠을까?", "imagine", "external"),
    ("내가 팀장이었다면 회의를 어떻게 시작했을까?", "imagine", "situation"),
]


def test_passes_questions_that_only_look_banned():
    for text, type_, axis in PASS:
        kept, dropped = filters.apply([q(text, type=type_, axis=axis)], OTHERS)
        assert len(kept) == 1 and dropped == [], (text, dropped)


def test_drops_advice_only_when_it_recommends():
    for text in ["미리 말하는 게 좋아", "먼저 말하는 게 낫지 않을까?", "다음엔 일찍 준비하는 게 좋겠어", "혼자 하는 게 나을까?"]:
        kept, dropped = filters.apply([q(text)], OTHERS)
        assert kept == [] and dropped[0][1] == "조언·훈계", text


def test_drops_other_peoples_actions():
    # 앞의 둘은 11/14 Qwen 이 실제로 낸 질문
    for text in ["사장님이 매출이 아니라 손님 반응으로 피드백을 주셨다면 어땠을까?",
                 "혼자 결정해야 했던 상황이 아니라, 상사가 옆에서 지시를 내렸다면 어땠을까?",
                 "팀장이 먼저 방향을 정리했다면 어땠을까?",
                 "사장님이 매출을 묻지 않았다면 어땠을까?",
                 "팀원들이 회의에 빠지지 않았다면 어땠을까?"]:
        kept, dropped = filters.apply([q(text, type="imagine", axis="external")], OTHERS)
        assert kept == [] and dropped[0][1] == "다른 사람의 행동", (text, dropped)


def test_drops_impossible_assumption():
    kept, dropped = filters.apply([q("시간 부족을 알리고 기한을 연장 요청했다면 어땠을까?", type="imagine", axis="external")], OTHERS)
    assert kept == [] and dropped[0][1] == "할 수 없는 가정"


def test_drops_comparison_and_lecture():
    kept, dropped = filters.apply([q("다른 사람들은 어떻게 했을까?"), q("기록을 미리 해야 해?")], OTHERS)
    assert kept == []
    assert {d[1] for d in dropped} == {"남과 비교", "조언·훈계"}


def test_dedupes_similar_questions():
    kept, dropped = filters.apply([q("완성 대신 초안만 저장하는 습관을 만들어볼까?"),
                                   q("완성 대신 초안으로 만족했다면 어땠을까?", type="imagine", axis="situation"),
                                   q("완성 대신 초안만 저장하는 습관을 만들어 볼까?")], OTHERS)
    assert len(kept) == 2  # 1·2 는 다른 말, 3 은 1 과 같음
    assert dropped[0][1] == "겹치는 질문"


def test_caps_at_max_and_validates_done_id():
    texts = ["다음 회의 때 첫 5분 안에 한마디만 꺼내볼까?", "조원 한 명과 1:1로 짧은 커피 시간을 잡아볼까?",
             "그때 역할을 먼저 정했다면 어땠을까?", "혼자가 아니라 둘이서 맡았다면 어땠을까?",
             "온라인으로 진행했다면 어땠을까?", "기간을 한 달로 잡았다면 어땠을까?"]
    raw = [q(t, type="imagine" if i >= 2 else "try_next", axis="situation" if i >= 2 else "method",
             done="e9" if i == 0 else "e2") for i, t in enumerate(texts)]
    kept, _ = filters.apply(raw, OTHERS)
    assert len(kept) == QUESTION_MAX
    assert kept[0].done_experience_id is None   # 목록에 없는 id 는 버림
    assert kept[1].done_experience_id == "e2"
