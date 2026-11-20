"""로컬 Postgres 가 있을 때만. 임베딩 모델은 가짜(단어 해시 → 1024차원)로 바꿔 끼운다.
   DATABASE_URL=... pytest tests/test_integration_embedding.py"""
import os, uuid, asyncio, hashlib, math
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


def fake_vector(text: str) -> list[float]:
    """단어 집합 → 1024차원 정규화 벡터. 같은 단어가 많이 겹칠수록 코사인이 높다.
       단어 하나를 여덟 칸에 ±1 로 흩는다. 한 칸에만 두면 서로 다른 단어가 1/1024 로 같은 칸에 떨어져서,
       로컬 DB 에 예전 실행의 카드가 쌓일수록 다른 tok 의 카드가 임계값을 넘어 끼어든다
       (11/15: 쌓인 카드 950장에서 관련 경험 통합 테스트가 25번 중 8번 실패)."""
    v = [0.0] * 1024
    for w in text.replace("/", " ").replace(",", " ").replace(".", " ").split():
        d = hashlib.sha256(w.encode()).digest()
        for i in range(8):
            v[int.from_bytes(d[2 * i:2 * i + 2], "big") % 1024] += 1.0 if d[16] >> i & 1 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


async def fake_embed(texts):
    return [fake_vector(t) for t in texts]


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DB)
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    from app.config import settings; settings.cache_clear()
    import app.embedding.client as ec
    monkeypatch.setattr(ec, "embed", fake_embed)
    import app.embedding.service as es
    monkeypatch.setattr(es, "embed_one", lambda t: _one(t))
    # /internal/sweep 은 질문 sweep 도 돌린다. 여기선 LLM 이 없으니(3회 재시도 × 수십 건 = 분 단위) 끈다
    import app.questions.sweep as qs
    async def _no_question_sweep(): return 0
    monkeypatch.setattr(qs, "sweep_missing_question_batches", _no_question_sweep)
    from app.main import app
    with TestClient(app) as client:
        yield client


async def _one(t):
    return (await fake_embed([t]))[0]


async def _seed_card(uid, activity, role, chips, one_liner, visibility="anonymous"):
    conn = await asyncpg.connect(DB)
    await conn.execute("insert into auth.users (id) values ($1) on conflict do nothing", uid)
    eid = await conn.fetchval(
        "insert into experience (user_id, activity_name, genre, role, period_start, did_chips, one_liner, visibility, "
        " published_at, published_snapshot) values ($1, $2, '서비스·응대', $3, '2026-01', $4, $5, $6, now() - interval '2 minutes', "
        " jsonb_build_object('activity_name', $2::text, 'genre', '서비스·응대', 'role', $3::text, 'did_chips', $4::text[], 'one_liner', $5::text)) "
        "returning id", uid, activity, role, chips, one_liner, visibility)
    await conn.close()
    return eid


async def _embedding_set(eid, timeout=5.0):
    conn = await asyncpg.connect(DB)
    try:
        for _ in range(int(timeout * 10)):
            v = await conn.fetchval("select embedding is not null from experience where id = $1", eid)
            if v:
                return True
            await asyncio.sleep(0.1)
        return False
    finally:
        await conn.close()


async def _match(query_text, me, k=5000):   # 로컬 DB 에 평가용 카드(embed_eval.py) 수백 장이 있어도 묻히지 않게 크게
    from app.embedding.client import to_pg
    conn = await asyncpg.connect(DB)
    try:
        return await conn.fetch("select * from match_public_cards($1::vector, $2, $3, 0.0)", to_pg(fake_vector(query_text)), me, k)
    finally:
        await conn.close()


def _hook(client, eid, visibility):
    return client.post("/v1/internal/experience-published", headers={"X-Webhook-Secret": "s3cret"},
                       json={"type": "UPDATE", "table": "experience",
                             "record": {"id": str(eid), "visibility": visibility}, "old_record": {"visibility": "private"}})


def test_publish_webhook_embeds_and_match_excludes_me_and_blocked(env):
    client = env
    me, other, blocked = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    mine = asyncio.run(_seed_card(me, "카페 알바", "바리스타", ["고객 응대"], "손님이랑 얘기하는 게 좋다"))
    theirs = asyncio.run(_seed_card(other, "카페 알바", "바리스타", ["고객 응대"], "손님이랑 얘기하는 게 좋다"))
    blk = asyncio.run(_seed_card(blocked, "카페 알바", "바리스타", ["고객 응대"], "손님이랑 얘기하는 게 좋다"))
    for eid in (mine, theirs, blk):
        r = _hook(client, eid, "anonymous")
        assert r.status_code == 202 and r.json()["action"] == "embed", r.text
        assert asyncio.run(_embedding_set(eid))

    async def block():
        conn = await asyncpg.connect(DB)
        await conn.execute("insert into block (user_id, blocked_id) values ($1, $2)", me, blocked)
        await conn.close()
    asyncio.run(block())

    rows = asyncio.run(_match("카페 알바 바리스타 고객 응대 손님이랑 얘기하는 게 좋다", me))
    ids = [r["experience_id"] for r in rows]
    assert theirs in ids and mine not in ids and blk not in ids
    assert rows[0]["similarity"] > 0.7


