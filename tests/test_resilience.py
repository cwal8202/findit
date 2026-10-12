"""비용·장애 대비.

- 재매칭: 상위 후보가 지난번 판정한 것과 같으면 LLM 판정 생략(비용이 '새 후보가 들어온 신고' 수에만 비례).
  판정이 실패했으면 기록하지 않아 다음에 다시 판정. 신고자가 설정을 바꾸면 기록 초기화.
- Gemini 장애: 추출 실패 → 신고 문장으로 검색, 임베딩 실패 → 키워드(BM25) 검색, 판정 실패 → 점수 없이 후보 반환.
외부 서비스 없음: 검색·Gemini 목킹.
"""

import urllib.error

import pytest

from apps import store
from apps.agent import extract, graph, rematch
from apps.api import gemini, search


@pytest.fixture(autouse=True)
def clean_db():
    store.init()
    store._run("DELETE FROM lost_items")
    yield


def _cands(*ids):
    return [{"atc_id": i, "fd_sn": "1", "name": "지갑", "score": 0.9 - n * 0.01} for n, i in enumerate(ids)]


@pytest.fixture
def world(monkeypatch):
    """검색 결과(현재 색인 상위 후보)와 판정 호출 횟수를 조절하는 가짜 환경."""
    w = {"cands": _cands("A", "B"), "grade_calls": 0, "grade_ok": True}

    monkeypatch.setattr(graph.search_mod, "search", lambda q, **k: [dict(c) for c in w["cands"]])

    def fake_grade(text, cands, lang="ko"):
        w["grade_calls"] += 1
        return [{"id": i, "score": 30, "reason": "x"} for i in range(len(cands))] if w["grade_ok"] else []

    monkeypatch.setattr(graph.gemini, "grade", fake_grade)
    monkeypatch.setattr(rematch, "notifier", type("N", (), {"notify": lambda *a, **k: None})())
    return w


def _open_report():
    return store.add("검정 지갑", {"item": "지갑"}, [], ["검정 지갑"], None, None)


def test_rematch_skips_grading_when_candidates_unchanged(world):
    lid = _open_report()
    rematch.rematch_open(verbose=False)
    assert world["grade_calls"] == 1
    assert set(store.get(lid)["graded_ids"]) == {"A_1", "B_1"}
    rematch.rematch_open(verbose=False)  # 같은 후보 → 판정 생략
    assert world["grade_calls"] == 1


def test_rematch_grades_again_when_new_candidate_arrives(world):
    _open_report()
    rematch.rematch_open(verbose=False)
    world["cands"] = _cands("C", "A")  # 새 습득물 C가 상위에
    rematch.rematch_open(verbose=False)
    assert world["grade_calls"] == 2


def test_failed_grading_is_not_recorded(world):
    lid = _open_report()
    world["grade_ok"] = False  # Gemini 장애
    rematch.rematch_open(verbose=False)
    assert not store.get(lid)["graded_ids"]
    world["grade_ok"] = True
    rematch.rematch_open(verbose=False)  # 다음 재매칭에서 다시 판정
    assert world["grade_calls"] == 2


def test_user_changes_reset_graded(world):
    lid = _open_report()
    rematch.rematch_open(verbose=False)
    store.update_alerts(lid, True, "2099-01-01T00:00:00+00:00")  # 약한 후보 옵트인 → 같은 후보도 알림 판단이 달라짐
    assert not store.get(lid)["graded_ids"]
    rematch.rematch_open(verbose=False)
    assert world["grade_calls"] == 2
    store.dismiss(lid, "A")
    assert not store.get(lid)["graded_ids"]


def test_registration_seeds_graded_keys():
    lid = store.add("x", {}, [], [], None, None, graded_ids=["A_1"])
    assert store.get(lid)["graded_ids"] == ["A_1"]
    assert graph.graded_keys([{"atc_id": "A", "fd_sn": "1", "grade": 70},
                              {"atc_id": "B", "fd_sn": "1", "grade": None}]) == ["A_1"]


def test_extract_falls_back_to_text_when_llm_down(monkeypatch):
    monkeypatch.setattr(extract.gemini, "generate_json", lambda *a, **k: None)
    ex = extract.extract("어제 강남역에서 검정 지갑 잃어버렸어요", lost_date="2026-10-12")
    assert ex["item"].startswith("어제 강남역") and ex["is_lost_report"] and ex["degraded"]
    assert ex["lost_date"] == "2026-10-12"


def test_search_falls_back_to_keyword_when_embedding_down(monkeypatch):
    def boom(q):
        raise urllib.error.HTTPError("u", 503, "down", {}, None)

    seen = {}
    monkeypatch.setattr(search.gemini, "embed_query", boom)
    monkeypatch.setattr(search, "_os_search", lambda body: seen.setdefault("q", body["query"]) and [])
    assert search.search("검정 지갑") == []
    assert "multi_match" in seen["q"]


def test_grade_network_error_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise TimeoutError("slow")

    monkeypatch.setattr(gemini, "_post", boom)
    assert gemini.grade("지갑", [{"name": "지갑"}]) == []
    assert gemini.generate_json("x") is None
