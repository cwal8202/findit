"""수집기 — 최근 N일 창 재조회 시 이미 색인된 항목 스킵, 새 항목 0건이어도 재매칭 진행, bulk 분할.

외부 서비스 없음: data.go.kr(client)·OpenSearch(urlopen)·재매칭은 목킹.
"""

import io
import json
import sys

from apps.collector import run


def _li(atc: str) -> dict:
    return {"atcId": atc, "fdSn": "1", "fdPrdtNm": "지갑", "prdtClNm": "지갑 > 남성용", "depPlace": "강남경찰서"}


def test_fetch_skips_existing_and_enriches_only_new(monkeypatch):
    pages = {1: [_li("A"), _li("B")], 2: [_li("C")]}
    monkeypatch.setattr(run.client, "get_list", lambda s, a, b, page=1, rows=100:
                        {"items": pages.get(page, []), "total": 3, "code": "00", "msg": ""})
    detailed = []
    monkeypatch.setattr(run.client, "get_detail", lambda s, atc, sn: detailed.append(atc) or {})
    monkeypatch.setattr(run.time, "sleep", lambda s: None)
    monkeypatch.setattr(run, "existing_ids", lambda ids: {"A_1", "C_1"})

    docs = run.fetch("police", "20260901", "20260907", rows=2, max_items=100, enrich=True, skip_existing=True)

    assert [d["atc_id"] for d in docs] == ["B"]
    assert detailed == ["B"]                     # 상세(항목당 1콜)는 새 항목에만


def test_fetch_without_skip_keeps_all(monkeypatch):
    monkeypatch.setattr(run.client, "get_list", lambda *a, **k:
                        {"items": [_li("A"), _li("B")], "total": 2, "code": "00", "msg": ""})
    monkeypatch.setattr(run, "existing_ids", lambda ids: (_ for _ in ()).throw(AssertionError("호출 금지")))
    docs = run.fetch("police", "20260901", "20260901", rows=100, max_items=100, enrich=False)
    assert [d["atc_id"] for d in docs] == ["A", "B"]


def test_main_with_no_new_items_skips_index_but_rematches(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run", "--source", "both", "--start", "20260901",
                                      "--end", "20260907", "--enrich", "--rematch"])
    monkeypatch.setattr(run, "fetch", lambda *a, **k: [])
    monkeypatch.setattr(run, "bulk_index", lambda docs: (_ for _ in ()).throw(AssertionError("빈 bulk 금지")))
    monkeypatch.setattr(run.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b'{"count": 5985}'))
    called = []
    monkeypatch.setattr("apps.agent.rematch.rematch_open", lambda: called.append(1))

    run.main()                                   # 예전엔 빈 _bulk → HTTP 400으로 죽고 재매칭도 건너뜀

    assert called == [1]


def test_bulk_index_splits_into_chunks(monkeypatch):
    sizes = []

    def fake_urlopen(req, timeout=0):
        n = len(req.data.decode().strip().split("\n")) // 2
        sizes.append(n)
        return io.BytesIO(json.dumps({"took": 1, "items": [{"index": {"status": 201}}] * n}).encode())

    monkeypatch.setattr(run.urllib.request, "urlopen", fake_urlopen)
    docs = [{"atc_id": str(i), "fd_sn": "1"} for i in range(1200)]
    res = run.bulk_index(docs, chunk=500)
    assert sizes == [500, 500, 200] and res == {"took": 3, "errors": 0, "count": 1200}
