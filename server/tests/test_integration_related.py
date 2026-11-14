"""로컬 Postgres 가 있을 때만. 임베딩은 가짜(단어 해시), LLM 재정렬도 가짜로 바꿔 끼운다.
   테스트마다 고유한 단어(tok)를 써서 로컬 DB 에 남아 있는 다른 카드(평가용 481장 포함)와 안 섞이게 한다."""
import asyncio, json, uuid
import pytest, asyncpg
from fastapi.testclient import TestClient

from test_integration_embedding import DB, _reachable, fake_vector, fake_embed

pytestmark = pytest.mark.skipif(not _reachable(), reason="로컬 DB 없음")


class FakeLLM:
    """후보 순서를 뒤집어 돌려준다 (재정렬이 실제로 반영되는지 보려고). mode='fail' 이면 연결 실패."""
    def __init__(self):
        self.calls, self.mode, self.users = 0, "reverse", []

    async def complete_json(self, system, user, schema, **kw):
        from app.llm.client import LLMConnectionError, LLMResult
        self.calls += 1
        self.users.append(user)
        if self.mode == "fail":
            raise LLMConnectionError("fake down")
        n = user.count("\n[")          # 후보 줄 수
        order = list(range(n, 0, -1)) if self.mode == "reverse" else []
        return LLMResult(data={"items": [{"n": i, "reason": f"이유 {i}"} for i in order]}, model="fake-llm",
                         prompt_tokens=None, completion_tokens=None, attempts=1)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DB)
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("AUTH_DEV_BYPASS", "1")
    from app.config import settings; settings.cache_clear()
    import app.embedding.client as ec
    monkeypatch.setattr(ec, "embed", fake_embed)
    import app.embedding.service as es
    async def _one(t): return (await fake_embed([t]))[0]
    monkeypatch.setattr(es, "embed_one", _one)
    import app.llm.client as lc
    llm = FakeLLM()
    monkeypatch.setattr(lc, "complete_json", llm.complete_json)
    from app.main import app
    with TestClient(app) as client:
        yield client, llm


def run(coro):
    return asyncio.run(coro)


async def _sql(q, *args, fetch=None):
    conn = await asyncpg.connect(DB)
    try:
        if fetch == "val":
            return await conn.fetchval(q, *args)
        if fetch == "all":
            return await conn.fetch(q, *args)
        return await conn.execute(q, *args)
    finally:
        await conn.close()


def _snap(tok, role=None, genre="알바", one_liner="새벽"):
    return {"activity_name": f"활동{tok}", "genre": genre, "role": role or f"역할{tok}",
            "did_chips": [f"칩{tok}"], "one_liner": one_liner}


def card(tok, *, uid=None, visibility="anonymous", embedded=True, **kw):
    """남의 공개 카드 하나. embedded=False 면 임베딩 없이 (웹훅으로 채우는 테스트용)."""
    from app.embedding.client import to_pg
    from app.embedding.text import card_text
    uid = uid or uuid.uuid4()
    s = _snap(tok, **kw)
    async def go():
        await _sql("insert into auth.users (id) values ($1) on conflict do nothing", uid)
        return await _sql(
            "insert into experience (user_id, activity_name, genre, role, period_start, did_chips, one_liner, visibility, "
            " published_at, published_snapshot, embedding) values ($1,$2,$3,$4,'2026-01',$5,$6,$7, now(), $8::jsonb, $9::vector) "
            "returning id", uid, s["activity_name"], s["genre"], s["role"], s["did_chips"], s["one_liner"], visibility,
            json.dumps(s, ensure_ascii=False), to_pg(fake_vector(card_text(s))) if embedded else None, fetch="val")
    return run(go()), uid


def question(tok, *, uid=None, text="야간에 해보면?", notify=False):
    """질문 주인의 비공개 경험 + 거기서 나온 질문 하나."""
    uid = uid or uuid.uuid4()
    async def go():
        await _sql("insert into auth.users (id) values ($1) on conflict do nothing", uid)
        eid = await _sql(
            "insert into experience (user_id, activity_name, genre, role, period_start, did_chips) "
            "values ($1,$2,'알바',$3,'2026-01',$4) returning id", uid, f"활동{tok}", f"역할{tok}", [f"칩{tok}"], fetch="val")
        qid = await _sql(
            "insert into question (experience_id, user_id, type, axis, text, source, source_activity_name, notify_on_related) "
            "values ($1,$2,'try_next','method',$3,'한 일 칩',$4,$5) returning id", eid, uid, text, f"활동{tok}", notify, fetch="val")
        return qid, eid
    qid, eid = run(go())
    return qid, uid, eid


