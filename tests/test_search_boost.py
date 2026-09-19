"""검색 파이프라인 핵심 — 지역 '가점'은 하드필터가 아니라 코사인 점수에 덧셈(soft).

OpenSearch/Gemini 없이 목킹으로 부스트 로직만 검증(eval에서 검증한 규칙의 회귀 방지).
"""

from apps.api import search


def _mock(monkeypatch, hits):
    monkeypatch.setattr(search.gemini, "embed_query", lambda q: [0.1] * 768)
    monkeypatch.setattr(search, "_os_search", lambda body: hits)
    monkeypatch.setattr(search.resolver, "resolve",
                        lambda dep: "부산" if "부산" in dep else "강남")


def test_region_boost_is_additive_and_reranks(monkeypatch):
    hits = [
        {"_id": "A_1", "_score": 0.90, "_source": {"atc_id": "A", "fd_sn": "1", "dep_place": "부산역"}},
        {"_id": "B_1", "_score": 0.88, "_source": {"atc_id": "B", "fd_sn": "1", "dep_place": "서울강남"}},
    ]
    _mock(monkeypatch, hits)
    res = search.search("지갑", region_set=["강남"], w_region=0.05)
    # B는 지역집합에 들어 +0.05 → 0.93 > A 0.90 → 순위 역전
    assert res[0]["atc_id"] == "B" and res[0]["score"] == 0.93
    assert res[1]["atc_id"] == "A" and res[1]["score"] == 0.90


def test_no_region_set_keeps_embedding_order(monkeypatch):
    hits = [
        {"_id": "A_1", "_score": 0.90, "_source": {"atc_id": "A", "fd_sn": "1", "dep_place": "부산역"}},
        {"_id": "B_1", "_score": 0.88, "_source": {"atc_id": "B", "fd_sn": "1", "dep_place": "서울강남"}},
    ]
    _mock(monkeypatch, hits)
    res = search.search("지갑", region_set=[], w_region=0.05)  # 집합 없으면 가점 없음
    assert [r["atc_id"] for r in res] == ["A", "B"]


def test_boost_never_excludes(monkeypatch):
    # soft 가점: 지역 불일치라도 후보에서 빠지지 않음(하드필터 아님)
    hits = [
        {"_id": "A_1", "_score": 0.90, "_source": {"atc_id": "A", "fd_sn": "1", "dep_place": "부산역"}},
    ]
    _mock(monkeypatch, hits)
    res = search.search("지갑", region_set=["강남"], w_region=0.05)
    assert len(res) == 1 and res[0]["atc_id"] == "A"  # 부산 후보 그대로 유지
