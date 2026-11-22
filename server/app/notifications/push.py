"""Expo Push 로 보내기. https://docs.expo.dev/push-notifications/sending-notifications/
   메시지 하나 = 기기 토큰 하나. 응답의 ticket 이 메시지와 같은 순서로 온다."""
from __future__ import annotations
import logging

import httpx

from ..config import settings
from ..constants import PUSH_CHUNK, PUSH_TIMEOUT_S

log = logging.getLogger("push")


class PushError(Exception):
    """Expo 에 닿지 못했거나 요청째로 거절됨. 호출한 쪽은 알림을 그대로 두고 다음에 다시 보낸다."""


async def send(messages: list[dict]) -> list[dict]:
    """messages: [{to, body, data}]. 돌려주는 것: 같은 순서의 ticket [{status: ok|error, details: {error}}]."""
    if not messages:
        return []
    s = settings()
    headers = {"Authorization": f"Bearer {s.expo_access_token}"} if s.expo_access_token else {}
    tickets: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=PUSH_TIMEOUT_S) as client:
            for i in range(0, len(messages), PUSH_CHUNK):
                part = messages[i:i + PUSH_CHUNK]
                r = await client.post(s.expo_push_url, json=part, headers=headers)
                if r.status_code != 200:
                    raise PushError(f"Expo {r.status_code}")
                data = r.json().get("data")
                if not isinstance(data, list) or len(data) != len(part):
                    raise PushError("Expo 응답 모양이 다름")
                tickets.extend(data)
    except (httpx.HTTPError, ValueError) as err:
        raise PushError(type(err).__name__) from err
    return tickets


def dead_tokens(messages: list[dict], tickets: list[dict]) -> list[str]:
    """Expo 가 DeviceNotRegistered 라고 답한 토큰. 앱이 지워졌거나 권한이 꺼진 기기다 → push_device 에서 지운다."""
    out = []
    for m, t in zip(messages, tickets):
        if isinstance(t, dict) and t.get("status") == "error" and (t.get("details") or {}).get("error") == "DeviceNotRegistered":
            out.append(m["to"])
    return out
