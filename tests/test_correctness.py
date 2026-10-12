"""정확성·견고성 — 지역 매칭 정밀화, KST 날짜, LLM 응답 검증, API 키 헤더 전달.

외부 서비스 없음(urlopen·LLM 목킹).
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from apps.agent import extract as extract_mod
from apps.api import gemini
from apps.api.region import region_matches


@pytest.mark.parametrize("region, region_set, expected", [
    ("대구광역시 중구", ["대구"], True),          # 광역시 전체
    ("서울특별시 중구", ["서울 중구"], True),
    ("부산광역시 중구", ["서울 중구"], False),    # 같은 이름의 다른 도시 구 — 예전엔 가점됨
    ("서울특별시 중랑구", ["중"], False),          # 부분 문자열 과잉 매칭 — 예전엔 가점됨
    ("부산광역시 강서구", ["서울 강서"], False),
    ("서울특별시 강서구", ["서울 강서"], True),
    ("경상북도 청도군", ["청도"], True),           # '청도'의 '도'를 떼지 않음
    ("부산광역시 영도구", ["부산 영도"], True),
    ("서울특별시 마포구", ["마포"], True),
    ("강남구", ["서울 강남"], True),               # 해석 지역에 시도가 없으면 구만 비교
    ("경기도 수원시 팔달구", ["경기 수원"], True),
    ("전라남도 순천시", ["전남 순천", "광양"], True),
    ("", ["서울"], False),
])
def test_region_matches_precise(region, region_set, expected):
    assert region_matches(region, region_set) is expected


def test_extract_uses_kst_today(monkeypatch):
    seen = {}
    monkeypatch.setattr(gemini, "generate_json", lambda p, **k: seen.setdefault("p", p) and {})
    extract_mod.extract("어제 지갑 잃어버렸어요")
    kst_today = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
    assert f"오늘은 {kst_today}" in seen["p"]


def test_extract_survives_malformed_llm_output(monkeypatch):
    monkeypatch.setattr(gemini, "generate_json", lambda p, **k: {
        "item": ["지갑", "카드지갑"], "brand": None, "color": 3, "is_lost_report": True})
    out = extract_mod.extract("지갑")
    assert out["item"] == "지갑, 카드지갑" and out["brand"] == "" and out["color"] == "3"
    monkeypatch.setattr(gemini, "generate_json", lambda p, **k: ["not", "a", "dict"])
    assert extract_mod.extract("지갑")["item"] == ""


def test_clean_grades_filters_bad_items():
    raw = [{"id": "0", "score": "87.6", "reason": "ok"}, {"id": 5, "score": 90}, {"id": "x", "score": 1},
           {"id": 1, "score": 150}, "junk"]
    assert gemini.clean_grades(raw, 2) == [{"id": 0, "score": 88, "reason": "ok"}, {"id": 1, "score": 100}]
    assert gemini.clean_grades({"results": [{"id": 0, "score": 10}]}, 1) == [{"id": 0, "score": 10}]
    assert gemini.clean_grades("nope", 3) == []


def test_api_key_sent_in_header_not_url(monkeypatch):
    seen = {}

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(req, timeout=0):
        seen["url"], seen["headers"] = req.full_url, dict(req.header_items())
        return R()
    monkeypatch.setattr(gemini.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(gemini.json, "load", lambda r: json.loads(r.read()))
    gemini._post("https://example.test/models/x:generateContent", {})
    assert "key=" not in seen["url"]
    assert any(k.lower() == "x-goog-api-key" for k in seen["headers"])
