"""알림 주기 작업. lifespan 에서 띄운다. 1분마다 9시 관련 경험 알림·리마인드를 만들고 밀린 푸시를 보고, 한 시간마다 오래된 알림을 지운다."""
from __future__ import annotations
import asyncio
import logging

from ..constants import NOTIFY_PURGE_EVERY_TICKS, NOTIFY_TICK_S
from . import service

log = logging.getLogger("notify")


async def run_forever():
    tick = 0
    while True:
        await asyncio.sleep(NOTIFY_TICK_S)
        tick += 1
        jobs = [service.daily_related, service.reminders, service.dispatch_pending] + ([service.purge_old] if tick % NOTIFY_PURGE_EVERY_TICKS == 0 else [])
        for job in jobs:
            try:
                await job()
            except Exception:  # noqa: BLE001 — 루프는 죽지 않는다
                log.exception("알림 작업 실패: %s", job.__name__)
