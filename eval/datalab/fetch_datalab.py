"""한국관광 데이터랩 — 기초지자체(시군구) 일별 방문자수(현지인·외지인·외국인) 수집 → 캐시.

API: 한국관광공사_빅데이터_지역별 방문자수(공공데이터포털, B551011/DataLabService/locgoRegnVisitrDDList).
키: .env DATA_GO_KR_SERVICE_KEY_DECODED (공공데이터포털 활용신청 필요, 개발계정 자동승인·일 1,000콜).

실행:  uv run python -m eval.datalab.fetch_datalab --start 20260801 --end 20260830
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path

from apps.api.config import settings

URL = "https://apis.data.go.kr/B551011/DataLabService/locgoRegnVisitrDDList"
OUT = Path(__file__).parent / "data"


def fetch(start: str, end: str, rows: int = 10000) -> list[dict]:
    items, page = [], 1
    while True:
        q = urllib.parse.urlencode({
            "serviceKey": settings.data_go_kr_service_key_decoded, "numOfRows": rows, "pageNo": page,
            "MobileOS": "ETC", "MobileApp": "FindIt", "startYmd": start, "endYmd": end, "_type": "json"})
        with urllib.request.urlopen(f"{URL}?{q}", timeout=60) as r:
            body = json.load(r)["response"]["body"]
        got = (body.get("items") or {}).get("item") or []
        items += got
        print(f"  p{page}: {len(items)}/{body['totalCount']}")
        if not got or len(items) >= body["totalCount"]:
            return items
        page += 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    a = ap.parse_args()
    items = fetch(a.start, a.end)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"visitors_{a.start}_{a.end}.json"
    path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    days = sorted({i["baseYmd"] for i in items})
    print(f"저장: {path.name} — {len(items)}행, {days[0]}~{days[-1]} ({len(days)}일), "
          f"시군구 {len({i['signguCode'] for i in items})}개")


if __name__ == "__main__":
    main()
