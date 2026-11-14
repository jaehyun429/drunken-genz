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
    kept, dropped = filters.apply([q("단골 이름 외우기 시작하지 않았다면 어땠을까?", type="imagine", axis="situation")], OTHERS)
    assert kept == [] and dropped[0][1] == "잘한 일을 뒤집기만 한 질문"


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
