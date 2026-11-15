"""경험 → LLM 입력 문단, 입력 해시. 임베딩 입력(CLAUDE.md 8장)과 같은 문단을 쓴다."""
from __future__ import annotations
import hashlib
import re
from .schemas import ExperienceInput, OtherExperience
from ..constants import QUESTION_PROMPT, QUESTION_REASK_PROMPT, QUESTION_TEXT_MAX
from ..llm.client import load_prompt

_WS = re.compile(r"\s+")


def _norm(s: str | None) -> str:
    return _WS.sub(" ", (s or "").strip())


def record_text(e: ExperienceInput) -> str:
    """사람이 읽는 꼴. 프롬프트에 그대로 들어간다. 사진·공식활동 id 같은 건 안 넣는다."""
    status = "진행 중" if e.status == "in_progress" else "끝남"
    period = e.period_start + (" ~ " + e.period_end if e.period_end else " ~ (진행 중)")
    lines = [f"활동: {_norm(e.activity_name)} ({_norm(e.genre)}, {status})",
             f"역할: {_norm(e.role)}", f"기간: {period}"]
    if e.place:
        lines.append(f"장소: {_norm(e.place)}")
    chips = [_norm(c) for c in e.did_chips + e.custom_chips if _norm(c)]
    if chips:
        lines.append("주로 한 일: " + ", ".join(chips))
    for k, v in e.did_notes.items():
        if _norm(v):
            lines.append(f"  - {_norm(k)}: {_norm(v)}")
    cond = []
    for k, v in e.conditions.items():
        vals = v if isinstance(v, list) else [v]
        vals = [_norm(x) for x in vals if _norm(x)]
        if vals:
            cond.append(f"{_norm(k)}={'/'.join(vals)}")
    if cond:
        lines.append("조건(내가 고를 수 있었던 것): " + ", ".join(cond))
    if e.situation_chips:
        lines.append("상황(고를 수 없었던 것): " + ", ".join(_norm(c) for c in e.situation_chips))
    for k, v in e.situation_notes.items():
        if _norm(v):
            lines.append(f"  - {_norm(k)}: {_norm(v)}")
    if e.emotion_chips:
        lines.append("감정(참고만, 질문 재료 아님): " + ", ".join(_norm(c) for c in e.emotion_chips))
    if e.one_liner:
        lines.append(f"한 줄: {_norm(e.one_liner)}")
    if e.ai_tags:
        lines.append("AI 태그: " + ", ".join(_norm(t) for t in e.ai_tags))
    if e.logs:
        lines.append("회차 기록:")
        for lg in e.logs:
            bits = [lg.log_date]
            if lg.situation_chips:
                bits.append("상황 " + "/".join(lg.situation_chips))
            if lg.one_liner:
                bits.append(_norm(lg.one_liner))
            lines.append("  - " + " · ".join(bits))
    return "\n".join(lines)


def record_fragments(e: ExperienceInput) -> list[str]:
    """출처가 될 수 있는 조각들 (칩·문장 하나씩). record_text 와 같은 재료에서 뽑는다. 감정은 질문 재료가 아니라 뺀다."""
    out: list[str | None] = [e.activity_name, e.genre, e.role, e.place]
    out += e.did_chips + e.custom_chips + list(e.did_notes.values())
    for k, v in e.conditions.items():
        vals = [_norm(x) for x in (v if isinstance(v, list) else [v]) if _norm(x)]
        if vals:
            out.append(f"{_norm(k)}={'/'.join(vals)}")
    out += e.situation_chips + list(e.situation_notes.values()) + [e.one_liner] + e.ai_tags
    for lg in e.logs:
        out += lg.situation_chips + [lg.one_liner]
    return [_norm(x) for x in out if _norm(x)]


def input_hash(e: ExperienceInput) -> str:
    """같은 입력이면 같은 해시 → LLM 안 부르고 재사용 (CLAUDE.md 4장 '출력')."""
    return hashlib.sha256(record_text(e).encode("utf-8")).hexdigest()


def prompt_version() -> str:
    """지금 쓰는 프롬프트 버전. 저장된 질문을 재사용해도 되는지 볼 때 쓴다."""
    return load_prompt(QUESTION_PROMPT)[0]


def others_text(others: list[OtherExperience]) -> str:
    if not others:
        return "(없음)"
    return "\n".join(f"- [{o.id}] {_norm(o.activity_name)} / {_norm(o.genre)} / {_norm(o.role)} / "
                     f"{', '.join(_norm(c) for c in o.did_chips)}" for o in others)


def build_messages(e: ExperienceInput, others: list[OtherExperience]) -> tuple[str, str, str]:
    """(prompt_version, system, user)."""
    version, system = load_prompt(QUESTION_PROMPT)
    system = system.replace("{text_max}", str(QUESTION_TEXT_MAX))
    user = f"## 경험 기록\n{record_text(e)}\n\n## 이 사람의 다른 기록 (이미 해본 것 판정용)\n{others_text(others)}"
    return version, system, user


def reask_text(kept: list[str], dropped: list[tuple[str, str]], need: int) -> str:
    """걸러낸 뒤 질문이 모자랄 때 사용자 메시지 뒤에 붙이는 글. 통과한 질문·버린 질문과 이유를 알려주고 need 개만 더 받는다."""
    _, body = load_prompt(QUESTION_REASK_PROMPT)
    kept_lines = "\n".join(f"- {t}" for t in kept) or "(없음)"
    dropped_lines = "\n".join(f"- {t} → {why}" for t, why in dropped) or "(없음)"
    return (body.replace("{have}", str(len(kept))).replace("{need}", str(need))
                .replace("{kept}", kept_lines).replace("{dropped}", dropped_lines))
