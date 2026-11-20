"""공식 활동 CSV 넣기 (scripts/import_activities.py). 로컬 DB 가 있을 때만."""
import asyncio
import uuid

import asyncpg
import pytest
from test_integration_record_db import DB, _reachable

from scripts.import_activities import import_csv

pytestmark = pytest.mark.skipif(not _reachable(), reason="로컬 DB 없음")
HEAD = "name,organizer,aliases,genre,type,source_url\n"


def _rows(tok):
    async def go():
        conn = await asyncpg.connect(DB)
        try:
            return await conn.fetch("select name, organizer, aliases, genre, status "
                                    "from official_activity where name like $1 order by name", f"%{tok}%")
        finally:
            await conn.close()
    return asyncio.run(go())


def test_import_upserts_by_name(tmp_path):
    tok = "zq" + uuid.uuid4().hex[:6]
    f = tmp_path / "a.csv"
    f.write_text("﻿" + HEAD                                   # 엑셀 BOM
                 + f"서포터즈 {tok},주최A,서포|서폿,경영·마케팅,대외활동,https://a\n"
                 + f"기자단 {tok},주최B,,,,\n", encoding="utf-8")
    assert asyncio.run(import_csv(str(f), DB)) == (2, 0, [])
    sup = {r["name"]: r for r in _rows(tok)}[f"서포터즈 {tok}"]
    assert sup["aliases"] == ["서포", "서폿"] and sup["status"] == "verified"

    f.write_text(HEAD + f"서포터즈{tok},주최C,,,,\n", encoding="utf-8")    # 띄어쓰기 달라도 같은 행
    assert asyncio.run(import_csv(str(f), DB)) == (0, 1, [])
    rows = {r["organizer"] for r in _rows(tok)}
    assert rows == {"주최B", "주최C"}


def test_bad_rows_reported_and_nothing_written(tmp_path):
    tok = "zq" + uuid.uuid4().hex[:6]
    f = tmp_path / "b.csv"
    f.write_text(HEAD
                 + f"괜찮은 활동 {tok},,,,,\n"
                 + f"분야 틀림 {tok},,,알바,,\n"
                 + f"별칭 많음 {tok},,a|b|c|d,,,\n"
                 + "가,,,,,\n"
                 + f"괜찮은 활동{tok},,,,,\n", encoding="utf-8")            # 같은 이름 두 번
    _, updated, errors = asyncio.run(import_csv(str(f), DB))
    assert [e.split(" ")[0] for e in errors] == ["3번", "4번", "5번"]
    assert "분야" in errors[0] and "별칭" in errors[1] and "2~40자" in errors[2]
    assert updated == 1                                             # 같은 이름은 덮어쓰기로 셈 (오류 아님)
    assert _rows(tok) == []                                         # 한 줄도 안 들어감


def test_dry_run_writes_nothing(tmp_path):
    tok = "zq" + uuid.uuid4().hex[:6]
    f = tmp_path / "c.csv"
    f.write_text(HEAD + f"검사만 {tok},,,,,\n", encoding="utf-8")
    assert asyncio.run(import_csv(str(f), DB, dry_run=True)) == (1, 0, [])
    assert _rows(tok) == []
