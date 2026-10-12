"""개인정보 — 신고자 삭제(관리 토큰 필수)·보유 기간 경과 자동 삭제·방침/약관 페이지. 외부 서비스 없음."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from apps import store
from apps.agent import rematch
from apps.api.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_db():
    store.init()
    store._run("DELETE FROM lost_items")
    yield


def _iso(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _report(watch_until=None):
    token = store.new_token()
    lid = store.add("검정 지갑", {}, [], [], None, None, email="a@b.co",
                    token_hash=store.hash_token(token), watch_until=watch_until)
    return lid, token


def test_owner_can_delete_report():
    lid, token = _report()
    assert client.delete(f"/lost-items/{lid}", headers={"X-Report-Token": "wrong"}).status_code == 404
    assert store.get(lid)
    r = client.delete(f"/lost-items/{lid}", headers={"X-Report-Token": token})
    assert r.status_code == 200 and r.json()["deleted"] == lid
    assert store.get(lid) is None


def test_purge_deletes_only_after_retention():
    old, _ = _report(watch_until=_iso(-31))     # 알림 끝난 지 31일 → 삭제
    recent, _ = _report(watch_until=_iso(-5))   # 끝난 지 5일 → 유지
    active, _ = _report(watch_until=_iso(20))   # 아직 알림 중 → 유지
    assert rematch.purge_old(verbose=False) == 1
    assert store.get(old) is None and store.get(recent) and store.get(active)


def test_purge_legacy_report_uses_created_plus_default_window():
    lid, _ = _report()
    store._run("UPDATE lost_items SET created_at=? WHERE id=?", (_iso(-61), lid))  # 등록 61일 전 = 기본 30 + 보유 30 초과
    assert rematch.purge_old(verbose=False) == 1


def test_policy_pages_served():
    for path, word in (("/privacy", "개인정보처리방침"), ("/terms", "이용약관")):
        r = client.get(path)
        assert r.status_code == 200 and word in r.text
    assert 'href="/privacy"' in client.get("/").text
