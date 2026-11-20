"""소윤 기록 테이블(db/000_soyun_record.sql)의 권한·제약·트리거·웹훅. 로컬 DB 가 있을 때만 돈다.
   bash ../db/apply_local.sh 로 DB 를 올린 뒤:  pytest tests/test_integration_record_db.py"""
import asyncio, json, os, uuid
import asyncpg, pytest

DB = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:54329/genz")


def _reachable() -> bool:
    try:
        async def ping():
            c = await asyncpg.connect(DB); await c.close()
        asyncio.run(asyncio.wait_for(ping(), 2)); return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="로컬 DB 없음")

EXP = ("insert into experience (activity_name, genre, role, period_start, did_chips, status) "
       "values ('편의점 알바', '서비스·응대', '야간 근무', '2026-01', '{판매}', $1) returning id, updated_at")


def run(coro):
    return asyncio.run(coro)


async def _user(conn) -> uuid.UUID:
    uid = uuid.uuid4()
    await conn.execute("insert into auth.users (id) values ($1)", uid)
    return uid


async def as_app(conn, uid, sql, *args, fetch="fetchrow"):
    """앱(authenticated, 그 사용자 JWT)으로 한 문장 실행."""
    async with conn.transaction():
        await conn.execute("set local role authenticated")
        await conn.execute("select set_config('request.jwt.claim.sub', $1, true)", str(uid))
        return await getattr(conn, fetch)(sql, *args)


def test_app_writes_own_experience_but_not_locked_columns():
    async def go():
        conn = await asyncpg.connect(DB)
        me, other = await _user(conn), await _user(conn)
        row = await as_app(conn, me, EXP, "done")                    # user_id 는 기본값 auth.uid()
        assert await as_app(conn, me, "update experience set official_period = 13 where id = $1 returning official_period",
                            row["id"], fetch="fetchval") == 13      # 기수는 앱이 고름
        assert await conn.fetchval("select user_id from experience where id = $1", row["id"]) == me
        assert await as_app(conn, other, "select * from experience where id = $1", row["id"]) is None  # 남은 못 봄
        for col, val in [("visibility", "'anonymous'"), ("published_snapshot", "'{}'"), ("accept_questions", "false"),
                         ("embedding", "null"), ("user_id", f"'{other}'")]:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await as_app(conn, me, f"update experience set {col} = {val} where id = $1", row["id"], fetch="execute")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await as_app(conn, me, "insert into experience (activity_name, genre, role, period_start, did_chips, visibility) "
                                   "values ('편의점 알바', '서비스·응대', '야간 근무', '2026-01', '{판매}', 'profile')", fetch="execute")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):   # 공식 활동은 서버만
            await as_app(conn, me, "select * from official_activity", fetch="fetch")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):   # 웹훅 함수를 RPC 로 못 부름
            await as_app(conn, me, "select soyun_post_webhook('/x', '{}')", fetch="execute")
        await conn.close()
    run(go())


def test_updated_at_is_the_version():
    async def go():
        conn = await asyncpg.connect(DB)
        me = await _user(conn)
        row = await as_app(conn, me, EXP, "done")
        upd = "update experience set one_liner = $3 where id = $1 and updated_at = $2 returning updated_at"
        v2 = await as_app(conn, me, upd, row["id"], row["updated_at"], "첫 수정", fetch="fetchval")
        assert v2 and v2 > row["updated_at"]
        assert await as_app(conn, me, upd, row["id"], row["updated_at"], "옛 버전으로", fetch="fetchval") is None  # VERSION_CONFLICT
        # 재현 서버가 embedding 만 채우면 버전 안 바뀜
        await conn.execute("update experience set embedding = array_fill(0.1::real, array[1024])::vector where id = $1", row["id"])
        assert await conn.fetchval("select updated_at from experience where id = $1", row["id"]) == v2
        await conn.close()
    run(go())


