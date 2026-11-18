"""공식 활동 CSV 넣기 (scripts/import_activities.py). 로컬 DB 가 있을 때만."""
import asyncio
import uuid

import asyncpg
import pytest
from test_integration_record_db import DB, _reachable

from scripts.import_activities import import_csv

pytestmark = pytest.mark.skipif(not _reachable(), reason="로컬 DB 없음")
HEAD = "name,organizer,period,aliases,genre,type,series_key\n"


def _rows(tok):
    async def go():
        conn = await asyncpg.connect(DB)
        try:
            return await conn.fetch("select name, organizer, period, aliases, genre, series_key, status "
                                    "from official_activity where name like $1 order by period", f"%{tok}%")
        finally:
            await conn.close()
    return asyncio.run(go())


def test_import_upserts_and_groups_series(tmp_path):
    tok = "zq" + uuid.uuid4().hex[:6]
    f = tmp_path / "a.csv"
    f.write_text("﻿" + HEAD                                   # 엑셀 BOM
                 + f"서포터즈 {tok},주최A,12기,서포|서폿,경영·마케팅,대외활동,\n"
                 + f"서포터즈 {tok},주최A,13기,,경영·마케팅,대외활동,\n", encoding="utf-8")
    assert asyncio.run(import_csv(str(f), DB)) == (2, 0, [])
    rows = _rows(tok)
    assert [r["period"] for r in rows] == ["12기", "13기"]
    assert rows[0]["aliases"] == ["서포", "서폿"] and rows[0]["status"] == "verified"
    assert rows[0]["series_key"] == rows[1]["series_key"] == f"서포터즈{tok}"     # 비우면 이름으로 묶임

    f.write_text(HEAD + f"서포터즈{tok},주최B,13기,,,,\n", encoding="utf-8")    # 띄어쓰기 달라도 같은 행
    assert asyncio.run(import_csv(str(f), DB)) == (0, 1, [])
    assert _rows(tok)[1]["organizer"] == "주최B" and len(_rows(tok)) == 2


def test_bad_rows_reported_and_nothing_written(tmp_path):
    tok = "zq" + uuid.uuid4().hex[:6]
    f = tmp_path / "b.csv"
    f.write_text(HEAD
                 + f"괜찮은 활동 {tok},,,,,,\n"
                 + f"분야 틀림 {tok},,,,알바,,\n"
                 + f"별칭 많음 {tok},,,a|b|c|d,,,\n"
                 + "가,,,,,,\n", encoding="utf-8")
    added, updated, errors = asyncio.run(import_csv(str(f), DB))
    assert [e.split(" ")[0] for e in errors] == ["3번", "4번", "5번"]
    assert "분야" in errors[0] and "별칭" in errors[1] and "2~40자" in errors[2]
    assert _rows(tok) == []                                         # 한 줄도 안 들어감


def test_dry_run_writes_nothing(tmp_path):
    tok = "zq" + uuid.uuid4().hex[:6]
    f = tmp_path / "c.csv"
    f.write_text(HEAD + f"검사만 {tok},,,,,,\n", encoding="utf-8")
    assert asyncio.run(import_csv(str(f), DB, dry_run=True)) == (1, 0, [])
    assert _rows(tok) == []