def ask(client, uid, qid, filter_=None):
    body = {"question_id": str(qid)} | ({"filter": filter_} if filter_ else {})
    return client.post("/v1/related", headers={"Authorization": f"Bearer dev:{uid}"}, json=body)


def ids(resp):
    assert resp.status_code == 200, resp.text
    return [it["card"]["experience_id"] for it in resp.json()["items"]]


def cache(qid, filter_="all"):
    return run(_sql("select items::text from related_cache where question_id = $1 and filter = $2", qid, filter_, fetch="val"))


def hook(client, eid, visibility):
    r = client.post("/v1/internal/experience-published", headers={"X-Webhook-Secret": "s3cret"},
                    json={"type": "UPDATE", "table": "experience", "record": {"id": str(eid), "visibility": visibility}})
    assert r.status_code == 202, r.text


def test_first_open_computes_reranks_caches_and_excludes_mine_blocked_private(env):
    client, llm = env
    tok = uuid.uuid4().hex[:8]
    qid, me, _ = question(tok)
    a, _ = card(tok, one_liner="새벽")                     # 유사도 1위
    b, _ = card(tok, one_liner="새벽 손님 많음")              # 2위
    card(tok, uid=me)                                      # 내 공개 카드 — 내 질문엔 안 붙음
    blk, blocked_uid = card(tok)
    card(tok, visibility="private")                        # 남의 나만 보기
    run(_sql("insert into block (user_id, blocked_id) values ($1, $2)", blocked_uid, me))   # 상대가 나를 차단한 방향도 빠짐

    r = ask(client, me, qid)
    assert ids(r) == [str(b), str(a)], "가짜 LLM 이 뒤집은 순서 그대로"
    first = r.json()["items"][0]
    assert first["reason"] == "이유 2"
    assert set(first["card"]) == {"experience_id", "activity_name", "genre", "role", "did_chips", "one_liner",
                                  "official_activity", "published_at", "accept_questions", "qa_count", "is_mine"}
    assert first["card"]["is_mine"] is False and first["card"]["activity_name"] == f"활동{tok}"
    assert llm.calls == 1
    assert "## 질문\n야간에 해보면?" in llm.users[0] and llm.users[0].count("\n[") == 2, "LLM 엔 걸러진 후보만"

    assert ids(ask(client, me, qid)) == [str(b), str(a)]
    assert llm.calls == 1, "두 번째는 캐시"
    assert [it["experience_id"] for it in json.loads(cache(qid))] == [str(b), str(a)]


def test_no_candidate_gives_empty_list_without_calling_llm(env):
    client, llm = env
    qid, me, _ = question(uuid.uuid4().hex[:8])
    assert ids(ask(client, me, qid)) == []
    assert llm.calls == 0 and cache(qid) == "[]"


def test_llm_may_drop_every_candidate(env):
    client, llm = env
    tok = uuid.uuid4().hex[:8]
    qid, me, _ = question(tok)
    card(tok)
    llm.mode = "none"
    assert ids(ask(client, me, qid)) == [] and cache(qid) == "[]"


def test_unpublished_or_deleted_card_triggers_recompute(env):
    client, llm = env
    tok = uuid.uuid4().hex[:8]
    qid, me, _ = question(tok)
    a, _ = card(tok, one_liner="새벽")
    b, _ = card(tok, one_liner="새벽 손님 많음")
    assert ids(ask(client, me, qid)) == [str(b), str(a)] and llm.calls == 1

    run(_sql("update experience set visibility = 'private' where id = $1", a))
    hook(client, a, "private")                              # 웹훅이 캐시를 지움
    assert cache(qid) is None
    assert ids(ask(client, me, qid)) == [str(b)] and llm.calls == 2

    run(_sql("delete from experience where id = $1", b))    # 삭제는 웹훅이 없다 → 읽을 때 알아채고 다시 계산
    assert ids(ask(client, me, qid)) == [] and cache(qid) == "[]"
    assert llm.calls == 2, "후보가 없으니 LLM 은 안 부름"


