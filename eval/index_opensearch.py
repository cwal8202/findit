"""코퍼스를 OpenSearch(found_items)에 색인.

임베딩 캐시가 있는 항목만 색인 → nori BM25 / KNN / 하이브리드를 동일 문서집합에서 비교.
문서 _id = "<atcId>_<fdSn>".

실행:  python eval/index_opensearch.py
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "data" / "corpus.json"
CACHE = ROOT / "data" / "emb_cache.json"
MAPPING = ROOT.parent / "infra" / "opensearch" / "mappings" / "found_items.json"
OS_URL = os.environ.get("OPENSEARCH_URL", "http://localhost:9200")  # 배포 시 http://opensearch:9200
INDEX = "found_items"


def ensure_index() -> None:
    """인덱스 없으면 매핑 적용해 생성(배포에서 별도 curl 불필요)."""
    try:
        urllib.request.urlopen(urllib.request.Request(f"{OS_URL}/{INDEX}", method="HEAD"), timeout=30)
        print(f"인덱스 {INDEX} 이미 존재")
        return
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    post(f"/{INDEX}", MAPPING.read_text(encoding="utf-8"), method="PUT")
    print(f"인덱스 {INDEX} 생성(매핑 적용)")


def post(path: str, body: str, method: str = "POST") -> dict:
    req = urllib.request.Request(
        f"{OS_URL}{path}", body.encode("utf-8"),
        {"Content-Type": "application/json"}, method=method,
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def to_doc(it: dict, emb: list[float]) -> dict:
    cat = it.get("cat", "")
    parts = [p.strip() for p in cat.split(">")]
    return {
        "atc_id": it["atcId"],
        "fd_sn": it.get("fdSn", "1"),
        "source": it.get("src", ""),
        "name": it.get("name", ""),
        "description": "",  # 상세(uniq)는 list에 없음 — 수집기 enrich 단계에서 채움
        "subject": it.get("subj", ""),
        "category": cat,
        "category_top": parts[0] if parts else "",
        "category_sub": parts[-1] if len(parts) > 1 else "",
        "color": it.get("color", ""),
        "dep_place": it.get("dep", ""),
        "found_at": it.get("ymd", None) or None,
        "embedding": emb,
    }


def main() -> None:
    ensure_index()
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    items = [it for it in corpus if it["atcId"] in cache]
    print(f"색인 대상 {len(items)}건 (임베딩 보유분)")

    lines: list[str] = []
    for it in items:
        _id = f"{it['atcId']}_{it.get('fdSn', '1')}"
        lines.append(json.dumps({"index": {"_index": INDEX, "_id": _id}}))
        lines.append(json.dumps(to_doc(it, cache[it["atcId"]]), ensure_ascii=False))
    ndjson = "\n".join(lines) + "\n"

    # 배치로 나눠 bulk (한 번에 너무 크지 않게)
    B = 200
    docs = list(zip(lines[0::2], lines[1::2]))
    errors = 0
    for i in range(0, len(docs), B):
        chunk = docs[i : i + B]
        body = "\n".join(a + "\n" + b for a, b in chunk) + "\n"
        res = post("/_bulk", body)
        if res.get("errors"):
            errors += sum(1 for x in res["items"] if x["index"].get("error"))
        print(f"  bulk {min(i+B, len(docs))}/{len(docs)}")
    post(f"/{INDEX}/_refresh", "", method="POST")
    cnt = post(f"/{INDEX}/_count", "{}")["count"]
    print(f"완료. 색인 문서수={cnt}, 오류={errors}")


if __name__ == "__main__":
    main()
