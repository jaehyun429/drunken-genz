"""로컬 Postgres 가 있을 때만. Expo 는 가짜로 바꿔 끼운다.
   bash ../db/apply_local.sh 로 DB 를 올린 뒤:  pytest tests/test_integration_notification.py"""
import asyncio, json, uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pytest, asyncpg
from fastapi.testclient import TestClient

from test_integration_embedding import DB, _reachable

pytestmark = pytest.mark.skipif(not _reachable(), reason="로컬 DB 없음")
KST = ZoneInfo("Asia/Seoul")
NOON = datetime(2026, 11, 23, 12, 0, tzinfo=KST)       # 낮
NIGHT = datetime(2026, 11, 23, 23, 30, tzinfo=KST)     # 방해 금지 안
MORNING = datetime(2026, 11, 24, 8, 0, 30, tzinfo=KST) # 방해 금지가 끝난 직후


class FakeExpo:
    def __init__(self):
        self.sent, self.fail, self.dead = [], False, set()

    async def __call__(self, messages):
        from app.notifications.push import PushError
        if self.fail:
            raise PushError("가짜 연결 실패")
        self.sent.extend(messages)
        return [{"status": "error", "details": {"error": "DeviceNotRegistered"}} if m["to"] in self.dead
                else {"status": "ok", "id": "x"} for m in messages]


def db(fn):
    async def go():
        conn = await asyncpg.connect(DB)
        try:
            return await fn(conn)
        finally:
            await conn.close()
    return asyncio.run(go())


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DB)
    monkeypatch.setenv("AUTH_DEV_BYPASS", "1")
    from app.config import settings; settings.cache_clear()
    fake = FakeExpo()
    from app.notifications import push
    monkeypatch.setattr(push, "send", fake)
    # 다른 테스트가 남긴 알림이 섞이지 않게 전부 본 것으로 표시
    db(lambda c: c.execute("update notification set push_checked_at = now() where push_checked_at is null"))
    # 다른 테스트가 남긴 "관련 경험이 새로 붙은 질문"이 9시 알림 개수에 섞이지 않게
    db(lambda c: c.execute("update question set related_notified_at = now() where related_new and related_notified_at is null"))
    from app.main import app
    with TestClient(app) as client:
        yield client, fake


def run(client, coro_fn, *args, **kw):
    """서버의 이벤트 루프에서 서비스 함수를 돌린다 (DB 풀이 그 루프에 묶여 있다)."""
    return client.portal.call(lambda: coro_fn(*args, **kw))


def new_user(device="ExponentPushToken[a]"):
    uid = uuid.uuid4()
    db(lambda c: c.execute("insert into auth.users (id) values ($1)", uid))
    if device:
        add_device(uid, "phone", device)
    return uid


def add_device(uid, device_id, token):
    db(lambda c: c.execute("insert into push_device (user_id, device_id, token, platform) values ($1, $2, $3, 'ios')",
                           uid, device_id, token))


def add_exp(uid, name="카페 알바"):
    return db(lambda c: c.fetchval(
        "insert into experience (user_id, status, activity_name, genre, role, period_start, did_chips) "
        "values ($1, 'done', $2, '서비스·응대', '직원', '2026-01', '{고객 응대}') returning id", uid, name))


def add_noti(uid, type_, payload, at, key=None):
    """알림을 만들고 만든 시각을 at 으로 맞춘다."""
    async def go(c):
        nid = await c.fetchval("select notification_add($1, $2, $3::jsonb, $4)", uid, type_, json.dumps(payload), key)
        await c.execute("update notification set created_at = $2 where id = $1", nid, at)
        return nid
    return db(go)


def noti(nid):
    return db(lambda c: c.fetchrow("select * from notification where id = $1", nid))


def dispatch(client, now):
    from app.notifications import service
    return run(client, service.dispatch_pending, now)


