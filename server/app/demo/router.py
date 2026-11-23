"""기능 확인용 페이지 (기획·디자인이 서버 기능을 눌러 보는 용도). 실제 앱이 아니다.
   DEMO_PASSWORD 를 넣은 서버에서만 열린다. 넣지 않으면 여기 있는 주소는 전부 404.

   실제 앱은 내 기록·질문·알림을 Supabase 에서 직접 읽고 쓴다. 이 페이지에는 Supabase 로그인이 없어서,
   앱이 직접 하는 부분(기록 저장, 목록 읽기, 담기)만 여기 /demo/* 로 대신한다.
   AI 가 도는 부분(질문 다시 만들기, 더 적어보기 답, 분석, 분석 응답)은 페이지가 실제 엔드포인트를 그대로 부른다."""
from __future__ import annotations
import hmac
import pathlib
import uuid
from datetime import date

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .. import db
from ..auth import current_user_id, demo_accounts
from ..config import settings
from ..errors import AppError
from ..notifications import service as notify
from ..questions import service as questions

router = APIRouter(tags=["demo"])
_PAGE = pathlib.Path(__file__).with_name("page.html")


def _enabled() -> None:
    if not settings().demo_password:
        raise AppError("NOT_FOUND", "없는 주소")


@router.get("/demo", response_class=HTMLResponse, dependencies=[Depends(_enabled)], include_in_schema=False)
async def page():
    return HTMLResponse(_PAGE.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


class LoginIn(BaseModel):
    password: str


@router.post("/v1/demo/login", dependencies=[Depends(_enabled)])
async def login(body: LoginIn):
    """비밀번호가 맞으면 확인용 계정 이름들을 준다. 페이지는 `Bearer demo:<비밀번호>:<이름>` 으로 다른 주소를 부른다 (둘 다 URL 인코딩)."""
    if not hmac.compare_digest(body.password.encode(), settings().demo_password.encode()):
        raise AppError("UNAUTHENTICATED", "비밀번호가 다름")
    return {"accounts": list(demo_accounts())}


class ExperienceIn(BaseModel):
    status: str = "done"
    activity_name: str
    genre: str
    type: str | None = None
    role: str
    period_start: str
    period_end: str | None = None
    did_chips: list[str]
    did_notes: dict[str, str] = Field(default_factory=dict)
    conditions: dict[str, str] = Field(default_factory=dict)
    situation_chips: list[str] = Field(default_factory=list)
    emotion_chips: list[str] = Field(default_factory=list)
    one_liner: str | None = None


def _clean(xs: list[str]) -> list[str]:
    return [x.strip() for x in xs if x and x.strip()]


@router.post("/v1/demo/experiences", status_code=201, dependencies=[Depends(_enabled)])
async def save_experience(body: ExperienceIn, bg: BackgroundTasks, user_id: uuid.UUID = Depends(current_user_id)):
    """기록 저장 (실제 앱에서는 앱이 Supabase 에 직접 넣는다). 글자 수·개수 제한은 DB 가 검사한다.
       저장하면 실제와 같이 질문 생성을 시작한다 (DB 웹훅이 먼저 시작했으면 건너뜀)."""
    did = _clean(body.did_chips)
    async with db.pool().acquire() as conn:
        try:
            eid = await conn.fetchval(
                "insert into experience (user_id, status, activity_name, genre, type, role, period_start, period_end, "
                " did_chips, did_notes, conditions, situation_chips, emotion_chips, one_liner) "
                "values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11::jsonb, $12, $13, $14) returning id",
                user_id, body.status, body.activity_name.strip(), body.genre, body.type or None, body.role.strip(),
                body.period_start, body.period_end or None, did,
                {k: v.strip() for k, v in body.did_notes.items() if k in did and v.strip()},
                {k: v for k, v in body.conditions.items() if v}, _clean(body.situation_chips), _clean(body.emotion_chips),
                (body.one_liner or "").strip() or None)
        except (asyncpg.CheckViolationError, asyncpg.NotNullViolationError) as err:
            raise AppError("VALIDATION_FAILED", "기록이 DB 제한에 걸림", {"constraint": err.constraint_name or err.column_name})
        if not await conn.fetchval("select 1 from question_batch where experience_id = $1 limit 1", eid):
            batch = await questions.start_batch(conn, eid, user_id, trigger="save")
            bg.add_task(questions.run_batch, batch["id"])
    return {"id": str(eid)}


class LogIn(BaseModel):
    log_date: date | None = None
    situation_chips: list[str]
    emotion_chip: str
    one_liner: str | None = None


@router.post("/v1/demo/experiences/{experience_id}/logs", status_code=201, dependencies=[Depends(_enabled)])
async def add_log(experience_id: uuid.UUID, body: LogIn, user_id: uuid.UUID = Depends(current_user_id)):
    """회차 기록 (진행 중 활동에만). 분석에서는 회차 기록도 기록 하나로 센다."""
    async with db.pool().acquire() as conn:
        status = await conn.fetchval("select status from experience where id = $1 and user_id = $2", experience_id, user_id)
        if status is None:
            raise AppError("NOT_FOUND", "경험 없음")
        if status != "in_progress":
            raise AppError("VALIDATION_FAILED", "진행 중 활동에만 회차 기록을 남길 수 있음", {"field": "status"})
        try:
            await conn.execute(
                "insert into experience_log (id, experience_id, user_id, log_date, situation_chips, emotion_chips, one_liner) "
                "values ($1, $2, $3, coalesce($4, current_date), $5, $6, $7)", uuid.uuid4(), experience_id, user_id,
                body.log_date, _clean(body.situation_chips), _clean([body.emotion_chip]), (body.one_liner or "").strip() or None)
        except asyncpg.CheckViolationError as err:
            raise AppError("VALIDATION_FAILED", "회차 기록이 DB 제한에 걸림", {"constraint": err.constraint_name})
    return {"ok": True}


@router.delete("/v1/demo/experiences/{experience_id}", dependencies=[Depends(_enabled)])
async def delete_experience(experience_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user_id)):
    async with db.pool().acquire() as conn:
        res = await conn.execute("delete from experience where id = $1 and user_id = $2", experience_id, user_id)
    if res.endswith(" 0"):
        raise AppError("NOT_FOUND", "경험 없음")
    return {"ok": True}


