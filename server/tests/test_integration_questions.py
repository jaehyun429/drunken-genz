"""로컬 Postgres 가 있을 때만 도는 통합 테스트. LLM 은 가짜로 바꿔 끼운다.
   bash ../db/apply_local.sh 로 DB 를 올린 뒤:  DATABASE_URL=... pytest tests/test_integration_questions.py"""
import os, uuid, asyncio, json
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

def fq(type, axis, text, source="한 줄"):
    return {"type": type, "axis": axis, "text": text, "source": source, "done_experience_id": None}


# 처음 생성에 모델이 내는 후보. 더 적어보기 1 + 해볼 것 2 + 상상해보기 3 (그 가운데 하나는 금지 13 에 걸린다)
FIRST = [
    fq("write_more", "gap_result", "그 뒤로 어떻게 됐나요?", ""),
    fq("try_next", "method", "다음 회의 때 첫 5분 안에 한마디만 꺼내볼까요?", "협업 조율 보강글"),
    fq("imagine", "situation", "그때 역할을 먼저 정했다면 어땠을까요?"),
    fq("imagine", "situation", "단골 이름 외우기 시작하지 않았다면 어땠을까요?"),
    fq("try_next", "role", "다음엔 발표를 맡는 역할에 지원해보면 어떨까요?", "교환학생"),
    fq("imagine", "external", "만약 준비 기간이 한 주 더 있었다면 어땠을까요?"),
]
# 다시 만들기에서 모델이 내는 새 후보
SECOND = [
    fq("try_next", "scale", "다음엔 여럿이 하는 팀 과제로 해보면 어떨까요?", "협업 조율"),
    fq("imagine", "external", "만약 수업이 대면이었다면 어땠을까요?"),
    fq("try_next", "did", "다음엔 발표 대본도 직접 써보면 어떨까요?", "조사·리서치"),
    fq("imagine", "external", "만약 학기가 한 달 더 길었다면 어땠을까요?"),
]
HOOK = {"X-Webhook-Secret": "s3cret"}


class FakeLLM:
    """queue 에 넣은 답을 차례로 주고, 비면 FIRST 를 준다."""
    def __init__(self): self.calls = 0; self.fail = None; self.queue = []; self.users = []
    async def __call__(self, system, user, schema, **kw):
        from app.llm.client import LLMResult, LLMConnectionError
        self.calls += 1; self.users.append(user)
        if self.fail == "connection":
            raise LLMConnectionError("가짜 연결 실패")
        data = self.queue.pop(0) if self.queue else FIRST
        return LLMResult(data={"questions": data}, model="fake", prompt_tokens=1, completion_tokens=1, attempts=1)


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
        "values ($1, '교환학생', '기타', '교환학생', '2026-09', '{조사·리서치,협업 조율}', "
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


def bearer(uid):
    return {"Authorization": f"Bearer dev:{uid}"}


def saved_hook(client, uid, eid):
    return client.post("/v1/internal/experience-saved", headers=HOOK,
                       json={"type": "INSERT", "table": "experience", "record": {"id": str(eid), "user_id": str(uid)}})


def first_generation(client):
    uid, eid = asyncio.run(_seed())
    assert saved_hook(client, uid, eid).status_code == 202
    assert asyncio.run(_wait_batch(eid))["status"] == "done"
    return uid, eid


async def _sql(query, *args, fetch=None):
    conn = await asyncpg.connect(DB)
    try:
        if fetch == "val":
            return await conn.fetchval(query, *args)
        return await conn.execute(query, *args)
    finally:
        await conn.close()


