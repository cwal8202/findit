"""검색 데모 — 아무 문장이나 넣으면 비슷한 습득물을 찾아 보여준다.

문장 → Gemini 임베딩(뜻) → OpenSearch KNN(의미검색) → 상위 결과 출력.
실제 사용자 질의처럼 자유 입력을 테스트하는 용도.

실행:
  python eval/search_demo.py "지하철에 흰색 무선이어폰 두고 내렸어요"
  python eval/search_demo.py            # 인자 없으면 예시 질의 몇 개 자동 실행
"""

from __future__ import annotations

import json
import sys
import urllib.request

from run_embed import embed_batch  # Gemini 임베딩 재사용

OS_URL = "http://localhost:9200"
INDEX = "found_items"


def knn_search(qv: list[float], show_n: int = 10, candidates: int = 100) -> list[dict]:
    # 근사 KNN(HNSW)은 후보를 넉넉히(candidates) 뽑아야 진짜 정답을 놓치지 않는다.
    # 그 뒤 상위 show_n개만 보여준다.
    body = {"size": candidates, "query": {"knn": {"embedding": {"vector": qv, "k": candidates}}}}
    req = urllib.request.Request(
        f"{OS_URL}/{INDEX}/_search", json.dumps(body).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["hits"]["hits"][:show_n]


def show(query: str) -> None:
    print(f"\n🔎 질의: \"{query}\"")
    qv = embed_batch([query])[0]
    hits = knn_search(qv, show_n=10, candidates=100)
    print(f"   → 비슷한 습득물 {len(hits)}건:")
    for rank, h in enumerate(hits, 1):
        s = h["_source"]
        score = h["_score"]
        print(
            f"   {rank:2}. [{score:.3f}] {s['name']}"
            f"  · {s['color']} · {s['category_top']}"
            f"  · 보관:{s['dep_place']} ({s['source']}) · {s.get('found_at','')}"
        )


def main() -> None:
    if len(sys.argv) > 1:
        show(" ".join(sys.argv[1:]))
        return
    # 인자 없으면: 골든셋에 없던 '자유 질의' 예시로 실제 동작 확인
    for q in [
        "지하철에 흰색 무선이어폰 두고 내렸어요",
        "검은색 장지갑 잃어버림, 카드 여러 장 들어있음",
        "회색 아이폰 케이스 낀 채로 분실",
        "명품 여성 가방 잃어버렸어요",
    ]:
        show(q)


if __name__ == "__main__":
    main()
