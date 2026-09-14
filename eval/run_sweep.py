"""하이브리드 가중치 스윕 (API 호출 0 — 캐시된 임베딩 + BM25 재사용).

hybrid = alpha * BM25_norm + (1-alpha) * EMB_norm 을 alpha=0.0~1.0 로 훑어
각 지점의 Recall@1/5/10, MRR 을 측정한다. 최적 alpha 를 찾는다.

실행:  python eval/run_sweep.py
"""

from __future__ import annotations

import json
from pathlib import Path

from run_baseline import BM25, KS, tokenize, doc_text
from run_embed import CACHE, CORPUS, GOLDEN, cos_ranked, minmax, norm

ROOT = Path(__file__).resolve().parent


def rank_of(ranked: list[tuple[int, float]], target: int | None) -> int | None:
    return next((r for r, (i, _) in enumerate(ranked, 1) if i == target), None)


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

    # 임베딩 확보된 항목만
    corpus = [it for it in corpus if it["atcId"] in cache]
    id_to_idx = {it["atcId"]: i for i, it in enumerate(corpus)}
    mat = [norm(cache[it["atcId"]]) for it in corpus]
    bm25 = BM25([tokenize(doc_text(it)) for it in corpus])
    n = len(golden)
    print(f"코퍼스 {len(corpus)}건 / 질의 {n}개\n")

    # 질의별 BM25/EMB 정규화 점수 사전계산
    per_q = []
    for q in golden:
        target = id_to_idx.get(q["expected"])
        bm_n = minmax(dict(bm25.search(tokenize(q["query"]))))
        qv = norm(cache[f"q::{q['id']}"])
        em_n = minmax(dict(cos_ranked(qv, mat)))
        per_q.append((target, bm_n, em_n))

    print(f"{'alpha(BM25)':>11} {'R@1':>6} {'R@5':>6} {'R@10':>6} {'MRR':>6}")
    print("-" * 44)
    best = (-1.0, -1.0)  # (mrr, alpha)
    rows = []
    for step in range(11):
        alpha = step / 10.0
        ranks = []
        for target, bm_n, em_n in per_q:
            hy = {
                i: alpha * bm_n.get(i, 0.0) + (1 - alpha) * em_n.get(i, 0.0)
                for i in range(len(corpus))
            }
            ranks.append(rank_of(sorted(hy.items(), key=lambda x: x[1], reverse=True), target))
        m = metrics(ranks, n)
        rows.append((alpha, m))
        tag = ""
        if alpha == 0.0:
            tag = "  ← 순수 EMB"
        elif alpha == 1.0:
            tag = "  ← 순수 BM25"
        print(f"{alpha:>11.1f} {m['R@1']:>6.3f} {m['R@5']:>6.3f} {m['R@10']:>6.3f} {m['MRR']:>6.3f}{tag}")
        if m["MRR"] > best[0]:
            best = (m["MRR"], alpha)
    print("-" * 44)
    print(f"최적 alpha = {best[1]:.1f} (BM25 {best[1]*100:.0f}% : EMB {(1-best[1])*100:.0f}%), MRR = {best[0]:.3f}")


if __name__ == "__main__":
    main()
