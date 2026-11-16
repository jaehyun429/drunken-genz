"""앱 토큰 검증 (app/auth.py). DB·네트워크 없이 돈다.
   시험용 키 쌍으로 토큰을 만들고, Supabase 공개키 주소(JWKS)는 가짜 함수로 바꿔 끼운다."""
import base64, hashlib, hmac, json, time, uuid

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.auth as auth
from app.config import settings
from app.constants import AUTH_JWKS_REFETCH_MIN_S, AUTH_JWKS_TTL_S, AUTH_JWT_LEEWAY_S
from app.errors import AppError, app_error_handler

URL = "https://proj.supabase.co"
ISS = f"{URL}/auth/v1"
JWKS_URL = f"{ISS}/.well-known/jwks.json"
SECRET = "s" * 40
UID = uuid.UUID("11111111-2222-3333-4444-555555555555")


def ec_key():
    return ec.generate_private_key(ec.SECP256R1())


def jwk_of(private, kid, alg="ES256"):
    algo = jwt.algorithms.ECAlgorithm if alg == "ES256" else jwt.algorithms.RSAAlgorithm
    return {**algo.to_jwk(private.public_key(), as_dict=True), "kid": kid, "alg": alg, "use": "sig"}


def claims(**over):
    now = int(time.time())
    c = {"sub": str(UID), "aud": "authenticated", "iss": ISS, "iat": now, "exp": now + 3600, "role": "authenticated"}
    c.update(over)
    return {k: v for k, v in c.items() if v is not None}


def token(private, kid="k1", alg="ES256", **over):
    return jwt.encode(claims(**over), private, algorithm=alg, headers={"kid": kid})


class Fake:
    """가짜 Supabase. keys 를 바꾸면 다음에 받을 때 반영된다. calls 로 몇 번 받았는지 센다."""
    def __init__(self):
        self.keys, self.calls, self.down, self.now = [], [], False, 1000.0

    def fetch(self, url):
        self.calls.append(url)
        if self.down:
            raise ConnectionError("down")
        return {"keys": list(self.keys)}


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "")
    monkeypatch.setenv("AUTH_DEV_BYPASS", "0")
    settings.cache_clear()
    f = Fake()
    monkeypatch.setattr(auth, "_fetch_jwks", f.fetch)
    monkeypatch.setattr(auth.time, "monotonic", lambda: f.now)
    auth._jwks.reset()
    yield f
    auth._jwks.reset()
    settings.cache_clear()


def uid_of(tok):
    return auth.current_user_id(f"Bearer {tok}")


def rejected(tok, code="UNAUTHENTICATED"):
    with pytest.raises(AppError) as e:
        uid_of(tok)
    assert e.value.code == code, e.value
    return e.value


# ── ES256 (지금 프로젝트의 Current 키) ─────────────────────────────────────────

def test_es256_token_is_verified_with_jwks(fake):
    k = ec_key(); fake.keys = [jwk_of(k, "k1")]
    assert uid_of(token(k)) == UID
    assert fake.calls == [JWKS_URL]


def test_jwks_is_fetched_once_and_reused(fake):
    k = ec_key(); fake.keys = [jwk_of(k, "k1")]
    for _ in range(20):
        assert uid_of(token(k)) == UID
    assert len(fake.calls) == 1


