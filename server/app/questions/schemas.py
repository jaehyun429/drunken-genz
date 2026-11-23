"""질문 생성 입력·출력 모양."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field

AXES_TRY = ("role", "scale", "method", "autonomy", "did", "field", "decision")   # decision: 내가 내린 결정인 상황 칩 (PRD 6.6)
AXES_IMAGINE = ("situation", "external")
# 더 적어보기가 묻는 빈 칸. 순서가 우선순위다 (기능명세서 14.1: ② 내가 한 일·이유 → ③ 결과·배움 → ① 상황)
AXES_WRITE_MORE = ("gap_action", "gap_result", "gap_situation")
WRITE_MORE_SLOT_NAMES = {"gap_action": "내가 한 일·이유", "gap_result": "결과·배움", "gap_situation": "상황"}
QuestionType = Literal["try_next", "imagine", "write_more"]


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
    answers: list[AnswerInput] = Field(default_factory=list)   # 더 적어보기에 답한 것. 기록 원문(보강)으로 친다


class LogInput(BaseModel):
    log_date: str
    situation_chips: list[str] = Field(default_factory=list)
    emotion_chips: list[str] = Field(default_factory=list)
    one_liner: str | None = None


class AnswerInput(BaseModel):
    """더 적어보기 질문과 그 답 (question.answer). TODO(기획): 답을 기록의 어느 칸에 둘지 정해지면 거기서 읽는다."""
    slot: str          # AXES_WRITE_MORE 중 하나
    question: str
    answer: str


class OtherExperience(BaseModel):
    """'이미 해본 것' 판정용. 본인의 다른 기록 요약."""
    id: str
    activity_name: str
    genre: str
    role: str
    did_chips: list[str] = Field(default_factory=list)


class GeneratedQuestion(BaseModel):
    type: QuestionType
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
    attempts: int                        # LLM 호출 시도 수 (다시 묻기 포함)
    reasks: int = 0                      # 질문이 모자라 다시 물은 횟수
    no_new: bool = False                 # 재생성인데 이전에 보여준 질문과 안 겹치는 새 질문이 없음 → 저장된 질문을 그대로 둔다


# LLM 에 강제할 JSON 스키마 (vLLM structured output)
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",  # 개수 상한은 filters 에서 자름 (Anthropic 호환 API 가 maxItems 를 거부)
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["try_next", "imagine", "write_more"]},
                    "axis": {"type": "string", "enum": list(AXES_TRY + AXES_IMAGINE + AXES_WRITE_MORE)},
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
