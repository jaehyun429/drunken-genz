"""asyncpg 풀. 서버는 service_role 급 연결(DATABASE_URL)로 붙으니 RLS 를 안 탄다 → 사용자 구분은 코드에서 반드시 한다."""
import json
import asyncpg
from .config import settings

_pool: asyncpg.Pool | None = None


async def _init_conn(conn: asyncpg.Connection):
    # jsonb ↔ dict 자동 변환
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def connect() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(settings().database_url, min_size=1, max_size=10, init=_init_conn)
    return _pool


async def close():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    assert _pool is not None, "db.connect() 먼저"
    return _pool
