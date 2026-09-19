"""API 배선 — FastAPI 앱 로드/헬스체크/에러 경로(외부 서비스 불필요한 것만)."""

from fastapi.testclient import TestClient

from apps.api.main import app

client = TestClient(app)


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_index_served():
    r = client.get("/")
    assert r.status_code == 200
    assert "FindIt" in r.text
    assert "no-store" in r.headers.get("cache-control", "")  # 배포 즉시 반영


def test_unknown_lost_item_404():
    r = client.get("/lost-items/does-not-exist")
    assert r.status_code == 404
