"""OpenSearch 실측: nori BM25 vs KNN vs 하이브리드 (동일 901건 / 골든셋).

- BM25: multi_match(name^2, subject, category, description) — 인덱스의 nori 분석기 적용
- KNN : knn_vector(embedding) 코사인
- HYB : 두 랭킹의 점수를 min-max 정규화 후 alpha 가중합 + alpha 스윕

실행:  python eval/run_os.py   (OpenSearch가 떠 있어야 함)
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from run_baseline import KS

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "data" / "corpus.json"
GOLDEN = ROOT / "golden" / "golden.json"
CACHE = ROOT / "data" / "emb_cache.json"
OS_URL = "http://localhost:9200"
INDEX = "found_items"
SIZE = 300


def search(body: dict) -> list[tuple[str, float]]:
    req = urllib.request.Request(
        f"{OS_URL}/{INDEX}/_search", json.dumps(body).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        hits = json.load(r)["hits"]["hits"]
    return [(h["_id"], h["_score"]) for h in hits]


def minmax(pairs: list[tuple[str, float]]) -> dict[str, float]:
    if not pairs:
        return {}
    vals = [s for _, s in pairs]
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    return {i: (s - lo) / rng for i, s in pairs}


def rank_of(order: list[str], target: str) -> int | None:
    return next((r for r, i in enumerate(order, 1) if i == target), None)


def metrics(ranks: list[int | None], n: int) -> dict[str, float]:
    out = {f"R@{k}": 0.0 for k in KS}
    rr = 0.0
    for rank in ranks:
        if rank:
            rr += 1.0 / rank
            for k in KS:
                if rank <= k:
                    out[f"R@{k}"] += 1
    for k in KS:
        out[f"R@{k}"] /= n
    out["MRR"] = rr / n
    return out


def main() -> None:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))["queries"]
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    fdsn = {c["atcId"]: c.get("fdSn", "1") for c in corpus}
    n = len(golden)

    per_q = []  # (target_id, bm_norm, kn_norm, bm_order, kn_order)
    for q in golden:
        tgt = f"{q['expected']}_{fdsn.get(q['expected'], '1')}"
        bm = search({
            "size": SIZE,
            "query": {"multi_match": {
                "query": q["query"],
                "fields": ["name^2", "subject", "category", "description"],
            }},
        })
        qv = cache[f"q::{q['id']}"]
        kn = search({
            "size": SIZE,
            "query": {"knn": {"embedding": {"vector": qv, "k": SIZE}}},
        })
        per_q.append((tgt, minmax(bm), minmax(kn), [i for i, _ in bm], [i for i, _ in kn]))

    # 표: nori-BM25 / KNN / HYB(0.5)
    print(f"코퍼스 {INDEX} 901건 / 질의 {n}개 / nori BM25\n")
    print(f"{'id':4} {'BM25':>5} {'KNN':>5} {'HYB':>5}  질의")
    print("-" * 70)
    r_bm, r_kn, r_hy = [], [], []
    for q, (tgt, bmn, knn, bmo, kno) in zip(golden, per_q):
        rb = rank_of(bmo, tgt)
        rk = rank_of(kno, tgt)
        ids = set(bmn) | set(knn)
        hy = {i: 0.5 * bmn.get(i, 0) + 0.5 * knn.get(i, 0) for i in ids}
        hyo = [i for i, _ in sorted(hy.items(), key=lambda x: x[1], reverse=True)]
        rh = rank_of(hyo, tgt)
        r_bm.append(rb); r_kn.append(rk); r_hy.append(rh)
        f = lambda r: (str(r) if r else "-")
        print(f"{q['id']:4} {f(rb):>5} {f(rk):>5} {f(rh):>5}  {q['query']}")

    print("-" * 70)
    mb, mk, mh = metrics(r_bm, n), metrics(r_kn, n), metrics(r_hy, n)
    print(f"{'':6} {'BM25':>6} {'KNN':>6} {'HYB':>6}")
    for k in KS:
        print(f"R@{k:<3} {mb[f'R@{k}']:>6.3f} {mk[f'R@{k}']:>6.3f} {mh[f'R@{k}']:>6.3f}")
    print(f"MRR   {mb['MRR']:>6.3f} {mk['MRR']:>6.3f} {mh['MRR']:>6.3f}")

    # alpha 스윕 (BM25 비중)
    print("\n=== 하이브리드 가중치 스윕 (alpha=BM25 비중) ===")
    print(f"{'alpha':>6} {'R@1':>6} {'R@5':>6} {'MRR':>6}")
    best = (-1.0, 0.0)
    for step in range(11):
        a = step / 10.0
        ranks = []
        for tgt, bmn, knn, _, _ in per_q:
            ids = set(bmn) | set(knn)
            hy = {i: a * bmn.get(i, 0) + (1 - a) * knn.get(i, 0) for i in ids}
            ranks.append(rank_of([i for i, _ in sorted(hy.items(), key=lambda x: x[1], reverse=True)], tgt))
        m = metrics(ranks, n)
        tag = "  ←KNN" if a == 0 else ("  ←BM25" if a == 1 else "")
        print(f"{a:>6.1f} {m['R@1']:>6.3f} {m['R@5']:>6.3f} {m['MRR']:>6.3f}{tag}")
        if m["MRR"] > best[0]:
            best = (m["MRR"], a)
    print(f"\n최적 alpha={best[1]:.1f} (BM25 {best[1]*100:.0f}%:KNN {(1-best[1])*100:.0f}%), MRR={best[0]:.3f}")


if __name__ == "__main__":
    main()
