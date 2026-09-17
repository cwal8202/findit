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


class LostItemRequest(BaseModel):
    text: str                       # 자연어 분실 신고
    lost_date: str | None = None    # 명시하면 우선(없으면 LLM이 문장에서 환산)
    email: str | None = None        # 매칭 시 알림 받을 이메일(신고자별)


class MatchResponse(BaseModel):
    """에이전트 매칭 결과 — 추출·지역집합·fan-out 쿼리까지 투명하게 노출."""
    id: str = ""                    # 저장된 분실물 id
    status: str = "open"            # open | matched
    query: str
    extracted: dict = {}
    region_set: list[str] = []
    queries: list[str] = []
    count: int = 0
    matches: list[FoundItem] = []
