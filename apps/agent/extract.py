"""LLM 추출 — 분실물 자연어 신고 → 구조화 필드."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from apps.api import gemini

_PROMPT = (Path(__file__).parent / "prompts" / "extract.txt").read_text(encoding="utf-8")
_IMG_PROMPT = (Path(__file__).parent / "prompts" / "extract_image.txt").read_text(encoding="utf-8")
_FIELDS = ("item", "brand", "color", "category", "place_context", "lost_date", "features")


def extract(text: str, lost_date: str | None = None) -> dict:
    res = gemini.generate_json(_PROMPT.format(text=text, today=date.today().isoformat())) or {}
    out = {k: (res.get(k) or "").strip() for k in _FIELDS}
    if lost_date:  # 사용자가 명시했으면 우선
        out["lost_date"] = lost_date
    return out


def extract_image(image_b64: str, mime: str = "image/jpeg",
                  note: str = "", lost_date: str | None = None) -> dict:
    """사진(+선택 메모) → 텍스트 추출과 동일한 필드 스키마. 장소/날짜는 메모에서만."""
    prompt = _IMG_PROMPT.format(today=date.today().isoformat(), note=note or "")
    res = gemini.generate_json_image(prompt, image_b64, mime) or {}
    out = {k: (res.get(k) or "").strip() for k in _FIELDS}
    if lost_date:  # 사용자가 명시했으면 우선
        out["lost_date"] = lost_date
    return out
