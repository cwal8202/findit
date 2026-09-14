"""FindIt 가점(boosting) 실험 — 임베딩 위에 지역/시간/카테고리/색상 가점을 얹고 eval.

핵심 질문: 장소(지역) 가점이 "비슷한 것 수백 개" 중 정답을 실제로 위로 올리나?
그리고 어느 가중치부터 기존 점수가 흐려지나(over-boost)?

- 오프라인: 코퍼스/질의 임베딩은 emb_cache.json 재사용(없으면 Gemini로 소량 임베딩 후 캐시).
- 지역 해석: dep(보관장소) → 구/시 (eval/data/dep_gazetteer.json, build_gazetteer.py 산출).
- 가점은 하드필터가 아니라 코사인 점수에 가산(soft boost). 가중치=0이면 순수 임베딩과 동일.

실행:
  python eval/run_boost.py                 # golden_hard.json 로 가중치 스윕
  python eval/run_boost.py --probe "검정 지갑"   # 질의 임베딩→상위 후보+지역 확인(케이스 제작용)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from run_embed import cos_ranked, embed_batch, norm  # 임베딩 유틸 재사용

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "data" / "corpus.json"
CACHE = ROOT / "data" / "emb_cache.json"
GAZ = ROOT / "data" / "dep_gazetteer.json"
HARD = ROOT / "golden" / "golden_hard.json"
KS = (1, 3, 5, 10)


# ---------- 지역 해석기 (build_gazetteer 3단계 폴백과 동일 규칙) ----------
def make_resolver(gaz: dict):
    st, ps, toks = gaz["stations"], gaz["police_stations"], gaz["region_tokens"]
    names = sorted(gaz.get("name_to_region", {}).items(), key=lambda kv: -len(kv[0]))
    geo_path = ROOT / "data" / "dep_geocoded.json"  # 카카오 지오코딩 캐시(있으면 폴백)
    geocoded = json.loads(geo_path.read_text(encoding="utf-8")) if geo_path.exists() else {}
    memo: dict[str, str] = {}

    def resolve(dep: str) -> str:
        if dep in memo:
            return memo[dep]
        memo[dep] = _resolve(dep)
        return memo[dep]

    def _resolve(dep: str) -> str:
        if dep in st:
            return st[dep]["region"]
        if dep.endswith("경찰서") and dep[:-3] in ps:
            return ps[dep[:-3]]
        for suf in ("지구대", "파출소", "치안센터", "출장소"):
            if dep.endswith(suf) and dep[: -len(suf)] in st:
                return st[dep[: -len(suf)]]["region"]
        for t in toks:
            if t in dep:
                return t
        for short, reg in names:  # 도시/구 짧은이름 substring(괄호 안 포함)
            if short in dep:
                return reg
        return geocoded.get(dep, "")  # 최후: 카카오 지오코딩 캐시

    return resolve


def region_match(resolved: str, region_set: list[str]) -> bool:
    """resolved(예 '전라남도 순천시')가 region_set(예 ['순천','광주'])의 어느 항목이라도 포함하면 True."""
    return any(r and r in resolved for r in region_set)


# ---------- 임베딩 로딩(캐시 우선, 없으면 소량 임베딩) ----------
def load_cache() -> dict:
    return json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}


def ensure_query_vec(cache: dict, key: str, text: str) -> list[float]:
    if key in cache:
        return cache[key]
    print(f"  · 캐시에 없어 임베딩: {key} = {text!r}")
    vec = embed_batch([text])[0]
    cache[key] = vec
    CACHE.write_text(json.dumps(cache), encoding="utf-8")
    return vec


def emb_text(it: dict) -> str:
    return f"{it['name']}. 분류:{it['cat']}. 색상:{it['color']}"


# ---------- 채점 ----------
def metrics(ranks: list[int | None], n: int) -> dict[str, float]:
    out = {f"R@{k}": 0.0 for k in KS}
    rr = 0.0
    for r in ranks:
        if r:
            rr += 1.0 / r
            for k in KS:
                if r <= k:
                    out[f"R@{k}"] += 1
    for k in KS:
        out[f"R@{k}"] /= n
    out["MRR"] = rr / n
    return out


_BASE_CACHE: dict[int, dict] = {}  # id(qv) -> {idx: cosine}, 스윕 중 재계산 방지


def score_and_rank(qv, corpus, mat, resolve, hint, weights, mode="add") -> list[tuple[int, float]]:
    """임베딩 코사인 + 가점 → 재정렬된 (idx, score).
    mode='add'  : s += w  (덧셈, 스케일 무시 → 확신 높은 매칭도 흔들 수 있음)
    mode='mult' : s *= (1+w)  (곱셈, 코사인 크기=확신도 보존 → 애매한 군집만 재정렬)
    """
    base = _BASE_CACHE.get(id(qv))
    if base is None:
        base = dict(cos_ranked(qv, mat))  # idx -> cosine (질의당 1회)
        _BASE_CACHE[id(qv)] = base
    wr, wt, wc, wcol = weights
    rs = hint.get("region_set") or []
    loss = hint.get("loss_date") or ""
    cat_h = hint.get("cat_hint") or ""
    col_h = hint.get("color_hint") or ""

    def apply(s, w, hit):
        if not w or not hit:
            return s
        return s * (1 + w) if mode == "mult" else s + w

    scored = []
    for i, it in enumerate(corpus):
        s = base.get(i, 0.0)
        s = apply(s, wr, rs and region_match(resolve(it["dep"]), rs))
        if wt and loss and it["ymd"] < loss:  # 습득일<분실일이면 감점(양쪽 모드 공통)
            s = s * (1 - wt) if mode == "mult" else s - wt
        s = apply(s, wc, cat_h and cat_h in it["cat"])
        s = apply(s, wcol, col_h and col_h in it["color"])
        scored.append((i, s))
    return sorted(scored, key=lambda x: x[1], reverse=True)


def rank_of(ranked, target_idx):
    return next((r for r, (i, _) in enumerate(ranked, 1) if i == target_idx), None)


# ---------- probe: 케이스 제작용 ----------
def probe(query: str, topn: int = 25) -> None:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    cache = load_cache()
    corpus = [it for it in corpus if it["atcId"] in cache]
    mat = [norm(cache[it["atcId"]]) for it in corpus]
    resolve = make_resolver(json.loads(GAZ.read_text(encoding="utf-8")))
    qv = norm(ensure_query_vec(cache, f"probe::{query}", query))
    ranked = cos_ranked(qv, mat)
    print(f"\n질의 {query!r} — 임베딩 상위 {topn} (지역=gazetteer 해석)\n")
    print(f"{'순위':>3} {'atcId':18} {'지역':14} {'ymd':11} 물품/색상")
    for r, (i, sc) in enumerate(ranked[:topn], 1):
        it = corpus[i]
        reg = resolve(it["dep"]) or "(미해석)"
        print(f"{r:>3} {it['atcId']:18} {reg:14} {it['ymd']:11} {it['name']}/{it['color']}")


# ---------- 메인: 가중치 스윕 ----------
def main() -> None:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    cache = load_cache()
    corpus = [it for it in corpus if it["atcId"] in cache]
    id2idx = {it["atcId"]: i for i, it in enumerate(corpus)}
    mat = [norm(cache[it["atcId"]]) for it in corpus]
    resolve = make_resolver(json.loads(GAZ.read_text(encoding="utf-8")))
    hard = json.loads(HARD.read_text(encoding="utf-8"))["cases"]

    # 질의 벡터 확보 (동일 query는 재사용 → probe 캐시와 키 공유해 재임베딩 방지)
    for h in hard:
        h["_qv"] = norm(ensure_query_vec(cache, f"probe::{h['query']}", h["query"]))
        h["_tgt"] = id2idx.get(h["expected"])

    n = len(hard)
    miss = [h["id"] for h in hard if h["_tgt"] is None]
    if miss:
        print(f"⚠ 정답이 코퍼스(캐시)에 없음: {miss}")

    # 1) EMB-only 기준
    print(f"hard 케이스 {n}개 / 코퍼스 {len(corpus)}건\n")
    print("=== [1] EMB-only vs EMB+지역가점(w=0.15) 항목별 순위 ===")
    print(f"{'id':4} {'EMB':>5} {'+지역':>6}  질의 / 지역set")
    base_ranks, boost_ranks = [], []
    W = (0.15, 0.0, 0.0, 0.0)  # 지역만 켜기
    for h in hard:
        rb = rank_of(score_and_rank(h["_qv"], corpus, mat, resolve, h, (0, 0, 0, 0)), h["_tgt"])
        rr = rank_of(score_and_rank(h["_qv"], corpus, mat, resolve, h, W), h["_tgt"])
        base_ranks.append(rb); boost_ranks.append(rr)
        f = lambda r: str(r) if r else "-"
        print(f"{h['id']:4} {f(rb):>5} {f(rr):>6}  {h['query']} / {h.get('region_set')}")
    mb, mr = metrics(base_ranks, n), metrics(boost_ranks, n)
    print(f"\n{'':6}{'R@1':>7}{'R@3':>7}{'R@5':>7}{'MRR':>7}")
    print(f"EMB   {mb['R@1']:>7.3f}{mb['R@3']:>7.3f}{mb['R@5']:>7.3f}{mb['MRR']:>7.3f}")
    print(f"+지역 {mr['R@1']:>7.3f}{mr['R@3']:>7.3f}{mr['R@5']:>7.3f}{mr['MRR']:>7.3f}")

    # 2) 지역 가중치 스윕 (0.0 ~ 0.6) — 어디서 오르고 어디서 흐려지나
    print("\n=== [2] 지역 가중치 스윕 (다른 가점 off) ===")
    print(f"{'w_region':>8}{'R@1':>7}{'R@3':>7}{'MRR':>7}")
    for step in range(0, 13):
        w = round(step * 0.05, 2)
        ranks = [rank_of(score_and_rank(h["_qv"], corpus, mat, resolve, h, (w, 0, 0, 0)), h["_tgt"]) for h in hard]
        m = metrics(ranks, n)
        print(f"{w:>8.2f}{m['R@1']:>7.3f}{m['R@3']:>7.3f}{m['MRR']:>7.3f}")

    # 3.5) 흐림(muddying) 스트레스 테스트: 강한 브랜드 질의(기존 29문항)에
    #      "틀린 지역"을 줬을 때 정답이 묻히나? (순천→광주: 정답이 추측 지역 밖일 때)
    GOLDEN = ROOT / "golden" / "golden.json"
    g29 = json.loads(GOLDEN.read_text(encoding="utf-8"))["queries"]
    g29 = [q for q in g29 if f"q::{q['id']}" in cache and q["expected"] in id2idx]
    for q in g29:
        q["_qv"] = norm(cache[f"q::{q['id']}"])
        q["_tgt"] = id2idx[q["expected"]]
        tgt_reg = resolve(corpus[q["_tgt"]]["dep"])
        # 일부러 틀린 지역집합: 정답 지역이 서울이면 부산, 아니면 서울
        q["_wrong"] = {"region_set": ["부산"] if "서울" in tgt_reg else ["서울"]}
    ng = len(g29)
    print(f"\n=== [3.5] 흐림 테스트: 브랜드 질의 {ng}개에 '틀린 지역' 가점 (정답은 그 지역 밖) ===")
    print("  → MRR이 유지되면 안전, 급락하면 과가점. 안전한 상한 가중치 찾기")
    print(f"{'w_region':>8}{'R@1':>7}{'MRR':>7}")
    for step in (0, 1, 2, 3, 4, 6, 8, 12):
        w = round(step * 0.05, 2)
        ranks = [rank_of(score_and_rank(q["_qv"], corpus, mat, resolve, q["_wrong"], (w, 0, 0, 0)), q["_tgt"]) for q in g29]
        m = metrics(ranks, ng)
        print(f"{w:>8.2f}{m['R@1']:>7.3f}{m['MRR']:>7.3f}")

    # 3) 시간·카테고리·색상 개별 추가 효과 (지역 0.15 고정 위에)
    print("\n=== [3] 지역(0.15) 위에 시간/카테고리/색상 추가 시 델타 ===")
    combos = {
        "지역": (0.15, 0, 0, 0),
        "지역+시간": (0.15, 0.10, 0, 0),
        "지역+카테고리": (0.15, 0, 0.10, 0),
        "지역+색상": (0.15, 0, 0, 0.10),
        "전부": (0.15, 0.10, 0.10, 0.10),
    }
    print(f"{'조합':14}{'R@1':>7}{'R@3':>7}{'MRR':>7}")
    for name, w in combos.items():
        ranks = [rank_of(score_and_rank(h["_qv"], corpus, mat, resolve, h, w), h["_tgt"]) for h in hard]
        m = metrics(ranks, n)
        print(f"{name:14}{m['R@1']:>7.3f}{m['R@3']:>7.3f}{m['MRR']:>7.3f}")

    # 4) 덧셈 vs 곱셈: hard셋은 고치고 브랜드셋은 안 흐리는가? (핵심 결론)
    print("\n=== [4] 덧셈(add) vs 곱셈(mult) 지역 가점 — 두 목표 동시 달성? ===")
    print("  hard MRR↑ 이 목표, brand-wrong MRR 유지가 안전(기준 0.940)")
    print(f"{'mode':5}{'w':>6}{'hardMRR':>9}{'brandWrongMRR':>15}")
    for mode in ("add", "mult"):
        for w in (0.05, 0.15, 0.30, 0.60):
            hr = [rank_of(score_and_rank(h["_qv"], corpus, mat, resolve, h, (w, 0, 0, 0), mode), h["_tgt"]) for h in hard]
            br = [rank_of(score_and_rank(q["_qv"], corpus, mat, resolve, q["_wrong"], (w, 0, 0, 0), mode), q["_tgt"]) for q in g29]
            print(f"{mode:5}{w:>6.2f}{metrics(hr, n)['MRR']:>9.3f}{metrics(br, ng)['MRR']:>15.3f}")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--probe":
        probe(sys.argv[2])
    else:
        main()
