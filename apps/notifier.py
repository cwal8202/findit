"""알림(Notifier) — 채널 독립 인터페이스. 비즈니스 로직은 이 인터페이스로만 알림.

헌장: 코어에 카카오/웹 의존성 금지. v1은 ConsoleNotifier(로그), 추후 KakaoNotifier 등을 같은
인터페이스로 주입(코어 무변경). 되돌릴 수 없는 공식 신고 제출은 별도 HITL(여기선 통보만).
"""

from __future__ import annotations

import sys
from typing import Protocol


def _emit(msg: str) -> None:
    """콘솔 인코딩(Windows cp949 등)이 이모지를 못 찍어도 요청을 죽이지 않도록 안전 출력."""
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        buf = getattr(sys.stdout, "buffer", None)
        if buf is not None:
            buf.write((msg + "\n").encode("utf-8", "replace"))
            buf.flush()


class Notifier(Protocol):
    def notify(self, lost: dict, match: dict) -> None:
        ...


class ConsoleNotifier:
    """개발/데모용 — 콘솔로 매칭 알림 출력."""

    def notify(self, lost: dict, match: dict) -> None:
        who = lost.get("user_id", "anon")
        text = (lost.get("text") or "")[:30]
        _emit(
            f"🔔 [알림→{who}] 분실물 '{text}' 매칭!\n"
            f"    → {match.get('name')}/{match.get('color')} "
            f"(grade {match.get('grade')}) | 보관:{match.get('dep_place')} "
            f"지역:{match.get('region') or '-'} | 습득일:{match.get('found_at')}"
        )


# 주입 지점 — 나중에 KakaoNotifier 등으로 교체
notifier: Notifier = ConsoleNotifier()
