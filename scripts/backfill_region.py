"""기존 색인 문서에 region(구/시) 필드 백필 — dep_place → gazetteer 해석. Gemini 비용 0, 멱등.

'전체 둘러보기' 지역 필터·집계는 저장된 keyword `region`이 필요. 이미 색인된 문서는 비어 있으므로
한 번 채운다(임베딩 재계산 없음, 스크롤+bulk update). 신규 수집분은 collector가 색인 시 채움.

실행(서버):
  docker compose -f docker-compose.prod.yml exec -T backend uv run python -m scripts.backfill_region
"""

from __future__ import annotations

import json
import urllib.request

from apps.api.config import settings
from apps.api.region import resolver

OS = settings.opensearch_url
IDX = settings.opensearch_index


def _req(path: str, body: dict | None, method: str = "POST", ndjson: bool = False) -> dict:
    data = None
    if body is not None:
        data = ("\n".join(body["_lines"]) + "\n").encode("utf-8") if ndjson else json.dumps(body).encode("utf-8")
    ctype = "application/x-ndjson" if ndjson else "application/json"
    req = urllib.request.Request(f"{OS}{path}", data, {"Content-Type": ctype}, method=method)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def main() -> None:
    # region 필드가 keyword로 매핑되도록 보장(term 필터·terms 집계용). 새 필드 추가는 허용됨.
    _req(f"/{IDX}/_mapping", {"properties": {"region": {"type": "keyword"}}}, method="PUT")

    res = _req(f"/{IDX}/_search?scroll=2m",
               {"size": 500, "_source": ["dep_place", "region"], "query": {"match_all": {}}})
    sid = res["_scroll_id"]
    hits = res["hits"]["hits"]
    updated = skipped = 0
    while hits:
        lines: list[str] = []
        for h in hits:
            s = h["_source"]
            reg = resolver.resolve(s.get("dep_place", ""))
            if reg == (s.get("region") or ""):
                skipped += 1
                continue
            lines.append(json.dumps({"update": {"_index": IDX, "_id": h["_id"]}}))
            lines.append(json.dumps({"doc": {"region": reg}}, ensure_ascii=False))
        if lines:
            br = _req("/_bulk", {"_lines": lines}, ndjson=True)
            updated += sum(1 for it in br.get("items", []) if it.get("update", {}).get("status", 500) < 300)
        res = _req("/_search/scroll", {"scroll": "2m", "scroll_id": sid})
        sid = res["_scroll_id"]
        hits = res["hits"]["hits"]

    _req(f"/{IDX}/_refresh", None)
    print(f"region 백필 완료: 업데이트 {updated}건, 스킵(이미 동일) {skipped}건")


if __name__ == "__main__":
    main()
