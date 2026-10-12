"""API 배선·보안 — 헬스체크, 신고 관리 토큰, 공개 목록 제거, 클라이언트 데이터 불신, 레이트리밋·일일 상한,
입력 상한(사진·검색어), 외국어 검색어 번역 캐시. 외부 서비스(OpenSearch·Gemini)는 목킹."""

import base64

import pytest
from fastapi.testclient import TestClient

from apps import store
from apps.api import gemini, main
from apps.api import search as search_mod
from apps.api.config import settings
from apps.api.limits import client_ip
from apps.api.main import app
from apps.api.models import MAX_IMAGE_B64

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_db():
    store.init()
    store._run("DELETE FROM lost_items")
    yield


def _report(text="검정 지갑", email="", matches=None):
    """관리 토큰이 있는 신고 하나 → (id, token)."""
    token = store.new_token()
    lid = store.add(text, {}, [], [], None, None, email=email, token_hash=store.hash_token(token),
                    matches=matches or [])
    return lid, token


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_index_served():
    r = client.get("/")
    assert r.status_code == 200 and "FindIt" in r.text
    assert "no-store" in r.headers.get("cache-control", "")  # 배포 즉시 반영


def test_ids_are_unguessable():
    lid, _ = _report()
    assert len(lid) == 32                                    # 128비트(기존 48비트 12자리에서 확대)


def test_public_list_removed():
    _report(email="secret@example.com")
    assert client.get("/lost-items").status_code in (404, 405)  # 남의 신고 문장·이메일 공개 목록 제거


def test_mine_returns_only_owned_reports_without_private_fields():
    a, ta = _report("내 지갑", email="secret@example.com")
    b, _ = _report("남의 지갑")
    body = client.post("/lost-items/mine", json={"items": [{"id": a, "token": ta}, {"id": b, "token": "wrong"}]}).json()
    assert [x["text"] for x in body] == ["내 지갑"]
    assert "secret@example.com" not in str(body) and "token_hash" not in str(body)


def test_detail_requires_token():
    lid, token = _report(email="secret@example.com")
    assert client.get(f"/lost-items/{lid}").status_code == 404                       # 토큰 없음
    assert client.get(f"/lost-items/{lid}", headers={"X-Report-Token": "x"}).status_code == 404
    body = client.get(f"/lost-items/{lid}", headers={"X-Report-Token": token}).json()
    assert body["id"] == lid and "email" not in body and "token_hash" not in body


def test_confirm_uses_server_side_match_only():
    lid, token = _report(matches=[{"atc_id": "A_1", "name": "검정 지갑", "grade": 88}])
    h = {"X-Report-Token": token}
    assert client.post(f"/lost-items/{lid}/confirm", json={"atc_id": "EVIL"}, headers=h).status_code == 400
    assert client.post(f"/lost-items/{lid}/confirm", json={"atc_id": "A_1"}).status_code == 404  # 토큰 없음
    assert client.post(f"/lost-items/{lid}/confirm", json={"atc_id": "A_1"}, headers=h).status_code == 200
    row = store.get(lid)
    assert row["status"] == "confirmed" and row["best_match"]["name"] == "검정 지갑"  # 서버 저장 후보


def test_dismiss_requires_token():
    lid, token = _report()
    assert client.post(f"/lost-items/{lid}/dismiss", json={"atc_id": "A_1"}).status_code == 404
    assert client.post(f"/lost-items/{lid}/dismiss", json={"atc_id": "A_1"},
                       headers={"X-Report-Token": token}).json()["dismissed"] == ["A_1"]


def test_search_rate_limited(monkeypatch):
    monkeypatch.setattr(settings, "limit_search_hour", 2)
    monkeypatch.setattr(search_mod, "search", lambda *a, **k: [])
    codes = [client.get("/found-items/search", params={"q": "지갑"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_daily_ai_budget_caps_everyone(monkeypatch):
    monkeypatch.setattr(settings, "daily_ai_budget", 3)
    monkeypatch.setattr(search_mod, "search", lambda *a, **k: [])
    assert client.get("/found-items/search", params={"q": "지갑", "grade": True}).status_code == 200  # 2단위
    r = client.get("/found-items/search", params={"q": "지갑", "grade": True})
    assert r.status_code == 429 and "이용량" in r.json()["detail"]


def test_report_rate_limited_before_any_llm(monkeypatch):
    monkeypatch.setattr(settings, "limit_report_10min", 1)
    called = []
    from apps.agent import graph
    monkeypatch.setattr(graph, "stream", lambda *a, **k: called.append(1) or iter([("done", {"extracted": {"is_lost_report": False}})]))
    assert client.post("/lost-items/stream", json={"text": "검정 지갑"}).status_code == 200
    assert client.post("/lost-items/stream", json={"text": "검정 지갑"}).status_code == 429
    assert called == [1]                                     # 두 번째는 LLM 호출 전에 차단


def test_image_size_and_type_limits():
    big = "A" * (MAX_IMAGE_B64 + 1)
    assert client.post("/lost-items/image/stream", json={"image_b64": big}).status_code == 422
    small = base64.b64encode(b"x").decode()
    assert client.post("/lost-items/image/stream", json={"image_b64": small, "mime": "text/html"}).status_code == 422


def test_browse_limits(monkeypatch):
    assert client.get("/found-items/browse", params={"q": "가" * 101}).status_code == 422
    assert client.get("/found-items/browse", params={"page": 500, "size": 24}).status_code == 400


def test_browse_translation_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(gemini, "generate_json", lambda p, **k: calls.append(p) or {"ko": "지갑"})
    monkeypatch.setattr(search_mod, "_os_raw", lambda body: {"hits": {"hits": [], "total": {"value": 0}}})
    for _ in range(3):
        assert client.get("/found-items/browse", params={"q": "Wallet"}).status_code == 200
    assert len(calls) == 1                                   # 같은 외국어 검색어는 번역 1회
    assert '"q": "Wallet"' in calls[0]                        # 검색어는 JSON 데이터로 감싸 전달


class _Req:
    def __init__(self, host, xff=""):
        self.client = type("C", (), {"host": host})()
        self.headers = {"x-forwarded-for": xff} if xff else {}


def test_client_ip_trusts_forwarded_only_from_internal_proxy():
    assert client_ip(_Req("172.18.0.5", "6.6.6.6, 1.2.3.4")) == "1.2.3.4"  # 프록시가 붙인 맨 오른쪽
    assert client_ip(_Req("8.8.8.8", "1.2.3.4")) == "8.8.8.8"              # 외부에서 위조한 헤더 무시


def test_valid_email():
    assert main._valid_email("me@example.com")
    assert not main._valid_email("me@") and not main._valid_email("a b@c.com") and not main._valid_email("")
