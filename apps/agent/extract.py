"""LLM 추출 — 분실물 자연어 신고 → 구조화 필드."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from apps.api import gemini

_PROMPT = (Path(__file__).parent / "prompts" / "extract.txt").read_text(encoding="utf-8")
_IMG_PROMPT = (Path(__file__).parent / "prompts" / "extract_image.txt").read_text(encoding="utf-8")
_FIELDS = ("item", "brand", "color", "category", "place_context", "lost_date", "features")
KST = timezone(timedelta(hours=9))


def _today() -> str:
    """오늘(KST) — 컨테이너는 UTC라 date.today()를 쓰면 새벽 0~9시에 '어제'가 하루 틀림."""
    return datetime.now(KST).date().isoformat()


def _s(v) -> str:
    """LLM 필드 정리 — 문자열이 아니게 와도(리스트·숫자·None) 요청이 죽지 않게 문자열로."""
    if isinstance(v, str):
        return v.strip()[:200]
    if isinstance(v, list):
        return ", ".join(str(x).strip() for x in v if x)[:200]
    return "" if v is None else str(v).strip()[:200]


def extract(text: str, lost_date: str | None = None) -> dict:
    res = gemini.generate_json(_PROMPT.format(text=text, today=_today()))
    res = res if isinstance(res, dict) else {}
    out = {k: _s(res.get(k)) for k in _FIELDS}
    out["is_lost_report"] = bool(res.get("is_lost_report", True))  # 분실물 신고 아니면 false(검색 스킵)
    if lost_date:  # 사용자가 명시했으면 우선
        out["lost_date"] = lost_date
    return out


def extract_image(image_b64: str, mime: str = "image/jpeg",
                  note: str = "", lost_date: str | None = None) -> dict:
    """사진(+선택 메모) → 텍스트 추출과 동일한 필드 스키마. 장소/날짜는 메모에서만."""
    prompt = _IMG_PROMPT.format(today=_today(), note=note or "")
    res = gemini.generate_json_image(prompt, image_b64, mime)
    res = res if isinstance(res, dict) else {}
    out = {k: _s(res.get(k)) for k in _FIELDS}
    if lost_date:  # 사용자가 명시했으면 우선
        out["lost_date"] = lost_date
    return out
