"""환경변수. 코드에 주소·키·모델명을 박지 않는다 (CLAUDE.md 2장)."""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql://postgres:postgres@localhost:54329/genz"
    supabase_url: str = ""            # https://<프로젝트>.supabase.co  앱 토큰(ES256)을 공개키로 검증할 때 쓴다. 비밀값 아님
    supabase_jwt_secret: str = ""     # HS256 토큰을 받을 때만. 비워 두면 HS256 토큰은 거절
    auth_dev_bypass: bool = False
    webhook_secret: str = "change-me"

    llm_base_url: str = "http://localhost:18000/v1"
    llm_model: str = "local"
    llm_api_key: str = "x"
    llm_timeout_s: float = 20.0
    llm_disable_thinking: bool = True
    llm_deterministic: bool = True    # temperature 0 + seed 0 전송. vLLM 은 받지만 Gemini·Anthropic 호환 API 는 400 → 대체 API 쓸 땐 0

    # 임베딩 — BGE-M3 고정 (CLAUDE.md 2장). openai = vLLM /v1/embeddings, local = 이 프로세스 안에서 sentence-transformers
    embed_backend: str = "openai"
    embed_base_url: str = "http://localhost:18001/v1"
    embed_model: str = "BAAI/bge-m3"
    embed_api_key: str = "x"
    embed_timeout_s: float = 30.0


@lru_cache
def settings() -> Settings:
    return Settings()