def test_webhook_generates_four_with_write_more_first(env):
    """PRD 7.2 구성: 4개 고정, 더 적어보기가 앞, 해볼 것·상상해보기 각 1개 이상."""
    client, fake = env
    uid, eid = asyncio.run(_seed())
    r = saved_hook(client, uid, eid)
    assert r.status_code == 202, r.text
    batch = asyncio.run(_wait_batch(eid))
    assert batch["status"] == "done" and batch["trigger"] == "save" and batch["input_hash"] and not batch["no_new"]
    qs = asyncio.run(_questions(eid))
    assert [(q["type"], q["text"][:5]) for q in qs] == [("write_more", "그 뒤로 "), ("try_next", "다음 회의"),
                                                        ("imagine", "그때 역할"), ("try_next", "다음엔 발")]
    assert qs[0]["axis"] == "gap_result" and qs[0]["source"] == "결과·배움" and qs[0]["answer"] is None
    assert qs[1]["source_activity_name"] == "교환학생"
    assert json.loads(batch["shown_texts"]) == [q["text"] for q in qs[1:]], "더 적어보기는 '이전에 보여준 질문'에 안 넣는다"
    assert "이전에 보여준 질문" not in fake.users[0]
    # 웹훅 재전송은 무시
    assert saved_hook(client, uid, eid).json().get("ignored") is True
    assert fake.calls == 1


def test_regenerate_avoids_shown_questions_keeps_saved_and_limits_per_24h(env):
    """PRD 7.2 재생성: 기록당 24시간 1회, 이전에 보여준 질문과 겹치지 않는 후보."""
    client, fake = env
    uid, eid = first_generation(client)
    asyncio.run(_sql("update question set saved = true where experience_id = $1 and axis = 'method'", eid))

    fake.queue = [SECOND]
    r = client.post("/v1/questions/regenerate", headers=bearer(uid), json={"experience_id": str(eid)})
    assert r.status_code == 202, r.text
    batch = asyncio.run(_wait_batch(eid))
    assert batch["generation"] == 2 and batch["status"] == "done" and not batch["no_new"]
    assert fake.calls == 2, "같은 기록이어도 저장된 질문을 다시 쓰지 않고 새로 만든다"
    shown = fake.users[1].split("## 이전에 보여준 질문 (다시 내지 않는다)\n")[1]
    assert "- 다음 회의 때 첫 5분 안에 한마디만 꺼내볼까요?" in shown and "- 그때 역할을 먼저 정했다면 어땠을까요?" in shown
    assert "그 뒤로 어떻게 됐나요?" not in shown
    qs = asyncio.run(_questions(eid))
    saved = [q for q in qs if q["saved"]]
    assert len(saved) == 1 and saved[0]["generation"] == 1     # 담은 건 남음
    fresh = [q for q in qs if q["generation"] == 2]
    assert [q["text"] for q in fresh] == [x["text"] for x in SECOND]
    assert len(qs) == 5 and all(q["generation"] == 2 or q["saved"] for q in qs), "안 담은 이전 질문과 안 답한 더 적어보기는 지워진다"

    r = client.post("/v1/questions/regenerate", headers=bearer(uid), json={"experience_id": str(eid)})
    assert r.status_code == 429 and r.json()["error"]["code"] == "QUESTION_REGEN_LIMIT"
    assert r.json()["error"]["details"] == {"limit": 1, "window_hours": 24}
    # 24시간이 지나면 다시 된다 (자정 기준이 아니다)
    asyncio.run(_sql("update question_batch set created_at = created_at - interval '25 hours' where experience_id = $1", eid))
    fake.queue = [[fq("try_next", "field", "다음엔 다른 나라 학교에도 지원해보면 어떨까요?", "교환학생"),
                   fq("imagine", "external", "만약 기숙사가 학교 안에 있었다면 어땠을까요?")]]
    r = client.post("/v1/questions/regenerate", headers=bearer(uid), json={"experience_id": str(eid)})
    assert r.status_code == 202, r.text
    assert asyncio.run(_wait_batch(eid))["generation"] == 3
    # 지워진 1번째 생성의 질문도 '이전에 보여준 질문'에 계속 남는다
    assert "- 그때 역할을 먼저 정했다면 어땠을까요?" in fake.users[-2] + fake.users[-1]

    # 남의 경험은 404
    r = client.post("/v1/questions/regenerate", headers=bearer(uuid.uuid4()), json={"experience_id": str(eid)})
    assert r.status_code == 404


