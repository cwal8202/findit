"""RouteResolver — 장소/교통 맥락 → 지역집합(구/시). 인터페이스로 분리.

v1: LLM 세계지식 스텁(노선 API 없이 동작, 신뢰도 낮음 → HITL 확인 전제, soft 가점이라 틀려도 배제 X).
추후: 지하철/버스/기차 노선 API를 같은 인터페이스에 주입 → 에이전트 코드 무변경, 정확도만 상승.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from apps.api import gemini

_PROMPT = (Path(__file__).parent / "prompts" / "route.txt").read_text(encoding="utf-8")


class RouteResolver(Protocol):
    def expand(self, place_context: str) -> list[str]:
        ...


class LLMRouteResolver:
    """LLM 지식으로 노선/장소 → 경유 구/시 집합."""

    def expand(self, place_context: str) -> list[str]:
        if not place_context:
            return []
        res = gemini.generate_json(_PROMPT.format(place=place_context)) or {}
        regions = res.get("regions") or []
        return [r.strip() for r in regions if isinstance(r, str) and r.strip()][:20]


resolver: RouteResolver = LLMRouteResolver()
