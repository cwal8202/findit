"""데이터랩 외국인 방문자수 × 습득물 — 시군 단위 결합 분석.

입력(같은 기간으로 맞춤):
  data/visitors_<기간>.json  한국관광 데이터랩 기초지자체 일별 방문자수(현지인·외지인·외국인)
  data/found_<기간>.json     경찰청·포털기관 습득물 시군구별 건수(보관장소 기준)
출력:
  data/analysis_<기간>.json · .csv  시군별 결합표 + 요약 지표

매칭 규칙
- 데이터랩은 '수원시'(시 전체)와 '수원시 장안구'(구)가 함께 있음. 구 합계 > 시 행(여러 구 방문 중복)이므로
  **시 행을 쓰고 구 행은 제외** → 시군 단위(일반구는 시로 합침). 습득물도 같은 규칙으로 구 → 시.
- 습득물 지역 표기가 제각각('서울 강남구', '강남구')이라 시도 별칭을 정규화. 시도가 없으면 이름이 전국에서
  하나뿐일 때만 매칭('중구'·'동구' 등 중복 이름은 시도 없으면 제외).
- 방문자수는 일별 값의 기간 합계(연인원). 습득물은 '보관장소'의 지역(분실 장소와 다를 수 있음).

실행:  uv run python -m eval.datalab.analyze --period 20260801_20260830
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).parent / "data"

SIDO = {  # 데이터랩 시군구코드 앞 2자리 → 시도 별칭(습득물 지역 표기 정규화용)
    "11": ("서울특별시", {"서울", "서울시", "서울특별시"}),
    "26": ("부산광역시", {"부산", "부산시", "부산광역시"}),
    "27": ("대구광역시", {"대구", "대구시", "대구광역시"}),
    "28": ("인천광역시", {"인천", "인천시", "인천광역시"}),
    "12": ("전남광주통합특별시", {"전남광주통합특별시", "전라남도", "전남", "광주", "광주시", "광주광역시"}),
    "30": ("대전광역시", {"대전", "대전시", "대전광역시"}),
    "31": ("울산광역시", {"울산", "울산시", "울산광역시"}),
    "36": ("세종특별자치시", {"세종", "세종시", "세종특별자치시"}),
    "41": ("경기도", {"경기", "경기도"}),
    "43": ("충청북도", {"충북", "충청북도"}),
    "44": ("충청남도", {"충남", "충청남도"}),
    "47": ("경상북도", {"경북", "경상북도"}),
    "48": ("경상남도", {"경남", "경상남도"}),
    "50": ("제주특별자치도", {"제주", "제주도", "제주특별자치도"}),
    "51": ("강원특별자치도", {"강원", "강원도", "강원특별자치도"}),
    "52": ("전북특별자치도", {"전북", "전라북도", "전북특별자치도"}),
}
ALIAS = {a: code for code, (_, names) in SIDO.items() for a in names}

# 관광 교통 거점 보정 사전 — 보관장소 이름만으론 시군구가 안 잡히거나(해석 실패) 시도까지만 잡히는 대형 거점.
# 위치가 확실한 곳만(2026 행정구역 기준). 위치 불확실·전국 단위(부산유실물센터·핸드폰찾기콜센터 등)는 보정 안 함.
HUBS = {
    "인천국제공항(터미널1)": "인천광역시 영종구", "인천국제공항(터미널2)": "인천광역시 영종구",
    "인천공항세관": "인천광역시 영종구", "인천공항세관T2": "인천광역시 영종구", "인천영종경찰서": "인천광역시 영종구",
    "인천제물포경찰서": "인천광역시 제물포구", "인천역(한국철도공사)": "인천광역시 제물포구",
    "동인천역(한국철도공사)": "인천광역시 제물포구", "인천시청역(유실물센터)": "인천광역시 남동구",
    "서울역(한국철도공사)": "서울특별시 용산구", "서울역(GTX-A)": "서울특별시 용산구",
    "에이치디씨 아이파크몰": "서울특별시 용산구", "더현대서울": "서울특별시 영등포구",
    "서울식물원": "서울특별시 강서구", "수서역(에스알고속철도)": "서울특별시 강남구",
    "수색역(한국철도공사)": "서울특별시 은평구", "왕십리역(한국철도공사)": "서울특별시 성동구",
    # 서울교통공사 유실물센터(역 위치): 시청(1·2호선)·충무로(3·4)·왕십리(5·8)·태릉입구(6·7)
    "시청유실물센터": "서울특별시 중구", "충무로유실물센터": "서울특별시 중구",
    "왕십리유실물센터": "서울특별시 성동구", "태릉유실물센터": "서울특별시 노원구",
    "부산역(한국철도공사)": "부산광역시 동구", "부산항국제여객터미널": "부산광역시 동구",
    "부전역(한국철도공사)": "부산광역시 부산진구", "유실물센터서면역(부산교통공사)": "부산광역시 부산진구",
    "롯데백화점(부산본점)": "부산광역시 부산진구",
    "동대구역(한국철도공사)": "대구광역시 동구", "대구국제공항": "대구광역시 동구",
    "대전역(한국철도공사)": "대전광역시 동구", "서대전역(한국철도공사)": "대전광역시 중구",
    "울산역(한국철도공사)": "울산광역시 울주군", "행신역(한국철도공사)": "경기도 고양시",
    "손님상담실(에버랜드)": "경기도 용인시", "캐리비안베이안내(에버랜드)": "경기도 용인시",
    "하이원리조트": "강원특별자치도 정선군",
}


def apply_hubs(found: dict) -> tuple[dict[str, int], int]:
    """거점 보정: 시도까지만 해석된 건은 시도에서 빼서, 해석 실패 건은 새로, 거점의 시군구로 옮김."""
    by_region = dict(found["by_region"])
    moved = 0
    for key, n in found.get("sido_only_top", {}).items():
        reg, dep = key.split(" | ", 1)
        if dep in HUBS:
            by_region[reg] -= n
            by_region[HUBS[dep]] = by_region.get(HUBS[dep], 0) + n
            moved += n
    for dep, n in found.get("unresolved_top", {}).items():
        if dep in HUBS:
            by_region[HUBS[dep]] = by_region.get(HUBS[dep], 0) + n
            moved += n
    return by_region, moved


def load_visitors(period: str) -> dict[str, dict]:
    """시군 단위 방문자 합계. key = 시군구코드(시 행), 값 = {sido, name, local, outsider, foreign}."""
    rows = json.loads((DATA / f"visitors_{period}.json").read_text(encoding="utf-8"))
    names = {r["signguCode"]: r["signguNm"] for r in rows}
    parents = {c for c in names if any(k[:4] == c[:4] and k != c for k in names) and c.endswith("0")}
    out: dict[str, dict] = {}
    for r in rows:
        c = r["signguCode"]
        if c[:4] + "0" in parents and c not in parents:  # 일반구 행 → 시 행과 중복이라 제외
            continue
        d = out.setdefault(c, {"code": c, "sido": SIDO[c[:2]][0], "name": names[c],
                               "local": 0.0, "outsider": 0.0, "foreign": 0.0})
        d[{"1": "local", "2": "outsider", "3": "foreign"}[r["touDivCd"]]] += float(r["touNum"])
    return out


def match_region(region: str, by_key: dict, name_index: dict) -> str | None:
    """습득물 지역 문자열 → 데이터랩 시 행 코드. 못 맞추면 None."""
    toks = region.split()
    sido = ALIAS.get(toks[0]) if toks else None
    rest = toks[1:] if sido else toks
    if sido == "36":  # 세종은 단일 시(읍면동이 붙어 옴)
        return by_key.get(("36", "세종특별자치시"))
    if not rest:
        return None
    name = rest[0]  # '수원시 장안구' → '수원시'(시 단위) / '강남구' / '가평군'
    if sido:
        return by_key.get((sido, name))
    cands = name_index.get(name, [])
    return cands[0] if len(cands) == 1 else None  # 시도 없고 이름 중복('중구' 등) → 제외


def rank(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):  # 동점은 평균 순위
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(a: list[float], b: list[float]) -> float:
    ra, rb = rank(a), rank(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = sum((x - ma) ** 2 for x in ra) ** 0.5
    vb = sum((y - mb) ** 2 for y in rb) ** 0.5
    return cov / (va * vb)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", required=True, help="예: 20260801_20260830")
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args()

    vis = load_visitors(a.period)
    found = json.loads((DATA / f"found_{a.period}.json").read_text(encoding="utf-8"))
    by_key = {(c[:2], d["name"]): c for c, d in vis.items()}
    name_index: dict[str, list[str]] = defaultdict(list)
    for c, d in vis.items():
        name_index[d["name"]].append(c)

    by_region, hub_moved = apply_hubs(found)
    items = defaultdict(int)
    unmatched: dict[str, int] = {}
    for region, n in by_region.items():
        if n <= 0:
            continue
        code = match_region(region, by_key, name_index)
        if code:
            items[code] += n
        else:
            unmatched[region] = n
    matched = sum(items.values())

    rows = []
    for c, d in vis.items():
        total_v = d["local"] + d["outsider"] + d["foreign"]
        rows.append({**d, "visitors": total_v, "foreign_share": d["foreign"] / total_v if total_v else 0,
                     "found": items.get(c, 0)})
    rows.sort(key=lambda r: -r["foreign"])

    F = sum(r["foreign"] for r in rows)
    top = rows[: a.top]
    top_f = sum(r["foreign"] for r in top) / F
    top_items = sum(r["found"] for r in top) / matched
    rho = spearman([r["foreign"] for r in rows], [r["found"] for r in rows])
    rho_all = spearman([r["visitors"] for r in rows], [r["found"] for r in rows])

    summary = {
        "period": a.period, "regions": len(rows),
        "found_total": found["total"], "found_region_resolved": found["resolved"], "found_matched": matched,
        "found_matched_rate": matched / found["total"], "found_hub_corrected": hub_moved,
        "foreign_visitors_total": F,
        f"top{a.top}_foreign_share": top_f, f"top{a.top}_found_share": top_items,
        "spearman_foreign_vs_found": rho, "spearman_allvisitors_vs_found": rho_all,
        "unmatched_regions_top": dict(sorted(unmatched.items(), key=lambda x: -x[1])[:15]),
    }
    out = {"summary": summary, "rows": rows}
    (DATA / f"analysis_{a.period}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(DATA / f"analysis_{a.period}.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["순위(외국인)", "시도", "시군구", "외국인 방문(연인원)", "전체 방문(연인원)", "외국인 비중", "습득물(건)"])
        for i, r in enumerate(rows, 1):
            w.writerow([i, r["sido"], r["name"], round(r["foreign"]), round(r["visitors"]),
                        f"{r['foreign_share']:.2%}", r["found"]])

    print(f"기간 {a.period} · 시군 {len(rows)}개 · 습득물 {found['total']:,}건 중 시군 매칭 {matched:,}건"
          f"({matched / found['total']:.1%}, 거점 보정 {hub_moved:,}건 포함)")
    print(f"외국인 방문 상위 {a.top}개 시군 → 외국인 방문의 {top_f:.1%}, 습득물의 {top_items:.1%}")
    print(f"스피어만 상관: 외국인 방문 vs 습득물 {rho:.3f} · 전체 방문 vs 습득물 {rho_all:.3f}")
    print(f"\n상위 {a.top}:")
    for i, r in enumerate(top, 1):
        print(f" {i:2}. {r['sido']} {r['name']:<10} 외국인 {r['foreign']:>11,.0f} ({r['foreign_share']:.1%})  습득물 {r['found']:>6,}")
    print("\n미매칭 상위:", summary["unmatched_regions_top"])


if __name__ == "__main__":
    main()