def test_regenerate_with_nothing_new_keeps_questions_and_sets_no_new(env):
    """PRD 7.2: 겹치지 않는 후보가 없으면 '새로 만들 질문이 없어요'. 질문은 그대로 둔다."""
    client, fake = env
    uid, eid = first_generation(client)
    before = [(q["id"], q["text"]) for q in asyncio.run(_questions(eid))]
    # 모델이 이미 보여준 것만 또 낸다 (다시 묻기에도 같은 답)
    fake.queue = [FIRST[:5], FIRST[:5]]
    r = client.post("/v1/questions/regenerate", headers=bearer(uid), json={"experience_id": str(eid)})
    assert r.status_code == 202, r.text
    batch = asyncio.run(_wait_batch(eid))
    assert batch["status"] == "done" and batch["no_new"] and batch["generation"] == 2 and json.loads(batch["shown_texts"]) == []
    assert [(q["id"], q["text"]) for q in asyncio.run(_questions(eid))] == before
    assert fake.calls == 3, "처음 1번 + 다시 만들기 1번 + 모자라서 다시 묻기 1번"
    # TODO(기획) 임시값: 새 질문이 없던 시도도 24시간 1회에 센다
    r = client.post("/v1/questions/regenerate", headers=bearer(uid), json={"experience_id": str(eid)})
    assert r.status_code == 429


def test_regenerate_with_fewer_new_candidates_shows_only_the_new_ones(env):
    """새 후보가 4개에 못 미치면 이전 질문으로 채우지 않고 새 것만 보여준다. TODO(기획): 4개를 채워야 하는지."""
    client, fake = env
    uid, eid = first_generation(client)
    # queue 가 비어 FIRST 를 또 낸다. 처음에 안 뽑혀 보여준 적 없는 상상해보기 하나만 새 후보다
    r = client.post("/v1/questions/regenerate", headers=bearer(uid), json={"experience_id": str(eid)})
    assert r.status_code == 202, r.text
    batch = asyncio.run(_wait_batch(eid))
    assert batch["status"] == "done" and not batch["no_new"]
    qs = asyncio.run(_questions(eid))
    assert [(q["type"], q["text"]) for q in qs] == [("write_more", "그 뒤로 어떻게 됐나요?"),
                                                    ("imagine", "만약 준비 기간이 한 주 더 있었다면 어땠을까요?")]
    assert json.loads(batch["shown_texts"]) == ["만약 준비 기간이 한 주 더 있었다면 어땠을까요?"]


