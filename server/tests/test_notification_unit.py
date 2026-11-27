"""알림의 순수 부분: 분류별 켜고 끄기, 정해진 시각 푸시, 묶음 키, 푸시 문구, Expo 응답 읽기."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.notifications import push, text
from app.notifications.service import group_key, push_due, type_on


def test_six_categories_decide_on_off():
    assert type_on(None, "answer") and type_on({}, "helpful"), "없는 키는 켬"
    assert not type_on({"qa": False}, "new_question") and not type_on({"qa": False}, "answer"), "질문/답변을 끄면 둘 다"
    assert type_on({"qa": False}, "related_found") and not type_on({"recommend": False}, "related_found")
    assert not type_on({"reach": False}, "helpful") and not type_on({"reach": False}, "saved")
    assert not type_on({"answer": False}, "answer") and type_on({"answer": False}, "new_question"), "예전처럼 종류 이름으로 끈 값도 받는다"
    assert not type_on({"reminder": False}, "reminder") and type_on({"qa": True}, "answer")
    assert type_on("깨진 값", "answer") and type_on({"qa": False}, "처음 보는 종류")


def test_reach_notifications_wait_for_six_pm_korean_time():
    kst = ZoneInfo("Asia/Seoul")
    noon = datetime(2026, 11, 23, 12, 0, tzinfo=kst)
    assert push_due("answer", noon) == noon, "대부분은 생긴 즉시"
    assert push_due("helpful", noon) == datetime(2026, 11, 23, 18, 0, tzinfo=kst)
    assert push_due("saved", datetime(2026, 11, 23, 18, 0, tzinfo=kst)) == datetime(2026, 11, 23, 18, 0, tzinfo=kst)
    assert push_due("helpful", datetime(2026, 11, 23, 18, 1, tzinfo=kst)) == datetime(2026, 11, 24, 18, 0, tzinfo=kst), "6시를 넘기면 다음 날"
    assert push_due("helpful", datetime(2026, 11, 23, 3, 0, tzinfo=timezone.utc)) == datetime(2026, 11, 23, 18, 0, tzinfo=kst)


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


def test_dead_tokens_are_only_device_not_registered():
    messages = [{"to": "a"}, {"to": "b"}, {"to": "c"}, {"to": "d"}]
    tickets = [{"status": "ok", "id": "1"}, {"status": "error", "details": {"error": "DeviceNotRegistered"}},
               {"status": "error", "details": {"error": "MessageRateExceeded"}}, {"status": "error"}]
    assert push.dead_tokens(messages, tickets) == ["b"]
