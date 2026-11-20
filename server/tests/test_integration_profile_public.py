"""프로필 테이블 권한 + 공개 카드 뷰 + 스냅샷 → 재현 PublicCard (db/000_soyun_record.sql). 로컬 DB 가 있을 때만."""
import asyncio
import json

import asyncpg
import pytest
from test_integration_record_db import DB, _reachable, _user, as_app

from app.cards.public import fetch_public_rows, to_public_card
from app.publish.snapshot import make_snapshot

pytestmark = pytest.mark.skipif(not _reachable(), reason="로컬 DB 없음")


def run(coro):
    return asyncio.run(coro)


async def _conn():
    conn = await asyncpg.connect(DB)
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    return conn


def test_profile_own_row_and_locked_columns():
    async def go():
        conn = await _conn()
        me, other = await _user(conn), await _user(conn)
        await as_app(conn, me, "insert into profile (nickname, school, show_school) values ('두디', '한국대', true)", fetch="execute")
        assert await conn.fetchval("select user_id from profile where nickname = '두디' and user_id = $1", me) == me
        assert await as_app(conn, other, "select * from profile where user_id = $1", me) is None   # 남은 못 봄
        assert await as_app(conn, me, "update profile set grade = '3학년' where user_id = $1 returning grade", me,
                            fetch="fetchval") == "3학년"
        for sql in ["update profile set school_verified = true where user_id = $1",
                    "update profile set user_id = gen_random_uuid() where user_id = $1"]:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await as_app(conn, me, sql, me, fetch="execute")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):   # 가입 때 인증된 척 못 함
            await as_app(conn, other, "insert into profile (nickname, school_verified) values ('남', true)", fetch="execute")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):   # 남의 user_id 로 못 만듦 (RLS)
            await as_app(conn, other, "insert into profile (user_id, nickname) values ($1, '사칭')", me, fetch="execute")
        await conn.execute("update profile set school_verified = true where user_id = $1", me)    # 서버는 됨
        await conn.close()
    run(go())


def test_profile_starts_all_private():
    async def go():
        conn = await _conn()
        me = await _user(conn)
        row = await as_app(conn, me, "insert into profile (nickname, bio) values ('기록하는 고양이', '배울 점이 분명한 역할을') "
                                     "returning show_school, show_major, show_grade, show_bio, show_qa, school_verified")
        assert not any(row.values())                       # 처음엔 전부 비공개, 인증 안 됨
        await conn.close()
    run(go())


@pytest.mark.parametrize("nick", ["가", " 가 ", "가" * 13,
                                  "010-1234-5678", "두디01012345678", "a@b.co", "dudi@gmail.com"])   # 연락처 모양
def test_profile_nickname_rejected(nick):
    async def go():
        conn = await _conn()
        me = await _user(conn)
        with pytest.raises(asyncpg.CheckViolationError):
            await as_app(conn, me, "insert into profile (nickname) values ($1)", nick, fetch="execute")
        await conn.close()
    run(go())


async def _exp(conn, uid, visibility="private", snapshot=None, official_id=None):
    return await conn.fetchval(
        "insert into experience (user_id, activity_name, genre, role, period_start, did_chips, one_liner, place, photos,"
        " official_activity_id, visibility, published_snapshot, masked, published_at) "
        "values ($1, '학생회관 축제 운영', '행사 운영', '행사기획팀 팀장', '2026-03', '{부스 운영,동선 짜기}', '줄이 길었다',"
        " '학생회관 302호', '{photos/a.jpg}', $2, $3, $4, $5, case when $3 = 'private' then null else now() end) returning id",
        uid, official_id, visibility, snapshot, None if snapshot is None else [])


def test_public_view_shows_only_published_with_snapshot():
    async def go():
        conn = await _conn()
        me = await _user(conn)
        priv = await _exp(conn, me)
        no_snap = await _exp(conn, me, "anonymous")
        pub = await _exp(conn, me, "profile", {"activity_name": "축제 운영"})
        rows = {r["experience_id"]: r for r in await conn.fetch(
            "select * from experience_public where experience_id = any($1::uuid[])", [priv, no_snap, pub])}
        assert list(rows) == [pub]
        assert set(rows[pub].keys()) == {"experience_id", "user_id", "published_snapshot", "masked", "published_at",
                                         "accept_questions", "visibility"}   # 원본 칸(장소·사진 등)은 없음
        assert rows[pub]["visibility"] == "profile"
        with pytest.raises(asyncpg.InsufficientPrivilegeError):          # 앱은 뷰도 못 읽음 (FastAPI 만)
            await as_app(conn, me, "select * from experience_public", fetch="fetch")
        await conn.close()
    run(go())


def test_snapshot_becomes_public_card_and_ignores_later_edits():
    async def go():
        conn = await _conn()
        me, viewer = await _user(conn), await _user(conn)
        oid = await conn.fetchval("insert into official_activity (name, status) values ('대학 축제 연합', 'verified') "
                                  "on conflict do nothing returning id") or \
            await conn.fetchval("select id from official_activity where name = '대학 축제 연합'")
        eid = await _exp(conn, me, official_id=oid)
        exp = dict(await conn.fetchrow("select * from experience where id = $1", eid))
        snap, masked = make_snapshot(exp, {"name": "대학 축제 연합", "status": "verified"},
                                     {"role": "행사 운영 담당", "place": "교내 행사장"})
        await conn.execute("update experience set visibility = 'anonymous', published_snapshot = $2, masked = $3, "
                           "published_at = now() where id = $1", eid, snap, masked)
        await conn.execute("update experience set one_liner = '고친 글', role = '총괄' where id = $1", eid)  # 공개 뒤 원본 수정

        card = to_public_card((await fetch_public_rows(conn, [eid], viewer))[eid], viewer)
        assert card["role"] == "행사 운영 담당" and card["one_liner"] == "줄이 길었다"     # 가린 값 + 공개 당시 글
        assert card["did_chips"] == ["부스 운영", "동선 짜기"] and card["genre"] == "행사 운영"
        assert card["official_activity"] == {"name": "대학 축제 연합", "verified": True}
        assert card["is_mine"] is False and card["accept_questions"] is True
        stored = await conn.fetchrow("select published_snapshot, masked from experience_public where experience_id = $1", eid)
        assert "photos" not in stored["published_snapshot"] and "학생회관 302호" not in json.dumps(stored, default=str, ensure_ascii=False)
        assert stored["masked"] == ["place", "role"]
        await conn.close()
    run(go())
