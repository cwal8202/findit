"""질의 fan-out — 구조화 필드 → 넓게~좁게 여러 검색 쿼리.

하나의 정교한 쿼리 대신 여러 각도로: 임베딩이 특정 표현을 놓쳐도 다른 쿼리가 건짐.
규칙기반(LLM 불필요). 중복 제거·순서 유지.
"""

from __future__ import annotations


def build_queries(ex: dict) -> list[str]:
    brand, color, item, cat = ex.get("brand", ""), ex.get("color", ""), ex.get("item", ""), ex.get("category", "")
    cands = [
        " ".join(x for x in (brand, color, item) if x),   # 좁게: 닥스 검정 지갑
        " ".join(x for x in (color, item) if x),           # 중간: 검정 지갑
        item or cat,                                        # 넓게: 지갑
    ]
    out: list[str] = []
    for q in cands:
        q = q.strip()
        if q and q not in out:
            out.append(q)
    return out
