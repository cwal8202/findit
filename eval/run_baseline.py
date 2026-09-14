"""FindIt 매칭 성능 baseline (인프라/API 키 불필요).

순수 파이썬 BM25로 골든셋을 채점한다. 한국어 형태소 분석기 없이
'문자 바이그램 + 영문 단어' 토큰화를 쓴다(가벼운 근사 baseline).
나중에 nori/임베딩으로 교체 시 이 숫자가 하한선 비교 기준이 된다.

실행:  python eval/run_baseline.py
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "data" / "corpus.json"
GOLDEN = ROOT / "golden" / "golden.json"

K1, B = 1.5, 0.75
KS = (1, 5, 10)


def tokenize(text: str) -> list[str]:
    """문자 바이그램 + 영문/숫자 단어. 한국어 형태소 분석기 대용 근사."""
    text = text.lower()
    tokens: list[str] = []
    # 영문/숫자 연속열은 단어 토큰으로
    tokens += re.findall(r"[a-z0-9]+", text)
    # 한글은 문자 바이그램으로 (공백/기호 제거 후)
    hangul = re.sub(r"[^가-힣]", "", text)
    tokens += [hangul[i : i + 2] for i in range(len(hangul) - 1)]
    return tokens


def doc_text(item: dict) -> str:
    """검색 대상 텍스트. 물품명을 2회 넣어 가중(간이 필드 부스팅)."""
    name = item.get("name", "")
    return " ".join([name, name, item.get("cat", ""), item.get("color", "")])


class BM25:
    def __init__(self, docs: list[list[str]]):
        self.docs = docs
        self.N = len(docs)
        self.len = [len(d) for d in docs]
        self.avgdl = sum(self.len) / self.N if self.N else 0.0
        self.tf = [Counter(d) for d in docs]
        df: Counter[str] = Counter()
        for d in docs:
            df.update(set(d))
        self.idf = {
            t: math.log(1 + (self.N - n + 0.5) / (n + 0.5)) for t, n in df.items()
        }
        self.postings: dict[str, list[int]] = defaultdict(list)
        for i, d in enumerate(docs):
            for t in set(d):
                self.postings[t].append(i)

    def search(self, query: list[str]) -> list[tuple[int, float]]:
        scores: dict[int, float] = defaultdict(float)
        for t in query:
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i in self.postings[t]:
                f = self.tf[i][t]
                denom = f + K1 * (1 - B + B * self.len[i] / self.avgdl)
                scores[i] += idf * (f * (K1 + 1)) / denom
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def main() -> None:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))["queries"]
    id_to_idx = {it["atcId"]: i for i, it in enumerate(corpus)}

    bm25 = BM25([tokenize(doc_text(it)) for it in corpus])

    print(f"코퍼스 {len(corpus)}건 / 질의 {len(golden)}개 / baseline=BM25(char-bigram)\n")
    print(f"{'id':4} {'정답순위':>7}  질의")
    print("-" * 68)

    recall = {k: 0 for k in KS}
    rr_sum = 0.0
    for q in golden:
        ranked = bm25.search(tokenize(q["query"]))
        target = id_to_idx.get(q["expected"])
        rank = next((r for r, (i, _) in enumerate(ranked, 1) if i == target), None)
        if rank:
            rr_sum += 1.0 / rank
            for k in KS:
                if rank <= k:
                    recall[k] += 1
        mark = "" if not q.get("synonym_hard") else "  [동의어난도]"
        rank_s = f"{rank}위" if rank else "미포함"
        print(f"{q['id']:4} {rank_s:>7}  {q['query']}{mark}")
        # 상위 3개 미리보기
        for i, sc in ranked[:3]:
            it = corpus[i]
            hit = "←정답" if i == target else ""
            print(f"       · {sc:5.2f} {it['name']} / {it['color']} / {it['cat']} {hit}")

    n = len(golden)
    print("-" * 68)
    print("종합 지표")
    for k in KS:
        print(f"  Recall@{k:<2} = {recall[k]/n:.3f}  ({recall[k]}/{n})")
    print(f"  MRR      = {rr_sum/n:.3f}")


if __name__ == "__main__":
    main()
