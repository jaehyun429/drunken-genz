from app.embedding.text import card_text, pick_source


def test_card_text_is_one_paragraph_with_all_fields():
    t = card_text({"activity_name": "카페 알바", "genre": "알바", "role": "바리스타",
                   "did_chips": ["고객 응대", "판매"], "did_notes": {"고객 응대": "단골 이름 외우기"},
                   "situation_chips": ["결과가 숫자로 평가됐다"], "emotion_chips": ["재미"],
                   "one_liner": "손님이랑  얘기하는 건\n좋다.", "ai_tags": ["단골"]})
    assert t.startswith("카페 알바 / 알바 / 바리스타")
    for piece in ("한 일: 고객 응대, 판매", "고객 응대: 단골 이름 외우기", "상황: 결과가 숫자로 평가됐다",
                  "감정: 재미", "손님이랑 얘기하는 건 좋다.", "태그: 단골"):
        assert piece in t
    assert "\n" not in t


def test_card_text_skips_empty_and_place_and_period():
    t = card_text({"activity_name": "봉사", "genre": "봉사", "role": "멘토", "place": "학생회관 302호",
                   "period_start": "2026-03", "did_chips": [], "one_liner": None})
    assert t == "봉사 / 봉사 / 멘토"


def test_pick_source_prefers_snapshot():
    row = {"activity_name": "원본 이름", "role": "행사기획팀 팀장", "genre": "행사",
           "published_snapshot": {"activity_name": "가린 이름", "role": "행사 운영 담당", "genre": "행사"}}
    assert pick_source(row)["role"] == "행사 운영 담당"
    row["published_snapshot"] = None
    assert pick_source(row)["role"] == "행사기획팀 팀장"