def test_trailing_slash_in_supabase_url(fake, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", URL + "/"); settings.cache_clear()
    k = ec_key(); fake.keys = [jwk_of(k, "k1")]
    assert uid_of(token(k)) == UID
    assert fake.calls == [JWKS_URL]


def test_token_signed_by_another_key_with_same_kid(fake):
    fake.keys = [jwk_of(ec_key(), "k1")]
    rejected(token(ec_key(), "k1"))


@pytest.mark.parametrize("over", [
    {"exp": int(time.time()) - AUTH_JWT_LEEWAY_S - 5},    # 만료
    {"aud": "anon"}, {"aud": None},                       # 로그인 사용자 토큰이 아님
    {"iss": "https://other.supabase.co/auth/v1"},         # 다른 프로젝트
    {"sub": None}, {"sub": "not-a-uuid"}, {"sub": 7},
    {"exp": None},
    {"iat": int(time.time()) + 3600},                     # 아직 발급 전
])
def test_bad_claims_are_rejected(fake, over):
    k = ec_key(); fake.keys = [jwk_of(k, "k1")]
    rejected(token(k, **over))


def test_small_clock_skew_is_tolerated(fake):
    k = ec_key(); fake.keys = [jwk_of(k, "k1")]
    now = int(time.time())
    assert uid_of(token(k, iat=now + 5, exp=now + 3600)) == UID
    assert uid_of(token(k, exp=now - 5)) == UID


def test_rs256_token(fake):
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    fake.keys = [jwk_of(k, "r1", "RS256")]
    assert uid_of(token(k, "r1", "RS256")) == UID


def test_alg_must_match_key_type(fake):
    """EC 키의 kid 에 RS256 을 붙여도 500 이 아니라 401."""
    r = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    fake.keys = [jwk_of(ec_key(), "k1")]
    rejected(token(r, "k1", "RS256"))


def test_token_without_kid(fake):
    k = ec_key(); fake.keys = [jwk_of(k, "k1")]
    rejected(jwt.encode(claims(), k, algorithm="ES256"))
    assert fake.calls == []


# ── 키 돌리기·캐시 ────────────────────────────────────────────────────────────

def test_unknown_kid_refetches_but_not_more_often_than_min_interval(fake):
    old, new = ec_key(), ec_key()
    fake.keys = [jwk_of(old, "k1")]
    assert uid_of(token(old)) == UID                      # 1번째 받기
    fake.keys = [jwk_of(old, "k1"), jwk_of(new, "k2")]    # Supabase 에서 키를 돌림
    for _ in range(5):
        rejected(token(new, "k2"))                        # 방금 받았으니 다시 안 받는다
    assert len(fake.calls) == 1
    fake.now += AUTH_JWKS_REFETCH_MIN_S + 1
    assert uid_of(token(new, "k2")) == UID                # 2번째 받기
    assert uid_of(token(old, "k1")) == UID
    for _ in range(5):
        rejected(token(ec_key(), "nope"))
    assert len(fake.calls) == 2


def test_revoked_key_stops_working_after_ttl(fake):
    k = ec_key(); fake.keys = [jwk_of(k, "k1")]
    assert uid_of(token(k)) == UID
    fake.keys = []                                        # 키 폐기
    fake.now += AUTH_JWKS_TTL_S - 1
    assert uid_of(token(k)) == UID                        # 아직 담아 둔 것
    fake.now += 2
    rejected(token(k))
    assert len(fake.calls) == 2


def test_jwks_down_with_no_keys_is_500_not_401(fake):
    k = ec_key(); fake.keys = [jwk_of(k, "k1")]; fake.down = True
    rejected(token(k), "INTERNAL")
    rejected(token(k), "INTERNAL")
    assert len(fake.calls) == 1                           # 실패해도 간격은 지킨다
    fake.down = False; fake.now += AUTH_JWKS_REFETCH_MIN_S + 1
    assert uid_of(token(k)) == UID


def test_jwks_down_keeps_using_keys_it_already_has(fake):
    k = ec_key(); fake.keys = [jwk_of(k, "k1")]
    assert uid_of(token(k)) == UID
    fake.down = True; fake.now += AUTH_JWKS_TTL_S + 1
    assert uid_of(token(k)) == UID
    assert len(fake.calls) == 2


@pytest.mark.parametrize("body", [{"nope": 1}, [1, 2], {"keys": "abc"}, {"keys": [{"kty": "???"}, {"kty": "EC"}, 7]}, {"keys": []}])
def test_broken_jwks_body(fake, monkeypatch, body):
    monkeypatch.setattr(auth, "_fetch_jwks", lambda url: body)
    """받는 데 실패한 것(모양이 다름)은 500, 받았는데 쓸 키가 없는 것은 401."""
    fetched_ok = isinstance(body, dict) and isinstance(body.get("keys"), list)
    rejected(token(ec_key()), "UNAUTHENTICATED" if fetched_ok else "INTERNAL")


def test_es256_without_supabase_url_is_server_error(fake, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", ""); settings.cache_clear()
    rejected(token(ec_key()), "INTERNAL")
    assert fake.calls == []


# ── HS256 (예전 방식) ────────────────────────────────────────────────────────

def hs(secret=SECRET, **over):
    return jwt.encode(claims(**over), secret, algorithm="HS256")


def test_hs256_token_when_secret_is_set(fake, monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET); settings.cache_clear()
    assert uid_of(hs()) == UID
    assert fake.calls == []
    rejected(hs("x" * 40))
    rejected(hs(iss="https://other.supabase.co/auth/v1"))


def test_hs256_without_supabase_url_skips_issuer_check(fake, monkeypatch):
    """로컬처럼 SUPABASE_URL 없이 Secret 만 넣은 서버."""
    monkeypatch.setenv("SUPABASE_URL", ""); monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET); settings.cache_clear()
    assert uid_of(hs()) == UID


@pytest.mark.parametrize("configured", ["", "change-me"])
def test_hs256_is_rejected_when_secret_is_unset_or_placeholder(fake, monkeypatch, configured):
    """Secret 을 안 넣은 서버가 빈 값·예시 값으로 서명한 토큰을 받으면 누구나 위조할 수 있다."""
    monkeypatch.setenv("SUPABASE_JWT_SECRET", configured); settings.cache_clear()
    rejected(hs(configured or "change-me"))
    rejected(hs(SECRET))


def test_public_key_cannot_be_used_as_hs256_secret(fake, monkeypatch):
    """공개키는 누구나 받을 수 있다. 그 글자를 HS256 비밀로 써서 서명한 토큰이 통하면 안 된다."""
    k = ec_key(); fake.keys = [jwk_of(k, "k1")]
    pem = k.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=")
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "k1"}).encode())
    body = b64(json.dumps(claims()).encode())
    forged = (head + b"." + body + b"." + b64(hmac.new(pem, head + b"." + body, hashlib.sha256).digest())).decode()
    rejected(forged)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET); settings.cache_clear()
    rejected(forged)


