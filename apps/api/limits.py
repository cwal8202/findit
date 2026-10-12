"""남용 방지 — IP·키별 레이트리밋 + 하루 전체 AI 사용량 상한.

외부 의존성 없이 메모리 카운터(슬라이딩 윈도). 운영은 uvicorn 단일 프로세스라 충분하고,
재시작 시 초기화된다(워커를 늘리면 Redis 등 공유 저장소로 바꿔야 함).

- hit(key, limit, window): 윈도 안 호출 수가 limit 이상이면 429(Retry-After 포함).
- spend(units): 하루(KST) AI 사용량 상한 — 초과 시 429. LLM·임베딩을 부르는 요청만 소비.
- client_ip(request): Caddy(내부망) 뒤에서는 X-Forwarded-For의 맨 오른쪽(프록시가 붙인 실제 IP)만 신뢰.
"""

from __future__ import annotations

import ipaddress
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

from .config import settings

KST = timezone(timedelta(hours=9))


class Limiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self._hits: dict[str, deque] = defaultdict(deque)
        self._day = ""
        self._used = 0

    def hit(self, key: str, limit: int, window: float, message: str = "요청이 너무 많아요. 잠시 후 다시 시도해주세요.") -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] >= window:
                q.popleft()
            if len(q) >= limit:
                retry = int(window - (now - q[0])) + 1
                raise HTTPException(status_code=429, detail=message, headers={"Retry-After": str(retry)})
            q.append(now)

    def spend(self, units: int = 1) -> None:
        today = datetime.now(KST).date().isoformat()
        with self._lock:
            if today != self._day:
                self._day, self._used = today, 0
            if self._used + units > settings.daily_ai_budget:
                raise HTTPException(status_code=429,
                                    detail="오늘 AI 검색 이용량이 모두 소진됐어요. 내일 다시 이용해주세요.")
            self._used += units

    @property
    def used_today(self) -> int:
        return self._used


limiter = Limiter()


def _is_private(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    xff = request.headers.get("x-forwarded-for", "")
    if xff and _is_private(peer):  # 내부망 프록시(Caddy)가 붙인 값만 신뢰 — 맨 오른쪽이 실제 접속 IP
        return xff.split(",")[-1].strip() or peer
    return peer
