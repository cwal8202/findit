"""경찰관서 주소 CSV → dep(보관장소) gazetteer 생성 + 코퍼스 커버리지 측정.

입력: 경찰청_전국 지구대 파출소 주소 현황 CSV (data.go.kr 파일데이터, 로그인/키 불필요)
출력: eval/data/dep_gazetteer.json  { "복현지구대": {"sido":"대구광역시","sigungu":"북구","region":"대구광역시 북구"}, ... }

관서명(예: "복현") + 구분(예: "지구대") = 코퍼스 dep("복현지구대")로 조인.
주소 첫 토큰=시도, 그 다음 시/군/구 토큰을 구 단위까지 추출.

실행:  python eval/build_gazetteer.py [csv경로]
"""

from __future__ import annotations

import csv
import glob
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "data" / "corpus.json"
OUT = ROOT / "data" / "dep_gazetteer.json"


def find_csv(argv: list[str]) -> str:
    if len(argv) > 1:
        return argv[1]
    for d in (Path(os.path.expanduser("~")) / "Downloads", ROOT / "data"):
        hits = [f for f in glob.glob(str(d / "*.csv")) if "지구대" in f or "관서" in f]
        if hits:
            return hits[0]
    raise SystemExit("CSV를 못 찾음. 경로를 인자로 주세요.")


def parse_region(addr: str) -> tuple[str, str]:
    """주소 → (시도, 시군구), 구/시/군 단위에서 딱 끊음.
    '서울특별시 광진구 …' → ('서울특별시','광진구')
    '경기도 수원시 장안구 …' → ('경기도','수원시 장안구')
    '전라남도 순천시 중앙로 …' → ('전라남도','순천시')
    '경상남도 산청군 신안면 …' → ('경상남도','산청군')
    """
    toks = addr.split()
    if not toks:
        return "", ""
    sido, rest = toks[0], toks[1:]
    if not rest:
        return sido, ""
    first = rest[0]
    if first.endswith("시"):
        if len(rest) >= 2 and rest[1].endswith("구"):
            return sido, f"{first} {rest[1]}"  # 수원시 장안구
        return sido, first                      # 순천시
    if first.endswith("구") or first.endswith("군"):
        return sido, first                      # 광진구 / 산청군
    return sido, first


def modal(vals: list[str]) -> str:
    from collections import Counter
    return Counter(v for v in vals if v).most_common(1)[0][0] if any(vals) else ""


def main() -> None:
    csv_path = find_csv(sys.argv)
    print(f"CSV: {csv_path}")
    raw = open(csv_path, "rb").read()
    text = raw.decode("cp949")  # data.go.kr 파일데이터 관례
    rows = list(csv.DictReader(text.splitlines()))
    print(f"관서 행: {len(rows)}")

    gaz: dict[str, dict] = {}          # 관서명(+구분) → region
    station_regions: dict[str, list] = {}  # 경찰서 컬럼값 → [region,...] (경찰서 단위 추정용)
    region_tokens: set[str] = set()    # 시/군/구 풀네임 (예 '수원시','장안구')
    name_to_region: dict[str, str] = {}  # 도시/구 짧은이름(예 '부산','익산','광진') → region (substring 폴백용)

    SIDO_SHORT = {  # 시도 짧은이름 → 대표 region
        "서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시", "인천": "인천광역시",
        "광주": "광주광역시", "대전": "대전광역시", "울산": "울산광역시", "세종": "세종특별자치시",
        "제주": "제주특별자치도",
    }

    for r in rows:
        name = (r.get("관서명") or "").strip()
        gubun = (r.get("구분") or "").strip()
        addr = (r.get("주소") or "").strip()
        station = (r.get("경찰서") or "").strip()
        if not name or not addr:
            continue
        sido, sigungu = parse_region(addr)
        region = f"{sido} {sigungu}".strip()
        for key in {f"{name}{gubun}", name}:
            gaz[key] = {"sido": sido, "sigungu": sigungu, "region": region, "addr": addr}
        if station:
            station_regions.setdefault(station, []).append(region)
        for tok in sigungu.split():  # '수원시','장안구' 등 풀네임 토큰
            if len(tok) >= 2 and tok[-1] in "시군구":
                region_tokens.add(tok)
                short = tok[:-1]  # '수원시'→'수원', '장안구'→'장안'
                if len(short) >= 2:
                    name_to_region.setdefault(short, region)

    # 시도 짧은이름도 등록 (예 '부산' → 부산광역시)
    for short, reg in SIDO_SHORT.items():
        name_to_region.setdefault(short, reg)

    # 경찰서 단위 region = 해당 경찰서 소속 지구대/파출소들의 최빈 region
    station_gaz = {st: modal(rs) for st, rs in station_regions.items()}

    # 긴 이름부터 매칭(부산진>부산 오탐 줄임)
    name_items = sorted(name_to_region.items(), key=lambda kv: -len(kv[0]))
    payload = {"stations": gaz, "police_stations": station_gaz,
               "region_tokens": sorted(region_tokens),
               "name_to_region": dict(name_items)}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"gazetteer 저장: {OUT}  (관서 {len(gaz)} / 경찰서 {len(station_gaz)} / "
          f"풀네임토큰 {len(region_tokens)} / 짧은이름 {len(name_to_region)})")

    # --- dep → region 해석기 (4단계 폴백) ---
    def resolve(dep: str) -> tuple[str, str]:
        if dep in gaz:
            return gaz[dep]["region"], "exact"
        if dep.endswith("경찰서"):
            st = dep[:-3]
            if st in station_gaz:
                return station_gaz[st], "station"
        for suf in ("지구대", "파출소", "치안센터", "출장소"):
            if dep.endswith(suf) and dep[: -len(suf)] in gaz:
                return gaz[dep[: -len(suf)]]["region"], "suffix"
        for tok in region_tokens:  # 풀네임 토큰(수원시…)
            if tok in dep:
                return tok, "token"
        for short, reg in name_items:  # 도시/구 짧은이름 substring(부산·익산·광진…, 괄호 안 포함)
            if short in dep:
                return reg, "name"
        return "", "miss"

    # --- 커버리지 측정 ---
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    deps = [c["dep"] for c in corpus]
    uniq = set(deps)
    from collections import Counter
    tiers = Counter(resolve(d)[1] for d in uniq)
    item_tiers = Counter(resolve(d)[1] for d in deps)
    print("\n=== 코퍼스 dep 커버리지 (해석 단계별) ===")
    print(f"{'단계':8} {'고유dep':>7} {'항목':>7}")
    for t in ("exact", "station", "suffix", "token", "miss"):
        print(f"{t:8} {tiers.get(t,0):>7} {item_tiers.get(t,0):>7}")
    hit_u = sum(v for k, v in tiers.items() if k != "miss")
    hit_i = sum(v for k, v in item_tiers.items() if k != "miss")
    print(f"{'합계매칭':8} {hit_u:>7}({hit_u/len(uniq)*100:.0f}%) {hit_i:>6}({hit_i/len(deps)*100:.0f}%)")
    miss = sorted(d for d in uniq if resolve(d)[1] == "miss")
    print(f"\n미매칭 dep 샘플 15개:")
    for d in miss[:15]:
        print(f"  · {d}")


if __name__ == "__main__":
    main()
