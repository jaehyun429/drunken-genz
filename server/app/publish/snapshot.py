"""공개 스냅샷 (소윤) — 공개 확정 순간 experience 행을 복사한 것. experience.published_snapshot 에 들어간다.
   나중에 원본을 고쳐도 공개본은 안 바뀐다 (CLAUDE.md 8장). 남에게 보이는 카드 내용은 전부 여기서 나온다.

   키 이름은 experience 컬럼과 같다 — 재현 cards/public.py·embedding/text.py 가 이 이름으로 읽는다.
   넣지 않는 것: id·user_id (누구 것인지는 뷰의 user_id 로만), 사진 (공개는 나중, 13장), pinned, 공개 설정,
   시각들. 원본 글자는 가림을 받아들인 칸이면 남지 않는다 (가린 값으로 바뀐 채 복사)."""
from __future__ import annotations

from ..constants import SNAPSHOT_VERSION

# 스냅샷에 들어가는 experience 칸. 순서는 의미 없음
SNAPSHOT_FIELDS = (
    "status", "activity_name", "official_period", "genre", "type", "role", "period_start", "period_end", "place",
    "did_chips", "did_notes", "custom_chips", "conditions",
    "situation_chips", "situation_notes", "emotion_chips", "one_liner", "ai_tags",
)
# 가림 값으로 바꿀 수 있는 칸 (익명 처리가 제안하는 곳). 나머지 칸은 바꾸지 않는다
# 기수는 넓힐 말이 없어서 가리면 None (카드에서 빠짐). 미리보기에서 늘 가릴지 묻는다 (11/20 (a)안)
MASKABLE_FIELDS = ("activity_name", "official_period", "role", "place", "one_liner")


def make_snapshot(exp: dict, official: dict | None = None, replaced: dict[str, str | None] | None = None) -> tuple[dict, list[str]]:
    """experience 행 → (published_snapshot, masked).
       official: 연결된 공식 활동 행 {name, status} 또는 None.
       replaced: 사용자가 받아들인 가림 {칸: 가린 값} (기수는 None). masked 는 가린 칸 이름 목록뿐 (원래 글자는 어디에도 안 남김)."""
    replaced = replaced or {}
    bad = set(replaced) - set(MASKABLE_FIELDS)
    if bad:
        raise ValueError(f"가릴 수 없는 칸: {sorted(bad)}")
    snap = {k: _copy(exp.get(k)) for k in SNAPSHOT_FIELDS}
    snap.update(replaced)
    snap["official_activity"] = (
        {"name": official["name"], "verified": official.get("status") == "verified"} if official else None)
    snap["snapshot_version"] = SNAPSHOT_VERSION
    return snap, sorted(replaced)


def _copy(v):
    """list/dict 는 새로 만들어 원본과 끊는다 (asyncpg 가 준 값을 나중에 고쳐도 스냅샷이 안 바뀌게)."""
    if isinstance(v, (list, tuple)):
        return list(v)
    if isinstance(v, dict):
        return dict(v)
    return v
