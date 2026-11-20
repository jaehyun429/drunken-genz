"""로컬 Postgres 가 있을 때만. LLM 은 가짜로 바꿔 끼운다.
   bash ../db/apply_local.sh 로 DB 를 올린 뒤:  pytest tests/test_integration_analysis.py"""
import asyncio, datetime, uuid
import pytest, asyncpg
from fastapi.testclient import TestClient

from test_integration_embedding import DB, _reachable

pytestmark = pytest.mark.skipif(not _reachable(), reason="로컬 DB 없음")

P_TIRED = {"text": "지쳤다고 고른 기록이 두 개 있어요.", "evidence": ["e1", "e2"]}
P_OFFLINE = {"text": "오프라인으로 한 활동이 세 개 있어요.", "evidence": ["e1", "e2", "e3"]}
P_PEOPLE = {"text": "손님을 직접 만나는 일을 여러 번 골랐어요.", "evidence": ["e2", "e3"]}


class FakeLLM:
    def __init__(self):
        self.calls, self.users, self.answer, self.fail = 0, [], [P_TIRED, P_OFFLINE], False

    async def __call__(self, system, user, schema, **kw):
        from app.llm.client import LLMConnectionError, LLMResult
        self.calls += 1
        self.users.append(user)
        if self.fail:
            raise LLMConnectionError("가짜 연결 실패")
        return LLMResult(data={"patterns": self.answer}, model="fake", prompt_tokens=1, completion_tokens=1, attempts=1)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DB)
    monkeypatch.setenv("AUTH_DEV_BYPASS", "1")
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    from app.config import settings; settings.cache_clear()
    fake = FakeLLM()
    import app.analysis.generate as gen
    monkeypatch.setattr(gen, "complete_json", fake)
    from app.main import app
    with TestClient(app) as client:
        yield client, fake


def db(fn):
    async def go():
        conn = await asyncpg.connect(DB)
        try:
            return await fn(conn)
        finally:
            await conn.close()
    return asyncio.run(go())


def new_user():
    uid = uuid.uuid4()
    db(lambda c: c.execute("insert into auth.users (id) values ($1)", uid))
    return uid


def add_exp(uid, name, minutes_ago, one_liner="한 줄", status="done"):
    """created_at 을 벌려 넣어 번호(e1, e2 …)가 넣은 순서대로 붙게 한다."""
    return db(lambda c: c.fetchval(
        "insert into experience (user_id, status, activity_name, genre, role, period_start, did_chips, conditions, "
        " emotion_chips, one_liner, created_at) values ($1, $2, $3, '서비스·응대', '직원', '2026-01', '{고객 응대}', "
        " '{\"방식\": \"오프라인\"}', '{지침}', $4, now() - make_interval(mins => $5)) returning id",
        uid, status, name, one_liner, minutes_ago))


def add_log(uid, eid, date, one_liner):
    lid = uuid.uuid4()
    db(lambda c: c.execute(
        "insert into experience_log (id, experience_id, user_id, log_date, situation_chips, emotion_chips, one_liner) "
        "values ($1, $2, $3, $4, '{손님이 몰렸다}', '{지침}', $5)", lid, eid, uid, datetime.date.fromisoformat(date), one_liner))
    return lid


def three(uid):
    return [add_exp(uid, n, m) for n, m in (("카페 알바", 30), ("편의점 야간 알바", 20), ("축제 부스", 10))]


def auth(uid):
    return {"Authorization": f"Bearer dev:{uid}"}


def call(client, uid, force=None):
    return client.post("/v1/analysis", headers=auth(uid), json=None if force is None else {"force": force})


