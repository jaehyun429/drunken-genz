"""경험 → LLM 입력 문단, 입력 해시. 임베딩 입력(CLAUDE.md 8장)과 같은 문단을 쓴다."""
from __future__ import annotations
import hashlib
import re
from .schemas import ExperienceInput, OtherExperience, WRITE_MORE_SLOT_NAMES
from ..constants import QUESTION_PROMPT, QUESTION_REASK_PROMPT, QUESTION_TEXT_MAX, QUESTION_DECISION_CHIPS
from ..llm.client import load_prompt

_WS = re.compile(r"\s+")


def _norm(s: str | None) -> str:
    return _WS.sub(" ", (s or "").strip())


_DECISIONS = frozenset(_WS.sub("", c) for c in QUESTION_DECISION_CHIPS)


def is_decision(chip: str) -> bool:
    """상황 칩이 '내가 내린 결정'인지 (PRD 6.6). 해볼 것의 재료가 된다."""
    return _WS.sub("", chip or "") in _DECISIONS


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
    # 상황 칩은 둘로 나눈다: 내가 내린 결정 → 해볼 것, 그 밖(직접 입력 포함) → 상상해보기
    decided = [c for c in e.situation_chips if is_decision(c)]
    happened = [c for c in e.situation_chips if not is_decision(c)]
    notes = {k: v for k, v in e.situation_notes.items() if _norm(v)}
    if decided:
        lines.append("내가 내린 결정(고를 수 있었던 것): " + ", ".join(_norm(c) for c in decided))
        for k, v in notes.items():
            if is_decision(k):
                lines.append(f"  - {_norm(k)}: {_norm(v)}")
    if happened:
        lines.append("상황(고를 수 없었던 것): " + ", ".join(_norm(c) for c in happened))
    for k, v in notes.items():
        if not (decided and is_decision(k)):
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
    if e.answers:
        lines.append("더 적어본 것:")
        for a in e.answers:
            lines.append(f"  - [{WRITE_MORE_SLOT_NAMES.get(a.slot, a.slot)}] {_norm(a.question)} → {_norm(a.answer)}")
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
    out += [a.answer for a in e.answers]
    return [_norm(x) for x in out if _norm(x)]


def input_hash(e: ExperienceInput) -> str:
    """기록 문단의 해시. question_batch 에 기록으로 남긴다.
       11/14 부터는 같은 해시여도 저장된 질문을 재사용하지 않는다 (재생성은 이전 질문과 안 겹쳐야 한다)."""
    return hashlib.sha256(record_text(e).encode("utf-8")).hexdigest()


def prompt_version() -> str:
    """지금 쓰는 프롬프트 버전."""
    return load_prompt(QUESTION_PROMPT)[0]


def others_text(others: list[OtherExperience]) -> str:
    if not others:
        return "(없음)"
    return "\n".join(f"- [{o.id}] {_norm(o.activity_name)} / {_norm(o.genre)} / {_norm(o.role)} / "
                     f"{', '.join(_norm(c) for c in o.did_chips)}" for o in others)


def has_raw_text(e: ExperienceInput) -> bool:
    """원문이 있는지 (기능명세서 14.1). 원문 = 한 줄, 보강글, 회차 기록의 한 줄, 더 적어본 것. 칩은 원문이 아니다."""
    texts = [e.one_liner, *e.did_notes.values(), *e.situation_notes.values(),
             *(lg.one_liner for lg in e.logs), *(a.answer for a in e.answers)]
    return any(_norm(t) for t in texts)


def write_more_text(e: ExperienceInput) -> str:
    """더 적어보기 판단에 쓰는 사실. 원문이 아예 없는지는 코드가 알 수 있어 모델에 맡기지 않는다."""
    if has_raw_text(e):
        first = "- 원문: 있음. 원문을 읽고 세 칸 가운데 적혀 있지 않은 칸만 묻는다"
    else:
        first = "- 원문: 없음 (칩만 있음). 세 칸 모두 빈 칸"
    answered = [f"{WRITE_MORE_SLOT_NAMES[s]}({s})" for s in WRITE_MORE_SLOT_NAMES if any(a.slot == s for a in e.answers)]
    return first + "\n- 이미 답한 칸: " + (", ".join(answered) if answered else "(없음)")


def build_messages(e: ExperienceInput, others: list[OtherExperience],
                   previous: list[str] | None = None) -> tuple[str, str, str]:
    """(prompt_version, system, user). previous 는 사용자가 다시 만들기를 눌렀을 때 이전에 보여준 질문 본문."""
    version, system = load_prompt(QUESTION_PROMPT)
    system = system.replace("{text_max}", str(QUESTION_TEXT_MAX))
    user = (f"## 경험 기록\n{record_text(e)}\n\n## 더 적어보기 판단\n{write_more_text(e)}"
            f"\n\n## 이 사람의 다른 기록 (이미 해본 것 판정용)\n{others_text(others)}")
    if previous:
        user += "\n\n## 이전에 보여준 질문 (다시 내지 않는다)\n" + "\n".join(f"- {_norm(t)}" for t in previous)
    return version, system, user


def reask_text(kept: list[str], dropped: list[tuple[str, str]], need: dict[str, int]) -> str:
    """걸러낸 뒤 질문이 모자랄 때 사용자 메시지 뒤에 붙이는 글. 통과한 질문·버린 질문과 이유를 알려주고 모자란 종류만 더 받는다.
       need 는 {"try_next": 1, "imagine": 2} 꼴."""
    _, body = load_prompt(QUESTION_REASK_PROMPT)
    kept_lines = "\n".join(f"- {t}" for t in kept) or "(없음)"
    dropped_lines = "\n".join(f"- {t} → {why}" for t, why in dropped) or "(없음)"
    names = {"try_next": "try_next(해볼 것)", "imagine": "imagine(상상해보기)"}
    need_text = ", ".join(f"{names[t]} {n}개" for t, n in need.items() if n)
    return (body.replace("{have}", str(len(kept))).replace("{need}", need_text)
                .replace("{kept}", kept_lines).replace("{dropped}", dropped_lines))
