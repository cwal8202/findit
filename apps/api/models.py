"""API 스키마 (pydantic). 정규화된 습득물 결과."""

from __future__ import annotations

from pydantic import BaseModel


class FoundItem(BaseModel):
    atc_id: str
    fd_sn: str = "1"
    name: str = ""
    subject: str = ""
    category: str = ""
    color: str = ""
    dep_place: str = ""      # 보관장소(원본)
    region: str = ""         # 해석된 구/시
    found_at: str = ""       # 습득일
    source: str = ""
    image_url: str = ""
    score: float             # 임베딩 + 가점 후 점수
    grade: int | None = None    # LLM 판정 0~100 (grade=true일 때만)
    reason: str | None = None   # LLM 근거


class SearchResponse(BaseModel):
    query: str
    count: int
    region_set: list[str] = []
    graded: bool = False
    results: list[FoundItem]
