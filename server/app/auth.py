"""Supabase JWT → user_id. 앱은 Authorization: Bearer <supabase access token>."""
import uuid
import jwt
from fastapi import Depends, Header
from .config import settings
from .errors import AppError


def current_user_id(authorization: str | None = Header(default=None)) -> uuid.UUID:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AppError("UNAUTHENTICATED", "Authorization 헤더 없음")
    token = authorization.split(" ", 1)[1].strip()
    s = settings()

    # 로컬 개발: Bearer dev:<uuid>
    if s.auth_dev_bypass and token.startswith("dev:"):
        try:
            return uuid.UUID(token[4:])
        except ValueError:
            raise AppError("UNAUTHENTICATED", "dev 토큰 형식: dev:<uuid>")

    try:
        claims = jwt.decode(token, s.supabase_jwt_secret, algorithms=["HS256"], audience="authenticated")
    except jwt.ExpiredSignatureError:
        raise AppError("UNAUTHENTICATED", "로그인 만료")
    except jwt.InvalidTokenError as e:
        raise AppError("UNAUTHENTICATED", f"토큰 검증 실패: {type(e).__name__}")
    try:
        return uuid.UUID(claims["sub"])
    except (KeyError, ValueError):
        raise AppError("UNAUTHENTICATED", "sub 없음")


def verify_webhook_secret(x_webhook_secret: str | None = Header(default=None)) -> None:
    """소윤 쪽 DB 웹훅 → 내 서버. 비밀 헤더만 확인."""
    if not x_webhook_secret or x_webhook_secret != settings().webhook_secret:
        raise AppError("UNAUTHENTICATED", "웹훅 비밀 불일치")


UserId = Depends(current_user_id)
