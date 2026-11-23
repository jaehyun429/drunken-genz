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
from ..constants import (NOTIFICATION_KEEP_DAYS, PUSH_BATCH, PUSH_MAX_AGE_H, QUIET_END, QUIET_START, RELATED_NOTIFY_HOUR,
                         REMINDER_LATE_MAX_MIN, TZ)
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


# ---------- 새 관련 경험 알림 (하루 한 번 오전 9시) ----------

async def daily_related(now: datetime | None = None) -> int:
    """관련 경험이 새로 붙은 질문(related_new)을 사용자별로 모아 related_found 알림 하나를 만든다 (지은 11/14). 만든 알림 수 반환.
       - 1분마다 불리지만 한국 시간 9시대에만 일하고, 오늘 이미 받은 사람은 건너뛴다. 9시 뒤에 붙은 것은 다음 날 아침에 간다
       - count = 새로 붙은 경험 수. 경험 하나가 질문 여러 개에 맞아도 한 번만 센다
       - 지금 볼 수 없는 카드(비공개·삭제·차단)와 내 경험은 세지 않는다. 그래서 0개가 되면 알림을 만들지 않는다
       - 한 번 알린 질문은 related_notified_at 으로 표시해서 사용자가 안 열어 봐도 다시 알리지 않는다"""
    now = now or _now()
    if now.astimezone(KST).hour != RELATED_NOTIFY_HOUR:
        return 0
    key = group_key("related_found", "all", now)
    made = 0
    async with db.pool().acquire() as conn, conn.transaction():
        rows = await conn.fetch(
            "select q.user_id, array_agg(distinct q.id) as question_ids, count(distinct p.experience_id) as n "
            "from question q "
            "join related_cache c on c.question_id = q.id and c.filter = 'all' "
            "cross join lateral jsonb_array_elements(c.items) it "
            "join experience_public p on p.experience_id::text = it->>'experience_id' "
            "where q.related_new and q.related_notified_at is null and p.user_id <> q.user_id "
            "  and not exists (select 1 from block b where (b.user_id = q.user_id and b.blocked_id = p.user_id) "
            "                                         or (b.user_id = p.user_id and b.blocked_id = q.user_id)) "
            "  and not exists (select 1 from notification n where n.user_id = q.user_id and n.grouped_key = $1) "
            "group by q.user_id", key)
        for r in rows:
            qids = list(r["question_ids"])
            nid = await add(conn, r["user_id"], "related_found", {"question_ids": [str(q) for q in qids]}, key, bump=False)
            await conn.execute("update notification set count = $2 where id = $1", nid, r["n"])
            await conn.execute("update question set related_notified_at = $2 where id = any($1::uuid[])", qids, now)
            made += 1
    if made:
        log.info("새 관련 경험 알림 %d건", made)
    return made


# ---------- 리마인드 ----------

def reminder_due(cfg, now: datetime) -> datetime | None:
    """설정 한 개 {"days": [1,3,5], "time": "21:00"} 가 지금 보낼 차례면 그 예정 시각, 아니면 None.
       요일은 0=일 1=월 … 6=토. 7 도 일요일로 받는다 (앱이 어느 쪽으로 세든 맞게). 시간은 한국 시간.
       예정 시각부터 REMINDER_LATE_MAX_MIN 분 안에만 보낸다. 자정을 넘긴 경우를 위해 어제 것도 본다."""
    try:
        days = {int(d) % 7 for d in cfg["days"]}
        at = _hhmm(str(cfg["time"]))
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    local = now.astimezone(KST)
    for day in (local.date(), local.date() - timedelta(days=1)):
        due = datetime.combine(day, at, tzinfo=KST)
        if day.isoweekday() % 7 in days and due <= local < due + timedelta(minutes=REMINDER_LATE_MAX_MIN):
            return due
    return None


async def reminders(now: datetime | None = None) -> int:
    """진행 중 활동 리마인드. 사용자가 활동마다 고른 요일·시간(notification_setting.reminders)에 알림 한 건. 만든 수 반환.
       - 사용자가 직접 고른 활동만. 자동으로 켜지 않는다 (기획 "1117 수정" 알림 표)
       - 활동이 끝났거나 지워졌으면 건너뛴다. 리마인드 종류를 껐으면 알림함에도 만들지 않는다
       - 활동·날짜당 한 번 (묶음 키). 푸시는 dispatch_pending 이 보낸다
       - 예정 시각이 그 사람의 방해 금지 시간 안이면 알림함에만 쌓고 푸시는 보내지 않는다 (아침에 뒤늦게 보내지 않는다) TODO(기획)
       TODO(기획): 오늘 이미 회차 기록을 쓴 활동은 건너뛴다 (쓴 직후에 "기록을 남겨볼까요"가 오지 않게). 명세에 없다"""
    now = now or _now()
    today = now.astimezone(KST).date()
    made = 0
    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            "select s.user_id, e.id as experience_id, r.value as cfg, s.quiet_start, s.quiet_end "
            "from notification_setting s cross join lateral jsonb_each(s.reminders) r "
            "join experience e on e.id::text = r.key and e.user_id = s.user_id and e.status = 'in_progress' "
            "where jsonb_typeof(s.reminders) = 'object' and coalesce(s.types->>'reminder', 'true') <> 'false' "
            "  and not exists (select 1 from experience_log l where l.experience_id = e.id and l.log_date = $1)", today)
        for r in rows:
            due = reminder_due(r["cfg"], now)
            if due is None:
                continue
            key = group_key("reminder", r["experience_id"], due)
            if await conn.fetchval("select 1 from notification where user_id = $1 and grouped_key = $2", r["user_id"], key):
                continue
            nid = await add(conn, r["user_id"], "reminder", {"experience_id": str(r["experience_id"])}, key, bump=False)
            if in_quiet(due.timetz().replace(tzinfo=None), r["quiet_start"], r["quiet_end"]):
                await conn.execute("update notification set push_checked_at = $2 where id = $1", nid, now)
            made += 1
    if made:
        log.info("리마인드 알림 %d건", made)
    return made


# ---------- 정리 ----------

async def purge_old() -> int:
    """30일 지난 알림 삭제. Supabase 에서는 pg_cron 이 하지만 pg_cron 이 없는 곳에서도 지워지게 서버도 한다."""
    async with db.pool().acquire() as conn:
        res = await conn.execute("delete from notification where created_at < now() - $1::interval",
                                 timedelta(days=NOTIFICATION_KEEP_DAYS))
    return int(res.split()[-1])
