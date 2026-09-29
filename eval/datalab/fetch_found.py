"""경찰청·포털기관 습득물 목록(상세 X) → 보관장소를 시군구로 해석 → 시군구별 건수 집계 캐시.

데이터랩 방문자수와 같은 기간으로 맞추기 위한 수집. 원본은 저장하지 않고 집계만(레포 용량·개인정보 최소화).
지역 해석은 FindIt 운영과 같은 해석기(gazetteer + 지오코딩 캐시, apps.api.region).

실행:  uv run python -m eval.datalab.fetch_found --start 20260801 --end 20260830
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from apps.api.region import resolver
from apps.collector import client

OUT = Path(__file__).parent / "data"


def list_all(source: str, start: str, end: str, rows: int = 1000) -> list[dict]:
    items, page = [], 1
    while True:
        res = client.get_list(source, start, end, page=page, rows=rows)
        items += res["items"]
        if page % 10 == 0 or len(items) >= res["total"]:
            print(f"  {source} p{page}: {len(items)}/{res['total']}", flush=True)
        if not res["items"] or len(items) >= res["total"]:
            return items
        page += 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    a = ap.parse_args()
    by_region: Counter = Counter()
    by_source: Counter = Counter()
    unresolved: Counter = Counter()
    sido_only: Counter = Counter()  # 시도까지만 해석된 보관장소(공항·지하철 운영사 등 광역 기관) — 보정 근거 확인용
    for src in ("police", "portal"):
        for li in list_all(src, a.start, a.end):
            by_source[src] += 1
            reg = resolver.resolve(li.get("depPlace", ""))
            if reg:
                by_region[reg] += 1
                if len(reg.split()) == 1:
                    sido_only[f"{reg} | {li.get('depPlace', '')}"] += 1
            else:
                unresolved[li.get("depPlace", "")] += 1
    total = sum(by_source.values())
    out = {"start": a.start, "end": a.end, "total": total, "by_source": dict(by_source),
           "resolved": sum(by_region.values()), "by_region": dict(by_region.most_common()),
           "unresolved_top": dict(unresolved.most_common(30)),
           "sido_only_top": dict(sido_only.most_common(60))}
    path = OUT / f"found_{a.start}_{a.end}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {path.name} — 총 {total}건(경찰 {by_source['police']}·포털 {by_source['portal']}), "
          f"시군구 해석 {out['resolved']}건({out['resolved'] / total:.1%}), 시군구 {len(by_region)}개")


if __name__ == "__main__":
    main()
