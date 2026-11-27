"""푸시 문구. 기획의 "1117 수정" 알림 표에 있는 예시 그대로다. 익명이라 상대 닉네임은 어디에도 넣지 않는다 (CLAUDE.local.md 9장).
   알림함 화면의 문구는 앱이 type·count·payload 로 만든다. 여기는 푸시에 실어 보내는 한 줄만."""
from __future__ import annotations

FALLBACK = "새 알림이 있어요"


def body(type_: str, count: int = 1, activity_name: str | None = None) -> str:
    """알림 한 건의 푸시 문구. activity_name 은 받는 사람 본인의 활동 이름이다."""
    name = (activity_name or "").strip()
    if type_ == "new_question":
        return f"「{name}」에 질문이 왔어요" if name else "내 경험에 질문이 왔어요"
    if type_ == "answer":
        return "남긴 질문에 답변이 달렸어요"
    if type_ == "related_found":
        return f"담은 질문에 맞는 경험이 {count}개 생겼어요"
    if type_ == "helpful":
        return "누군가 내 경험이 도움이 됐다고 남겼어요"
    if type_ == "saved":
        return f"오늘 {count}명이 내 경험을 담았어요"
    if type_ == "reminder":
        return f"「{name}」 이번 주 기록을 남겨볼까요?" if name else "이번 주 기록을 남겨볼까요?"
    if type_ == "draft":
        return "쓰다 만 기록이 있어요"
    return FALLBACK      # 모르는 종류 (verification, 커피챗 등 아직 안 만드는 것)

