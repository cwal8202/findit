"""실시간 진행(SSE) — 에이전트 단계 스트림 순서, API 이벤트·저장, 도중 오류, 입력 길이 제한.

외부 서비스 없음: LLM 추출·노선 확장·검색·grade·Vision은 모듈 함수를 목킹(컴파일된 그래프가 호출하는 지점).
"""

import json

import pytest
from fastapi.testclient import TestClient

from apps import store
from apps.agent import extract as extract_mod
from apps.agent import fanout, graph, route
from apps.api import gemini
from apps.api import search as search_mod
from apps.api.main import app

client = TestClient(app)

EX = {"item": "지갑", "color": "검정", "place_context": "2호선", "is_lost_report": True}


def _cand(atc: str) -> dict:
    return {"atc_id": atc, "fd_sn": "1", "name": "검정 지갑", "score": 0.9, "grade": None}


@pytest.fixture
def fake_pipeline(monkeypatch):
    monkeypatch.setattr(extract_mod, "extract", lambda text, lost_date=None: dict(EX))
    monkeypatch.setattr(route, "resolver", type("R", (), {"expand": lambda self, p: ["강남", "서초"]})())
    monkeypatch.setattr(fanout, "build_queries", lambda ex: ["검정 지갑", "지갑"])
    monkeypatch.setattr(search_mod, "search", lambda q, **k: [_cand("A"), _cand("B")])
    monkeypatch.setattr(gemini, "grade", lambda text, cands, lang="ko":
                        [{"id": 0, "score": 60, "reason": "색 일치"}, {"id": 1, "score": 85, "reason": "일치"}])


def test_graph_stream_emits_steps_in_order(fake_pipeline):
    steps = list(graph.stream("어제 2호선에서 검정 지갑"))
    assert [s for s, _ in steps] == ["extract", "route", "fanout", "search", "grade", "done"]
    parts = dict(steps)
    assert parts["route"]["region_set"] == ["강남", "서초"]
    assert len(parts["search"]["candidates"]) == 2
    final = parts["done"]
    assert [m["grade"] for m in final["matches"]] == [85, 60]  # 판정 후 점수순


def test_graph_stream_stops_after_extract_when_not_lost_report(monkeypatch, fake_pipeline):
    monkeypatch.setattr(extract_mod, "extract", lambda text, lost_date=None: {"is_lost_report": False})
    assert [s for s, _ in graph.stream("오늘 날씨 어때?")] == ["extract", "done"]


def test_stream_image_emits_visual_step(monkeypatch, fake_pipeline):
    monkeypatch.setattr(extract_mod, "extract_image", lambda b64, mime, note="", lost_date=None: dict(EX))
    monkeypatch.setattr(graph, "_visual_compare", lambda *a, **k: None)
    steps = [s for s, _ in graph.stream_image("AAAA", "image/png", note="2호선")]
    assert steps == ["extract", "route", "fanout", "search", "grade", "visual", "done"]


def _events(body: str) -> list[tuple[str, dict]]:
    out = []
    for block in body.strip().split("\n\n"):
        ev = next(line[6:].strip() for line in block.split("\n") if line.startswith("event:"))
        data = next(line[5:].strip() for line in block.split("\n") if line.startswith("data:"))
        out.append((ev, json.loads(data)))
    return out


@pytest.fixture
def quiet_notifier(monkeypatch):
    sent = []
    monkeypatch.setattr("apps.notifier.notifier",
                        type("N", (), {"notify": lambda self, lost, ms, weak=False: sent.append(weak)})())
    return sent


def test_api_stream_sends_steps_then_saved_result(monkeypatch, fake_pipeline, quiet_notifier):
    r = client.post("/lost-items/stream", json={"text": "어제 2호선에서 검정 지갑", "email": "me@x.com"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    evs = _events(r.text)
    assert [e for e, _ in evs] == ["extract", "route", "fanout", "search", "grade", "result"]
    search = dict(evs)["search"]["candidates"]
    assert len(search) == 2 and "atc_id" in search[0]      # FoundItem 스키마로 정리돼 전달
    assert all(c["grade"] is None for c in search)          # 검색 단계는 판정 전 후보(보낸 시점 스냅샷)
    result = dict(evs)["result"]
    assert result["status"] == "candidate" and result["matches"][0]["grade"] == 85
    assert store.get(result["id"])["best_grade"] == 85      # 기존 경로와 동일하게 저장
    assert quiet_notifier == [False]                        # 유력 후보 알림 1회


def test_api_stream_invalid_input_not_saved(monkeypatch, fake_pipeline, quiet_notifier):
    monkeypatch.setattr(extract_mod, "extract", lambda text, lost_date=None: {"is_lost_report": False})
    evs = _events(client.post("/lost-items/stream", json={"text": "오늘 날씨 어때?"}).text)
    assert [e for e, _ in evs] == ["extract", "result"]
    assert dict(evs)["result"]["status"] == "invalid" and dict(evs)["result"]["id"] == ""
    assert quiet_notifier == []


def test_api_stream_error_event_hides_internals(monkeypatch, fake_pipeline):
    def boom(q, **k):
        raise RuntimeError("secret internal detail")
    monkeypatch.setattr(search_mod, "search", boom)
    evs = _events(client.post("/lost-items/stream", json={"text": "검정 지갑"}).text)
    assert [e for e, _ in evs] == ["extract", "route", "fanout", "error"]
    assert "secret" not in dict(evs)["error"]["detail"]


def test_text_over_limit_rejected():
    assert client.post("/lost-items/stream", json={"text": "가" * 501}).status_code == 422
    assert client.post("/lost-items", json={"text": "가" * 501}).status_code == 422
