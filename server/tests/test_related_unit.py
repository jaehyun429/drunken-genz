"""관련 경험: DB·LLM 없이 도는 부분."""
from app.constants import RELATED_MAX, RELATED_REASON_MAX
from app.related import rerank as rr
from app.related.service import passes_filter, query_text, valid_filter


def test_parse_drops_out_of_range_duplicates_and_caps():
    data = {"items": [{"n": 2, "reason": "같은 일을 매니저 역할로 해본 기록"}, {"n": 2, "reason": "중복"},
                      {"n": 0, "reason": None}, {"n": 99, "reason": None}, {"n": "3", "reason": None},
                      {"n": True, "reason": None}, {"n": 1, "reason": None}]}
    out = rr.parse(data, 3)
    assert [(x.index, x.reason) for x in out] == [(1, "같은 일을 매니저 역할로 해본 기록"), (0, None)]
    many = {"items": [{"n": i, "reason": None} for i in range(1, 30)]}
    assert len(rr.parse(many, 29)) == RELATED_MAX


def test_reason_is_dropped_but_card_kept_when_reason_breaks_rules():
    assert rr.clean_reason("  야간에   혼자 일해본 기록 ") == "야간에 혼자 일해본 기록"
    assert rr.clean_reason("가" * (RELATED_REASON_MAX + 1)) is None
    assert rr.clean_reason("연락은 010-1234-5678") is None
    assert rr.clean_reason("당신보다 더 나은 경험") is None
    assert rr.clean_reason("") is None and rr.clean_reason(None) is None and rr.clean_reason(3) is None
    out = rr.parse({"items": [{"n": 1, "reason": "x@y.com 으로 연락"}]}, 1)
    assert len(out) == 1 and out[0].reason is None


def test_prompt_has_version_and_no_unfilled_placeholders():
    version, system, user = rr.build_messages("야간에 해보면?", "카페 알바 / 알바 / 바리스타", ["편의점 / 알바 / 야간", "학원 / 알바 / 조교"])
    assert version == "v1"
    assert "{max}" not in system and "{reason_max}" not in system and str(RELATED_REASON_MAX) in system
    assert "[1] 편의점" in user and "[2] 학원" in user and "야간에 해보면?" in user


def test_query_text_puts_source_context_before_question():
    src = {"activity_name": "카페 알바", "genre": "알바", "role": "바리스타", "did_chips": ["고객 응대"], "custom_chips": []}
    assert query_text(src, "야간에  해보면?") == "카페 알바 / 알바 / 바리스타. 한 일: 고객 응대. 야간에 해보면?"
    assert query_text({"activity_name": "삭제된 활동"}, "q?") == "삭제된 활동. q?"   # 출처 경험이 지워진 담은 질문


def test_filters():
    assert all(valid_filter(f) for f in ("all", "same_role", "different_role", "genre:동아리"))
    assert not any(valid_filter(f) for f in ("", "genre:", "role", "genre: ", "all "))
    src = {"role": "바리스타"}
    assert passes_filter("same_role", {"role": " 바리 스타"}, src) and not passes_filter("same_role", {"role": "매니저"}, src)
    assert passes_filter("different_role", {"role": "매니저"}, src) and not passes_filter("different_role", {"role": "바리스타"}, src)
    assert passes_filter("genre:동아리", {"genre": "동아리"}, src) and not passes_filter("genre:동아리", {"genre": "알바"}, src)
    # 출처 경험이 지워져 역할을 모르면: 같은 역할은 없음, 다른 역할은 전부
    assert not passes_filter("same_role", {"role": "바리스타"}, {}) and passes_filter("different_role", {"role": "바리스타"}, {})
