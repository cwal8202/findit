"""FindIt API — 습득물 검색 엔드포인트.

실행:  uv run uvicorn apps.api.main:app --reload
문서:  http://localhost:8000/docs
"""

from __future__ import annotations

import json
import re
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from apps import store

from . import search as search_mod
from .config import settings
from .gemini import normalize_lang
from .limits import client_ip, limiter
from .models import (
    MAX_TEXT,
    AlertSettingsRequest,
    ConfirmRequest,
    DismissRequest,
    FoundItem,
    LostItemImageRequest,
    LostItemRequest,
    MatchResponse,
    MineRequest,
    SearchResponse,
    VerifyRequest,
)

_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
_HOUR, _DAY = 3600, 86400

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


@app.get("/privacy", include_in_schema=False)
def privacy() -> FileResponse:
    return FileResponse(_WEB / "privacy.html", headers={"Cache-Control": "no-cache"})


@app.get("/terms", include_in_schema=False)
def terms() -> FileResponse:
    return FileResponse(_WEB / "terms.html", headers={"Cache-Control": "no-cache"})


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "index": settings.opensearch_index, "os": settings.opensearch_url}


@app.get("/found-items/search", response_model=SearchResponse)
def search_found_items(
    request: Request,
    q: str = Query(..., max_length=MAX_TEXT, description="분실물 자연어 설명 (예: '지하철에 두고 온 흰색 무선이어폰')"),
    region: str | None = Query(None, max_length=300, description="검색 지역집합, 쉼표구분 (예: '순천,광양,여수'). 넓게(구/시)."),
    lost_date: str | None = Query(None, max_length=10, description="분실일 YYYY-MM-DD (습득일<분실일 감점)"),
    grade: bool = Query(False, description="LLM grading 재정렬+근거 (Gemini 호출)"),
    size: int = Query(10, ge=1, le=50, description="반환 개수"),
    candidates: int = Query(100, ge=10, le=200, description="KNN 후보 수 (넉넉히)"),
    w_region: float = Query(0.05, ge=0.0, le=1.0, description="지역 가점 가중치 (eval 권장 ~0.05)"),
    w_time: float = Query(0.0, ge=0.0, le=1.0, description="시간(습득일<분실일) 감점 가중치"),
) -> SearchResponse:
    limiter.hit(f"search:{client_ip(request)}", settings.limit_search_hour, _HOUR)
    limiter.spend(2 if grade else 1)  # 임베딩 1 + (판정 LLM 1)
    region_set = [r.strip() for r in region.split(",") if r.strip()] if region else []
    items = search_mod.search(
        q, region_set=region_set, lost_date=lost_date, do_grade=grade,
        size=size, candidates=candidates, w_region=w_region, w_time=w_time,
    )
    return SearchResponse(
        query=q, count=len(items), region_set=region_set, graded=grade,
        results=[FoundItem(**it) for it in items],
    )


def _valid_email(email: str) -> bool:
    return bool(email) and bool(_EMAIL_RE.match(email))


def _send_verification(lid: str, email: str, lang: str) -> None:
    """확인 메일 발송 — 같은 주소로 하루 N통 제한(남의 주소에 확인 메일 폭탄 방지). 고정 문구 + 링크만."""
    from apps.notifier import notifier

    limiter.hit(f"verifymail:{email.lower()}", settings.limit_verify_mail_day, _DAY,
                message="이 이메일로 확인 메일을 너무 많이 보냈어요. 내일 다시 시도해주세요.")
    token = store.new_token()
    expires = (datetime.now(timezone.utc) + timedelta(hours=settings.verify_hours)).isoformat()
    store.set_pending_email(lid, email, store.hash_token(token), expires)
    notifier.verify(email, lang, f"{settings.public_base_url}/email/verify?token={token}")