def test_unsigned_and_unknown_alg(fake):
    k = ec_key(); fake.keys = [jwk_of(k, "k1")]
    rejected(jwt.encode(claims(), None, algorithm="none", headers={"kid": "k1"}))
    rejected(jwt.encode(claims(), "x" * 64, algorithm="HS512"))
    rejected("abc.def.ghi")
    rejected("")


# ── 헤더·개발용 토큰·응답 모양 ────────────────────────────────────────────────

def test_header_missing_or_not_bearer(fake):
    for h in (None, "", "Basic abc", "Bearer"):
        with pytest.raises(AppError) as e:
            auth.current_user_id(h)
        assert e.value.code == "UNAUTHENTICATED"


def test_dev_token_only_when_bypass_is_on(fake, monkeypatch):
    rejected(f"dev:{UID}")
    monkeypatch.setenv("AUTH_DEV_BYPASS", "1"); settings.cache_clear()
    assert uid_of(f"dev:{UID}") == UID
    rejected("dev:nope")
    k = ec_key(); fake.keys = [jwk_of(k, "k1")]
    assert uid_of(token(k)) == UID                        # 켜져 있어도 진짜 토큰은 그대로 검증


def test_error_shape_through_fastapi(fake):
    api = FastAPI()
    api.add_exception_handler(AppError, app_error_handler)

    @api.get("/me")
    def me(uid: uuid.UUID = auth.UserId):
        return {"id": str(uid)}

    k = ec_key(); fake.keys = [jwk_of(k, "k1")]
    with TestClient(api) as c:
        r = c.get("/me", headers={"Authorization": f"Bearer {token(k)}"})
        assert r.status_code == 200 and r.json() == {"id": str(UID)}
        r = c.get("/me", headers={"Authorization": f"Bearer {token(ec_key())}"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"
        assert c.get("/me").status_code == 401


def test_real_http_fetch_from_a_local_jwks_server(monkeypatch):
    """가짜 함수 없이 httpx 로 실제로 받아 오는 길. 로컬에 공개키 주소를 하나 띄운다."""
    import http.server, threading
    k = ec_key()
    body = json.dumps({"keys": [jwk_of(k, "k1")]}).encode()
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            ok = self.path == "/auth/v1/.well-known/jwks.json"
            self.send_response(200 if ok else 404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body if ok else b"{}")

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setenv("SUPABASE_URL", base)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "")
    monkeypatch.setenv("AUTH_DEV_BYPASS", "0")
    settings.cache_clear(); auth._jwks.reset()
    try:
        assert uid_of(token(k, iss=f"{base}/auth/v1")) == UID
        assert uid_of(token(k, iss=f"{base}/auth/v1")) == UID
        assert hits == ["/auth/v1/.well-known/jwks.json"]
    finally:
        srv.shutdown(); auth._jwks.reset(); settings.cache_clear()
