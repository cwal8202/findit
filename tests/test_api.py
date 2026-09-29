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


def test_public_list_hides_email_and_id():
    """공개 신고 목록: 이메일(개인정보)·id(남이 /alerts 등으로 조작 가능) 미노출, 화면에 필요한 필드만."""
    from apps import store
    lid = store.add("검정 지갑", {"item": "지갑"}, ["강남"], ["지갑"],
                    {"atc_id": "A_1", "name": "검정 반지갑", "tel": "02-000"}, 85,
                    status="candidate", email="secret@example.com")
    body = client.get("/lost-items").json()
    row = next(x for x in body if x["text"] == "검정 지갑")
    assert "secret@example.com" not in str(body) and lid not in str(body)
    assert set(row) == {"text", "status", "best_match", "best_grade", "created_at", "watch_until"}
    assert row["best_match"] == {"name": "검정 반지갑"} and row["best_grade"] == 85


def test_detail_hides_email():
    from apps import store
    lid = store.add("x", {}, [], [], None, None, email="secret@example.com")
    body = client.get(f"/lost-items/{lid}").json()
    assert body["id"] == lid and "email" not in body and "user_id" not in body
