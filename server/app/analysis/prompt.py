"""기록들 → LLM 입력, 입력 해시.
   넣는 것은 그 사용자 본인의 경험과 회차 기록뿐이다 (CLAUDE.local.md 7장, POL-07).
   남의 반응·Q&A 는 넣지 않고, 사용자가 고친 문장(edited_text)도 넣지 않는다 (6장)."""
from __future__ import annotations
import hashlib
import re

from ..constants import (ANALYSIS_AVOID_PROMPT_MAX, ANALYSIS_PATTERN_MAX, ANALYSIS_PROMPT, ANALYSIS_TEXT_MAX,
                         PATTERN_MIN_EVIDENCE)
from ..llm.client import load_prompt
from .schemas import Record

_WS = re.compile(r"\s+")


def _norm(s) -> str:
    return _WS.sub(" ", str(s or "").strip())


def _join(xs) -> str:
    return ", ".join(x for x in (_norm(x) for x in (xs or [])) if x)


def experience_text(row) -> str:
    """경험 한 건. 질문 생성의 record_text 와 같은 재료지만 감정을 재료로 쓰고 회차 기록은 따로 센다."""
    status = "진행 중" if row["status"] == "in_progress" else "끝남"
    period = _norm(row["period_start"]) + (" ~ " + _norm(row["period_end"]) if row["period_end"] else "")
    lines = [f"활동: {_norm(row['activity_name'])} ({_norm(row['genre'])}, {status})",
             f"역할: {_norm(row['role'])}", f"기간: {period}"]
    did = _join(list(row["did_chips"] or []) + list(row["custom_chips"] or []))
    if did:
        lines.append("주로 한 일: " + did)
    for k, v in (row["did_notes"] or {}).items():
        if _norm(v):
            lines.append(f"  - {_norm(k)}: {_norm(v)}")
    cond = []
    for k, v in (row["conditions"] or {}).items():
        vals = "/".join(x for x in (_norm(x) for x in (v if isinstance(v, list) else [v])) if x)
        if vals:
            cond.append(f"{_norm(k)}={vals}")
    if cond:
        lines.append("조건: " + ", ".join(cond))
    if row["situation_chips"]:
        lines.append("상황: " + _join(row["situation_chips"]))
    for k, v in (row["situation_notes"] or {}).items():
        if _norm(v):
            lines.append(f"  - {_norm(k)}: {_norm(v)}")
    if row["emotion_chips"]:
        lines.append("감정: " + _join(row["emotion_chips"]))
    if row["one_liner"]:
        lines.append("한 줄: " + _norm(row["one_liner"]))
    if row["ai_tags"]:
        lines.append("AI 태그: " + _join(row["ai_tags"]))
    return "\n".join(lines)


def log_text(activity_name: str, row) -> str:
    bits = [f"{_norm(activity_name)} 회차 기록 {row['log_date']}"]
    if row["situation_chips"]:
        bits.append("상황: " + _join(row["situation_chips"]))
    if row["emotion_chips"]:
        bits.append("감정: " + _join(row["emotion_chips"]))
    if row["one_liner"]:
        bits.append("한 줄: " + _norm(row["one_liner"]))
    return " · ".join(bits)


def records_hash(records: list[Record]) -> str:
    """기록 집합의 해시. 기록이 늘거나 줄거나 내용이 바뀌면 달라진다. 번호(key)와 프롬프트 버전은 넣지 않는다."""
    parts = sorted(f"{r.experience_id}|{r.log_id or ''}|{r.text}" for r in records)
    return hashlib.sha256("\n\n".join(parts).encode("utf-8")).hexdigest()


def prompt_version() -> str:
    return load_prompt(ANALYSIS_PROMPT)[0]


def build_messages(records: list[Record], existing: list[str], blocked: list[str], need: int) -> tuple[str, str, str]:
    """(prompt_version, system, user). need 는 이번에 새로 낼 수 있는 최대 개수 (0 이면 근거만 다시 센다).
       문장 번호는 existing 이 p1…, blocked 가 n1… 이고 받은 목록의 순서 그대로다 (filters.recheck 가 같은 번호로 읽는다)."""
    version, system = load_prompt(ANALYSIS_PROMPT)
    system = (system.replace("{max}", str(max(0, min(need, ANALYSIS_PATTERN_MAX))))
              .replace("{text_max}", str(ANALYSIS_TEXT_MAX)).replace("{min_evidence}", str(PATTERN_MIN_EVIDENCE)))
    body = "\n\n".join(f"[{r.key}] {r.text}" for r in records)
    user = f"## 기록 ({len(records)}개)\n{body}"
    if existing:
        user += "\n\n## 이미 있는 문장\n" + "\n".join(f"- [p{i}] {_norm(t)}" for i, t in enumerate(existing, 1))
    if blocked:
        numbered = list(enumerate(blocked, 1))[-ANALYSIS_AVOID_PROMPT_MAX:]
        user += "\n\n## 만들지 않을 문장\n" + "\n".join(f"- [n{i}] {_norm(t)}" for i, t in numbered)
    return version, system, user
