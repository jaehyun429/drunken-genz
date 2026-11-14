"""BGE-M3 임베딩 호출. 백엔드 둘: vLLM /v1/embeddings (GPU 서버) 또는 sentence-transformers (맥 개발용).
   어느 쪽이든 1024차원, L2 정규화된 벡터를 돌려준다 (코사인 = 내적)."""
from __future__ import annotations
import asyncio
import logging
import math

from ..config import settings
from ..constants import EMBED_DIM

log = logging.getLogger("embed")


class EmbedError(Exception):
    """임베딩 실패. 호출한 쪽은 embedding 을 null 로 두고, sweep 이 나중에 다시 채운다."""


def _check(vecs: list[list[float]], n: int) -> list[list[float]]:
    if len(vecs) != n:
        raise EmbedError(f"벡터 개수 불일치 {len(vecs)} != {n}")
    for v in vecs:
        if len(v) != EMBED_DIM:
            raise EmbedError(f"차원 {len(v)} != {EMBED_DIM} — 모델이 BGE-M3 가 아님?")
    return vecs


def _normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


# --- vLLM(OpenAI 호환) -------------------------------------------------------
_openai = None


async def _embed_openai(texts: list[str]) -> list[list[float]]:
    global _openai
    import openai
    from openai import AsyncOpenAI
    s = settings()
    if _openai is None:
        _openai = AsyncOpenAI(base_url=s.embed_base_url, api_key=s.embed_api_key, timeout=s.embed_timeout_s, max_retries=1)
    try:
        resp = await _openai.embeddings.create(model=s.embed_model, input=texts)
    except (openai.APIConnectionError, openai.APITimeoutError, openai.APIStatusError) as e:
        raise EmbedError(f"{type(e).__name__}: {str(e)[:200]}") from e
    data = sorted(resp.data, key=lambda d: d.index)
    return [_normalize(list(d.embedding)) for d in data]


# --- sentence-transformers (로컬) ---------------------------------------------
_local = None
_local_lock = asyncio.Lock()


def _load_local():
    global _local
    if _local is None:
        from sentence_transformers import SentenceTransformer   # 무거워서 여기서만 import
        _local = SentenceTransformer(settings().embed_model)
        log.info("로컬 임베딩 모델 로드: %s", settings().embed_model)
    return _local


async def _embed_local(texts: list[str]) -> list[list[float]]:
    async with _local_lock:                      # 모델 로드·추론은 한 번에 하나
        model = await asyncio.to_thread(_load_local)
        try:
            arr = await asyncio.to_thread(model.encode, texts, normalize_embeddings=True, batch_size=16)
        except Exception as e:
            raise EmbedError(f"{type(e).__name__}: {str(e)[:200]}") from e
    return [[float(x) for x in row] for row in arr]


async def embed(texts: list[str]) -> list[list[float]]:
    """여러 문단 → 벡터들. 빈 입력이면 빈 목록."""
    if not texts:
        return []
    backend = settings().embed_backend
    if backend == "local":
        vecs = await _embed_local(texts)
    elif backend == "openai":
        vecs = await _embed_openai(texts)
    else:
        raise EmbedError(f"모르는 EMBED_BACKEND: {backend}")
    return _check(vecs, len(texts))


async def embed_one(text: str) -> list[float]:
    return (await embed([text]))[0]


def to_pg(vec: list[float]) -> str:
    """asyncpg 로 pgvector 에 넣을 때: $1::vector 에 '[..]' 문자열."""
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"