@pytest.mark.parametrize("cols,vals", [
    ("did_chips", "'{}'"),                                   # 한 일 칩 0개
    ("did_chips", "'{a,b,c,d}'"),                            # 4개
    ("did_notes", f"'{json.dumps({'판매': 'x' * 81})}'"),    # 보강 81자
    ("emotion_chips", "'{a,b,c}'"),
    ("period_end", "'2025-12'"),                             # 시작보다 앞
    ("period_start", "'2026-13'"),
    ("activity_name", "'가'"),
    ("one_liner", f"'{'x' * 101}'"),
    ("genre", "'알바'"),                                     # 분야 9개 밖
    ("type", "'여행'"),                                      # 유형 7개 밖
    ("official_period", "0"),                                # 기수 1~99
    ("official_period", "100"),
])
def test_experience_checks(cols, vals):
    async def go():
        conn = await asyncpg.connect(DB)
        me = await _user(conn)
        row = await conn.fetchrow(
            "insert into experience (user_id, activity_name, genre, role, period_start, did_chips) "
            "values ($1, '편의점 알바', '서비스·응대', '야간 근무', '2026-01', '{판매}') returning id", me)
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(f"update experience set {cols} = {vals} where id = $1", row["id"])
        await conn.close()
    run(go())


def test_experience_log_only_on_my_in_progress_experience():
    async def go():
        conn = await asyncpg.connect(DB)
        me, other = await _user(conn), await _user(conn)
        mine = (await as_app(conn, me, EXP, "in_progress"))["id"]
        done = (await as_app(conn, me, EXP, "done"))["id"]
        theirs = (await as_app(conn, other, EXP, "in_progress"))["id"]
        ins = ("insert into experience_log (id, experience_id, situation_chips, emotion_chips, one_liner) "
               "values ($1, $2, '{바쁨}', '{뿌듯}', '오늘은 손님이 많았다')")
        log_id = uuid.uuid4()
        await as_app(conn, me, ins, log_id, mine, fetch="execute")
        with pytest.raises(asyncpg.CheckViolationError):           # 회차 감정 칩은 딱 1개
            await conn.execute("insert into experience_log (id, experience_id, user_id, situation_chips, emotion_chips) "
                               "values ($1, $2, $3, '{바쁨}', '{뿌듯,지침}')", uuid.uuid4(), mine, me)
        with pytest.raises(asyncpg.UniqueViolationError):          # 앱이 같은 id 로 다시 보내도 하나만
            await as_app(conn, me, ins, log_id, mine, fetch="execute")
        for eid in (done, theirs):                                 # 끝난 활동, 남의 경험
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await as_app(conn, me, ins, uuid.uuid4(), eid, fetch="execute")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):    # 다른 경험으로 옮기기 금지
            await as_app(conn, me, "update experience_log set experience_id = $2 where id = $1", log_id, done, fetch="execute")
        assert await as_app(conn, other, "select * from experience_log where id = $1", log_id) is None
        await conn.close()
    run(go())


def test_webhooks_fire_only_when_they_should():
    async def go():
        listen = await asyncpg.connect(DB)
        got = []
        await listen.add_listener("soyun_webhook", lambda *a: got.append(json.loads(a[-1])))
        conn = await asyncpg.connect(DB)
        me = await _user(conn)
        eid = (await as_app(conn, me, EXP, "done"))["id"]
        await as_app(conn, me, "update experience set one_liner = '고침' where id = $1", eid, fetch="execute")  # 안 감
        await conn.execute("update experience set visibility = 'anonymous', published_at = now() where id = $1", eid)
        await conn.execute("update experience set accept_questions = false where id = $1", eid)               # 안 감
        await conn.execute("delete from experience where id = $1", eid)
        priv = (await as_app(conn, me, EXP, "done"))["id"]
        await as_app(conn, me, "delete from experience where id = $1", priv, fetch="execute")                # 비공개 삭제는 안 감
        await asyncio.sleep(0.3)
        mine = [g for g in got if g["id"] in (str(eid), str(priv))]
        assert [(g["path"], g["type"], g["id"]) for g in mine] == [
            ("/v1/internal/experience-saved", "INSERT", str(eid)),
            ("/v1/internal/experience-published", "UPDATE", str(eid)),
            ("/v1/internal/experience-published", "DELETE", str(eid)),
            ("/v1/internal/experience-saved", "INSERT", str(priv)),
        ]
        await conn.close(); await listen.close()
    run(go())
