"""FastAPI 진입점. uvicorn app.main:app --reload"""
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI

from . import db
from .errors import AppError, app_error_handler, unhandled_error_handler
from .questions.router import router as questions_router
from .embedding.router import router as embedding_router
from .related.router import router as related_router
from .analysis.router import router as analysis_router
from .questions import sweep
from .notifications import scheduler as notify_scheduler
from .activities import router as activities_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await db.connect()
    task = asyncio.create_task(sweep.run_forever())   # 웹훅 놓친 경험 주기 점검
    notify_task = asyncio.create_task(notify_scheduler.run_forever())   # 밀린 푸시, 오래된 알림 삭제
    yield
    task.cancel()
    notify_task.cancel()
    await db.close()


app = FastAPI(title="genz-server (재현)", version="0.1.0", lifespan=lifespan)
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)
# 베이스 URL 은 https://api.<도메인>/v1 (API 명세 2부)
app.include_router(questions_router, prefix="/v1")
app.include_router(embedding_router, prefix="/v1")
app.include_router(related_router, prefix="/v1")
app.include_router(activities_router, prefix="/v1")   # 소윤
app.include_router(analysis_router, prefix="/v1")


@app.get("/v1/health")
async def health():
    return {"ok": True}