def test_answering_write_more_stores_the_line_and_earns_one_regeneration(env):
    """기능명세서 14.1: 답 한 줄(80자) 저장 → 기록 원문에 추가, 답한 칸은 다시 묻지 않음. PRD 7.2: 답할 때마다 재생성 1회."""
    client, fake = env
    uid, eid = first_generation(client)
    qs = asyncio.run(_questions(eid))
    more, other = qs[0]["id"], qs[1]["id"]
    url = f"/v1/questions/{more}/answer"

    r = client.post(url, headers=bearer(uid), json={"text": "가" * 81})
    assert r.status_code == 400 and r.json()["error"]["details"]["field"] == "text"
    assert client.post(url, headers=bearer(uid), json={"text": "   "}).status_code == 400
    r = client.post(f"/v1/questions/{other}/answer", headers=bearer(uid), json={"text": "답"})
    assert r.status_code == 400 and r.json()["error"]["details"]["field"] == "id"
    assert client.post(url, headers=bearer(uuid.uuid4()), json={"text": "답"}).status_code == 404
    assert client.post(f"/v1/questions/{uuid.uuid4()}/answer", headers=bearer(uid), json={"text": "답"}).status_code == 404
    assert client.post(url, json={"text": "답"}).status_code == 401

    r = client.post(url, headers=bearer(uid), json={"text": " 조원들과 역할을 다시 나눴다. "})
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["id"] == str(more) and first["answered_at"]
    # 같은 글을 다시 보내면 그대로, 다른 글이면 거부
    assert client.post(url, headers=bearer(uid), json={"text": "조원들과 역할을 다시 나눴다."}).json() == first
    r = client.post(url, headers=bearer(uid), json={"text": "다른 답"})
    assert r.status_code == 400 and r.json()["error"]["details"]["field"] == "answer"
    assert fake.calls == 1, "답을 저장해도 질문을 자동으로 다시 만들지 않는다"

    # 24시간 1회를 이미 썼어도 답 하나당 1회가 더 생긴다
    fake.queue = [SECOND]
    assert client.post("/v1/questions/regenerate", headers=bearer(uid), json={"experience_id": str(eid)}).status_code == 202
    assert asyncio.run(_wait_batch(eid))["generation"] == 2
    fake.queue = [[fq("write_more", "gap_result", "그래서 무엇을 배웠나요?", ""), fq("write_more", "gap_action", "그때 어떤 일을 직접 했나요?", ""),
                   fq("try_next", "field", "다음엔 다른 나라 학교에도 지원해보면 어떨까요?", "교환학생"),
                   fq("imagine", "external", "만약 기숙사가 학교 안에 있었다면 어땠을까요?"),
                   fq("try_next", "autonomy", "다음엔 일정을 직접 짜보면 어떨까요?", "조사·리서치"),
                   fq("imagine", "external", "만약 같은 과 친구가 함께 갔다면 어땠을까요?")]]
    r = client.post("/v1/questions/regenerate", headers=bearer(uid), json={"experience_id": str(eid)})
    assert r.status_code == 202, r.text
    assert asyncio.run(_wait_batch(eid))["generation"] == 3
    r = client.post("/v1/questions/regenerate", headers=bearer(uid), json={"experience_id": str(eid)})
    assert r.status_code == 429 and r.json()["error"]["details"]["limit"] == 2

    # 답은 기록 원문으로 모델에 들어가고, 답한 칸은 다시 묻지 않는다
    assert "더 적어본 것:\n  - [결과·배움] 그 뒤로 어떻게 됐나요? → 조원들과 역할을 다시 나눴다." in fake.users[-1]
    assert "- 이미 답한 칸: 결과·배움(gap_result)" in fake.users[-1]
    qs = asyncio.run(_questions(eid))
    kept = [q for q in qs if q["id"] == more]
    assert len(kept) == 1 and kept[0]["answer"] == "조원들과 역할을 다시 나눴다.", "답한 더 적어보기는 다시 만들어도 남는다"
    new = [q for q in qs if q["generation"] == 3]
    assert [(q["type"], q["axis"]) for q in new] == [("write_more", "gap_action"), ("try_next", "field"),
                                                     ("imagine", "external"), ("try_next", "autonomy")]


def test_write_more_has_no_related_and_cannot_be_saved(env):
    """기능명세서 14.1 '하지 않는 것': 관련 경험, 담기."""
    client, fake = env
    uid, eid = first_generation(client)
    more = asyncio.run(_questions(eid))[0]["id"]
    r = client.post("/v1/related", headers=bearer(uid), json={"question_id": str(more)})
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_FAILED"
    assert r.json()["error"]["details"]["field"] == "question_id"
    with pytest.raises(asyncpg.CheckViolationError):
        asyncio.run(_sql("update question set saved = true where id = $1", more))


