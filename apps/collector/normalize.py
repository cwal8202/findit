"""API 원본(목록+상세) → OpenSearch found_items 색인 문서.

색인 스키마와 필드명 일치(infra/opensearch/mappings/found_items.json).
임베딩 텍스트는 기존 색인과 동일 공식(name+분류+색상) — 신규 항목이 기존과 비교 가능하도록.
"""

from __future__ import annotations


def normalize(source: str, li: dict, detail: dict | None = None) -> dict:
    detail = detail or {}
    cat = li.get("prdtClNm", "") or detail.get("prdtClNm", "")
    parts = [p.strip() for p in cat.split(">")]
    return {
        "atc_id": li.get("atcId", ""),
        "fd_sn": li.get("fdSn", "1"),
        "source": source,
        "name": li.get("fdPrdtNm", "") or detail.get("fdPrdtNm", ""),
        "subject": li.get("fdSbjt", ""),
        "category": cat,
        "category_top": parts[0] if parts else "",
        "category_sub": parts[1] if len(parts) > 1 else "",
        "color": li.get("clrNm", ""),
        "dep_place": li.get("depPlace", "") or detail.get("depPlace", ""),
        "found_at": li.get("fdYmd", "") or detail.get("fdYmd", ""),
        "found_place": detail.get("fdPlace", ""),   # 상세에만 존재(습득장소)
        "description": detail.get("uniq", ""),        # 상세에만 존재(특이사항)
        "image_url": li.get("fdFilePathImg", ""),
    }


def embed_text(doc: dict) -> str:
    """기존 색인과 동일한 임베딩 텍스트 공식(일관성). 향후 특이사항 포함 재색인은 별도 결정."""
    return f"{doc['name']}. 분류:{doc['category']}. 색상:{doc['color']}"


def doc_id(doc: dict) -> str:
    return f"{doc['atc_id']}_{doc['fd_sn']}"
