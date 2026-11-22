"""알림의 순수 부분: 방해 금지 시간, 묶음 키, 푸시 문구, Expo 응답 읽기."""
from datetime import datetime, time, timezone

from app.notifications import push, text
from app.notifications.service import group_key, in_quiet


def test_quiet_hours_cross_midnight():
    night = (time(22, 0), time(8, 0))
    assert in_quiet(time(22, 0), *night) and in_quiet(time(23, 59), *night) and in_quiet(time(7, 59), *night)
    assert not in_quiet(time(8, 0), *night) and not in_quiet(time(21, 59), *night) and not in_quiet(time(12, 0), *night)
    assert in_quiet(time(13, 30), time(13, 0), time(14, 0)) and not in_quiet(time(14, 0), time(13, 0), time(14, 0))
    assert not in_quiet(time(3, 0), time(0, 0), time(0, 0)), "시작과 끝이 같으면 방해 금지 없음"


def test_group_key_uses_korean_date():
    # UTC 15:30 은 한국 시간으로 다음 날 00:30
    assert group_key("new_question", "exp1", datetime(2026, 11, 22, 15, 30, tzinfo=timezone.utc)) == "new_question:exp1:2026-11-23"
    assert group_key("new_question", "exp1", datetime(2026, 11, 22, 14, 59, tzinfo=timezone.utc)) == "new_question:exp1:2026-11-22"


def test_push_text_follows_the_planning_table_and_never_names_people():
    assert text.body("new_question", 3, "과제전 시상식 준비") == "「과제전 시상식 준비」에 질문이 왔어요"
    assert text.body("new_question") == "내 경험에 질문이 왔어요"
    assert text.body("answer") == "남긴 질문에 답변이 달렸어요"
    assert text.body("related_found", 2) == "담은 질문에 맞는 경험이 2개 생겼어요"
    assert text.body("helpful") == "누군가 내 경험이 도움이 됐다고 남겼어요"
    assert text.body("saved", 3) == "오늘 3명이 내 경험을 담았어요"
    assert text.body("reminder", 1, "미생물 캡스톤") == "「미생물 캡스톤」 이번 주 기록을 남겨볼까요?"
    assert text.body("coffeechat_request") == text.FALLBACK and text.body("처음 보는 종류") == text.FALLBACK
    assert text.digest(3) == "밤사이 알림 3개가 있어요"


def test_dead_tokens_are_only_device_not_registered():
    messages = [{"to": "a"}, {"to": "b"}, {"to": "c"}, {"to": "d"}]
    tickets = [{"status": "ok", "id": "1"}, {"status": "error", "details": {"error": "DeviceNotRegistered"}},
               {"status": "error", "details": {"error": "MessageRateExceeded"}}, {"status": "error"}]
    assert push.dead_tokens(messages, tickets) == ["b"]
