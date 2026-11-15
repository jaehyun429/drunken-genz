"""로컬 Postgres 가 있을 때만 도는 통합 테스트. LLM 은 가짜로 바꿔 끼운다.
   bash ../db/apply_local.sh 로 DB 를 올린 뒤:  DATABASE_URL=... pytest tests/test_integration_questions.py"""
import os, uuid, asyncio
import pytest, asyncpg
from fastapi.testclient import TestClient

DB = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:54329/genz")


def _reachable() -> bool:
    try:
        async def ping():
            c = await asyncpg.connect(DB); await c.close()
        asyncio.run(asyncio.wait_for(ping(), 2)); return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="로컬 DB 없음")

FAKE_QUESTIONS = {"questions": [
    {"type": "try_next", "axis": "method", "text": "다음 회의 때 첫 5분 안에 한마디만 꺼내볼까?", "source": "협업 조율 보강글", "done_experience_id": None},
    {"type": "imagine", "axis": "situation", "text": "그때 역할을 먼저 정했다면 어땠을까?", "source": "한 줄", "done_experience_id": None},
    {"type": "imagine", "axis": "situation", "text": "단골 이름 외우기 시작하지 않았다면 어땠을까?", "source": "한 줄", "done_experience_id": None},
]}


class FakeLLM:
    def __init__(self): self.calls = 0; self.fail = None
    async def __call__(self, system, user, schema, **kw):
        from app.llm.client import LLMResult, LLMConnectionError
        self.calls += 1
        if self.fail == "connection":
            raise LLMConnectionError("가짜 연결 실패")
        return LLMResult(data=FAKE_QUESTIONS, model="fake", prompt_tokens=1, completion_tokens=1, attempts=1)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DB)
    monkeypatch.setenv("AUTH_DEV_BYPASS", "1")
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    from app.config import settings; settings.cache_clear()
    fake = FakeLLM()
    import app.questions.generate as gen
    monkeypatch.setattr(gen, "complete_json", fake)
    from app.main import app
    with TestClient(app) as client:   # lifespan → db.connect
        yield client, fake


async def _seed():
    conn = await asyncpg.connect(DB)
    uid = uuid.uuid4()
    await conn.execute("insert into auth.users (id) values ($1)", uid)
    eid = await conn.fetchval(
        "insert into experience (user_id, activity_name, genre, role, period_start, did_chips, did_notes, one_liner) "
        "values ($1, '교환학생', '여행', '교환학생', '2026-09', '{조사·리서치,협업 조율}', "
        "'{\"협업 조율\": \"말을 못 꺼내서 역할이 정해져버림\"}', '조용한 사람이 됐다.') returning id", uid)
    await conn.close()
    return uid, eid


async def _wait_batch(eid, timeout=5.0):
    conn = await asyncpg.connect(DB)
    try:
        for _ in range(int(timeout * 10)):
            row = await conn.fetchrow("select * from question_batch where experience_id = $1 order by generation desc limit 1", eid)
            if row and row["status"] != "pending":
                return row
            await asyncio.sleep(0.1)
        return row
    finally:
        await conn.close()


async def _questions(eid):
    conn = await asyncpg.connect(DB)
    try:
        return await conn.fetch("select * from question where experience_id = $1 order by created_at", eid)
    finally:
        await conn.close()


def test_webhook_generates_and_filters(env):
    client, fake = env
    uid, eid = asyncio.run(_seed())
    r = client.post("/v1/internal/experience-saved", headers={"X-Webhook-Secret": "s3cret"},
                    json={"type": "INSERT", "table": "experience", "record": {"id": str(eid), "user_id": str(uid)}})
    assert r.status_code == 202, r.text
    batch = asyncio.run(_wait_batch(eid))
    assert batch["status"] == "done" and batch["trigger"] == "save" and batch["input_hash"]
    qs = asyncio.run(_questions(eid))
    assert [q["text"][:5] for q in qs] == ["다음 회의", "그때 역할"]   # 뒤집기 질문은 걸러짐
    assert qs[0]["source_activity_name"] == "교환학생"
    # 웹훅 재전송은 무시
    r2 = client.post("/v1/internal/experience-saved", headers={"X-Webhook-Secret": "s3cret"},
                     json={"type": "INSERT", "table": "experience", "record": {"id": str(eid), "user_id": str(uid)}})
    assert r2.json().get("ignored") is True
    assert fake.calls == 1


def test_regenerate_keeps_saved_and_limits_daily(env):
    client, fake = env
    uid, eid = asyncio.run(_seed())
    client.post("/v1/internal/experience-saved", headers={"X-Webhook-Secret": "s3cret"},
                json={"type": "INSERT", "table": "experience", "record": {"id": str(eid), "user_id": str(uid)}})
    asyncio.run(_wait_batch(eid))

    async def save_first():
        conn = await asyncpg.connect(DB)
        await conn.execute("update question set saved = true where experience_id = $1 and type = 'try_next'", eid)
        await conn.close()
    asyncio.run(save_first())

    auth = {"Authorization": f"dev:{uid}".replace("dev:", "Bearer dev:")}
    r = client.post("/v1/questions/regenerate", headers=auth, json={"experience_id": str(eid)})
    assert r.status_code == 202, r.text
    batch = asyncio.run(_wait_batch(eid))
    assert batch["generation"] == 2 and batch["status"] == "done"
    assert fake.calls == 1, "같은 입력 해시면 LLM 을 다시 부르지 않음"
    qs = asyncio.run(_questions(eid))
    saved = [q for q in qs if q["saved"]]
    assert len(saved) == 1 and saved[0]["generation"] == 1     # 담은 건 남음
    assert any(q["generation"] == 2 for q in qs)

    r = client.post("/v1/questions/regenerate", headers=auth, json={"experience_id": str(eid)})
    assert r.status_code == 429 and r.json()["error"]["code"] == "QUESTION_REGEN_LIMIT"

    # 남의 경험은 404
    other = uuid.uuid4()
    r = client.post("/v1/questions/regenerate", headers={"Authorization": f"Bearer dev:{other}"}, json={"experience_id": str(eid)})
    assert r.status_code == 404


