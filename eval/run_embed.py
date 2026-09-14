"""FindIt 매칭 성능 — 임베딩 & 하이브리드 (BM25+KNN) 비교.

Gemini 임베딩(768-dim) 코사인 유사도로 골든셋을 채점하고, BM25 baseline과
하이브리드(min-max 정규화 후 가중합)를 같은 골든셋으로 비교한다.

- 외부 의존성 없음(stdlib urllib). 코퍼스 임베딩은 eval/data/emb_cache.json에 캐시.
- 키는 .env 의 GEMINI_API_KEY 사용.

실행:  python eval/run_embed.py
"""

from __future__ import annotations

import json
import math
import time
import urllib.request
from pathlib import Path

from run_baseline import BM25, KS, tokenize, doc_text  # baseline 재사용

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "data" / "corpus.json"
GOLDEN = ROOT / "golden" / "golden.json"
CACHE = ROOT / "data" / "emb_cache.json"
ENV = ROOT.parent / ".env"


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


ENVV = load_env()
API_KEY = ENVV["GEMINI_API_KEY"]
MODEL = ENVV.get("GEMINI_EMBED_MODEL", "gemini-embedding-001")
DIM = int(ENVV.get("GEMINI_EMBED_DIM", "768"))
BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def embed_batch(texts: list[str], max_retry: int = 8) -> list[list[float]]:
    """batchEmbedContents 로 한 번에 임베딩. 429/5xx는 크래시 없이 참을성 있게 재시도."""
    url = f"{BASE}/{MODEL}:batchEmbedContents?key={API_KEY}"
    body = {
        "requests": [
            {
                "model": f"models/{MODEL}",
                "content": {"parts": [{"text": t}]},
                "outputDimensionality": DIM,
            }
            for t in texts
        ]
    }
    payload = json.dumps(body).encode()
    for attempt in range(max_retry):
        req = urllib.request.Request(
            url, payload, {"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.load(r)
            return [e["values"] for e in data["embeddings"]]
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and attempt < max_retry - 1:
                wait = min(60, 5 * (attempt + 1))  # 5,10,...,60s 이후 60s 고정
                print(f"    HTTP {e.code} → {wait}s 대기 후 재시도 ({attempt+1}/{max_retry})")
                time.sleep(wait)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            # 절전/네트워크 끊김 등 → 잠깐 쉬고 재시도
            if attempt < max_retry - 1:
                print(f"    네트워크 오류({e}) → 30s 후 재시도 ({attempt+1}/{max_retry})")
                time.sleep(30)
                continue
            raise
    raise RuntimeError("embed_batch: 재시도 소진")


def embed_texts(texts: list[str], chunk: int = 50) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), chunk):
        out += embed_batch(texts[i : i + chunk])
        print(f"    embedded {min(i+chunk, len(texts))}/{len(texts)}")
        time.sleep(1.5)
    return out