def test_same_card_same_day_is_one_row_and_only_the_first_is_pushed(env):
    client, fake = env
    uid = new_user()
    eid = add_exp(uid, "과제전 시상식 준비")
    from app.notifications.service import group_key
    key = group_key("new_question", eid, NOON)
    nid = add_noti(uid, "new_question", {"experience_id": str(eid), "qa_id": "q1"}, NOON, key)
    assert dispatch(client, NOON + timedelta(minutes=1)) == {"sent": 1, "checked": 1, "held": 0}
    [m] = fake.sent
    assert m["to"] == "ExponentPushToken[a]" and m["body"] == "「과제전 시상식 준비」에 질문이 왔어요"
    assert m["data"] == {"notification_id": str(nid), "type": "new_question", "payload": {"experience_id": str(eid), "qa_id": "q1"}}
    assert noti(nid)["pushed_at"] is not None

    db(lambda c: c.execute("update notification set read = true where id = $1", nid))
    assert add_noti(uid, "new_question", {"experience_id": str(eid), "qa_id": "q2"}, NOON, key) == nid, "행을 새로 만들지 않는다"
    assert add_noti(uid, "new_question", {"experience_id": str(eid), "qa_id": "q3"}, NOON, key) == nid
    row = noti(nid)
    assert row["count"] == 3 and row["read"] is False and json.loads(row["payload"])["qa_id"] == "q3"
    assert dispatch(client, NOON + timedelta(minutes=5))["sent"] == 0 and len(fake.sent) == 1, "묶음은 첫 건만 푸시"
    other_day = add_noti(uid, "new_question", {"experience_id": str(eid)}, NOON, group_key("new_question", eid, NOON + timedelta(days=1)))
    assert other_day != nid, "다른 날은 다른 행"


def test_quiet_hours_hold_then_one_digest_in_the_morning(env):
    client, fake = env
    uid = new_user()
    a = add_noti(uid, "answer", {"qa_id": "q1"}, NIGHT)
    b = add_noti(uid, "answer", {"qa_id": "q2"}, NIGHT + timedelta(hours=3))
    c = add_noti(uid, "helpful", {}, NIGHT + timedelta(hours=4))
    assert dispatch(client, NIGHT + timedelta(hours=5)) == {"sent": 0, "checked": 0, "held": 3}
    assert fake.sent == [] and noti(a)["push_checked_at"] is None, "알림함에만 쌓인다"
    assert dispatch(client, MORNING) == {"sent": 1, "checked": 3, "held": 0}
    assert fake.sent[0]["body"] == "밤사이 알림 3개가 있어요" and fake.sent[0]["data"] == {"type": "digest", "count": 3}
    assert all(noti(x)["pushed_at"] is not None for x in (a, b, c))

    # 밤사이 알림이 하나뿐이면 묶지 않고 그대로 보낸다
    lone = new_user("ExponentPushToken[b]")
    add_noti(lone, "answer", {"qa_id": "q9"}, NIGHT)
    dispatch(client, MORNING)
    assert fake.sent[-1]["to"] == "ExponentPushToken[b]" and fake.sent[-1]["body"] == "남긴 질문에 답변이 달렸어요"


def test_user_settings_decide_whether_a_push_goes_out(env):
    client, fake = env
    off = new_user("ExponentPushToken[off]")
    db(lambda c: c.execute("insert into notification_setting (user_id, push_enabled) values ($1, false)", off))
    n_off = add_noti(off, "answer", {}, NOON)

    picky = new_user("ExponentPushToken[picky]")      # 답변 알림만 끔, 방해 금지는 낮 11~13시로 바꿈
    db(lambda c: c.execute("insert into notification_setting (user_id, types, quiet_start, quiet_end) "
                           "values ($1, '{\"answer\": false}', '11:00', '13:00')", picky))
    n_answer = add_noti(picky, "answer", {}, NOON - timedelta(hours=2))
    n_helpful = add_noti(picky, "helpful", {}, NOON - timedelta(hours=2))

    nodevice = new_user(device=None)
    n_nodev = add_noti(nodevice, "answer", {}, NOON)

    dispatch(client, NOON)                            # picky 는 지금 방해 금지
    assert [m["to"] for m in fake.sent] == [] and noti(n_helpful)["push_checked_at"] is None
    assert noti(n_off)["push_checked_at"] is not None and noti(n_off)["pushed_at"] is None, "꺼 둔 사람: 알림함에만"
    assert noti(n_nodev)["push_checked_at"] is not None and noti(n_nodev)["pushed_at"] is None, "기기 없음: 알림함에만"

    dispatch(client, NOON + timedelta(hours=1, minutes=1))
    assert [(m["to"], m["body"]) for m in fake.sent] == [("ExponentPushToken[picky]", "누군가 내 경험이 도움이 됐다고 남겼어요")]
    assert noti(n_answer)["pushed_at"] is None and noti(n_answer)["push_checked_at"] is not None, "끈 종류는 푸시만 안 보낸다"


