"""Supabase JWT → user_id. 앱은 Authorization: Bearer <supabase access token>.

서명 방식 두 가지를 다 받는다. 어느 쪽인지는 토큰 머리의 alg 로 고른다.
- ES256·RS256: Supabase 가 공개하는 공개키(JWKS)로 검증한다. 서버에 비밀값이 필요 없다.
  11/16: 새 프로젝트의 Current 서명 키가 ECC(P-256) 라서 앱 토큰은 이쪽으로 온다
- HS256: 예전 방식. SUPABASE_JWT_SECRET 을 넣어 둔 서버만 받는다
키를 돌려도(HS256 ↔ ECC) 서버를 고치지 않게 둘 다 둔다."""
import hmac
import logging
import threading
import time
import uuid
from urllib.parse import unquote

import httpx
import jwt
from fastapi import Depends, Header

from .config import settings
from .constants import (AUTH_JWKS_ALGS, AUTH_JWKS_REFETCH_MIN_S, AUTH_JWKS_TIMEOUT_S, AUTH_JWKS_TTL_S,
                        AUTH_JWT_AUDIENCE, AUTH_JWT_LEEWAY_S)
from .errors import AppError

log = logging.getLogger("auth")

_PLACEHOLDER = "change-me"   # .env.example 에 있던 예시 값. 이 값으로 서명한 토큰을 받으면 누구나 위조할 수 있다


class JwksUnavailable(Exception):
    """공개키를 한 번도 받지 못했다. 토큰이 틀린 것이 아니라 서버가 확인을 못 하는 상태."""


def _fetch_jwks(url: str) -> dict:
    r = httpx.get(url, timeout=AUTH_JWKS_TIMEOUT_S)
    r.raise_for_status()
    return r.json()