def _persist_and_respond(text: str, result: dict, email: str,
                         notify_weak: bool = False, lang: str = "ko") -> MatchResponse:
    """매칭 결과 저장 + 응답 구성. 텍스트/사진 신고 공용.

    - 신고 관리 토큰 발급(해시만 저장) — 확인·제외·알림 설정에 필요, 응답으로 1회만 전달.
    - 메일에 실을 후보는 서버가 판정한 것만 저장(`matches`) — 클라이언트 데이터로 메일을 만들지 않음.
    - 이메일을 남기면 확인 메일만 보냄(double opt-in). 결과·알림 메일은 확인 후에.
    """
    from apps.agent.graph import graded_keys
    from apps.agent.rematch import alert_level, slim_matches, watch_until_after

    raw_matches = result.get("matches", [])
    top = raw_matches[0] if raw_matches else None
    best_grade = top.get("grade") if top else None
    email = (email or "").strip()
    if not _valid_email(email):
        email = ""
    notify_weak = bool(notify_weak and email)
    # strong이면 '유력 후보(candidate)' — 확정 아님, 사용자 확인 필요(HITL).
    status = "candidate" if alert_level(best_grade, False) == "strong" else "open"

    token = store.new_token()
    watch_until = watch_until_after()  # 기본 기간(결과창 '알림 설정'에서 변경)
    lid = store.add(
        text, result.get("extracted", {}), result.get("region_set", []),
        result.get("queries", []), top, best_grade, status=status, email="",
        notify_weak=notify_weak, watch_until=watch_until, lang=lang,
        token_hash=store.hash_token(token), matches=slim_matches(raw_matches),
        graded_ids=graded_keys(raw_matches),  # 재매칭이 같은 후보를 다시 판정하지 않게
    )
    email_pending = False
    if email:
        try:
            _send_verification(lid, email, lang)
            email_pending = True
        except HTTPException:  # 확인 메일 한도 초과 — 검색 결과는 그대로 돌려줌
            pass
    return MatchResponse(
        id=lid, token=token, email_pending=email_pending, status=status, watch_until=watch_until,
        query=text, extracted=result.get("extracted", {}),
        region_set=result.get("region_set", []),
        queries=result.get("queries", []),
        count=len(raw_matches),
        matches=[FoundItem(**m) for m in raw_matches],
    )


_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}  # 프록시 버퍼링 없이 즉시 전달


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _step_payload(part: dict) -> dict:
    """단계 결과 → 화면용 JSON. 후보·매칭은 FoundItem 스키마로 정리(내부 필드 제거)."""
    return {k: ([FoundItem(**m).model_dump() for m in v or []] if k in ("candidates", "matches") else v)
            for k, v in part.items()}


def _stream_match(steps, email: str, notify_weak: bool, text: str | None = None, lang: str = "ko"):
    """단계 스트림 → SSE 이벤트. 끝나면 저장 후 `result`(MatchResponse).

    이벤트: extract · route · fanout · search(판정 전 후보) · grade · [visual] · result | error
    """
    try:
        final = None
        for step, part in steps:
            if step == "done":
                final = part
                break
            yield _sse(step, _step_payload(part))
        if final is None:
            return
        ex = final.get("extracted", {})
        query = text or final.get("text", "사진 신고")
        if not ex.get("is_lost_report", True):  # 분실물 신고 아님 → 저장·검색·알림 없이 종료
            resp = MatchResponse(id="", status="invalid", query=query, extracted=ex, matches=[])
        else:
            resp = _persist_and_respond(query, final, email, notify_weak, lang)
        yield _sse("result", resp.model_dump())
    except Exception:  # noqa: BLE001 — 스트림 도중 실패는 HTTP 상태로 못 알리므로 이벤트로
        traceback.print_exc()
        yield _sse("error", {"detail": "처리 중 오류가 발생했어요. 잠시 후 다시 시도해주세요."})


def _guard_report(request: Request, image: bool = False) -> None:
    """신고 1건 = 추출·임베딩·판정 LLM(사진은 Vision·사진 대조까지) → IP별 한도 + 하루 전체 상한."""
    ip = client_ip(request)
    if image:
        limiter.hit(f"img10:{ip}", settings.limit_image_10min, 600)
        limiter.hit(f"imgday:{ip}", settings.limit_image_day, _DAY)
        limiter.spend(3)
    else:
        limiter.hit(f"rep10:{ip}", settings.limit_report_10min, 600)
        limiter.hit(f"repday:{ip}", settings.limit_report_day, _DAY)
        limiter.spend(1)


