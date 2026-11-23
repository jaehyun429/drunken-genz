"""기능 확인용 페이지의 서버 쪽. 로컬 Postgres 가 있을 때만. LLM 은 가짜."""
import asyncio, uuid
from urllib.parse import quote
import pytest, asyncpg
from fastapi.testclient import TestClient

from test_integration_embedding import DB, _reachable

pytestmark = pytest.mark.skipif(not _reachable(), reason="로컬 DB 없음")
EXP = {"status": "in_progress", "activity_name": "카페 알바", "genre": "서비스·응대", "type": "아르바이트", "role": "홀 직원",
       "period_start": "2026-09", "did_chips": ["고객 응대", " 마감 정리 "], "did_notes": {"고객 응대": "단골 이름을 외웠다", "없는 칩": "버려짐"},
       "conditions": {"규모": "2~5명", "방식": ""}, "situation_chips": ["손님이 몰렸다"], "emotion_chips": ["지침"], "one_liner": "주말이 길었다"}


def db(fn):
    async def go():
        conn = await asyncpg.connect(DB)
        try:
            return await fn(conn)
        finally:
            await conn.close()
    return asyncio.run(go())


def make(monkeypatch, password, accounts=""):
    monkeypatch.setenv("DATABASE_URL", DB)
    monkeypatch.setenv("DEMO_PASSWORD", password)
    monkeypatch.setenv("DEMO_ACCOUNTS", accounts)
    monkeypatch.delenv("AUTH_DEV_BYPASS", raising=False)
    from app.config import settings; settings.cache_clear()
    import app.questions.service as qs
    started = []

    async def fake_run(batch_id):          # 질문 생성은 여기서 보지 않는다. 시작됐는지만
        started.append(batch_id)
    monkeypatch.setattr(qs, "run_batch", fake_run)
    from app.main import app
    return TestClient(app), started


def test_everything_is_404_without_a_demo_password(monkeypatch):
    client, _ = make(monkeypatch, "")
    with client:
        assert client.get("/demo").status_code == 404
        assert client.post("/v1/demo/login", json={"password": ""}).status_code == 404
        assert client.get("/v1/demo/state", headers={"Authorization": "Bearer demo::" + quote("지은")}).status_code == 404
        # 비밀번호가 없는 서버에서는 demo 토큰이 다른 주소에서도 통하지 않는다
        assert client.post("/v1/analysis", headers={"Authorization": "Bearer demo::" + quote("지은")}).status_code == 401


def test_demo_account_can_save_a_record_and_use_the_real_endpoints(monkeypatch):
    uid, other = uuid.uuid4(), uuid.uuid4()
    db(lambda c: c.executemany("insert into auth.users (id) values ($1)", [(uid,), (other,)]))
    client, started = make(monkeypatch, "pw:with:colons", f"지은:{uid}, 명금:{other}, 깨진항목:xyz")
    tok = lambda pw, name: "Bearer demo:" + quote(pw, safe="") + ":" + quote(name, safe="")
    me = {"Authorization": tok("pw:with:colons", "지은")}
    with client:
        assert "기능 확인용" in client.get("/demo").text
        assert client.post("/v1/demo/login", json={"password": "틀림"}).status_code == 401
        assert client.post("/v1/demo/login", json={"password": "pw:with:colons"}).json() == {"accounts": ["지은", "명금"]}
        for bad in (tok("틀림", "지은"), tok("pw:with:colons", "없는계정"), "Bearer demo:pw", "Bearer demo:" + quote("지은")):
            assert client.get("/v1/demo/state", headers={"Authorization": bad}).status_code == 401

        r = client.post("/v1/demo/experiences", headers=me, json=EXP)
        assert r.status_code == 201, r.text
        eid = r.json()["id"]
        assert len(started) == 1, "저장하면 질문 생성을 시작한다"
        r = client.post("/v1/demo/experiences", headers=me, json={**EXP, "did_chips": ["하나", "둘", "셋", "넷"]})
        assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_FAILED", "개수 제한은 DB 가 본다"

        assert client.post(f"/v1/demo/experiences/{eid}/logs", headers=me,
                           json={"situation_chips": ["손님이 몰렸다"], "emotion_chip": "지침", "one_liner": "또 길었다"}).status_code == 201
        assert client.post(f"/v1/demo/experiences/{eid}/fake-question-notification", headers=me).status_code == 200
        assert client.post(f"/v1/demo/experiences/{eid}/fake-question-notification", headers=me).status_code == 200

        st = client.get("/v1/demo/state", headers=me).json()
        [e] = st["experiences"]
        assert e["did_chips"] == ["고객 응대", "마감 정리"] and e["did_notes"] == {"고객 응대": "단골 이름을 외웠다"}
        assert e["conditions"] == {"규모": "2~5명"} and e["batch"]["status"] == "pending" and len(e["logs"]) == 1
        assert [(n["type"], n["count"]) for n in st["notifications"]] == [("new_question", 2)], "같은 날은 한 행으로 묶인다"

        # 실제 엔드포인트도 같은 토큰으로 부른다. 기록 2개(경험 1 + 회차 1)라 분석은 잠김
        body = client.post("/v1/analysis", headers=me, json={"force": False}).json()
        assert body["locked"] is True and body["record_count"] == 2

        them = {"Authorization": tok("pw:with:colons", "명금")}
        assert client.get("/v1/demo/state", headers=them).json()["experiences"] == [], "계정마다 따로"
        assert client.delete(f"/v1/demo/experiences/{eid}", headers=them).status_code == 404
        assert client.delete(f"/v1/demo/experiences/{eid}", headers=me).status_code == 200
