"""FindIt LLM grading 실험 — 검색(EMB)이 좁힌 top-K를 Gemini가 재정렬.

가설: 임베딩이 애매하게 남긴 상위권(브랜드·모델 정보가 있는 질의)을 LLM이 근거로 바로잡는다.
- EMB top-K 후보 → gemini-2.5-flash가 각 후보 0~100 채점(+근거) → 재정렬.
- 채점은 grade_cache.json에 질의별 캐시(재실행 시 오프라인·무과금).
- LLM은 DB가 아니라 심판: K개만 판정(전체 코퍼스 프롬프트 금지).

실행:  python eval/run_grade.py         # 29 골든에 grading, EMB 대비 비교
       python eval/run_grade.py --k 15
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from run_boost import make_resolver
from run_embed import cos_ranked, norm

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "data" / "corpus.json"
CACHE = ROOT / "data" / "emb_cache.json"
GAZ = ROOT / "data" / "dep_gazetteer.json"
GOLDEN = ROOT / "golden" / "golden.json"
GRADE_CACHE = ROOT / "data" / "grade_cache.json"
ENV = ROOT.parent / ".env"
KS = (1, 3, 5, 10)
MODEL = "gemini-3.6-flash"
GEN_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

PROMPT = """당신은 분실물↔습득물 매칭 심판입니다.
사용자가 잃어버린 물건 설명과, 검색이 좁힌 습득물 후보 목록이 주어집니다.
각 후보가 '사용자가 잃어버린 바로 그 물건'일 가능성을 0~100으로 채점하세요.

원칙:
- 브랜드·모델·물품종류·색상 일치를 중시. 한글↔영문 브랜드(닥스=DAKS)는 같게 봄.
- 후보에 정보가 적으면 과신하지 말 것(적당한 중간 점수).
- 지역·날짜는 참고만. 근거는 한 줄로 짧게.

반드시 아래 JSON 배열로만 답하세요(설명 문장 금지):
[{{"id": <후보번호>, "score": <0~100 정수>, "reason": "<한 줄>"}}, ...]

잃어버린 물건: "{query}"

후보:
{candidates}
"""


def load_env() -> dict[str, str]:
    env = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def gemini_generate(prompt: str, key: str, max_retry: int = 6) -> str:
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }).encode()
    url = f"{GEN_URL}?key={key}"
    for attempt in range(max_retry):
        req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and attempt < max_retry - 1:
                wait = min(30, 5 * (attempt + 1))
                print(f"    HTTP {e.code} → {wait}s 후 재시도")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("gemini_generate: 재시도 소진")


def cand_line(n: int, it: dict, region: str) -> str:
    return (f"{n}. 물품명:{it['name']} / 제목:{it.get('subj','')} / "
            f"분류:{it['cat']} / 색상:{it['color']} / 지역:{region or '미상'} / 습득일:{it['ymd']}")


def metrics(ranks, n):
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


def rank_of(ids: list[str], target: str):
    return next((r for r, i in enumerate(ids, 1) if i == target), None)


def main(k: int = 10) -> None:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    corpus = [it for it in corpus if it["atcId"] in cache]
    mat = [norm(cache[it["atcId"]]) for it in corpus]
    resolve = make_resolver(json.loads(GAZ.read_text(encoding="utf-8")))
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))["queries"]
    id2idx = {it["atcId"]: i for i, it in enumerate(corpus)}
    grades = json.loads(GRADE_CACHE.read_text(encoding="utf-8")) if GRADE_CACHE.exists() else {}
    key = load_env()["GEMINI_API_KEY"]

    print(f"29 골든 / K={k} / {MODEL}\n")
    print(f"{'id':4} {'EMB':>4} {'GRADE':>6}  질의")
    print("-" * 60)
    emb_ranks, grade_ranks = [], []
    for q in golden:
        tgt = q["expected"]
        if tgt not in id2idx:
            emb_ranks.append(None); grade_ranks.append(None); continue
        qv = norm(cache[f"q::{q['id']}"])
        emb_order = [corpus[i]["atcId"] for i, _ in cos_ranked(qv, mat)]
        emb_ranks.append(rank_of(emb_order, tgt))

        topk_ids = emb_order[:k]
        # 채점(캐시)
        if q["id"] not in grades:
            cands = "\n".join(
                cand_line(n, corpus[id2idx[cid]], resolve(corpus[id2idx[cid]]["dep"]))
                for n, cid in enumerate(topk_ids)
            )
            raw = gemini_generate(PROMPT.format(query=q["query"], candidates=cands), key)
            try:
                scored = json.loads(raw)
            except json.JSONDecodeError:
                print(f"  ! {q['id']} JSON 파싱 실패, 원문: {raw[:80]}")
                scored = []
            grades[q["id"]] = scored
            GRADE_CACHE.write_text(json.dumps(grades, ensure_ascii=False, indent=1), encoding="utf-8")
            time.sleep(1.0)

        # 재정렬: grade 점수 desc, 동점은 원래 EMB 순서 유지
        score_by_n = {int(s["id"]): s.get("score", 0) for s in grades[q["id"]] if "id" in s}
        graded = sorted(range(len(topk_ids)), key=lambda n: (-score_by_n.get(n, -1), n))
        new_order = [topk_ids[n] for n in graded] + emb_order[k:]
        grade_ranks.append(rank_of(new_order, tgt))

        f = lambda r: str(r) if r else "-"
        mark = ""
        if emb_ranks[-1] and grade_ranks[-1]:
            if grade_ranks[-1] < emb_ranks[-1]: mark = "  ↑개선"
            elif grade_ranks[-1] > emb_ranks[-1]: mark = "  ↓악화"
        print(f"{q['id']:4} {f(emb_ranks[-1]):>4} {f(grade_ranks[-1]):>6}  {q['query']}{mark}")

    n = len(golden)
    me, mg = metrics(emb_ranks, n), metrics(grade_ranks, n)
    print("-" * 60)
    print(f"{'':6}{'R@1':>7}{'R@3':>7}{'R@5':>7}{'MRR':>7}")
    print(f"EMB   {me['R@1']:>7.3f}{me['R@3']:>7.3f}{me['R@5']:>7.3f}{me['MRR']:>7.3f}")
    print(f"GRADE {mg['R@1']:>7.3f}{mg['R@3']:>7.3f}{mg['R@5']:>7.3f}{mg['MRR']:>7.3f}")


if __name__ == "__main__":
    k = 10
    if "--k" in sys.argv:
        k = int(sys.argv[sys.argv.index("--k") + 1])
    main(k)
