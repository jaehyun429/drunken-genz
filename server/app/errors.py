"""에러 형식: {"error": {"code", "message", "details"}}. 코드는 API 명세 0번 목록만 (CLAUDE.md 12장)."""
from fastapi import Request
from fastapi.responses import JSONResponse

# code → HTTP. 새 코드가 필요하면 명세 0번에 먼저 추가한다.
ERROR_HTTP = {
    "UNAUTHENTICATED": 401,
    "FORBIDDEN": 403,
    "NOT_FOUND": 404,
    "VALIDATION_FAILED": 400,
    "VERSION_CONFLICT": 409,
    "PUBLISH_PREVIEW_STALE": 409,
    "QUESTION_REGEN_LIMIT": 429,
    "QUESTION_SAVE_LIMIT": 429,
    "QA_DAILY_LIMIT": 429,
    "QA_PER_CARD_LIMIT": 429,
    "HELPFUL_ALREADY_SENT": 409,
    "PROFILE_VERIFICATION_REQUIRED": 403,
    "AI_UNAVAILABLE": 503,
    "INTERNAL": 500,
}


class AppError(Exception):
    def __init__(self, code: str, message: str = "", details: dict | None = None):
        assert code in ERROR_HTTP, f"명세에 없는 에러 코드: {code}"
        self.code, self.message, self.details = code, message, details or {}
        super().__init__(f"{code}: {message}")


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=ERROR_HTTP[exc.code],
                        content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}})


async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    # 사용자 글 원문이나 키가 섞일 수 있어 메시지는 타입만 (CLAUDE.md 2장 "로그에 사용자 글 원문을 남기지 않는다")
    return JSONResponse(status_code=500,
                        content={"error": {"code": "INTERNAL", "message": type(exc).__name__, "details": {}}})
