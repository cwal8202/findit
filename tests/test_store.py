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


def test_set_email():
    lid = store.add("x", {}, [], [], None, None, email="")
    store.set_email(lid, "me@example.com")
    assert store.get(lid)["email"] == "me@example.com"


def test_get_unknown_returns_none():
    assert store.get("nope") is None
