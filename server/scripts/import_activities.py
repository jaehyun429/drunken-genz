"""공식 활동 목록 CSV → official_activity (소윤). 넣은 것은 전부 verified (검색에 바로 나옴).

   cd server && python -m scripts.import_activities ../db/seed/official_activity.csv            # 로컬
   DATABASE_URL='<Supabase 연결 주소>' python -m scripts.import_activities <파일> --dry-run      # 검사만
   DATABASE_URL='<Supabase 연결 주소>' python -m scripts.import_activities <파일>                # 넣기

   CSV 칸 (첫 줄 머리글, UTF-8. 구글 시트 → 파일 → 다운로드 → CSV)
     name        필수. 2~40자. 공식 이름
     organizer   주최 (선택)
     period      기수·연도 (선택). "13기", "2026" 등. 기수마다 한 줄
     aliases     별칭 (선택). 최대 3개, | 로 구분. 줄임말은 여기 넣어야 찾힘. 예: 과준위|과학기술준비위
     genre       분야 (선택). 9개 중 하나, 글자 그대로
     type        유형 (선택). 7개 중 하나, 글자 그대로
     series_key  기수 묶음 (선택). 비우면 이름에서 띄어쓰기 뺀 글자 → 이름이 같으면 기수끼리 자동으로 묶임
     그 밖의 칸 (예: source_url 확인한 곳) 은 무시. 고르는 기준은 db/seed/README.md

   - 같은 이름(띄어쓰기·대소문자 무시) + 같은 기수가 이미 있으면 새로 안 만들고 나머지 칸을 덮어씀. 다시 돌려도 됨
   - 한 줄이라도 틀리면 몇 번째 줄이 왜 틀렸는지 출력하고 아무것도 안 넣음
   - CSV 에서 지운 줄은 DB 에서 안 지움 (기록이 이어져 있을 수 있음). 빼려면 대시보드에서 status 를 pending 으로"""
from __future__ import annotations

import asyncio
import csv
import os
import pathlib
import sys

import asyncpg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.activities import search_text  # noqa: E402

COLUMNS = ["name", "organizer", "period", "aliases", "genre", "type", "series_key"]

_UPSERT = """
insert into official_activity (name, organizer, period, aliases, genre, type, series_key, status)
values ($1, $2, $3, $4, $5, $6, $7, 'verified')
on conflict (activity_search_text(name, '{}'), coalesce(period, ''))
do update set name = excluded.name, organizer = excluded.organizer, aliases = excluded.aliases,
              genre = excluded.genre, type = excluded.type, series_key = excluded.series_key, status = 'verified'
returning (xmax = 0) as inserted
"""


def _rows(path: str) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:      # utf-8-sig: 엑셀이 붙이는 BOM 제거
        reader = csv.DictReader(f)
        missing = set(COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"머리글에 없는 칸: {', '.join(sorted(missing))}  (필요: {','.join(COLUMNS)})")
        return list(reader)


def _values(r: dict) -> tuple:
    def cell(k):
        v = " ".join((r.get(k) or "").split())
        return v or None
    name = cell("name") or ""
    aliases = [a for a in (" ".join(x.split()) for x in (r.get("aliases") or "").split("|")) if a]
    return (name, cell("organizer"), cell("period"), aliases, cell("genre"), cell("type"),
            cell("series_key") or search_text(name))


async def import_csv(path: str, dsn: str, dry_run: bool = False) -> tuple[int, int, list[str]]:
    """(새로 넣은 수, 덮어쓴 수, 오류 목록). 오류가 있거나 dry_run 이면 되돌림."""
    rows = _rows(path)
    added = updated = 0
    errors: list[str] = []
    conn = await asyncpg.connect(dsn)
    try:
        tx = conn.transaction()
        await tx.start()
        for line, r in enumerate(rows, start=2):                  # 1번 줄은 머리글
            try:
                async with conn.transaction():                     # 줄마다 savepoint — 틀린 줄만 건너뛰고 계속 검사
                    inserted = await conn.fetchval(_UPSERT, *_values(r))
                added, updated = (added + 1, updated) if inserted else (added, updated + 1)
            except asyncpg.PostgresError as e:
                errors.append(f"{line}번 줄 ({r.get('name') or '이름 없음'}): {_reason(e)}")
        if errors or dry_run:
            await tx.rollback()
        else:
            await tx.commit()
    finally:
        await conn.close()
    return added, updated, errors


def _reason(e: asyncpg.PostgresError) -> str:
    c = getattr(e, "constraint_name", None) or ""
    if "name" in c:
        return "이름은 2~40자"
    if "aliases" in c:
        return "별칭은 3개까지"
    if "genre" in c:
        return "분야가 9개 목록에 없음 (글자 그대로, 가운뎃점 ·)"
    if "type" in c:
        return "유형이 7개 목록에 없음 (글자 그대로, 가운뎃점 ·)"
    if isinstance(e, asyncpg.UniqueViolationError):
        return "같은 이름·같은 기수가 파일 안에 두 번 있음"
    return type(e).__name__


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        raise SystemExit(__doc__)
    dry = "--dry-run" in sys.argv
    dsn = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:54329/genz")
    added, updated, errors = asyncio.run(import_csv(args[0], dsn, dry))
    for e in errors:
        print("✗", e)
    if errors:
        raise SystemExit(f"오류 {len(errors)}개 — 아무것도 안 넣음")
    print(f"{'(검사만) ' if dry else ''}새로 {added}개, 덮어씀 {updated}개")


if __name__ == "__main__":
    main()