class _Jwks:
    """공개키 묶음을 메모리에 담아 둔다. 요청마다 받지 않는다.
       - 담아 둔 지 AUTH_JWKS_TTL_S 가 지나면 다시 받는다 (폐기된 키를 계속 믿지 않게)
       - 모르는 kid 가 오면 다시 받는다 (키를 돌린 직후). 단 AUTH_JWKS_REFETCH_MIN_S 에 한 번까지만.
         아무 kid 나 넣은 토큰으로 Supabase 를 계속 부르게 만들 수 없게
       - 다시 받기에 실패하면 가지고 있던 키를 그대로 쓴다"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._url: str | None = None
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None   # 마지막으로 받는 데 성공한 때
        self._tried_at: float | None = None     # 마지막으로 받으려 한 때 (실패 포함)

    def reset(self) -> None:
        with self._lock:
            self._url, self._keys, self._fetched_at, self._tried_at = None, {}, None, None

    def _fresh(self, now: float) -> bool:
        return self._fetched_at is not None and now - self._fetched_at < AUTH_JWKS_TTL_S

    def key(self, url: str, kid: str) -> "jwt.PyJWK | None":
        now = time.monotonic()
        if url == self._url and self._fresh(now):
            hit = self._keys.get(kid)
            if hit is not None:
                return hit
        with self._lock:
            now = time.monotonic()
            if url != self._url:
                self._url, self._keys, self._fetched_at, self._tried_at = url, {}, None, None
            # 기다리는 사이 다른 요청이 이미 받아 왔을 수 있다
            need = kid not in self._keys or not self._fresh(now)
            may_try = self._tried_at is None or now - self._tried_at >= AUTH_JWKS_REFETCH_MIN_S
            if need and may_try:
                self._refresh(url, now)
            if self._fetched_at is None:
                raise JwksUnavailable(url)
            return self._keys.get(kid)

    def _refresh(self, url: str, now: float) -> None:
        self._tried_at = now
        try:
            items = _fetch_jwks(url)["keys"]
            if not isinstance(items, list):
                raise TypeError("keys 가 목록이 아님")
        except Exception as e:   # 연결 실패, 4xx·5xx, JSON 아님, 모양 다름. 무엇이든 요청을 죽이지 않고 가진 키로 버틴다
            log.warning("JWKS 받기 실패: %s", type(e).__name__)
            return
        keys: dict[str, jwt.PyJWK] = {}
        for item in items:
            try:
                k = jwt.PyJWK.from_dict(item)
            except Exception:   # 모르는 종류·깨진 키 하나 때문에 나머지 키까지 버리지 않는다
                continue
            if k.key_id and k.public_key_use in (None, "sig") and k.algorithm_name in AUTH_JWKS_ALGS:
                keys[k.key_id] = k
        self._keys, self._fetched_at = keys, now


_jwks = _Jwks()


def _decode(token: str) -> dict:
    s = settings()
    base = s.supabase_url.strip().rstrip("/")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError:
        raise AppError("UNAUTHENTICATED", "토큰 형식이 아님")
    alg = header.get("alg")
    checks = {"audience": AUTH_JWT_AUDIENCE, "leeway": AUTH_JWT_LEEWAY_S, "options": {"require": ["exp", "sub"]},
              "issuer": f"{base}/auth/v1" if base else None}

    if alg in AUTH_JWKS_ALGS:
        if not base:
            log.error("SUPABASE_URL 이 없어 %s 토큰을 확인할 수 없음", alg)
            raise AppError("INTERNAL", "SUPABASE_URL 미설정")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise AppError("UNAUTHENTICATED", "토큰에 kid 없음")
        try:
            jwk = _jwks.key(f"{base}/auth/v1/.well-known/jwks.json", kid)
        except JwksUnavailable:
            raise AppError("INTERNAL", "Supabase 공개키를 받지 못함")
        # 머리의 alg 와 공개키의 종류가 같아야 한다 (EC 키에 RS256 을 붙인 토큰 같은 것)
        if jwk is None or jwk.algorithm_name != alg:
            raise AppError("UNAUTHENTICATED", "모르는 서명 키")
        return jwt.decode(token, jwk.key, algorithms=[alg], **checks)

    if alg == "HS256":
        secret = s.supabase_jwt_secret
        if not secret or secret == _PLACEHOLDER:
            raise AppError("UNAUTHENTICATED", "HS256 토큰은 받지 않음")
        return jwt.decode(token, secret, algorithms=["HS256"], **checks)

    raise AppError("UNAUTHENTICATED", "지원하지 않는 서명 방식")


def demo_accounts() -> dict[str, uuid.UUID]:
    """DEMO_ACCOUNTS="이름:uuid,이름:uuid" → {이름: uuid}. 모양이 틀린 항목은 버린다."""
    out: dict[str, uuid.UUID] = {}
    for item in settings().demo_accounts.split(","):
        name, _, raw = item.strip().rpartition(":")
        try:
            if name.strip():
                out[name.strip()] = uuid.UUID(raw.strip())
        except ValueError:
            continue
    return out


def current_user_id(authorization: str | None = Header(default=None)) -> uuid.UUID:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AppError("UNAUTHENTICATED", "Authorization 헤더 없음")
    token = authorization.split(" ", 1)[1].strip()

    # 로컬 개발: Bearer dev:<uuid>
    if settings().auth_dev_bypass and token.startswith("dev:"):
        try:
            return uuid.UUID(token[4:])
        except ValueError:
            raise AppError("UNAUTHENTICATED", "dev 토큰 형식: dev:<uuid>")

    # 기능 확인용 페이지: Bearer demo:<비밀번호>:<계정 이름>. DEMO_PASSWORD 를 넣은 서버에서만 받는다.
    # HTTP 헤더에는 한글을 못 실어서 비밀번호와 이름은 URL 인코딩(encodeURIComponent)해서 온다
    if settings().demo_password and token.startswith("demo:"):
        password, _, name = token[5:].rpartition(":")
        password, name = unquote(password), unquote(name)
        user = demo_accounts().get(name)
        if user is None or not hmac.compare_digest(password.encode(), settings().demo_password.encode()):
            raise AppError("UNAUTHENTICATED", "확인용 계정이 아님")
        return user

    try:
        claims = _decode(token)
    except jwt.ExpiredSignatureError:
        raise AppError("UNAUTHENTICATED", "로그인 만료")
    except jwt.InvalidTokenError as e:
        raise AppError("UNAUTHENTICATED", f"토큰 검증 실패: {type(e).__name__}")
    try:
        return uuid.UUID(claims["sub"])
    except (KeyError, ValueError, TypeError, AttributeError):
        raise AppError("UNAUTHENTICATED", "sub 없음")


def verify_webhook_secret(x_webhook_secret: str | None = Header(default=None)) -> None:
    """소윤 쪽 DB 웹훅 → 내 서버. 비밀 헤더만 확인."""
    if not x_webhook_secret or x_webhook_secret != settings().webhook_secret:
        raise AppError("UNAUTHENTICATED", "웹훅 비밀 불일치")


UserId = Depends(current_user_id)
