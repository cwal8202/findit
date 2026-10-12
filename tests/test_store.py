"""분실물 저장소(SQLite 백엔드) 생애주기 — 등록→조회→확인/제외→이메일. JSON 컬럼 왕복 포함."""

import pytest

from apps import store


@pytest.fixture(autouse=True)
def clean_db():
    store.init()
    store._run("DELETE FROM lost_items")
    yield


def test_add_get_json_roundtrip():
    lid = store.add("검정 지갑", {"item": "지갑"}, ["강남", "서초"], ["검정 지갑", "지갑"], None, None)
    row = store.get(lid)
    assert row["text"] == "검정 지갑"
    assert row["extracted"] == {"item": "지갑"}       # TEXT→dict 파싱
    assert row["region_set"] == ["강남", "서초"]       # TEXT→list 파싱
    assert row["status"] == "open"
    assert row["best_match"] is None


def test_list_open_excludes_candidate():
    a = store.add("a", {}, [], [], None, None, status="open")
    b = store.add("b", {}, [], [], {"name": "X"}, 90, status="candidate")
    open_ids = [r["id"] for r in store.list_open()]
    assert a in open_ids and b not in open_ids
    assert len(store.all_items()) == 2


def test_confirm_sets_status_and_match():
    lid = store.add("x", {}, [], [], None, None)
    store.confirm(lid, {"atc_id": "1", "name": "지갑", "grade": 88})
    row = store.get(lid)
    assert row["status"] == "confirmed"
    assert row["best_match"]["name"] == "지갑"
    assert row["best_grade"] == 88


def test_dismiss_accumulates_and_dedups():
    lid = store.add("x", {}, [], [], None, None)
    store.dismiss(lid, "A_1")
    d2 = store.dismiss(lid, "B_1")
    d3 = store.dismiss(lid, "A_1")  # 중복은 무시
    assert d2 == ["A_1", "B_1"]
    assert d3 == ["A_1", "B_1"]
    assert store.get(lid)["status"] == "open"  # 제외 후 다시 찾는중


def test_pending_email_then_verify():
    lid = store.add("x", {}, [], [], None, None)
    token = store.new_token()
    store.set_pending_email(lid, "me@example.com", store.hash_token(token), "2999-01-01T00:00:00+00:00")
    row = store.get(lid)
    assert row["email"] == "me@example.com" and row["email_verified"] is False
    assert row["verify_hash"] != token                  # 토큰 원문은 저장하지 않음(해시만)
    assert store.verify_email("wrong") is None
    assert store.verify_email(token)["email_verified"] is True
    assert store.verify_email(token) is None            # 1회용


def test_check_token_constant_and_hashed():
    token = store.new_token()
    lid = store.add("x", {}, [], [], None, None, token_hash=store.hash_token(token))
    row = store.get(lid)
    assert store.check_token(row, token) and not store.check_token(row, "nope")
    assert not store.check_token(store.get(store.add("y", {}, [], [], None, None)), token)  # 토큰 없는 옛 신고


def test_notify_weak_default_and_update_alerts():
    lid = store.add("x", {}, [], [], None, None)
    row = store.get(lid)
    assert row["notify_weak"] is False and row["watch_until"] is None  # 기본은 옵트아웃
    store.update_alerts(lid, True, "2026-12-31T00:00:00+00:00")
    row = store.get(lid)
    assert (row["notify_weak"], row["watch_until"]) == (True, "2026-12-31T00:00:00+00:00")
    assert row["status"] == "open"                      # status 미지정 → 그대로
    store.update_alerts(lid, False, "2026-12-31T00:00:00+00:00", status="expired")
    assert store.get(lid)["status"] == "expired"


def test_matched_at_set_when_registered_as_candidate():
    lid = store.add("x", {}, [], [], {"name": "X"}, 90, status="candidate")
    assert store.get(lid)["matched_at"]                 # 예전엔 'matched'와 비교해 항상 비어 있었음


def test_mark_weak_notified_accumulates_and_dedups():
    lid = store.add("x", {}, [], [], None, None)
    store.mark_weak_notified(lid, "A_1")
    assert store.mark_weak_notified(lid, "A_1") == ["A_1"]
    assert store.mark_weak_notified(lid, "B_1") == ["A_1", "B_1"]


def test_get_unknown_returns_none():
    assert store.get("nope") is None