def test_block_after_caching_is_filtered_on_read_without_recompute(env):
    client, llm = env
    tok = uuid.uuid4().hex[:8]
    qid, me, _ = question(tok)
    a, a_owner = card(tok, one_liner="새벽")
    b, _ = card(tok, one_liner="새벽 손님 많음")
    assert ids(ask(client, me, qid)) == [str(b), str(a)]
    run(_sql("insert into block (user_id, blocked_id) values ($1, $2)", me, a_owner))
    assert ids(ask(client, me, qid)) == [str(b)]
    assert llm.calls == 1


def test_llm_failure_falls_back_to_similarity_order_and_is_not_cached(env):
    client, llm = env
    tok = uuid.uuid4().hex[:8]
    qid, me, _ = question(tok)
    a, _ = card(tok, one_liner="새벽")
    b, _ = card(tok, one_liner="새벽 손님 많음")
    llm.mode = "fail"
    r = ask(client, me, qid)
    assert ids(r) == [str(a), str(b)], "유사도 순"
    assert all(it["reason"] is None for it in r.json()["items"])
    assert cache(qid) is None, "실패한 결과는 저장하지 않는다"
    llm.mode = "reverse"
    assert ids(ask(client, me, qid)) == [str(b), str(a)] and cache(qid) is not None


def test_filters_are_applied_before_rerank_and_cached_separately(env):
    client, llm = env
    tok = uuid.uuid4().hex[:8]
    qid, me, _ = question(tok)
    same, _ = card(tok)
    other_role, _ = card(tok, role="매니저")
    club, _ = card(tok, genre="동아리")
    assert ids(ask(client, me, qid, "different_role")) == [str(other_role)]
    assert set(ids(ask(client, me, qid, "same_role"))) == {str(same), str(club)}
    assert ids(ask(client, me, qid, "genre:동아리")) == [str(club)]
    assert set(ids(ask(client, me, qid))) == {str(same), str(other_role), str(club)}
    n = run(_sql("select count(*) from related_cache where question_id = $1", qid, fetch="val"))
    assert n == 4


def test_new_public_card_fills_flagged_empty_question_and_marks_related_new(env):
    client, llm = env
    tok, other_tok = uuid.uuid4().hex[:8], uuid.uuid4().hex[:8]
    flagged, me, _ = question(tok)
    unflagged, me2, _ = question(tok)
    unrelated, me3, _ = question(other_tok, notify=True)
    for q, u in ((flagged, me), (unflagged, me2), (unrelated, me3)):
        assert ids(ask(client, u, q)) == []                 # 처음엔 0개
    assert ids(ask(client, me, flagged, "same_role")) == []
    run(_sql("update question set notify_on_related = true where id = $1", flagged))   # 앱이 "알려주세요"를 누름

    new, _ = card(tok, embedded=False)
    hook(client, new, "anonymous")                          # 공개 확정 웹훅 → 임베딩 → 맞춰보기

    async def state(q):
        for _ in range(50):
            if await _sql("select embedding is not null from experience where id = $1", new, fetch="val"):
                break
            await asyncio.sleep(0.1)
        return await _sql("select related_new from question where id = $1", q, fetch="val")
    assert run(state(flagged)) is True
    assert [it["experience_id"] for it in json.loads(cache(flagged))] == [str(new)]
    assert cache(flagged, "same_role") is None, "다른 filter 캐시는 지워서 다음에 다시 계산"
    assert run(state(unflagged)) is False and cache(unflagged) == "[]", "안 켠 질문은 안 건드림"
    assert run(state(unrelated)) is False, "켰어도 새 카드와 안 맞으면 그대로"
    assert llm.calls == 1, "새 카드가 임계값을 넘는 질문에만 LLM"
    assert ids(ask(client, me, flagged)) == [str(new)] and llm.calls == 1


def test_someone_elses_question_is_404_and_bad_filter_is_400(env):
    client, _ = env
    qid, me, _ = question(uuid.uuid4().hex[:8])
    r = ask(client, uuid.uuid4(), qid)
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert ask(client, me, uuid.uuid4()).status_code == 404
    r = ask(client, me, qid, "role:매니저")
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_FAILED"
    assert client.post("/v1/related", json={"question_id": str(qid)}).status_code == 401