@app.post("/lost-items/stream")
def register_lost_item_stream(req: LostItemRequest, request: Request) -> StreamingResponse:
    """`POST /lost-items`의 실시간 버전 — 추출·지역·검색·판정을 단계마다 SSE로 흘려보냄."""
    from apps.agent import graph as agent_graph

    _guard_report(request)
    lang = normalize_lang(req.lang)
    steps = agent_graph.stream(req.text, req.lost_date, lang)
    return StreamingResponse(_stream_match(steps, req.email or "", req.notify_weak, text=req.text, lang=lang),
                             media_type="text/event-stream", headers=_SSE_HEADERS)


@app.post("/lost-items/image/stream")
def register_lost_item_image_stream(req: LostItemImageRequest, request: Request) -> StreamingResponse:
    """`POST /lost-items/image`의 실시간 버전 — 사진 분석·지역·검색·판정·사진 대조를 단계마다 SSE로."""
    from apps.agent import graph as agent_graph

    _guard_report(request, image=True)
    lang = normalize_lang(req.lang)
    steps = agent_graph.stream_image(req.image_b64, req.mime, note=req.note,
                                     lost_date=req.lost_date, lang=lang)
    return StreamingResponse(_stream_match(steps, req.email or "", req.notify_weak, lang=lang),
                             media_type="text/event-stream", headers=_SSE_HEADERS)


@app.get("/found-items/browse")
def browse_found_items(
    request: Request,
    q: str = Query("", max_length=100, description="키워드 검색(물품명·설명·분류, BM25)"),
    region: str = Query("", max_length=60, description="지역(구/시) 필터 (예: '강남')"),
    date_from: str = Query("", max_length=10, description="습득일 시작 YYYY-MM-DD"),
    date_to: str = Query("", max_length=10, description="습득일 끝 YYYY-MM-DD"),
    page: int = Query(1, ge=1, description="페이지(1부터)"),
    size: int = Query(24, ge=1, le=100, description="페이지당 개수"),
) -> dict:
    """전체 습득물 둘러보기 — 키워드/지역/날짜 필터 + 최신순. 임베딩 미사용(외국어 키워드만 번역 1회·캐시)."""
    if page * size > 10_000:  # OpenSearch 기본 max_result_window
        raise HTTPException(status_code=400, detail="더 이상 볼 수 없는 페이지예요. 검색 조건을 좁혀주세요.")
    if search_mod.needs_translation(q):  # 캐시에 없는 외국어 검색어만 LLM 호출 → 한도·예산 소비
        limiter.hit(f"translate:{client_ip(request)}", settings.limit_translate_hour, _HOUR)
        limiter.spend(1)
    return search_mod.browse(q=q, region=region, date_from=date_from, date_to=date_to,
                             page=page, size=size)


@app.post("/lost-items", response_model=MatchResponse)
def register_lost_item(req: LostItemRequest, request: Request) -> MatchResponse:
    """분실물 자연어 신고 → 매칭 에이전트(추출→라우팅→fan-out→검색→grading) → 매칭 결과.

    등록 즉시 매칭 + DB 저장(open/candidate). 신규 습득물 유입 시 지속 재매칭.
    """
    from apps.agent import graph as agent_graph

    _guard_report(request)
    lang = normalize_lang(req.lang)
    result = agent_graph.run(req.text, req.lost_date, lang)
    if not result.get("extracted", {}).get("is_lost_report", True):  # 분실물 신고 아님 → 저장·검색·알림 없이 종료
        return MatchResponse(id="", status="invalid", query=req.text,
                             extracted=result.get("extracted", {}), matches=[])
    return _persist_and_respond(req.text, result, req.email or "", req.notify_weak, lang)


@app.post("/lost-items/image", response_model=MatchResponse)
def register_lost_item_image(req: LostItemImageRequest, request: Request) -> MatchResponse:
    """사진(+선택 메모) 신고 → Vision 추출 → 동일 파이프라인 → 매칭 결과."""
    from apps.agent import graph as agent_graph

    _guard_report(request, image=True)
    lang = normalize_lang(req.lang)
    result = agent_graph.run_image(req.image_b64, req.mime, note=req.note,
                                   lost_date=req.lost_date, lang=lang)
    return _persist_and_respond(result.get("text", "사진 신고"), result, req.email or "",
                                req.notify_weak, lang)


