"""분석 입출력 모양. DB 를 모르는 부분."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Record:
    """기록 하나 = 경험 하나 또는 회차 기록 하나 (CLAUDE.local.md 6장 '회차 기록도 개수에 넣는다')."""
    key: str                    # LLM 에 보여주는 번호. e1, e2.l1 (UUID 를 모델이 옮겨 적다 틀리지 않게)
    experience_id: str
    activity_name: str
    text: str                   # 프롬프트에 들어가는 글
    log_id: str | None = None   # 회차 기록이면 그 id
    log_date: str | None = None
    created_at: datetime | None = None   # 쓴 시각. "아니에요" 뒤에 쓴 기록인지 가릴 때 쓴다. 모델에는 안 보낸다


@dataclass
class Found:
    text: str
    keys: list[str]             # 근거 기록 번호. 입력에 있는 것만, 겹침 없이


@dataclass
class Outcome:
    patterns: list[Found]
    dropped: list[tuple[str, str]] = field(default_factory=list)   # (문장, 이유)
    model: str | None = None
    prompt_version: str | None = None
    attempts: int = 0
    rechecked: dict[str, list[str]] = field(default_factory=dict)   # 문장 번호(p1, n1) → 그 내용이 적힌 기록 번호


# Anthropic 호환 엔드포인트는 additionalProperties:false 가 필요하고 maxItems 를 거부한다 (11/14)
OUTPUT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["patterns", "existing"],
    "properties": {"patterns": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["text", "evidence"],
        "properties": {"text": {"type": "string"},
                       "evidence": {"type": "array", "items": {"type": "string"}}}}},
                   "existing": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "evidence"],
        "properties": {"id": {"type": "string"},
                       "evidence": {"type": "array", "items": {"type": "string"}}}}}},
}
