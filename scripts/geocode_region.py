"""색인 문서 중 region이 빈 것(주로 지구대·상호 등 gazetteer 미커버)을 카카오 지오코딩으로 채움.

backfill_region.py(정적 gazetteer)로도 안 잡힌 dep만 대상. 서로 다른 dep만 카카오에 1회씩 질의(캐시),
같은 dep을 쓰는 문서들은 한꺼번에 bulk update. Gemini 불필요, 카카오 무료 티어(일 10만).

실행(서버):
  docker compose -f docker-compose.prod.yml exec -T backend uv run python -m scripts.geocode_region
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request

from apps.api.config import settings

OS = settings.opensearch_url
IDX = settings.opensearch_index
KAKAO_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"

SIDO_NORM = {
    "서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시", "인천": "인천광역시",
    "광주": "광주광역시", "대전": "대전광역시", "울산": "울산광역시", "세종": "세종특별자치시",
    "경기": "경기도", "강원": "강원특별자치도", "충북": "충청북도", "충남": "충청남도",
    "전북": "전북특별자치도", "전남": "전라남도", "경북": "경상북도", "경남": "경상남도",
    "제주": "제주특별자치도",
}


def region_from_address(addr: str) -> str:
    toks = addr.split()
    if len(toks) < 2:
        return ""
    sido = SIDO_NORM.get(toks[0], toks[0])
    first = toks[1]
    if first.endswith("시") and len(toks) >= 3 and toks[2].endswith("구"):
        return f"{sido} {first} {toks[2]}"
    if first[-1] in "시군구":
        return f"{sido} {first}"
    return sido


def candidate_queries(dep: str) -> list[str]:
    m = re.search(r"[(（]([^)）]+)[)）]", dep)
    if not m:
        return [dep.strip()]
    brand = dep[: m.start()].strip()
    inside = re.sub(r"(점|지점|영업소)$", "", m.group(1)).strip()
    if inside in ("주", "주식회사") or not inside:
        return [re.sub(r"[(（][^)）]*[)）]", "", dep).strip()]
    out = []
    if brand:
        out.append(f"{brand} {inside}")
    out.append(inside)
    return list(dict.fromkeys(q for q in out if q))


def kakao_region(dep: str, key: str) -> str:
    for q in candidate_queries(dep):
        try:
            url = f"{KAKAO_URL}?{urllib.parse.urlencode({'query': q, 'size': 1})}"
            req = urllib.request.Request(url, headers={"Authorization": f"KakaoAK {key}"})
            with urllib.request.urlopen(req, timeout=15) as r:
                docs = json.load(r).get("documents", [])
        except Exception:  # noqa: BLE001
            docs = []
        if docs:
            reg = region_from_address(docs[0].get("road_address_name") or docs[0].get("address_name") or "")
            if reg:
                return reg
        time.sleep(0.2)
    return ""


def _req(path: str, body: dict | None, method: str = "POST", ndjson: bool = False) -> dict:
    data = None
    if body is not None:
        data = ("\n".join(body["_lines"]) + "\n").encode("utf-8") if ndjson else json.dumps(body).encode("utf-8")
    ctype = "application/x-ndjson" if ndjson else "application/json"
    req = urllib.request.Request(f"{OS}{path}", data, {"Content-Type": ctype}, method=method)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def main() -> None:
    key = getattr(settings, "kakao_rest_api_key", "") or ""
    if not key or key.startswith("여기에"):
        print("KAKAO_REST_API_KEY 미설정 → 지오코딩 건너뜀.")
        return

    # region 빈 문서 수집 → dep별 문서 id 묶기
    dep_ids: dict[str, list[str]] = {}
    res = _req(f"/{IDX}/_search?scroll=2m",
               {"size": 500, "_source": ["dep_place", "region"], "query": {"match_all": {}}})
    sid = res["_scroll_id"]
    hits = res["hits"]["hits"]
    while hits:
        for h in hits:
            s = h["_source"]
            if not (s.get("region") or "") and (s.get("dep_place") or "").strip():
                dep_ids.setdefault(s["dep_place"].strip(), []).append(h["_id"])
        res = _req("/_search/scroll", {"scroll": "2m", "scroll_id": sid})
        sid = res["_scroll_id"]
        hits = res["hits"]["hits"]

    deps = sorted(dep_ids)
    print(f"region 빈 문서의 고유 dep: {len(deps)}종 → 카카오 지오코딩 시작")
    lines: list[str] = []
    hit_deps = updated = 0
    for i, dep in enumerate(deps, 1):
        reg = kakao_region(dep, key)
        if not reg:
            continue
        hit_deps += 1
        for _id in dep_ids[dep]:
            lines.append(json.dumps({"update": {"_index": IDX, "_id": _id}}))
            lines.append(json.dumps({"doc": {"region": reg}}, ensure_ascii=False))
            updated += 1
        if len(lines) >= 1000:  # 주기적 flush
            _req("/_bulk", {"_lines": lines}, ndjson=True)
            lines = []
        if i % 50 == 0:
            print(f"  {i}/{len(deps)} 진행 (지역 확보 dep {hit_deps}, 문서 {updated})")

    if lines:
        _req("/_bulk", {"_lines": lines}, ndjson=True)
    _req(f"/{IDX}/_refresh", None)
    print(f"지오코딩 백필 완료: dep {hit_deps}/{len(deps)}종 해결, 문서 {updated}건 region 채움")


if __name__ == "__main__":
    main()
