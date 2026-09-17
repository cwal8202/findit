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


def _os_search(body: dict) -> list[dict]:
    req = urllib.request.Request(
        f"{settings.opensearch_url}/{settings.opensearch_index}/_search",
        json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["hits"]["hits"]


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