def norm(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def cos_ranked(qv: list[float], mat: list[list[float]]) -> list[tuple[int, float]]:
    scores = [(i, sum(a * b for a, b in zip(qv, row))) for i, row in enumerate(mat)]
    return sorted(scores, key=lambda x: x[1], reverse=True)


def minmax(scores: dict[int, float]) -> dict[int, float]:
    if not scores:
        return {}
    lo, hi = min(scores.values()), max(scores.values())
    rng = (hi - lo) or 1.0
    return {i: (s - lo) / rng for i, s in scores.items()}


def metrics(ranks: list[int | None], n: int) -> tuple[dict[int, float], float]:
    rec = {k: 0 for k in KS}
    rr = 0.0
    for rank in ranks:
        if rank:
            rr += 1.0 / rank
            for k in KS:
                if rank <= k:
                    rec[k] += 1
    return {k: rec[k] / n for k in KS}, rr / n


def main() -> None:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))["queries"]
    id_to_idx = {it["atcId"]: i for i, it in enumerate(corpus)}
    emb_text = lambda it: f"{it['name']}. 분류:{it['cat']}. 색상:{it['color']}"

    # --- 코퍼스 임베딩 (작은 배치·페이싱, rate limit이면 우아하게 중단) ---
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}

    def embed_into_cache(items: list[dict], chunk: int, label: str) -> bool:
        """items를 청크 단위로 임베딩해 캐시 저장. 실패 시 False 반환(크래시 X)."""
        todo = [it for it in items if it["atcId"] not in cache]
        if not todo:
            return True
        print(f"{label}: {len(todo)}건 임베딩 (캐시 {len(cache)})")
        for i in range(0, len(todo), chunk):
            part = todo[i : i + chunk]
            try:
                vecs = embed_batch([emb_text(it) for it in part])
            except Exception as e:  # noqa: BLE001
                print(f"  ! rate limit/오류로 중단: {e}\n  → 캐시된 것만으로 평가 진행")
                return False
            for it, v in zip(part, vecs):
                cache[it["atcId"]] = v
            CACHE.write_text(json.dumps(cache), encoding="utf-8")
            print(f"  embedded {min(i+chunk, len(todo))}/{len(todo)} (총 캐시 {len(cache)})")
            time.sleep(0.5)
        return True

    # 1) 골든 정답은 반드시 확보
    targets = [corpus[id_to_idx[q["expected"]]] for q in golden if q["expected"] in id_to_idx]
    embed_into_cache(targets, chunk=6, label="골든 정답")
    # 2) 나머지 코퍼스 top-up: 무료 쿼터 절약 위해 기본 skip (환경변수 FULL=1이면 시도)
    import os
    if os.environ.get("FULL") == "1":
        embed_into_cache(corpus, chunk=100, label="코퍼스 top-up")

    # 평가 대상 = 임베딩이 확보된 항목만
    corpus = [it for it in corpus if it["atcId"] in cache]
    id_to_idx = {it["atcId"]: i for i, it in enumerate(corpus)}
    mat = [norm(cache[it["atcId"]]) for it in corpus]
    missing = [q["id"] for q in golden if q["expected"] not in id_to_idx]
    if missing:
        print(f"  ⚠ 임베딩 미확보 골든: {missing} (해당 질의는 정답이 코퍼스에 없어 -로 표기)")

    # --- 질의 임베딩 (캐시: emb_cache 안에 q::<id> 키로, 소청크 저장) ---
    print("질의 임베딩...")
    q_todo = [q for q in golden if f"q::{q['id']}" not in cache]
    qchunk = 6
    for i in range(0, len(q_todo), qchunk):
        part = q_todo[i : i + qchunk]
        vecs = embed_batch([q["query"] for q in part])
        for q, v in zip(part, vecs):
            cache[f"q::{q['id']}"] = v
        CACHE.write_text(json.dumps(cache), encoding="utf-8")
        print(f"  질의 {min(i+qchunk, len(q_todo))}/{len(q_todo)}")
        time.sleep(4)
    qvecs = [norm(cache[f"q::{q['id']}"]) for q in golden]

    # --- BM25 준비 ---
    bm25 = BM25([tokenize(doc_text(it)) for it in corpus])

    print(f"\n코퍼스 {len(corpus)}건 / 질의 {len(golden)}개 / dim={DIM}\n")
    hdr = f"{'id':4} {'BM25':>6} {'EMB':>6} {'HYB':>6}  질의"
    print(hdr)
    print("-" * 78)

    r_bm, r_em, r_hy = [], [], []
    for q, qv in zip(golden, qvecs):
        target = id_to_idx.get(q["expected"])

        bm_list = bm25.search(tokenize(q["query"]))
        em_list = cos_ranked(qv, mat)

        def rank_of(ranked):
            return next((r for r, (i, _) in enumerate(ranked, 1) if i == target), None)

        # 하이브리드: 각 점수 min-max 정규화 후 0.5:0.5 가중합
        bm_n = minmax(dict(bm_list))
        em_n = minmax(dict(em_list))
        hy = {i: 0.5 * bm_n.get(i, 0) + 0.5 * em_n.get(i, 0) for i in range(len(corpus))}
        hy_list = sorted(hy.items(), key=lambda x: x[1], reverse=True)

        rb, re_, rh = rank_of(bm_list), rank_of(em_list), rank_of(hy_list)
        r_bm.append(rb); r_em.append(re_); r_hy.append(rh)
        f = lambda r: (f"{r}" if r else "-")
        hard = "  ★" if q.get("synonym_hard") else ""
        print(f"{q['id']:4} {f(rb):>6} {f(re_):>6} {f(rh):>6}  {q['query']}{hard}")

    n = len(golden)
    print("-" * 78)
    print(f"{'':4} {'BM25':>6} {'EMB':>6} {'HYB':>6}")
    for k in KS:
        mb = metrics(r_bm, n)[0][k]; me = metrics(r_em, n)[0][k]; mh = metrics(r_hy, n)[0][k]
        print(f"R@{k:<2} {mb:>6.3f} {me:>6.3f} {mh:>6.3f}")
    print(f"MRR  {metrics(r_bm,n)[1]:>6.3f} {metrics(r_em,n)[1]:>6.3f} {metrics(r_hy,n)[1]:>6.3f}")
    print("\n★ = 한글↔영문 브랜드 동의어 난도 케이스")


if __name__ == "__main__":
    main()