def _public_item(it: dict) -> dict:
    """'내 신고' 목록용 — 화면에 필요한 필드만(이메일 등 개인정보·내부 필드 제외)."""
    bm = it.get("best_match") or None
    return {
        "id": it.get("id"),
        "text": it.get("text", ""),
        "status": it.get("status"),
        "best_match": {"name": bm.get("name")} if bm else None,
        "best_grade": it.get("best_grade"),
        "created_at": it.get("created_at"),
        "watch_until": it.get("watch_until"),
        "email_verified": it.get("email_verified", False),
    }


_PRIVATE_FIELDS = ("email", "user_id", "token_hash", "verify_hash", "verify_expires", "graded_ids")


def _owned(lid: str, token: str | None) -> dict:
    """신고 관리 토큰 확인. 틀리면 존재 여부도 숨기고 404."""
    row = store.get(lid)
    if not store.check_token(row, token):
        raise HTTPException(status_code=404, detail="해당 분실물 신고 없음")
    return row


@app.post("/lost-items/mine")
def my_lost_items(req: MineRequest) -> list[dict]:
    """'내 신고' — 이 브라우저가 가진 (id, 관리 토큰)으로만 조회. 다른 사람 신고는 볼 수 없음."""
    tokens = {m.id: m.token for m in req.items}
    rows = [r for r in store.get_many(list(tokens)) if store.check_token(r, tokens.get(r["id"]))]
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return [_public_item(r) for r in rows]


@app.get("/lost-items/{lid}")
def get_lost_item(lid: str, x_report_token: str | None = Header(None)) -> dict:
    """신고 상세 — 관리 토큰을 가진 신고자만. 이메일 등 개인정보는 응답에 싣지 않음."""
    item = _owned(lid, x_report_token)
    return {k: v for k, v in item.items() if k not in _PRIVATE_FIELDS}


@app.post("/lost-items/{lid}/alerts")
def update_lost_item_alerts(lid: str, req: AlertSettingsRequest, request: Request,
                            x_report_token: str | None = Header(None)) -> dict:
    """결과창 '알림 설정' 저장 — 이메일·약한 후보 옵트인·알림 기간.

    - 알림 기간: 지금부터 watch_days일 동안 새 습득물과 매일 재매칭. 만료(expired) 신고는 저장 시 재개.
    - 새 이메일이면 확인 메일만 발송 — 링크를 눌러야 알림·현재 결과 메일이 감(남의 주소로 못 보냄).
    """
    from apps.agent.rematch import watch_until_after

    limiter.hit(f"alerts:{client_ip(request)}", settings.limit_alerts_hour, _HOUR)
    item = _owned(lid, x_report_token)
    email = (req.email or "").strip()
    if email and not _valid_email(email):
        raise HTTPException(status_code=400, detail="유효한 이메일이 아닙니다")
    notify_weak = bool(req.notify_weak and email)
    watch_until = watch_until_after(req.watch_days)
    status = "open" if item.get("status") == "expired" else None  # 만료 신고 → 다시 찾는중
    store.update_alerts(lid, notify_weak, watch_until, status=status)

    verification_sent = False
    if email != (item.get("email") or ""):
        if email:
            _send_verification(lid, email, item.get("lang") or "ko")
            verification_sent = True
        else:
            store.set_pending_email(lid, "", None, None)  # 이메일 비움 → 알림 해제
    verified = bool(email) and email == (item.get("email") or "") and item.get("email_verified", False)
    return {"ok": True, "email": email, "email_verified": verified, "verification_sent": verification_sent,
            "notify_weak": notify_weak, "watch_until": watch_until,
            "status": status or item.get("status")}


