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
