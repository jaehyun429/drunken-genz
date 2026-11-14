"""질문 생성 입력·출력 모양."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field

AXES_TRY = ("role", "scale", "method", "autonomy", "did", "field")
AXES_IMAGINE = ("situation", "external")


class ExperienceInput(BaseModel):
    """experience 행 중 질문 생성에 쓰는 것만. 소윤 테이블 모양(API 명세 1.1)을 따른다."""
    id: str
    status: Literal["in_progress", "done"] = "done"
    activity_name: str
    genre: str
    role: str
    period_start: str
    period_end: str | None = None
    place: str | None = None
    did_chips: list[str] = Field(default_factory=list)
    did_notes: dict[str, str] = Field(default_factory=dict)
    custom_chips: list[str] = Field(default_factory=list)
    conditions: dict[str, list[str] | str] = Field(default_factory=dict)
    situation_chips: list[str] = Field(default_factory=list)
    situation_notes: dict[str, str] = Field(default_factory=dict)
    emotion_chips: list[str] = Field(default_factory=list)
    one_liner: str | None = None
    ai_tags: list[str] = Field(default_factory=list)
    logs: list[LogInput] = Field(default_factory=list)   # 진행 중 활동의 회차 기록


class LogInput(BaseModel):
    log_date: str
    situation_chips: list[str] = Field(default_factory=list)
    emotion_chips: list[str] = Field(default_factory=list)
    one_liner: str | None = None


class OtherExperience(BaseModel):
    """'이미 해본 것' 판정용. 본인의 다른 기록 요약."""
    id: str
    activity_name: str
    genre: str
    role: str
    did_chips: list[str] = Field(default_factory=list)


class GeneratedQuestion(BaseModel):
    type: Literal["try_next", "imagine"]
    axis: str
    text: str
    source: str
    done_experience_id: str | None = None


class GenerationOutcome(BaseModel):
    questions: list[GeneratedQuestion]
    dropped: list[tuple[str, str]]       # (버린 질문 텍스트, 이유) — 디버깅·데모용
    input_hash: str
    model: str
    prompt_version: str
    attempts: int


# LLM 에 강제할 JSON 스키마 (vLLM structured output)
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",  # 개수 상한은 filters 에서 자름 (Anthropic 호환 API 가 maxItems 를 거부)
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["try_next", "imagine"]},
                    "axis": {"type": "string", "enum": list(AXES_TRY + AXES_IMAGINE)},
                    "text": {"type": "string"},
                    "source": {"type": "string"},
                    "done_experience_id": {"type": ["string", "null"]},
                },
                "required": ["type", "axis", "text", "source", "done_experience_id"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}