_VERIFY_PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>FindIt — 이메일 확인 / Confirm email</title>
<style>body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Malgun Gothic",sans-serif;background:#f5f6f8;
margin:0;padding:40px 16px;color:#1f2430}.c{max-width:460px;margin:0 auto;background:#fff;border:1px solid #e5e7eb;
border-radius:14px;padding:26px;text-align:center}h1{font-size:20px;color:#2563eb;margin:0 0 12px}p{color:#4b5563}
button{background:#2563eb;color:#fff;border:0;border-radius:10px;padding:13px 28px;font:inherit;font-weight:700;
cursor:pointer}button:disabled{opacity:.6}a{color:#2563eb}</style></head><body><div class="c">
<h1>FindIt</h1><p>분실물 알림을 받으려면 아래 버튼을 눌러 이메일을 확인해주세요.<br>
Press the button below to confirm your email and turn on lost-item alerts.</p>
<button id="b" onclick="go()">알림 받기 확인 · Confirm</button><p id="m"></p>
<p><a href="/">FindIt으로 가기 · Go to FindIt</a></p></div><script>
async function go(){const b=document.getElementById("b"),m=document.getElementById("m");b.disabled=true;
const t=new URLSearchParams(location.search).get("token")||"";
try{const r=await fetch("/email/verify",{method:"POST",headers:{"Content-Type":"application/json"},
body:JSON.stringify({token:t})});const d=await r.json();
m.textContent=r.ok?"✅ 확인됐어요! 이제 새 습득물이 들어오면 알려드려요. · Confirmed — alerts are on.":"⚠️ "+(d.detail||"오류")}
catch(e){m.textContent="⚠️ 네트워크 오류 · Network error";b.disabled=false}}</script></body></html>"""


@app.get("/email/verify", include_in_schema=False)
def verify_email_page() -> HTMLResponse:
    """확인 메일 링크 → 버튼 페이지(GET은 상태를 바꾸지 않음: 메일 보안 스캐너의 링크 선열람으로 자동 확인 방지)."""
    return HTMLResponse(_VERIFY_PAGE, headers={"Cache-Control": "no-store"})


@app.post("/email/verify")
def verify_email(req: VerifyRequest, request: Request) -> dict:
    """이메일 확인 → 알림 활성화. 저장된 후보(서버 판정)가 알림 기준을 넘으면 현재 결과도 바로 발송."""
    from apps.agent.rematch import alert_level

    limiter.hit(f"verify:{client_ip(request)}", settings.limit_alerts_hour, _HOUR)
    row = store.verify_email(req.token)
    if not row:
        raise HTTPException(status_code=400, detail="링크가 만료됐거나 올바르지 않아요. 알림 설정에서 다시 요청해주세요.")
    matches = row.get("matches") or []
    top = matches[0] if matches else None
    level = alert_level(top.get("grade"), row.get("notify_weak", False)) if top else None
    sent = False
    if level and row.get("status") in ("open", "candidate"):
        from apps.notifier import notifier
        notifier.notify(row, matches, weak=(level == "weak"))
        if level == "weak":
            store.mark_weak_notified(row["id"], top.get("atc_id"))
        sent = True
    return {"ok": True, "sent_results": sent}


@app.post("/lost-items/{lid}/confirm")
def confirm_lost_item(lid: str, req: ConfirmRequest, x_report_token: str | None = Header(None)) -> dict:
    """'내 물건이에요' → confirmed. 후보는 서버가 저장한 것에서 찾음(클라이언트 데이터 불신). 수령은 본인(HITL)."""
    item = _owned(lid, x_report_token)
    cands = (item.get("matches") or []) + ([item["best_match"]] if item.get("best_match") else [])
    match = next((m for m in cands if m.get("atc_id") == req.atc_id), None)
    if not match:
        raise HTTPException(status_code=400, detail="이 신고의 후보가 아니에요")
    store.confirm(lid, match)
    return {"ok": True, "status": "confirmed"}


@app.post("/lost-items/{lid}/dismiss")
def dismiss_lost_item(lid: str, req: DismissRequest, x_report_token: str | None = Header(None)) -> dict:
    """'아니에요' → 그 후보 제외하고 계속 탐색(open)."""
    _owned(lid, x_report_token)
    dismissed = store.dismiss(lid, req.atc_id)
    return {"ok": True, "status": "open", "dismissed": dismissed}


@app.delete("/lost-items/{lid}")
def delete_lost_item(lid: str, x_report_token: str | None = Header(None)) -> dict:
    """신고 삭제(신고자 요청) — 신고 문장·이메일·후보 기록을 즉시 완전 삭제. 이후 알림도 끝."""
    _owned(lid, x_report_token)
    store.delete(lid)
    return {"ok": True, "deleted": lid}
