from app.questions.schemas import ExperienceInput
from app.questions.source import fix_source

E = ExperienceInput(
    id="f03", activity_name="멋쟁이사자처럼 13기", genre="동아리·학회", role="프론트엔드 파트", period_start="2025-03",
    did_chips=["개발", "협업 조율"], did_notes={"협업 조율": "디자이너랑 피그마 싱크 맞추는 게 제일 힘들었음"},
    conditions={"규모": "6~15명", "방식": ["대면", "비대면"]},
    situation_chips=["처음 해보는 일이었다", "방향이 자주 바뀌었다"], emotion_chips=["불안"],
    one_liner="코드는 짰는데 왜 이 기능을 만드는지는 끝까지 몰랐다.")


def test_keeps_verbatim_source():
    for s in ("방향이 자주 바뀌었다", "규모=6~15명", "방식=대면/비대면", "협업 조율: 디자이너랑 피그마 싱크 맞추는 게 제일 힘들었음",
              "코드는 짰는데 왜 이 기능을 만드는지는 끝까지 몰랐다."):
        assert fix_source(s, E) == s


def test_fixes_typo_to_the_chip_in_the_record():
    assert fix_source("방향이가 자주 바뀌었다", E) == "방향이 자주 바뀌었다"      # 11/15 Qwen
    assert fix_source("코드는 짰는데 왜 이 기능을 만드는지 끝까지 몰랐다", E) == E.one_liner


def test_strips_item_labels_and_quotes():
    assert fix_source("상황(고르 수 없었던 것): 방향이 자주 바뀌었다", E) == "방향이 자주 바뀌었다"
    assert fix_source("한 줄: 코드는 짰는데 왜 이 기능을 만드는지는 끝까지 몰랐다.", E) == E.one_liner
    assert fix_source("역할: 프론트엔드 파트", E) == "프론트엔드 파트"
    assert fix_source("'처음 해보는 일이었다'", E) == "처음 해보는 일이었다"


def test_model_made_label_forms():        # 11/15 Haiku 가 실제로 낸 꼴
    assert fix_source("역할 칩 '프론트엔드 파트'", E) == "프론트엔드 파트"
    assert fix_source("주로 한 일 칩 '개발, 협업 조율'", E) == "개발, 협업 조율"
    assert fix_source("역할 칩 '프론트엔드 파트', 한 줄 '왜 이 기능을 만드는지는 끝까지 몰랐다'", E) == \
        "프론트엔드 파트, 왜 이 기능을 만드는지는 끝까지 몰랐다"
    assert fix_source("역할: 프론트엔드 파트 / 주로 한 일: 개발", E) == "프론트엔드 파트, 개발"
    assert fix_source("프론트엔드 파트 / 주로 한 일 '개발'", E) == "프론트엔드 파트, 개발"
    assert fix_source("주로 한 일 - 협업 조율: 디자이너랑 피그마 싱크 맞추는 게 제일 힘들었음", E) == \
        "협업 조율: 디자이너랑 피그마 싱크 맞추는 게 제일 힘들었음"


def test_label_copied_from_a_log_line():      # 회차 기록 줄은 "상황 …"이 붙은 채 기록 글에 있다
    e = ExperienceInput(**{**E.model_dump(), "logs": [
        {"log_date": "2026-10-20", "situation_chips": ["방향이 자주 바뀌었다"], "one_liner": "또 바뀜"}]})
    assert fix_source("상황 방향이 자주 바뀌었다", e) == "방향이 자주 바뀌었다"


def test_chip_with_part_of_its_note():
    assert fix_source("협업 조율: 피그마 싱크 맞추는 게 제일 힘들었음", E) == "협업 조율, 피그마 싱크 맞추는 게 제일 힘들었음"


def test_two_sources_joined():
    assert fix_source("규모=6~15명, 방향이가 자주 바뀌었다", E) == "규모=6~15명, 방향이 자주 바뀌었다"
    assert fix_source("처음 해보는 일이었다 / 프론트엔드 파트", E) == "처음 해보는 일이었다, 프론트엔드 파트"


def test_leaves_source_that_is_not_in_the_record():
    assert fix_source("백엔드 서버 배포", E) == "백엔드 서버 배포"
    assert fix_source("불안", E) == "불안"       # 감정은 조각 목록에 없지만 기록에 있는 글자라 그대로
