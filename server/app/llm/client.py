"""OpenAI 호환 LLM 호출 (GPU 서버 vLLM). 실패를 '연결 문제'와 '모델 문제'로 나눈다 (CLAUDE.md 4장 '실패했을 때')."""
from __future__ import annotations
import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import openai
from openai import AsyncOpenAI

from ..config import settings
from ..constants import LLM_MAX_ATTEMPTS, LLM_MAX_TOKENS, LLM_RETRY_BACKOFF_S

log = logging.getLogger("llm")
PROMPT_DIR = Path(__file__).parent / "prompts"


class LLMConnectionError(Exception):
    """요청이 안 감·시간 초과·모델 서버 다운. 앱에 '다시 시도' 버튼."""


class LLMModelError(Exception):
    """모델이 응답은 했지만 깨짐·형식 위반. 다시 눌러도 안 되니 안내만."""


@dataclass
class LLMResult:
    data: dict
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    attempts: int


def load_prompt(name: str) -> tuple[str, str]:
    """prompts/<name>.md 를 읽고 (버전, 본문) 반환. 첫 줄이 `version: vN` 이어야 한다 (CLAUDE.md 12장)."""
    text = (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
    first, _, body = text.partition("\n")
    assert first.startswith("version:"), f"{name}.md 첫 줄은 'version: vN'"
    return first.split(":", 1)[1].strip(), body.strip()


_client: AsyncOpenAI | None = None


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        s = settings()
        _client = AsyncOpenAI(base_url=s.llm_base_url, api_key=s.llm_api_key, timeout=s.llm_timeout_s, max_retries=0)
    return _client


def classify(exc: Exception) -> type[Exception]:
    """openai 예외 → 연결 / 모델."""
    if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError)):
        return LLMConnectionError
    if isinstance(exc, openai.APIStatusError):
        # 502/503/504 는 vLLM 앞단이 죽은 것. 그 외 4xx/500 은 요청·모델 쪽
        return LLMConnectionError if exc.status_code in (502, 503, 504, 429) else LLMModelError
    if isinstance(exc, (json.JSONDecodeError, KeyError, ValueError)):
        return LLMModelError
    return LLMModelError


async def complete_json(system: str, user: str, schema: dict, *, max_tokens: int = LLM_MAX_TOKENS,
                        max_attempts: int = LLM_MAX_ATTEMPTS) -> LLMResult:
    """JSON 스키마 강제 + temperature 0 + 재시도(기본 3회). 마지막 실패는 분류된 예외로 올린다."""
    s = settings()
    extra: dict = {}
    if s.llm_disable_thinking:
        extra["chat_template_kwargs"] = {"enable_thinking": False}
    last: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            kwargs: dict = {"temperature": 0, "seed": 0} if s.llm_deterministic else {}
            resp = await client().chat.completions.create(
                model=s.llm_model, max_tokens=max_tokens, **kwargs,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_schema", "json_schema": {"name": "out", "strict": True, "schema": schema}},
                extra_body=extra,
            )
            raw = resp.choices[0].message.content or ""
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                # 사용자 글 원문이 섞일 수 있어 앞부분 길이만 남긴다
                log.warning("LLM 응답이 JSON 아님 finish=%s len=%d", resp.choices[0].finish_reason, len(raw))
                raise
            if not isinstance(data, dict):
                raise ValueError("JSON 최상위가 객체가 아님")
            u = resp.usage
            return LLMResult(data=data, model=resp.model or s.llm_model, attempts=attempt,
                             prompt_tokens=getattr(u, "prompt_tokens", None),
                             completion_tokens=getattr(u, "completion_tokens", None))
        except Exception as e:  # noqa: BLE001 — 분류해서 다시 던짐
            last = e
            kind = classify(e)
            log.warning("LLM 실패 %d/%d %s: %s", attempt, max_attempts, kind.__name__, type(e).__name__)
            if attempt < max_attempts:
                await asyncio.sleep(LLM_RETRY_BACKOFF_S[min(attempt - 1, len(LLM_RETRY_BACKOFF_S) - 1)])
    assert last is not None
    raise classify(last)(f"{type(last).__name__}: {str(last)[:200]}") from last
