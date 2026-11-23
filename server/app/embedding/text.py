"""공개 카드 → 임베딩에 넣을 한 문단. 제목만 넣으면 안 된다 (CLAUDE.md 9장).
   활동명 + 장르 + 역할 + 한 일 칩 + 상황 칩 + 감정 + 한 줄 + AI 태그. 가림 처리된 공개본(published_snapshot) 기준."""
from __future__ import annotations
from ..constants import EMBED_TEXT_MAX_CHARS

# published_snapshot 의 키 이름은 experience 컬럼과 같다 (소윤 publish/snapshot.py, 11/20 확정)
FIELDS = ("activity_name", "genre", "role", "did_chips", "did_notes", "custom_chips",
          "situation_chips", "emotion_chips", "one_liner", "ai_tags")


def _norm(v) -> str:
    return " ".join(str(v).split()) if v is not None else ""


def _list(v) -> list[str]:
    if not v:
        return []
    if isinstance(v, str):
        return [_norm(v)]
    return [_norm(x) for x in v if _norm(x)]


def card_text(card: dict) -> str:
    """dict 하나(스냅샷 또는 experience 행) → 한 문단. 사진·장소·기간·공식활동 id 는 안 넣는다.
       장소는 익명 규칙으로 넓혀지는 값이라 비슷함 계산에 도움이 안 되고, 기간은 의미가 없다."""
    parts: list[str] = []
    head = " / ".join(x for x in (_norm(card.get("activity_name")), _norm(card.get("genre")), _norm(card.get("role"))) if x)
    if head:
        parts.append(head)
    did = _list(card.get("did_chips")) + _list(card.get("custom_chips"))
    if did:
        parts.append("한 일: " + ", ".join(did))
    notes = card.get("did_notes") or {}
    if isinstance(notes, dict):
        for k, v in notes.items():
            if _norm(v):
                parts.append(f"{_norm(k)}: {_norm(v)}")
    sit = _list(card.get("situation_chips"))
    if sit:
        parts.append("상황: " + ", ".join(sit))
    emo = _list(card.get("emotion_chips"))
    if emo:
        parts.append("감정: " + ", ".join(emo))
    if _norm(card.get("one_liner")):
        parts.append(_norm(card.get("one_liner")))
    tags = _list(card.get("ai_tags"))
    if tags:
        parts.append("태그: " + ", ".join(tags))
    return ". ".join(parts)[:EMBED_TEXT_MAX_CHARS]


def pick_source(row: dict) -> dict:
    """experience 행에서 임베딩에 쓸 dict. 공개본 스냅샷이 있으면 그것, 없으면(로컬 개발) 원본 컬럼."""
    snap = row.get("published_snapshot")
    if isinstance(snap, dict) and snap.get("activity_name"):
        return snap
    return {k: row.get(k) for k in FIELDS}
