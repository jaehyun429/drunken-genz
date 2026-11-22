"""알림 만들기와 푸시 내보내기 (CLAUDE.local.md 9장, API 명세 1.4·3장, 이슈 #18). 모든 DB 접근은 여기.
   앱은 notification 을 Supabase 에서 직접 읽는다. 서버는 행을 만들고 푸시를 보낸다.
   행은 두 군데서 생긴다: DB 트리거(Q&A)와 FastAPI(관련 경험, 리마인드). 어디서 생겼든 푸시는 dispatch_pending 이 본다."""
from __future__ import annotations
import logging
import uuid
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import asyncpg

from .. import db
from ..constants import (NOTIFICATION_KEEP_DAYS, PUSH_BATCH, PUSH_MAX_AGE_H, QUIET_END, QUIET_START, TZ)
from . import push, text

log = logging.getLogger("notify")
KST = ZoneInfo(TZ)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hhmm(s: str) -> time:
    h, m = s.split(":")[:2]
    return time(int(h), int(m))


def in_quiet(t: time, start: time, end: time) -> bool:
    """방해 금지 시간 안인가. 22:00~08:00 처럼 자정을 넘는 구간도 된다. 시작과 끝이 같으면 방해 금지 없음."""
    if start == end:
        return False
    if start < end:
        return start <= t < end
    return t >= start or t < end


def group_key(type_: str, experience_id, now: datetime | None = None) -> str:
    """묶음 키 `종류:경험id:한국날짜`. 같은 종류·같은 경험·같은 날이면 한 행으로 묶인다."""
    day = (now or _now()).astimezone(KST).strftime("%Y-%m-%d")
    return f"{type_}:{experience_id}:{day}"


async def add(conn: asyncpg.Connection, user_id: uuid.UUID, type_: str, payload: dict,
              grouped_key: str | None = None, bump: bool = True) -> uuid.UUID:
    """알림 한 건. 묶음 키가 같으면 count 만 올린다 (bump=False 면 이미 있을 때 그대로 둔다).
       푸시는 여기서 보내지 않는다. dispatch_pending 이 방해 금지와 설정을 보고 보낸다.
       차단한 사람의 활동으로 생기는 알림인지는 부르는 쪽이 먼저 거른다."""
    return await conn.fetchval("select notification_add($1, $2, $3::jsonb, $4, $5)", user_id, type_, payload, grouped_key, bump)


# ---------- 푸시 내보내기 ----------

async def dispatch_pending(now: datetime | None = None) -> dict:
    """아직 푸시 여부를 정하지 않은 알림을 본다. 1분마다 돈다.
       - 지금이 그 사람의 방해 금지 시간이면 건드리지 않는다 (알림함에는 이미 쌓여 있다)
       - 방해 금지 시간에 생긴 알림이 둘 이상 밀려 있으면 "밤사이 알림 N개" 하나로 보낸다 (방해 금지가 끝난 뒤 첫 차례 = 아침 8시)
       - 그 밖에는 한 건씩. 묶음 행은 처음 만들어질 때 한 번만 여기 걸리므로 "첫 건만 푸시"가 된다
       - 푸시를 전체로 껐거나 그 종류를 껐거나 등록된 기기가 없으면 보내지 않고 본 것으로만 표시한다
       돌려주는 것: {"sent": 보낸 메시지 수, "checked": 처리한 알림 수, "held": 방해 금지로 미룬 알림 수}"""
    now = now or _now()
    out = {"sent": 0, "checked": 0, "held": 0}
    async with db.pool().acquire() as conn, conn.transaction():
        # 서버가 여러 대여도 같은 알림을 두 번 보내지 않게 잠그고 가져온다
        rows = await conn.fetch(
            "select id, user_id, type, payload, count, created_at from notification "
            "where push_checked_at is null order by created_at limit $1 for update skip locked", PUSH_BATCH)
        by_user: dict[uuid.UUID, list[asyncpg.Record]] = {}
        for r in rows:
            by_user.setdefault(r["user_id"], []).append(r)
        for user_id, mine in by_user.items():
            res = await _dispatch_user(conn, user_id, mine, now)
            for k in out:
                out[k] += res[k]
    if out["checked"] or out["sent"]:
        log.info("푸시: 메시지 %d개, 알림 %d건 처리, %d건은 방해 금지로 미룸", out["sent"], out["checked"], out["held"])
    return out