def test_every_device_gets_it_and_dead_tokens_are_removed(env):
    client, fake = env
    uid = new_user("ExponentPushToken[old]")
    add_device(uid, "tablet", "ExponentPushToken[new]")
    fake.dead = {"ExponentPushToken[old]"}
    add_noti(uid, "answer", {}, NOON)
    assert dispatch(client, NOON)["sent"] == 2
    assert sorted(m["to"] for m in fake.sent) == ["ExponentPushToken[new]", "ExponentPushToken[old]"]
    left = db(lambda c: c.fetch("select token from push_device where user_id = $1", uid))
    assert [r["token"] for r in left] == ["ExponentPushToken[new]"], "DeviceNotRegistered 토큰은 지운다"


def test_expo_failure_keeps_it_for_the_next_round_and_stale_ones_are_dropped(env):
    client, fake = env
    uid = new_user()
    nid = add_noti(uid, "answer", {}, NOON)
    fake.fail = True
    assert dispatch(client, NOON) == {"sent": 0, "checked": 0, "held": 0}
    assert noti(nid)["push_checked_at"] is None
    fake.fail = False
    assert dispatch(client, NOON + timedelta(minutes=1))["sent"] == 1 and noti(nid)["pushed_at"] is not None

    old = add_noti(uid, "answer", {}, NOON - timedelta(hours=30))
    assert dispatch(client, NOON)["sent"] == 0, "하루 넘게 밀린 알림은 이제 와서 보내지 않는다"
    assert noti(old)["push_checked_at"] is not None and noti(old)["pushed_at"] is None


def test_purge_removes_notifications_older_than_30_days(env):
    client, _ = env
    uid = new_user()
    old = add_noti(uid, "answer", {}, datetime.now(KST) - timedelta(days=31))
    keep = add_noti(uid, "answer", {}, datetime.now(KST) - timedelta(days=29))
    from app.notifications import service
    assert run(client, service.purge_old) >= 1
    assert noti(old) is None and noti(keep) is not None


def test_qa_trigger_groups_and_skips_self_and_blocked(env):
    """notify_on_qa 는 아직 qa 에 붙이지 않았다 (소윤님과 합의 뒤). 여기서만 붙여 보고 되돌린다."""
    owner, asker, blocked = new_user(None), new_user(None), new_user(None)
    eid = add_exp(owner)
    db(lambda c: c.execute("insert into block (user_id, blocked_id) values ($1, $2)", owner, blocked))

    async def go(c):
        tx = c.transaction()
        await tx.start()
        try:
            await c.execute("create trigger qa_notify_test_trg after insert or update of answer on qa "
                            "for each row execute function notify_on_qa()")
            ask = "insert into qa (experience_id, asker_id, type, question) values ($1, $2, 'how', $3) returning id"
            q1 = await c.fetchval(ask, eid, asker, "어떻게 시작했나요? 궁금해요")
            await c.fetchval(ask, eid, asker, "두 번째 질문이에요 궁금해요")       # 002 의 함수는 여기서 유니크 충돌로 실패했다
            await c.fetchval(ask, eid, owner, "내 카드에 내가 남긴 질문이에요")
            qb = await c.fetchval(ask, eid, blocked, "차단된 사람이 남긴 질문이에요")
            await c.execute("update qa set answer = '답변이에요' where id = any($1::uuid[])", [q1, qb])
            return (await c.fetch("select type, count, payload from notification where user_id = $1", owner),
                    await c.fetch("select type, payload from notification where user_id = $1", asker),
                    await c.fetchval("select count(*) from notification where user_id = $1", blocked))
        finally:
            await tx.rollback()
    mine, theirs, none = db(go)
    assert [(r["type"], r["count"]) for r in mine] == [("new_question", 2)], "같은 카드·같은 날은 한 행, 내 질문·차단은 제외"
    assert [r["type"] for r in theirs] == ["answer"] and none == 0


# ---------- 새 관련 경험 알림 (오전 9시) ----------

NINE = datetime(2026, 11, 24, 9, 0, 30, tzinfo=KST)


def add_public_card(owner, name="공개 카드"):
    eid = add_exp(owner, name)
    db(lambda c: c.execute("update experience set visibility = 'anonymous', published_at = now(), "
                           "published_snapshot = '{}'::jsonb where id = $1", eid))
    return eid


