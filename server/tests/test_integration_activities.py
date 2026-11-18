"""공식 활동 검색 (소윤). 공식(verified)만 나온다. 로컬 DB 가 있을 때만. 테스트마다 고유 글자(tok)를 이름에 넣어 다른 데이터와 안 섞이게 한다."""
import asyncio
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient
from test_integration_record_db import DB, _reachable

pytestmark = pytest.mark.skipif(not _reachable(), reason="로컬 DB 없음")


def run(coro):
    return asyncio.run(coro)


async def _exec(q, *args):
    conn = await asyncpg.connect(DB)
    try:
        return await conn.fetchval(q, *args)
    finally:
        await conn.close()


def _user() -> uuid.UUID:
    uid = uuid.uuid4()
    run(_exec("insert into auth.users (id) values ($1)", uid))
    return uid


def _activity(name, status="verified", aliases=(), period=None, series=None, created_by=None, genre=None):
    return run(_exec("insert into official_activity (name, status, aliases, period, series_key, created_by, genre) "
                     "values ($1, $2, $3, $4, $5, $6, $7) returning id",
                     name, status, list(aliases), period, series, created_by, genre))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DB)
    monkeypatch.setenv("AUTH_DEV_BYPASS", "1")
    from app.config import settings; settings.cache_clear()
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def tok():
    return "zq" + uuid.uuid4().hex[:6]


def _search(client, me, q):
    r = client.get("/v1/activities/search", params={"q": q}, headers={"Authorization": f"Bearer dev:{me}"})
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_search_ignores_spaces_case_and_uses_aliases(client, tok):
    me = _user()
    aid = _activity(f"취업 박람회 {tok}", aliases=[f"취박{tok}"], genre="행사 운영")
    for q in (f"취업박람회{tok}", f"박람회 {tok.upper()}", f"취박{tok}"):
        items = _search(client, me, q)
        assert [i["id"] for i in items] == [str(aid)], q
    item = _search(client, me, tok)[0]
    assert item["genre"] == "행사 운영" and item["record_count"] is None
    assert _search(client, me, "가") == []                      # 2글자 미만
    assert _search(client, me, f"%{tok}") == []                 # % 는 글자 그대로


def test_initials_search(client, tok):
    me = _user()
    aid = _activity(f"취업 박람회 {tok}", aliases=[f"과학기술준비위원회{tok}"])
    assert run(_exec("select hangul_initials('취업 박람회A1')")) == "ㅊㅇ ㅂㄹㅎA1"
    for q in (f"ㅊㅇㅂㄹㅎ{tok}", f"취ㅇ ㅂㄹㅎ{tok}", f"ㄱㅎㄱㅅㅈㅂㅇㅇㅎ{tok}"):   # 초성만, 입력 중 섞임, 별칭 초성
        assert [i["id"] for i in _search(client, me, q)] == [str(aid)], q
    assert _search(client, me, f"ㅂㅂㅂ{tok}") == []


def test_pending_not_searchable(client, tok):
    me = _user()
    _activity(f"검수 전 활동 {tok}", status="pending", created_by=me)
    assert _search(client, me, tok) == []


def test_series_grouped_latest_first_and_record_count(client, tok):
    me = _user()
    old = _activity(f"서포터즈 {tok}", period="9기", series=tok)
    new = _activity(f"서포터즈 {tok}", period="13기", series=tok)
    items = _search(client, me, tok)
    assert len(items) == 1 and items[0]["id"] == str(new)
    assert [p["period"] for p in items[0]["periods"]] == ["13기", "9기"]
    for i in range(5):                                          # 기수 합으로 5개 → 숫자 보임
        run(_exec("insert into experience (user_id, activity_name, official_activity_id, genre, role, period_start, did_chips) "
                  "values ($1, '서포터즈', $2, '경영·마케팅', '기자', '2026-01', '{취재}')", me, old if i % 2 else new))
    assert _search(client, me, tok)[0]["record_count"] == 5


def test_prefix_match_ranks_first(client, tok):
    me = _user()
    inner = _activity(f"대학생 {tok} 기자단")
    prefix = _activity(f"{tok} 서포터즈")
    assert [i["id"] for i in _search(client, me, tok)] == [str(prefix), str(inner)]


def test_needs_login(client):
    assert client.get("/v1/activities/search", params={"q": "동아리"}).status_code == 401