class SaveIn(BaseModel):
    saved: bool


@router.post("/v1/demo/questions/{question_id}/save", dependencies=[Depends(_enabled)])
async def save_question(question_id: uuid.UUID, body: SaveIn, user_id: uuid.UUID = Depends(current_user_id)):
    """질문 담기 (실제 앱에서는 앱이 `question.saved` 를 직접 바꾼다). 더 적어보기는 담을 수 없다 (DB 제약)."""
    async with db.pool().acquire() as conn:
        try:
            res = await conn.execute("update question set saved = $3 where id = $1 and user_id = $2", question_id, user_id, body.saved)
        except (asyncpg.CheckViolationError, asyncpg.RaiseError) as err:
            raise AppError("VALIDATION_FAILED", "담을 수 없는 질문", {"reason": str(err)[:80]})
    if res.endswith(" 0"):
        raise AppError("NOT_FOUND", "질문 없음")
    return {"ok": True}


@router.post("/v1/demo/experiences/{experience_id}/fake-question-notification", dependencies=[Depends(_enabled)])
async def fake_question_notification(experience_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user_id)):
    """'내 카드에 새 질문' 알림을 하나 흉내 낸다 (Q&A 가 아직 없어서). 같은 날 여러 번 누르면 한 행으로 묶이고 개수만 오른다."""
    async with db.pool().acquire() as conn:
        if not await conn.fetchval("select 1 from experience where id = $1 and user_id = $2", experience_id, user_id):
            raise AppError("NOT_FOUND", "경험 없음")
        await notify.add(conn, user_id, "new_question", {"experience_id": str(experience_id), "demo": True},
                         notify.group_key("new_question", experience_id))
    return {"ok": True}


def _iso(v):
    return v.isoformat() if v is not None else None


@router.get("/v1/demo/state", dependencies=[Depends(_enabled)])
async def state(user_id: uuid.UUID = Depends(current_user_id)):
    """내 기록, 기록마다 질문 생성 상태와 질문, 알림함. 실제 앱은 이걸 Supabase 에서 직접 읽는다."""
    async with db.pool().acquire() as conn:
        exps = await conn.fetch("select * from experience where user_id = $1 order by created_at desc", user_id)
        logs = await conn.fetch("select * from experience_log where user_id = $1 order by log_date, created_at", user_id)
        batches = await conn.fetch(
            "select distinct on (experience_id) * from question_batch where user_id = $1 "
            "order by experience_id, generation desc", user_id)
        qs = await conn.fetch("select * from question where user_id = $1 order by created_at, id", user_id)
        notis = await conn.fetch("select * from notification where user_id = $1 order by created_at desc limit 30", user_id)
    batch_of = {b["experience_id"]: b for b in batches}
    out = []
    for e in exps:
        b = batch_of.get(e["id"])
        out.append({
            "id": str(e["id"]), "status": e["status"], "activity_name": e["activity_name"], "genre": e["genre"], "type": e["type"],
            "role": e["role"], "period_start": e["period_start"], "period_end": e["period_end"],
            "did_chips": list(e["did_chips"]), "did_notes": e["did_notes"], "conditions": e["conditions"],
            "situation_chips": list(e["situation_chips"]), "emotion_chips": list(e["emotion_chips"]), "one_liner": e["one_liner"],
            "logs": [{"log_date": str(lg["log_date"]), "situation_chips": list(lg["situation_chips"]),
                      "emotion_chips": list(lg["emotion_chips"]), "one_liner": lg["one_liner"]}
                     for lg in logs if lg["experience_id"] == e["id"]],
            "batch": None if b is None else {"status": b["status"], "failure_reason": b["failure_reason"], "generation": b["generation"],
                                             "no_new": b.get("no_new"), "model": b["model"], "prompt_version": b["prompt_version"]},
            "questions": [{"id": str(q["id"]), "type": q["type"], "axis": q["axis"], "text": q["text"], "source": q["source"],
                           "saved": q["saved"], "is_done": q["is_done"], "answer": q.get("answer")}
                          for q in qs if q["experience_id"] == e["id"]],
        })
    orphans = [{"id": str(q["id"]), "type": q["type"], "text": q["text"], "source_activity_name": q["source_activity_name"]}
               for q in qs if q["experience_id"] is None]      # 기록은 지워졌지만 담아 둬서 남은 질문
    return {"experiences": out, "saved_without_experience": orphans,
            "notifications": [{"id": str(n["id"]), "type": n["type"], "count": n["count"], "read": n["read"], "payload": n["payload"],
                               "created_at": _iso(n["created_at"]), "pushed_at": _iso(n["pushed_at"])} for n in notis]}
