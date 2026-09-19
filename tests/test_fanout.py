"""fan-out 쿼리 생성 — 구조화 필드 → 넓게~좁게 여러 쿼리(규칙기반, 중복제거)."""

from apps.agent.fanout import build_queries


def test_narrow_to_broad_order():
    q = build_queries({"brand": "닥스", "color": "검정", "item": "지갑", "category": "지갑"})
    assert q == ["닥스 검정 지갑", "검정 지갑", "지갑"]  # 좁게→중간→넓게


def test_dedup_when_fields_missing():
    # brand/color 없으면 세 후보가 모두 "지갑" → 중복 제거로 1개
    assert build_queries({"item": "지갑", "category": "지갑"}) == ["지갑"]


def test_category_fallback_without_item():
    q = build_queries({"item": "", "category": "전자기기"})
    assert q == ["전자기기"]


def test_empty_input():
    assert build_queries({}) == []
