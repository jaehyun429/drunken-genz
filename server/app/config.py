"""환경변수. 코드에 주소·키·모델명을 박지 않는다 (CLAUDE.md 2장)."""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql://postgres:postgres@localhost:54329/genz"
    supabase_jwt_secret: str = "change-me"
    auth_dev_bypass: bool = False
    webhook_secret: str = "change-me"

    llm_base_url: str = "http://localhost:18000/v1"
    llm_model: str = "local"
    llm_api_key: str = "x"
    llm_timeout_s: float = 20.0
    llm_disable_thinking: bool = True


@lru_cache
def settings() -> Settings:
    return Settings()
