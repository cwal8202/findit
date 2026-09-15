"""LLM 추출 — 분실물 자연어 신고 → 구조화 필드."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from apps.api import gemini

_PROMPT = (Path(__file__).parent / "prompts" / "extract.txt").read_text(encoding="utf-8")
_FIELDS = ("item", "brand", "color", "category", "place_context", "lost_date", "features")


def extract(text: str, lost_date: str | None = None) -> dict:
    res = gemini.generate_json(_PROMPT.format(text=text, today=date.today().isoformat())) or {}
    out = {k: (res.get(k) or "").strip() for k in _FIELDS}
    if lost_date:  # 사용자가 명시했으면 우선
        out["lost_date"] = lost_date
    return out