def test_regenerate_does_not_reuse_older_prompt_version(env):
    client, fake = env
    uid, eid = asyncio.run(_seed())
    client.post("/v1/internal/experience-saved", headers={"X-Webhook-Secret": "s3cret"},
                json={"type": "INSERT", "table": "experience", "record": {"id": str(eid), "user_id": str(uid)}})
    first = asyncio.run(_wait_batch(eid))
    from app.questions.prompt import prompt_version
    assert first["prompt_version"] == prompt_version()

    async def age_prompt():
        conn = await asyncpg.connect(DB)
        await conn.execute("update question_batch set prompt_version = 'v0' where experience_id = $1", eid)
        await conn.close()
    asyncio.run(age_prompt())

    r = client.post("/v1/questions/regenerate", headers={"Authorization": f"Bearer dev:{uid}"}, json={"experience_id": str(eid)})
    assert r.status_code == 202, r.text
    batch = asyncio.run(_wait_batch(eid))
    assert batch["status"] == "done" and batch["prompt_version"] == prompt_version()
    assert fake.calls == 2, "입력 해시가 같아도 프롬프트 버전이 다르면 다시 만든다"


def test_connection_failure_then_retry(env):
    client, fake = env
    uid, eid = asyncio.run(_seed())
    fake.fail = "connection"
    client.post("/v1/internal/experience-saved", headers={"X-Webhook-Secret": "s3cret"},
                json={"type": "INSERT", "table": "experience", "record": {"id": str(eid), "user_id": str(uid)}})
    batch = asyncio.run(_wait_batch(eid))
    assert batch["status"] == "failed" and batch["failure_reason"] == "connection"

    fake.fail = None
    r = client.post("/v1/questions/retry", headers={"Authorization": f"Bearer dev:{uid}"}, json={"experience_id": str(eid)})
    assert r.status_code == 202, r.text
    batch = asyncio.run(_wait_batch(eid))
    assert batch["status"] == "done" and batch["generation"] == 1
    # 재시도는 하루 횟수에 안 셈 → 재생성 1회 아직 가능
    r = client.post("/v1/questions/regenerate", headers={"Authorization": f"Bearer dev:{uid}"}, json={"experience_id": str(eid)})
    assert r.status_code == 202


def test_app_cannot_change_question_text(env):
    """컬럼 권한: authenticated 역할로 text 를 바꾸면 거부돼야 함 (CLAUDE.md 3장)."""
    uid, eid = asyncio.run(_seed())

    async def run():
        conn = await asyncpg.connect(DB)
        await conn.execute("insert into question (experience_id, user_id, source_activity_name, type, text, source) "
                           "values ($1, $2, 'x', 'try_next', '원문', '출처')", eid, uid)
        async with conn.transaction():
            await conn.execute("set local role authenticated")
            await conn.execute("select set_config('request.jwt.claim.sub', $1, true)", str(uid))
            ok = await conn.execute("update question set saved = true where user_id = $1", uid)
            try:
                await conn.execute("update question set text = '바꿈' where user_id = $1", uid)
                denied = False
            except asyncpg.InsufficientPrivilegeError:
                denied = True
        await conn.close()
        return ok, denied
    ok, denied = asyncio.run(run())
    assert ok.startswith("UPDATE") and denied


def test_sweep_picks_up_missed_webhook(env, monkeypatch):
    """웹훅이 안 왔어도 sweep 이 질문을 만든다. 저장 직후(1분 안)인 건 건너뛴다."""
    client, fake = env
    uid, eid = asyncio.run(_seed())
    client.post("/v1/internal/sweep", headers={"X-Webhook-Secret": "s3cret"})
    assert asyncio.run(_wait_batch(eid, timeout=0.3)) is None   # 방금 저장한 건 아직 안 건드림 (다른 테스트가 남긴 경험은 처리될 수 있음)

    async def age():
        conn = await asyncpg.connect(DB)
        # sweep 은 오래된 순으로 SWEEP_BATCH_LIMIT 개만 집는다. 로컬 DB 에 다른 테스트가 남긴 batch 없는 경험이
        # 쌓여 있어도 이 경험이 먼저 잡히게 제일 오래된 것으로 만든다
        await conn.execute("update experience set created_at = now() - interval '10 years' where id = $1", eid)
        await conn.close()
    asyncio.run(age())
    r = client.post("/v1/internal/sweep", headers={"X-Webhook-Secret": "s3cret"})
    assert r.json()["processed"] >= 1
    batch = asyncio.run(_wait_batch(eid))
    assert batch["status"] == "done" and batch["trigger"] == "save"
    # 두 번째 sweep 은 같은 경험을 다시 안 잡음
    client.post("/v1/internal/sweep", headers={"X-Webhook-Secret": "s3cret"})
    assert asyncio.run(_wait_batch(eid))["generation"] == 1