def test_locked_under_three_records_and_no_llm_call(env):
    client, fake = env
    uid = new_user()
    add_exp(uid, "카페 알바", 20); add_exp(uid, "편의점 야간 알바", 10)
    r = call(client, uid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["locked"] is True and body["record_count"] == 2 and body["patterns"] == []
    assert body["last_updated"] is None and body["has_new_records"] is False
    assert set(body) == {"locked", "record_count", "patterns", "criteria", "saved_questions", "last_updated", "has_new_records"}
    assert fake.calls == 0
    assert db(lambda c: c.fetchval("select count(*) from analysis_run where user_id = $1", uid)) == 0


def test_logs_count_as_records_and_can_be_evidence(env):
    client, fake = env
    uid = new_user()
    eid = add_exp(uid, "카페 알바", 30, status="in_progress")
    l1 = add_log(uid, eid, "2026-10-20", "주말 매출 얘기 또 들음")
    add_log(uid, eid, "2026-11-03", "단골이 이름을 물어봤다")
    fake.answer = [{"text": "매출 이야기를 두 번 적었어요.", "evidence": ["e1", "e1.l1"]}]
    body = call(client, uid).json()
    assert body["locked"] is False and body["record_count"] == 3 and fake.calls == 1
    assert "[e1.l1] 카페 알바 회차 기록 2026-10-20" in fake.users[0] and "[e1.l2]" in fake.users[0]
    [pat] = body["patterns"]
    assert pat["feedback"] is None and pat["edited_text"] is None
    assert pat["evidence"] == [
        {"experience_id": str(eid), "activity_name": "카페 알바", "log_id": None, "log_date": None},
        {"experience_id": str(eid), "activity_name": "카페 알바", "log_id": str(l1), "log_date": "2026-10-20"}]


def test_stored_result_until_records_change_and_force(env):
    client, fake = env
    uid = new_user()
    three(uid)
    first = call(client, uid).json()
    assert len(first["patterns"]) == 2 and first["has_new_records"] is False and fake.calls == 1

    again = call(client, uid, force=True).json()          # 기록이 그대로면 force 여도 LLM 을 안 부른다
    assert fake.calls == 1 and again["patterns"] == first["patterns"] and again["last_updated"] == first["last_updated"]

    add_exp(uid, "공모전", 1)
    stale = call(client, uid).json()                      # 새 기록이 생겨도 force 없이는 저장된 결과
    assert fake.calls == 1 and stale["has_new_records"] is True and stale["record_count"] == 4
    assert stale["patterns"] == first["patterns"]

    fake.answer = [P_PEOPLE]
    fresh = call(client, uid, force=True).json()
    assert fake.calls == 2 and fresh["has_new_records"] is False
    assert [p["text"] for p in fresh["patterns"]] == [P_PEOPLE["text"]], "피드백 없는 패턴은 새 결과로 바뀐다"
    assert fresh["last_updated"] > first["last_updated"]


def test_feedback_decides_what_survives_a_recompute(env):
    client, fake = env
    uid = new_user()
    three(uid)
    fake.answer = [P_TIRED, P_OFFLINE, P_PEOPLE]
    tired, offline, people = call(client, uid).json()["patterns"]

    def fb(pid, feedback, edited=None):
        return client.post("/v1/analysis/feedback", headers=auth(uid),
                           json={"pattern_id": pid, "feedback": feedback, "edited_text": edited})

    r = fb(tired["id"], "yes")
    assert r.status_code == 200 and r.json()["pattern"]["feedback"] == "yes" and r.json()["pattern"]["hidden"] is False
    r = fb(offline["id"], "no")
    assert r.json()["pattern"]["hidden"] is True
    assert fb(people["id"], "edit").status_code == 400, "고친 문장 없이 edit 는 안 됨"
    r = fb(people["id"], "edit", "  손님과  얘기하는 일을 골랐어요 ")
    assert r.json()["pattern"]["edited_text"] == "손님과 얘기하는 일을 골랐어요"
    assert fb(people["id"], "maybe").status_code == 400
    assert fb(str(uuid.uuid4()), "yes").status_code == 404
    other = new_user()
    r = client.post("/v1/analysis/feedback", headers=auth(other), json={"pattern_id": tired["id"], "feedback": "no"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND", "남의 패턴은 없는 것과 같다"

    shown = call(client, uid).json()["patterns"]
    assert [p["id"] for p in shown] == [tired["id"], people["id"]], "아니에요는 바로 숨는다"
    assert shown[1]["text"] == P_PEOPLE["text"] and shown[1]["edited_text"] == "손님과 얘기하는 일을 골랐어요"

    # 새 기록 → 다시 분석. 모델이 숨긴 패턴과 남겨둔 패턴을 또 내도 받지 않는다
    add_exp(uid, "공모전", 1)
    fake.answer = [{"text": "오프라인으로 한 활동이 네 개 있어요.", "evidence": ["e1", "e2", "e3", "e4"]},
                   {"text": "지쳤다고 고른 기록이 세 개 있어요.", "evidence": ["e1", "e2", "e4"]},
                   {"text": "고객 응대를 맡은 기록이 네 개 있어요.", "evidence": ["e1", "e4"]}]
    after = call(client, uid, force=True).json()["patterns"]
    assert [p["text"] for p in after] == [P_TIRED["text"], P_PEOPLE["text"], "고객 응대를 맡은 기록이 네 개 있어요."]
    assert [p["id"] for p in after][:2] == [tired["id"], people["id"]]
    prompt = fake.users[-1]
    assert "## 이미 있는 문장\n- " + P_TIRED["text"] in prompt and "## 만들지 않을 문장\n- " + P_OFFLINE["text"] in prompt
    assert "손님과 얘기하는 일을 골랐어요" not in prompt, "사용자가 고친 문장은 입력에 넣지 않는다"
    assert db(lambda c: c.fetchval("select count(*) from analysis_pattern where user_id = $1 and hidden", uid)) == 1


def test_later_is_replaced_and_no_can_be_undone(env):
    client, fake = env
    uid = new_user()
    three(uid)
    tired, offline = call(client, uid).json()["patterns"]
    post = lambda pid, f: client.post("/v1/analysis/feedback", headers=auth(uid), json={"pattern_id": pid, "feedback": f})
    assert post(tired["id"], "later").json()["pattern"]["feedback"] == "later"
    post(offline["id"], "no")
    assert post(offline["id"], "yes").json()["pattern"]["hidden"] is False
    add_exp(uid, "공모전", 1)
    fake.answer = [P_PEOPLE]
    after = call(client, uid, force=True).json()["patterns"]
    assert [p["text"] for p in after] == [P_OFFLINE["text"], P_PEOPLE["text"]], "later 는 지우고 yes 는 남긴다"


def test_llm_failure_is_ai_unavailable_and_stores_nothing(env):
    client, fake = env
    uid = new_user()
    three(uid)
    fake.fail = True
    r = call(client, uid)
    assert r.status_code == 503 and r.json()["error"]["code"] == "AI_UNAVAILABLE"
    assert r.json()["error"]["details"] == {"reason": "connection"}
    assert db(lambda c: c.fetchval("select count(*) from analysis_run where user_id = $1", uid)) == 0
    fake.fail = False
    assert len(call(client, uid).json()["patterns"]) == 2


def test_zero_patterns_is_stored_as_a_result(env):
    client, fake = env
    uid = new_user()
    three(uid)
    fake.answer = []
    body = call(client, uid).json()
    assert body["locked"] is False and body["patterns"] == [] and body["last_updated"] is not None
    call(client, uid)
    assert fake.calls == 1


def test_pattern_disappears_when_its_evidence_is_deleted(env):
    client, fake = env
    uid = new_user()
    e1, e2, e3 = three(uid)
    add_exp(uid, "공모전", 1)
    first = call(client, uid).json()["patterns"]
    assert len(first) == 2
    db(lambda c: c.execute("delete from experience where id = $1", e1))
    db(lambda c: c.execute("update experience set activity_name = '편의점 알바' where id = $1", e2))
    body = call(client, uid).json()
    assert [p["text"] for p in body["patterns"]] == [P_OFFLINE["text"]], "근거가 1개 남은 패턴은 안 보인다"
    assert [ev["experience_id"] for ev in body["patterns"][0]["evidence"]] == [str(e2), str(e3)]
    assert body["patterns"][0]["evidence"][0]["activity_name"] == "편의점 알바", "근거의 활동명은 지금 이름"
    assert body["has_new_records"] is True and fake.calls == 1


def test_only_my_records_go_to_the_model_and_saved_questions_come_back(env):
    client, fake = env
    me, other = new_user(), new_user()
    e1, _, _ = three(me)
    add_exp(other, "남의 기록", 5, one_liner="남의비밀한줄")
    qid = db(lambda c: c.fetchval(
        "insert into question (experience_id, user_id, source_activity_name, type, axis, text, source, saved) "
        "values ($1, $2, '카페 알바', 'try_next', 'role', '매니저 역할을 해보는 것은 어떨까요?', '직원', true) returning id", e1, me))
    db(lambda c: c.execute(
        "insert into question (experience_id, user_id, source_activity_name, type, axis, text, source) "
        "values ($1, $2, '카페 알바', 'imagine', 'situation', '안 담은 질문이요?', '한 줄')", e1, me))
    body = call(client, me).json()
    assert "남의비밀한줄" not in fake.users[0] and "남의 기록" not in fake.users[0]
    assert "매니저 역할" not in fake.users[0], "담은 질문은 v1 입력에 넣지 않는다"
    assert [q["id"] for q in body["saved_questions"]] == [str(qid)]
    assert body["saved_questions"][0]["text"] == "매니저 역할을 해보는 것은 어떨까요?"
    assert body["criteria"] == {"private": None, "public": None}


def test_requires_auth(env):
    client, _ = env
    assert client.post("/v1/analysis").status_code == 401