def test_unpublish_clears_embedding_and_related_cache(env):
    client = env
    owner, asker = uuid.uuid4(), uuid.uuid4()
    eid = asyncio.run(_seed_card(owner, "편의점 야간 알바", "야간 근무", ["판매"], "새벽 취객 상대"))
    _hook(client, eid, "anonymous")
    assert asyncio.run(_embedding_set(eid))

    async def seed_cache():
        conn = await asyncpg.connect(DB)
        await conn.execute("insert into auth.users (id) values ($1) on conflict do nothing", asker)
        qeid = await conn.fetchval(
            "insert into experience (user_id, activity_name, genre, role, period_start, did_chips) "
            "values ($1, '카페 알바', '서비스·응대', '바리스타', '2026-01', '{판매}') returning id", asker)
        qid = await conn.fetchval(
            "insert into question (experience_id, user_id, type, axis, text, source, source_activity_name) "
            "values ($1, $2, 'try_next', 'role', '야간에 일해보면?', '한 줄', '카페 알바') returning id", qeid, asker)
        await conn.execute("insert into related_cache (question_id, filter, items) values ($1, 'all', $2::jsonb)",
                           qid, f'[{{"experience_id": "{eid}", "reason": null, "score": 0.8}}]')
        await conn.execute("insert into related_cache (question_id, filter, items) values ($1, 'same_role', '[]'::jsonb)", qid)
        await conn.close()
        return qid
    qid = asyncio.run(seed_cache())

    r = _hook(client, eid, "private")
    assert r.status_code == 202 and r.json()["action"] == "unpublish"

    async def check():
        conn = await asyncpg.connect(DB)
        for _ in range(50):
            if await conn.fetchval("select embedding is null from experience where id = $1", eid):
                break
            await asyncio.sleep(0.1)
        emb_null = await conn.fetchval("select embedding is null from experience where id = $1", eid)
        filters = [r["filter"] for r in await conn.fetch("select filter from related_cache where question_id = $1", qid)]
        await conn.close()
        return emb_null, filters
    emb_null, filters = asyncio.run(check())
    assert emb_null
    assert filters == ["same_role"], "이 카드를 담은 캐시만 지워지고 나머지는 남음"


def test_delete_webhook_clears_related_cache(env):
    """공개된 경험을 삭제하면 DELETE 웹훅이 온다 (record 는 null). 그 카드를 담은 캐시만 지운다."""
    client = env
    owner, asker = uuid.uuid4(), uuid.uuid4()
    eid = asyncio.run(_seed_card(owner, "편의점 야간 알바", "야간 근무", ["판매"], "새벽 취객 상대"))
    other = asyncio.run(_seed_card(owner, "카페 알바", "바리스타", ["판매"], "단골이 생겼다"))

    async def seed_cache_then_delete():
        conn = await asyncpg.connect(DB)
        await conn.execute("insert into auth.users (id) values ($1) on conflict do nothing", asker)
        qeid = await conn.fetchval(
            "insert into experience (user_id, activity_name, genre, role, period_start, did_chips) "
            "values ($1, '축제 부스', '동아리', '기획', '2026-01', '{기획}') returning id", asker)
        qid = await conn.fetchval(
            "insert into question (experience_id, user_id, type, axis, text, source, source_activity_name) "
            "values ($1, $2, 'try_next', 'role', '야간에 일해보면?', '한 줄', '축제 부스') returning id", qeid, asker)
        await conn.execute("insert into related_cache (question_id, filter, items) values ($1, 'all', $2::jsonb)",
                           qid, f'[{{"experience_id": "{eid}", "reason": null, "score": 0.8}}]')
        await conn.execute("insert into related_cache (question_id, filter, items) values ($1, 'same_role', $2::jsonb)",
                           qid, f'[{{"experience_id": "{other}", "reason": null, "score": 0.7}}]')
        await conn.execute("delete from experience where id = $1", eid)
        await conn.close()
        return qid
    qid = asyncio.run(seed_cache_then_delete())

    r = client.post("/v1/internal/experience-published", headers={"X-Webhook-Secret": "s3cret"},
                    json={"type": "DELETE", "table": "experience", "schema": "public", "record": None,
                          "old_record": {"id": str(eid), "visibility": "anonymous"}})
    assert r.status_code == 202 and r.json() == {"experience_id": str(eid), "action": "delete"}, r.text

    async def check():
        conn = await asyncpg.connect(DB)
        for _ in range(50):
            filters = [x["filter"] for x in await conn.fetch("select filter from related_cache where question_id = $1", qid)]
            if filters == ["same_role"]:
                break
            await asyncio.sleep(0.1)
        await conn.close()
        return filters
    assert asyncio.run(check()) == ["same_role"], "지워진 카드를 담은 캐시만 지워지고 나머지는 남음"


def test_delete_webhook_without_old_id_is_ignored(env):
    r = env.post("/v1/internal/experience-published", headers={"X-Webhook-Secret": "s3cret"},
                 json={"type": "DELETE", "table": "experience", "record": None, "old_record": None})
    assert r.status_code == 202 and r.json() == {"ignored": True}


def test_sweep_fills_missing_embeddings(env):
    client = env
    uid = uuid.uuid4()
    pub = asyncio.run(_seed_card(uid, "축제 부스", "기획", ["기획"], "줄이 길었다"))
    priv = asyncio.run(_seed_card(uid, "비공개 기록", "기획", ["기획"], "안 나가야 함", visibility="private"))
    r = client.post("/v1/internal/sweep", headers={"X-Webhook-Secret": "s3cret"})
    assert r.status_code == 200 and r.json()["embedded"] >= 1

    async def check():
        conn = await asyncpg.connect(DB)
        a = await conn.fetchval("select embedding is not null from experience where id = $1", pub)
        b = await conn.fetchval("select embedding is null from experience where id = $1", priv)
        await conn.close()
        return a, b
    a, b = asyncio.run(check())
    assert a and b