def test_connection_failure_then_retry(env):
    client, fake = env
    uid, eid = asyncio.run(_seed())
    fake.fail = "connection"
    saved_hook(client, uid, eid)
    batch = asyncio.run(_wait_batch(eid))
    assert batch["status"] == "failed" and batch["failure_reason"] == "connection"

    fake.fail = None
    r = client.post("/v1/questions/retry", headers={"Authorization": f"Bearer dev:{uid}"}, json={"experience_id": str(eid)})
    assert r.status_code == 202, r.text
    batch = asyncio.run(_wait_batch(eid))
    assert batch["status"] == "done" and batch["generation"] == 1
    # 재시도는 재생성 횟수에 안 셈 → 재생성 1회 아직 가능
    r = client.post("/v1/questions/regenerate", headers={"Authorization": f"Bearer dev:{uid}"}, json={"experience_id": str(eid)})
    assert r.status_code == 202


def _post_saved(client, uid, eid):
    return client.post("/v1/internal/experience-saved", headers={"X-Webhook-Secret": "s3cret"},
                       json={"type": "INSERT", "table": "experience", "record": {"id": str(eid), "user_id": str(uid)}})


def test_record_shape_the_model_cannot_read_fails_the_batch(env):
    """DB 는 conditions 가 객체인지만 본다. 값이 숫자면 모델 입력으로 못 바꾼다. pending 으로 남지 않고 failed/model 이 된다."""
    client, fake = env
    uid, eid = asyncio.run(_seed())

    async def spoil():
        conn = await asyncpg.connect(DB)
        await conn.execute("update experience set conditions = '{\"규모\": 3}' where id = $1", eid)
        await conn.close()
    asyncio.run(spoil())

    assert _post_saved(client, uid, eid).status_code == 202
    batch = asyncio.run(_wait_batch(eid))
    assert batch["status"] == "failed" and batch["failure_reason"] == "model"
    assert batch["error_detail"] == "입력 모양 오류: conditions"   # 어느 칸인지만. 값은 안 남긴다
    assert fake.calls == 0


def test_unexpected_error_while_storing_fails_the_batch(env, monkeypatch):
    """저장 단계에서 난 예외도 밖으로 안 나가고 failed 로 남는다. 예외 글(사용자 글이 섞일 수 있다)은 안 남긴다."""
    client, fake = env
    uid, eid = asyncio.run(_seed())
    from app.questions import service

    async def boom(*a, **kw):
        raise RuntimeError("조용한 사람이 됐다.")
    monkeypatch.setattr(service, "_store", boom)

    assert _post_saved(client, uid, eid).status_code == 202
    batch = asyncio.run(_wait_batch(eid))
    assert batch["status"] == "failed" and batch["failure_reason"] == "model"
    assert batch["error_detail"] == "RuntimeError"
    assert asyncio.run(_questions(eid)) == []


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
    client.post("/v1/internal/sweep", headers=HOOK)
    assert asyncio.run(_wait_batch(eid, timeout=0.3)) is None   # 방금 저장한 건 아직 안 건드림 (다른 테스트가 남긴 경험은 처리될 수 있음)

    async def age():
        conn = await asyncpg.connect(DB)
        # sweep 은 오래된 순으로 SWEEP_BATCH_LIMIT 개만 집는다. 로컬 DB 에 다른 테스트가 남긴 batch 없는 경험이
        # 쌓여 있어도 이 경험이 먼저 잡히게 제일 오래된 것으로 만든다
        await conn.execute("update experience set created_at = now() - interval '10 years' where id = $1", eid)
        await conn.close()
    asyncio.run(age())
    r = client.post("/v1/internal/sweep", headers=HOOK)
    assert r.json()["processed"] >= 1
    batch = asyncio.run(_wait_batch(eid))
    assert batch["status"] == "done" and batch["trigger"] == "save"
    # 두 번째 sweep 은 같은 경험을 다시 안 잡음
    client.post("/v1/internal/sweep", headers=HOOK)
    assert asyncio.run(_wait_batch(eid))["generation"] == 1