async def _dispatch_user(conn: asyncpg.Connection, user_id: uuid.UUID, rows: list, now: datetime) -> dict:
    st = await conn.fetchrow("select push_enabled, types, quiet_start, quiet_end from notification_setting "
                             "where user_id = $1", user_id)
    enabled = st["push_enabled"] if st else True
    types = (st["types"] if st else None) or {}                 # 없는 키는 켬 (1117 수정 표: 기본 전부 켬)
    q_start = st["quiet_start"] if st else _hhmm(QUIET_START)
    q_end = st["quiet_end"] if st else _hhmm(QUIET_END)
    if in_quiet(now.astimezone(KST).time(), q_start, q_end):
        return {"sent": 0, "checked": 0, "held": len(rows)}

    # TODO(기획): 종류를 끈 알림을 알림함에도 안 쌓을지. 지금은 푸시만 안 보낸다
    fresh = [r for r in rows if r["created_at"] >= now - timedelta(hours=PUSH_MAX_AGE_H)]   # 오래 밀린 것은 이제 와서 보내지 않는다
    wanted = [r for r in fresh if enabled and types.get(r["type"]) is not False]
    tokens = [d["token"] for d in await conn.fetch("select token from push_device where user_id = $1", user_id)] if wanted else []
    night = [r for r in wanted if in_quiet(r["created_at"].astimezone(KST).time(), q_start, q_end)]
    bodies: list[tuple[str, dict]] = []                                                    # (문구, data)
    if len(night) >= 2:
        bodies.append((text.digest(len(night)), {"type": "digest", "count": len(night)}))
        singles = [r for r in wanted if r not in night]
    else:
        singles = wanted
    for r in singles:
        bodies.append((text.body(r["type"], r["count"], await _activity_name(conn, user_id, r["payload"])),
                       {"notification_id": str(r["id"]), "type": r["type"], "payload": r["payload"]}))
    messages = [{"to": t, "body": b, "data": d, "sound": "default"} for b, d in bodies for t in tokens]

    sent = 0
    if messages:
        try:
            tickets = await push.send(messages)
        except push.PushError as err:
            log.warning("Expo 전송 실패 (%s). 다음 차례에 다시 본다", err)
            return {"sent": 0, "checked": 0, "held": 0}
        dead = push.dead_tokens(messages, tickets)
        if dead:
            await conn.execute("delete from push_device where user_id = $1 and token = any($2::text[])", user_id, dead)
        sent = len(messages)
    pushed = [r["id"] for r in wanted] if messages else []
    await conn.execute("update notification set push_checked_at = $2 where id = any($1::uuid[])", [r["id"] for r in rows], now)
    if pushed:
        await conn.execute("update notification set pushed_at = $2 where id = any($1::uuid[])", pushed, now)
    return {"sent": sent, "checked": len(rows), "held": 0}


async def _activity_name(conn: asyncpg.Connection, user_id: uuid.UUID, payload) -> str | None:
    """문구에 넣을 활동 이름. 받는 사람 본인의 경험일 때만 (남의 활동 이름을 푸시에 싣지 않는다)."""
    eid = (payload or {}).get("experience_id") if isinstance(payload, dict) else None
    try:
        eid = uuid.UUID(str(eid)) if eid else None
    except ValueError:
        eid = None
    if eid is None:
        return None
    return await conn.fetchval("select activity_name from experience where id = $1 and user_id = $2", eid, user_id)


# ---------- 정리 ----------

async def purge_old() -> int:
    """30일 지난 알림 삭제. Supabase 에서는 pg_cron 이 하지만 pg_cron 이 없는 곳에서도 지워지게 서버도 한다."""
    async with db.pool().acquire() as conn:
        res = await conn.execute("delete from notification where created_at < now() - $1::interval",
                                 timedelta(days=NOTIFICATION_KEEP_DAYS))
    return int(res.split()[-1])
