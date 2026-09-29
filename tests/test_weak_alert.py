"""약한 후보 알림(신고별 옵트인) — 등급 판정 경계, 재매칭 중복 방지, 등록 시 이메일 조건.

외부 서비스 없음: 검색(graph.match_stored)과 알림(notifier)은 목킹. 임계는 기본값(강 80 / 약 50).
"""

import pytest

from apps import store
from apps.agent import rematch
from apps.api import main


class FakeNotifier:
    def __init__(self):
        self.sent: list[tuple[str, bool]] = []  # (1위 atc_id, weak)

    def notify(self, lost, matches, weak=False):
        self.sent.append((matches[0]["atc_id"], weak))


@pytest.fixture(autouse=True)
def clean_db():
    store.init()
    store._run("DELETE FROM lost_items")
    yield


@pytest.fixture
def fake(monkeypatch):
    n = FakeNotifier()
    monkeypatch.setattr(rematch, "notifier", n)
    monkeypatch.setattr("apps.notifier.notifier", n)  # 등록 경로는 지연 임포트
    return n


def _matches(atc: str, grade: int) -> list[dict]:
    return [{"atc_id": atc, "name": "지갑", "grade": grade, "score": 0.9}]


@pytest.mark.parametrize("grade, weak_on, expected", [
    (None, True, None),
    (49, True, None),        # 하한 미만 → 옵트인해도 무시
    (50, True, "weak"),      # 하한 포함
    (79, True, "weak"),
    (79, False, None),       # 옵트인 안 하면 기존 동작(알림 없음)
    (80, False, "strong"),   # 임계 포함 — 옵트인과 무관
    (95, True, "strong"),
])
def test_alert_level_boundaries(grade, weak_on, expected):
    assert rematch.alert_level(grade, weak_on) == expected


def test_rematch_weak_sent_once_and_stays_open(monkeypatch, fake):
    lid = store.add("검정 지갑", {}, [], [], None, None, email="me@x.com", notify_weak=True)
    monkeypatch.setattr(rematch.graph, "match_stored", lambda *a: _matches("A_1", 60))

    rematch.rematch_open(verbose=False)
    rematch.rematch_open(verbose=False)  # 같은 후보 → 재알림 없음

    assert fake.sent == [("A_1", True)]
    row = store.get(lid)
    assert row["status"] == "open"               # 약한 후보는 확정/유력 아님 → 계속 탐색
    assert row["weak_notified"] == ["A_1"]


def test_rematch_weak_new_candidate_alerts_again(monkeypatch, fake):
    store.add("검정 지갑", {}, [], [], None, None, email="me@x.com", notify_weak=True)
    monkeypatch.setattr(rematch.graph, "match_stored", lambda *a: _matches("A_1", 60))
    rematch.rematch_open(verbose=False)
    monkeypatch.setattr(rematch.graph, "match_stored", lambda *a: _matches("B_1", 70))
    rematch.rematch_open(verbose=False)
    assert fake.sent == [("A_1", True), ("B_1", True)]


def test_rematch_no_optin_keeps_old_behavior(monkeypatch, fake):
    store.add("검정 지갑", {}, [], [], None, None, email="me@x.com")  # notify_weak 기본 False
    monkeypatch.setattr(rematch.graph, "match_stored", lambda *a: _matches("A_1", 70))
    rematch.rematch_open(verbose=False)
    assert fake.sent == []


def test_rematch_strong_promotes_candidate(monkeypatch, fake):
    lid = store.add("검정 지갑", {}, [], [], None, None, email="me@x.com", notify_weak=True)
    monkeypatch.setattr(rematch.graph, "match_stored", lambda *a: _matches("A_1", 90))
    rematch.rematch_open(verbose=False)
    assert fake.sent == [("A_1", False)]
    assert store.get(lid)["status"] == "candidate"


def test_register_weak_needs_email(fake):
    result = {"matches": _matches("A_1", 60)}
    r1 = main._persist_and_respond("검정 지갑", result, email="", notify_weak=True)
    assert fake.sent == []                       # 이메일 없으면 옵트인 무효(관리자 주소로 새지 않게)
    assert store.get(r1.id)["notify_weak"] is False

    r2 = main._persist_and_respond("검정 지갑", result, email="me@x.com", notify_weak=True)
    assert fake.sent == [("A_1", True)]
    assert r2.status == "open"
    assert store.get(r2.id)["weak_notified"] == ["A_1"]  # 다음 재매칭에서 같은 후보 재알림 방지
