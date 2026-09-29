"""FindIt API — 습득물 검색 엔드포인트.

실행:  uv run uvicorn apps.api.main:app --reload
문서:  http://localhost:8000/docs
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from apps import store

from . import search as search_mod
from .config import settings
from .models import (
    AlertSettingsRequest,
    ConfirmRequest,
    DismissRequest,
    FoundItem,
    LostItemImageRequest,
    LostItemRequest,
    MatchResponse,
    SearchResponse,
)

app = FastAPI(title="FindIt API", version="0.1.0",
              description="분실물 자연어 질의 → 습득물 하이브리드 검색 + 지역 가점 + LLM grading")
store.init()  # 분실물 테이블 생성(멱등)


_WEB = Path(__file__).resolve().parents[2] / "apps" / "web"

# 정적 자산은 StaticFiles로 마운트(/static/...). 프로덕션에선 nginx/CDN이 대신 서빙.
app.mount("/static", StaticFiles(directory=_WEB / "static"), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    # index.html은 항상 최신으로(배포 후 브라우저 캐시로 옛 화면 붙잡는 문제 방지). 정적 자산은 캐시 허용.
    return FileResponse(_WEB / "index.html",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


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


def _persist_and_respond(text: str, result: dict, email: str,
                         notify_weak: bool = False) -> MatchResponse:
    """매칭 결과 저장 + (유력/옵트인 약한 후보면) 알림 + 응답 구성. 텍스트/사진 신고 공용."""
    from apps.agent.rematch import alert_level, watch_until_after

    raw_matches = result.get("matches", [])
    top = raw_matches[0] if raw_matches else None
    best_grade = top.get("grade") if top else None
    # 약한 후보 옵트인은 신고자 이메일이 있을 때만 — 없으면 관리자 기본 주소로 새지 않게
    notify_weak = bool(notify_weak and email)
    level = alert_level(best_grade, notify_weak) if top else None
    # strong이면 '유력 후보(candidate)' — 확정 아님, 사용자 확인 필요(HITL). weak은 open 유지.
    status = "candidate" if level == "strong" else "open"

    watch_until = watch_until_after()  # 기본 기간(결과창 '알림 설정'에서 변경)
    lid = store.add(
        text, result.get("extracted", {}), result.get("region_set", []),
        result.get("queries", []), top, best_grade, status=status, email=email,
        notify_weak=notify_weak, watch_until=watch_until,
    )
    if level:  # 유력 후보 → 알림 / 약한 후보(옵트인) → 참고 알림 + 중복 방지 기록
        from apps.notifier import notifier
        if level == "weak":
            store.mark_weak_notified(lid, top.get("atc_id"))
        notifier.notify(
            {"id": lid, "user_id": "anon", "text": text, "email": email}, raw_matches,
            weak=(level == "weak"),
        )
    return MatchResponse(
        id=lid, status=status, watch_until=watch_until, query=text,
        extracted=result.get("extracted", {}),
        region_set=result.get("region_set", []),
        queries=result.get("queries", []),
        count=len(raw_matches),
        matches=[FoundItem(**m) for m in raw_matches],
    )


@app.get("/found-items/browse")
def browse_found_items(
    q: str = Query("", description="키워드 검색(물품명·설명·분류, BM25)"),
    region: str = Query("", description="지역(구/시) 필터 (예: '강남')"),
    date_from: str = Query("", description="습득일 시작 YYYY-MM-DD"),
    date_to: str = Query("", description="습득일 끝 YYYY-MM-DD"),
    page: int = Query(1, ge=1, description="페이지(1부터)"),
    size: int = Query(24, ge=1, le=100, description="페이지당 개수"),
) -> dict:
    """전체 습득물 둘러보기 — 키워드/지역/날짜 필터 + 최신순. 임베딩·LLM 미사용(무료·즉시)."""
    return search_mod.browse(q=q, region=region, date_from=date_from, date_to=date_to,
                             page=page, size=size)


@app.post("/lost-items", response_model=MatchResponse)
def register_lost_item(req: LostItemRequest) -> MatchResponse:
    """분실물 자연어 신고 → 매칭 에이전트(추출→라우팅→fan-out→검색→grading) → 매칭 결과.

    등록 즉시 매칭 + DB 저장(open/candidate). 신규 습득물 유입 시 지속 재매칭.
    """
    from apps.agent import graph as agent_graph

    result = agent_graph.run(req.text, req.lost_date, req.lang or "ko")
    if not result.get("extracted", {}).get("is_lost_report", True):  # 분실물 신고 아님 → 저장·검색·알림 없이 종료
        return MatchResponse(id="", status="invalid", query=req.text,
                             extracted=result.get("extracted", {}), matches=[])
    return _persist_and_respond(req.text, result, req.email or "", req.notify_weak)


@app.post("/lost-items/image", response_model=MatchResponse)
def register_lost_item_image(req: LostItemImageRequest) -> MatchResponse:
    """사진(+선택 메모) 신고 → Vision 추출 → 동일 파이프라인 → 매칭 결과.

    사진 레인: 문장 대신 이미지에서 물품·색상·브랜드·특징을 추출해 검색. 이후는 텍스트와 동일.
    """
    from apps.agent import graph as agent_graph

    result = agent_graph.run_image(req.image_b64, req.mime, note=req.note,
                                   lost_date=req.lost_date, lang=req.lang or "ko")
    return _persist_and_respond(result.get("text", "사진 신고"), result, req.email or "",
                                req.notify_weak)


@app.get("/lost-items")
def list_lost_items() -> list[dict]:
    """등록된 분실물 신고 목록(상태 포함). open=찾는중, candidate=유력 후보, confirmed=확인, expired=알림 기간 만료."""
    return store.all_items()


@app.get("/lost-items/{lid}")
def get_lost_item(lid: str) -> dict:
    item = store.get(lid)
    if not item:
        raise HTTPException(status_code=404, detail="해당 분실물 신고 없음")
    return item


@app.post("/lost-items/{lid}/alerts")
def update_lost_item_alerts(lid: str, req: AlertSettingsRequest) -> dict:
    """결과창 '알림 설정' 저장 — 이메일·약한 후보 옵트인·알림 기간.

    - 알림 기간: 지금부터 watch_days일 동안 새 습득물과 매일 재매칭. 만료(expired) 신고는 저장 시 재개.
    - 새 이메일이면 현재 후보들을 그 주소로 바로 발송(사용자 본인이 명시적으로 트리거).
    """
    from apps.agent.rematch import watch_until_after

    item = store.get(lid)
    if not item:
        raise HTTPException(status_code=404, detail="해당 분실물 신고 없음")
    email = (req.email or "").strip()
    if email and "@" not in email:
        raise HTTPException(status_code=400, detail="유효한 이메일이 아닙니다")
    notify_weak = bool(req.notify_weak and email)  # 이메일 없으면 옵트인 무효
    watch_until = watch_until_after(req.watch_days)
    status = "open" if item.get("status") == "expired" else None  # 만료 신고 → 다시 찾는중
    store.update_alerts(lid, email, notify_weak, watch_until, status=status)

    sent_to = None
    if email and email != (item.get("email") or "") and req.matches:
        from apps.notifier import notifier
        notifier.notify({"id": lid, "user_id": "anon", "text": item.get("text", ""), "email": email},
                        req.matches)
        store.mark_weak_notified(lid, req.matches[0].get("atc_id"))  # 방금 받은 1위는 약한 알림 재발송 안 함
        sent_to = email
    return {"ok": True, "email": email, "notify_weak": notify_weak, "watch_until": watch_until,
            "status": status or item.get("status"), "sent_to": sent_to}


@app.post("/lost-items/{lid}/confirm")
def confirm_lost_item(lid: str, req: ConfirmRequest) -> dict:
    """사용자가 '내 물건이에요' 확인 → confirmed. 수령은 사용자가 보관기관에 문의(HITL)."""
    if not store.get(lid):
        raise HTTPException(status_code=404, detail="해당 분실물 신고 없음")
    store.confirm(lid, req.match)
    return {"ok": True, "status": "confirmed"}


@app.post("/lost-items/{lid}/dismiss")
def dismiss_lost_item(lid: str, req: DismissRequest) -> dict:
    """사용자가 '아니에요' → 그 후보 제외하고 계속 탐색(open)."""
    if not store.get(lid):
        raise HTTPException(status_code=404, detail="해당 분실물 신고 없음")
    dismissed = store.dismiss(lid, req.atc_id)
    return {"ok": True, "status": "open", "dismissed": dismissed}
