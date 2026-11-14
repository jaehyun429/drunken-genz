"""주기 점검: 웹훅을 놓친 경험 찾아 질문 생성. DB 웹훅은 실패해도 재전송이 없다 (소윤 11/14)."""
from __future__ import annotations
import asyncio
import logging
from datetime import timedelta

from .. import db
from ..constants import SWEEP_INTERVAL_S, SWEEP_MIN_AGE_S, SWEEP_BATCH_LIMIT
from . import service

log = logging.getLogger("sweep")


async def sweep_missing_question_batches() -> int:
    """question_batch 가 하나도 없는 경험 → trigger='save' 로 생성 시작. 처리한 개수 반환."""
    pool = db.pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "select e.id, e.user_id from experience e "
            "where not exists (select 1 from question_batch b where b.experience_id = e.id) "
            "  and e.created_at < now() - $1::interval "
            "order by e.created_at limit $2",
            timedelta(seconds=SWEEP_MIN_AGE_S), SWEEP_BATCH_LIMIT)
        batches = []
        for r in rows:
            batches.append(await service.start_batch(conn, r["id"], r["user_id"], trigger="save"))
    for b in batches:
        await service.run_batch(b["id"])
    if batches:
        log.info("sweep: 질문 batch 없던 경험 %d개 처리", len(batches))
    return len(batches)


async def run_forever():
    """lifespan 에서 띄우는 루프. 서버 시작 직후가 아니라 한 주기 뒤부터."""
    while True:
        await asyncio.sleep(SWEEP_INTERVAL_S)
        try:
            await sweep_missing_question_batches()
        except Exception:  # noqa: BLE001 — 루프는 죽지 않는다
            log.exception("sweep 실패")
