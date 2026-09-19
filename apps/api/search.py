"""검색 파이프라인 — 질의 임베딩 → OpenSearch KNN → 지역/시간 가점 → (선택) LLM grading.

eval에서 검증한 로직을 제품 경로로. 가점은 하드필터가 아니라 soft(코사인 점수에 가산).
기본 가중치 w_region=0.05 (eval 결론: 덧셈 소프트, 작게).
"""

from __future__ import annotations

import json
import urllib.request

from . import gemini
from .config import settings
from .region import resolver


def _os_raw(body: dict) -> dict:
    req = urllib.request.Request(
        f"{settings.opensearch_url}/{settings.opensearch_index}/_search",
        json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _os_search(body: dict) -> list[dict]:
    return _os_raw(body)["hits"]["hits"]


def _item(s: dict, _id: str = "", score: float = 0.0) -> dict:
    """OpenSearch _source → API용 습득물 dict(임베딩 제외). region 없으면 런타임 해석."""
    return {
        "atc_id": s.get("atc_id", _id.split("_")[0] if _id else ""),
        "fd_sn": s.get("fd_sn", "1"),
        "name": s.get("name", ""),
        "subject": s.get("subject", ""),
        "description": s.get("description", ""),
        "category": s.get("category", ""),
        "color": s.get("color", ""),
        "dep_place": s.get("dep_place", ""),
        "region": s.get("region") or resolver.resolve(s.get("dep_place", "")),
        "found_at": s.get("found_at", ""),
        "org_name": s.get("org_name", ""),
        "tel": s.get("tel", ""),
        "source": s.get("source", ""),
        "image_url": s.get("image_url", ""),
        "score": round(score, 4),
        "grade": None,
        "reason": None,
    }


def browse(q: str = "", region: str = "", date_from: str = "", date_to: str = "",
           page: int = 1, size: int = 24) -> dict:
    """전체 습득물 둘러보기 — BM25 키워드 + 지역/날짜 필터 + 최신순(임베딩·LLM 미사용, 무료·즉시).

    반환: {total, page, size, items, regions(집계)}. region 드롭다운은 regions 집계로 구성.
    """
    must: list[dict] = []
    if q:
        must.append({"multi_match": {
            "query": q, "fields": ["name^2", "subject", "description", "category"],
        }})
    filt: list[dict] = []
    if region:
        filt.append({"term": {"region": region}})
    if date_from or date_to:
        rng: dict[str, str] = {}
        if date_from:
            rng["gte"] = date_from
        if date_to:
            rng["lte"] = date_to
        filt.append({"range": {"found_at": rng}})

    body = {
        "from": max(0, (page - 1) * size), "size": size,
        "query": {"bool": {"must": must or [{"match_all": {}}], "filter": filt}},
        "sort": [{"found_at": {"order": "desc", "missing": "_last"}}, "_score"],
        "_source": {"excludes": ["embedding"]},
        "aggs": {"regions": {"terms": {"field": "region", "size": 100}}},
        "track_total_hits": True,
    }
    res = _os_raw(body)
    hits = res["hits"]["hits"]
    total = res["hits"]["total"]
    total = total["value"] if isinstance(total, dict) else total
    buckets = res.get("aggregations", {}).get("regions", {}).get("buckets", [])
    regions = [{"name": b["key"], "count": b["doc_count"]} for b in buckets if b["key"]]
    return {
        "total": total, "page": page, "size": size,
        "items": [_item(h["_source"], h["_id"], h.get("_score") or 0.0) for h in hits],
        "regions": regions,
    }


def search(
    q: str,
    region_set: list[str] | None = None,
    lost_date: str | None = None,
    do_grade: bool = False,
    size: int = 10,
    candidates: int = 100,
    w_region: float = 0.05,
    w_time: float = 0.0,
) -> list[dict]:
    region_set = region_set or []
    qv = gemini.embed_query(q)

    hits = _os_search({
        "size": candidates,
        "query": {"knn": {"embedding": {"vector": qv, "k": candidates}}},
        "_source": {"excludes": ["embedding"]},
    })

    # 가점: 임베딩 점수(base)에 지역/시간 가산
    scored = []
    for h in hits:
        s = h["_source"]
        base = h["_score"]
        region = resolver.resolve(s.get("dep_place", ""))
        score = base
        if w_region and region_set and resolver.match(region, region_set):
            score += w_region
        if w_time and lost_date and s.get("found_at", "") < lost_date:
            score -= w_time  # 습득일 < 분실일이면 감점
        scored.append({
            "atc_id": s.get("atc_id", h["_id"].split("_")[0]),
            "fd_sn": s.get("fd_sn", "1"),
            "name": s.get("name", ""),
            "subject": s.get("subject", ""),
            "description": s.get("description", ""),
            "category": s.get("category", ""),
            "color": s.get("color", ""),
            "dep_place": s.get("dep_place", ""),
            "region": region,
            "found_at": s.get("found_at", ""),
            "org_name": s.get("org_name", ""),
            "tel": s.get("tel", ""),
            "source": s.get("source", ""),
            "image_url": s.get("image_url", ""),
            "score": round(score, 4),
            "grade": None,
            "reason": None,
        })

    scored.sort(key=lambda it: -it["score"])
    top = scored[:size]

    # LLM grading (선택): top-K만 판정 후 재정렬
    if do_grade and top:
        gres = gemini.grade(q, top)
        gmap = {int(x["id"]): x for x in gres if "id" in x}
        for n, it in enumerate(top):
            g = gmap.get(n)
            if g:
                it["grade"] = g.get("score")
                it["reason"] = g.get("reason")
        top.sort(key=lambda it: -(it["grade"] if it["grade"] is not None else -1))

    return top
