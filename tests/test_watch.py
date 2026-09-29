"""알림 기간(감시 만료) + 결과창 '알림 설정' API.

- 기간이 지난 open 신고는 재매칭하지 않고 expired로(LLM 비용 중단).
- watch_until 없는 기존 신고는 등록일 + 기본 기간으로 간주.
- POST /lost-items/{id}/alerts: 이메일·약한 후보·기간 저장, 만료 신고 재개, 새 이메일이면 현재 결과 발송.
외부 서비스 없음: 검색·알림 목킹.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from apps import store
from apps.agent import rematch
from apps.api.config import settings
from apps.api.main import app

client = TestClient(app)


class FakeNotifier:
    def __init__(self):
        self.sent: list[str] = []  # 수신 이메일

    def notify(self, lost, matches, weak=False):
        self.sent.append(lost.get("email"))


@pytest.fixture(autouse=True)
def clean_db():
    store.init()
    store._run("DELETE FROM lost_items")
    yield


@pytest.fixture
def fake(monkeypatch):
    n = FakeNotifier()
    monkeypatch.setattr(rematch, "notifier", n)
    monkeypatch.setattr("apps.notifier.notifier", n)
    return n


def _iso(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def test_watch_until_after_clamps():
    d = lambda s: (datetime.fromisoformat(s) - datetime.now(timezone.utc)).days  # noqa: E731
    assert d(rematch.watch_until_after(7)) in (6, 7)
    assert d(rematch.watch_until_after(9999)) in (settings.watch_days_max - 1, settings.watch_days_max)
    assert d(rematch.watch_until_after(None)) in (settings.watch_days_default - 1, settings.watch_days_default)


def test_expired_report_is_not_rematched(monkeypatch, fake):
    lid = store.add("검정 지갑", {}, [], [], None, None, email="me@x.com", watch_until=_iso(-1))
    called = []
    monkeypatch.setattr(rematch.graph, "match_stored", lambda *a: called.append(1) or [])
    rematch.rematch_open(verbose=False)
    assert called == []                          # 검색·LLM 호출 없음
    assert store.get(lid)["status"] == "expired"


def test_active_report_is_rematched(monkeypatch, fake):
    lid = store.add("검정 지갑", {}, [], [], None, None, email="me@x.com", watch_until=_iso(10))
    monkeypatch.setattr(rematch.graph, "match_stored",
                        lambda *a: [{"atc_id": "A_1", "grade": 90, "score": 0.9}])
    rematch.rematch_open(verbose=False)
    assert store.get(lid)["status"] == "candidate" and fake.sent == ["me@x.com"]


def test_legacy_report_without_watch_until_uses_created_at(monkeypatch, fake):
    lid = store.add("검정 지갑", {}, [], [], None, None)  # watch_until 없음(기존 신고)
    old = (datetime.now(timezone.utc) - timedelta(days=settings.watch_days_default + 1)).isoformat()
    store._run("UPDATE lost_items SET created_at=? WHERE id=?", (old, lid))
    monkeypatch.setattr(rematch.graph, "match_stored", lambda *a: [])
    rematch.rematch_open(verbose=False)
    assert store.get(lid)["status"] == "expired"


def test_alerts_saves_settings_and_sends_results_for_new_email(fake):
    lid = store.add("검정 지갑", {}, [], [], None, None, email="")
    r = client.post(f"/lost-items/{lid}/alerts", json={
        "email": "me@x.com", "notify_weak": True, "watch_days": 90,
        "matches": [{"atc_id": "A_1", "name": "지갑", "grade": 60}]})
    assert r.status_code == 200
    body = r.json()
    assert body["sent_to"] == "me@x.com" and fake.sent == ["me@x.com"]  # 새 이메일 → 지금 결과 발송
    row = store.get(lid)
    assert row["email"] == "me@x.com" and row["notify_weak"] is True
    assert row["watch_until"] == body["watch_until"]
    assert row["weak_notified"] == ["A_1"]       # 방금 받은 1위는 약한 알림 재발송 안 함

    r2 = client.post(f"/lost-items/{lid}/alerts", json={  # 같은 이메일로 기간만 변경 → 재발송 없음
        "email": "me@x.com", "notify_weak": False, "watch_days": 7,
        "matches": [{"atc_id": "A_1"}]})
    assert r2.json()["sent_to"] is None and fake.sent == ["me@x.com"]
    assert store.get(lid)["notify_weak"] is False


def test_alerts_reopens_expired_and_ignores_weak_without_email(fake):
    lid = store.add("검정 지갑", {}, [], [], None, None, status="expired")
    r = client.post(f"/lost-items/{lid}/alerts", json={"email": "", "notify_weak": True, "watch_days": 30})
    assert r.json()["status"] == "open" and r.json()["notify_weak"] is False
    assert store.get(lid)["status"] == "open"


def test_alerts_rejects_invalid_email_and_unknown_id():
    lid = store.add("x", {}, [], [], None, None)
    assert client.post(f"/lost-items/{lid}/alerts", json={"email": "nope"}).status_code == 400
    assert client.post("/lost-items/missing/alerts", json={}).status_code == 404
