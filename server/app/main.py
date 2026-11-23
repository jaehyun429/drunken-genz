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
from .demo.router import router as demo_router
from .config import settings
from .notifications import scheduler as notify_scheduler

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
app.include_router(analysis_router, prefix="/v1")
app.include_router(demo_router)   # 기능 확인용 페이지. DEMO_PASSWORD 가 없으면 전부 404
if settings().demo_password:
    # 확인용 페이지를 다른 주소(Vercel 등)에 올려도 이 서버를 부를 수 있게. 쿠키는 안 쓰고 Authorization 헤더만 쓴다
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/v1/health")
async def health():
    return {"ok": True}
