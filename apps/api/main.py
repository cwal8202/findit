"""FindIt API — 습득물 검색 엔드포인트.

실행:  uv run uvicorn apps.api.main:app --reload
문서:  http://localhost:8000/docs
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query

from apps import store

from . import search as search_mod
from .config import settings
from .models import FoundItem, LostItemRequest, MatchResponse, SearchResponse

app = FastAPI(title="FindIt API", version="0.1.0",
              description="분실물 자연어 질의 → 습득물 하이브리드 검색 + 지역 가점 + LLM grading")
store.init()  # 분실물 테이블 생성(멱등)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "index": settings.opensearch_index, "os": settings.opensearch_url}


@app.get("/found-items/search", response_model=SearchResponse)
def search_found_items(
    q: str = Query(..., description="분실물 자연어 설명 (예: '지하철에 두고 온 흰색 무선이어폰')"),
    region: str | None = Query(None, description="검색 지역집합, 쉼표구분 (예: '순천,광양,여수'). 넓게(구/시)."),
    lost_date: str | None = Query(None, description="분실일 YYYY-MM-DD (습득일<분실일 감점)"),
    grade: bool = Query(False, description="LLM grading 재정렬+근거 (Gemini 호출)"),
    size: int = Query(10, ge=1, le=50, description="반환 개수"),
    candidates: int = Query(100, ge=10, le=500, description="KNN 후보 수 (넉넉히)"),
    w_region: float = Query(0.05, ge=0.0, le=1.0, description="지역 가점 가중치 (eval 권장 ~0.05)"),
    w_time: float = Query(0.0, ge=0.0, le=1.0, description="시간(습득일<분실일) 감점 가중치"),
) -> SearchResponse:
    region_set = [r.strip() for r in region.split(",") if r.strip()] if region else []
    items = search_mod.search(
        q, region_set=region_set, lost_date=lost_date, do_grade=grade,
        size=size, candidates=candidates, w_region=w_region, w_time=w_time,
    )
    return SearchResponse(
        query=q, count=len(items), region_set=region_set, graded=grade,
        results=[FoundItem(**it) for it in items],
    )


@app.post("/lost-items", response_model=MatchResponse)
def register_lost_item(req: LostItemRequest) -> MatchResponse:
    """분실물 자연어 신고 → 매칭 에이전트(추출→라우팅→fan-out→검색→grading) → 매칭 결과.

    v1: stateless(등록 즉시 매칭). 추후 DB 저장 + 신규 습득물 유입 시 지속 재매칭.
    """
    from apps.agent import graph as agent_graph

    result = agent_graph.run(req.text, req.lost_date)
    raw_matches = result.get("matches", [])
    top = raw_matches[0] if raw_matches else None
    best_grade = top.get("grade") if top else None
    status = "matched" if (best_grade or 0) >= settings.rematch_grade_threshold else "open"

    lid = store.add(
        req.text, result.get("extracted", {}), result.get("region_set", []),
        result.get("queries", []), top, best_grade, status=status,
    )
    return MatchResponse(
        id=lid, status=status, query=req.text,
        extracted=result.get("extracted", {}),
        region_set=result.get("region_set", []),
        queries=result.get("queries", []),
        count=len(raw_matches),
        matches=[FoundItem(**m) for m in raw_matches],
    )


@app.get("/lost-items")
def list_lost_items() -> list[dict]:
    """등록된 분실물 신고 목록(상태 포함). open=찾는중, matched=매칭됨."""
    return store.all_items()


@app.get("/lost-items/{lid}")
def get_lost_item(lid: str) -> dict:
    item = store.get(lid)
    if not item:
        raise HTTPException(status_code=404, detail="해당 분실물 신고 없음")
    return item