def add_question(uid, cards, related_new=True):
    """질문 하나와 그 질문에 붙은 관련 경험 캐시."""
    async def go(c):
        eid = await c.fetchval(
            "insert into experience (user_id, status, activity_name, genre, role, period_start, did_chips) "
            "values ($1, 'done', '내 활동', '서비스·응대', '직원', '2026-01', '{고객 응대}') returning id", uid)
        qid = await c.fetchval(
            "insert into question (experience_id, user_id, type, axis, text, source, source_activity_name, notify_on_related, related_new) "
            "values ($1, $2, 'try_next', 'method', $3, '한 일 칩', '내 활동', true, $4) returning id",
            eid, uid, f"다음엔 {uuid.uuid4().hex[:6]} 해보면 어떨까요?", related_new)
        await c.execute("insert into related_cache (question_id, filter, items) values ($1, 'all', $2::jsonb)", qid,
                        json.dumps([{"experience_id": str(x), "reason": None, "score": 0.7} for x in cards]))
        return qid
    return db(go)


def related_rows(uid):
    return db(lambda c: c.fetch("select * from notification where user_id = $1 and type = 'related_found'", uid))


def test_related_found_is_one_notification_per_user_at_nine(env):
    client, fake = env
    from app.notifications import service
    me, other = new_user(), new_user(device=None)
    card_a, card_b = add_public_card(other, "카드 A"), add_public_card(other, "카드 B")
    q1 = add_question(me, [card_a, card_b])
    q2 = add_question(me, [card_a])                      # 카드 A 는 질문 두 개에 맞는다 → 경험 수는 한 번만
    add_question(me, [card_b], related_new=False)        # 새로 붙은 것이 아닌 질문

    assert run(client, service.daily_related, NINE - timedelta(hours=1)) == 0 and related_rows(me) == [], "9시 전에는 안 만든다"
    assert run(client, service.daily_related, NINE) == 1
    [row] = related_rows(me)
    assert row["count"] == 2 and set(json.loads(row["payload"])["question_ids"]) == {str(q1), str(q2)}
    assert run(client, service.daily_related, NINE + timedelta(minutes=1)) == 0, "같은 날 두 번 만들지 않는다"

    dispatch(client, NINE + timedelta(minutes=1))
    assert fake.sent[-1]["body"] == "담은 질문에 맞는 경험이 2개 생겼어요"

    # 사용자가 안 열어 봐도(related_new 가 그대로여도) 다음 날 같은 질문으로 다시 알리지 않는다
    assert run(client, service.daily_related, NINE + timedelta(days=1)) == 0
    # 9시 뒤에 새로 붙은 질문은 다음 날 아침에 간다
    q3 = add_question(me, [card_b])
    assert run(client, service.daily_related, NINE + timedelta(minutes=30)) == 0
    assert run(client, service.daily_related, NINE + timedelta(days=1)) == 1
    rows = related_rows(me)
    assert len(rows) == 2 and any(json.loads(r["payload"])["question_ids"] == [str(q3)] and r["count"] == 1 for r in rows)


def test_related_found_skips_cards_the_user_cannot_see(env):
    client, _ = env
    from app.notifications import service
    me, other, blocked = new_user(), new_user(device=None), new_user(device=None)
    gone = add_public_card(other)
    db(lambda c: c.execute("update experience set visibility = 'private' where id = $1", gone))
    hidden = add_public_card(blocked)
    db(lambda c: c.execute("insert into block (user_id, blocked_id) values ($1, $2)", me, blocked))
    mine = add_public_card(me)
    add_question(me, [gone, hidden, mine])
    assert run(client, service.daily_related, NINE) == 0 and related_rows(me) == []


# ---------- 리마인드 ----------

MON_2100 = datetime(2026, 11, 23, 21, 0, 20, tzinfo=KST)      # 2026-11-23 은 월요일


def set_reminder(uid, eid, days, at="21:00", types=None, quiet=("22:00", "08:00")):
    from datetime import time as _t
    qs, qe = (_t(*map(int, x.split(":"))) for x in quiet)
    db(lambda c: c.execute(
        "insert into notification_setting (user_id, reminders, types, quiet_start, quiet_end) values ($1, $2::jsonb, $3::jsonb, $4, $5) "
        "on conflict (user_id) do update set reminders = excluded.reminders, types = excluded.types",
        uid, json.dumps({str(eid): {"days": days, "time": at}}), json.dumps(types or {}), qs, qe))


def ongoing(uid, name="주말 카페 알바"):
    eid = add_exp(uid, name)
    db(lambda c: c.execute("update experience set status = 'in_progress', period_end = null where id = $1", eid))
    return eid


def reminder_rows(uid):
    return db(lambda c: c.fetch("select * from notification where user_id = $1 and type = 'reminder'", uid))


def test_reminder_goes_out_once_on_the_chosen_day_and_time(env):
    client, fake = env
    from app.notifications import service
    uid = new_user()
    eid = ongoing(uid)
    set_reminder(uid, eid, [1, 4])                                   # 월, 목 21:00
    assert run(client, service.reminders, MON_2100 - timedelta(minutes=5)) == 0, "시간 전"
    assert run(client, service.reminders, MON_2100) == 1
    assert run(client, service.reminders, MON_2100 + timedelta(minutes=1)) == 0, "활동·날짜당 한 번"
    [row] = reminder_rows(uid)
    assert json.loads(row["payload"]) == {"experience_id": str(eid)}
    dispatch(client, MON_2100 + timedelta(minutes=1))
    assert fake.sent[-1]["body"] == "「주말 카페 알바」 이번 주 기록을 남겨볼까요?"
    assert run(client, service.reminders, MON_2100 + timedelta(days=1)) == 0, "화요일은 고르지 않았다"
    assert run(client, service.reminders, MON_2100 + timedelta(days=3)) == 1, "목요일"

    # 방해 금지 시간에 걸린 리마인드는 알림함에만 쌓이고 아침에도 푸시하지 않는다
    night = new_user("ExponentPushToken[n]")
    en = ongoing(night)
    set_reminder(night, en, [1], at="23:00")
    assert run(client, service.reminders, MON_2100 + timedelta(hours=2)) == 1
    dispatch(client, MORNING)
    [row] = reminder_rows(night)
    assert row["pushed_at"] is None and all(m["to"] != "ExponentPushToken[n]" for m in fake.sent)


def test_reminder_is_skipped_when_it_should_not_fire(env):
    client, _ = env
    from app.notifications import service
    # 끝난 활동
    a = new_user(); ea = add_exp(a); set_reminder(a, ea, [1])
    # 리마인드 종류를 끔
    b = new_user(); eb = ongoing(b); set_reminder(b, eb, [1], types={"reminder": False})
    # 오늘 이미 회차 기록을 씀
    c_ = new_user(); ec = ongoing(c_); set_reminder(c_, ec, [1])
    db(lambda c: c.execute("insert into experience_log (id, experience_id, user_id, log_date, situation_chips, emotion_chips) "
                           "values ($1, $2, $3, '2026-11-23', '{바빴다}', '{지침}')", uuid.uuid4(), ec, c_))
    # 서버가 꺼져 있다가 두 시간 늦게 켜짐
    d = new_user(); ed = ongoing(d); set_reminder(d, ed, [1], at="19:00")
    # 설정이 깨져 있음
    e = new_user(); ee = ongoing(e)
    db(lambda c: c.execute("insert into notification_setting (user_id, reminders) values ($1, $2::jsonb)", e,
                           json.dumps({str(ee): {"days": "월", "time": "밤"}, "엉뚱한 키": 3})))
    run(client, service.reminders, MON_2100)
    assert all(reminder_rows(u) == [] for u in (a, b, c_, d, e))


def test_reminder_day_numbers_and_midnight():
    from app.notifications.service import reminder_due
    sun = datetime(2026, 11, 22, 21, 0, tzinfo=KST)                  # 일요일
    assert reminder_due({"days": [0], "time": "21:00"}, sun) and reminder_due({"days": [7], "time": "21:00"}, sun), "일요일은 0 과 7 둘 다"
    assert reminder_due({"days": [1], "time": "21:00"}, sun) is None
    late = datetime(2026, 11, 23, 0, 10, tzinfo=KST)                 # 일요일 23:50 예정, 자정을 넘겨 처음 봄
    assert reminder_due({"days": [0], "time": "23:50"}, late) == datetime(2026, 11, 22, 23, 50, tzinfo=KST)
    assert reminder_due({"days": [0]}, sun) is None and reminder_due(None, sun) is None
