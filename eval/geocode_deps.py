"""gazetteer가 못 잡는 dep(보관장소)을 카카오 로컬 키워드검색으로 지오코딩 → 구/시.

설계: 빌드타임 1회 보강. 미해석 dep(역·랜드마크·상호)만 골라 카카오에 질의 →
주소에서 시/도+구/시 추출 → eval/data/dep_geocoded.json 에 캐시.
런타임엔 이 캐시를 로컬 조회(정적 데이터 원칙). 재실행 시 캐시된 건 건너뜀.

키(무료): https://developers.kakao.com → REST API 키 → .env 의 KAKAO_REST_API_KEY.
키가 없으면 안내만 하고 종료(크래시 X). 결과 없으면 ""로 캐시해 재호출 방지.

실행:
  python eval/geocode_deps.py             # 미해석 dep 지오코딩(키 필요)
  python eval/geocode_deps.py --selftest  # 주소 파싱 로직만 오프라인 검증(키 불필요)
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from run_boost import make_resolver  # 로컬 gazetteer 해석기 재사용

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "data" / "corpus.json"
GAZ = ROOT / "data" / "dep_gazetteer.json"
CACHE = ROOT / "data" / "dep_geocoded.json"
ENV = ROOT.parent / ".env"
KAKAO_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


SIDO_NORM = {  # 카카오 짧은 시도표기 → gazetteer 표기 정렬
    "서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시", "인천": "인천광역시",
    "광주": "광주광역시", "대전": "대전광역시", "울산": "울산광역시", "세종": "세종특별자치시",
    "경기": "경기도", "강원": "강원특별자치도", "충북": "충청북도", "충남": "충청남도",
    "전북": "전북특별자치도", "전남": "전라남도", "경북": "경상북도", "경남": "경상남도",
    "제주": "제주특별자치도",
}


def region_from_address(addr: str) -> str:
    """카카오 address_name('서울 마포구 서교동 …') → '서울특별시 마포구'. 구/시/군까지."""
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


def kakao_search(query: str, key: str) -> str:
    url = f"{KAKAO_URL}?{urllib.parse.urlencode({'query': query, 'size': 1})}"
    req = urllib.request.Request(url, headers={"Authorization": f"KakaoAK {key}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        docs = json.load(r).get("documents", [])
    if not docs:
        return ""
    d = docs[0]
    return region_from_address(d.get("road_address_name") or d.get("address_name") or "")


def candidate_queries(dep: str) -> list[str]:
    """검색어 후보를 '구체→일반' 순으로. 구체(브랜드+지점)로 동명이지 오매칭을 막고,
    없으면 지점명만으로 폴백(틀린 지역보다 무지역이 나음).
    '포토이즘(서면점)'→['포토이즘 서면','서면'], '삼성여객자동차'→['삼성여객자동차']."""
    import re
    m = re.search(r"[(（]([^)）]+)[)）]", dep)
    if not m:
        return [dep.strip()]
    brand = dep[: m.start()].strip()
    inside = re.sub(r"(점|지점|영업소)$", "", m.group(1)).strip()
    if inside in ("주", "주식회사") or not inside:  # (주) 같은 껍데기
        return [re.sub(r"[(（][^)）]*[)）]", "", dep).strip()]
    out = []
    if brand:
        out.append(f"{brand} {inside}")
    out.append(inside)
    return list(dict.fromkeys(q for q in out if q))  # 중복 제거·순서 유지


def selftest() -> None:
    cases = {
        "서울 마포구 양화로 160": "서울특별시 마포구",
        "경기 수원시 장안구 정조로 900": "경기도 수원시 장안구",
        "전남 순천시 중앙로 123": "전라남도 순천시",
        "부산 해운대구 해운대해변로 264": "부산광역시 해운대구",
    }
    ok = True
    for addr, want in cases.items():
        got = region_from_address(addr)
        flag = "✓" if got == want else "✗"
        if got != want:
            ok = False
        print(f"  {flag} {addr!r:40} → {got!r} (기대 {want!r})")
    print("selftest:", "PASS" if ok else "FAIL")


def main() -> None:
    env = load_env()
    key = env.get("KAKAO_REST_API_KEY", "")
    if not key or key.startswith("여기에"):
        print("KAKAO_REST_API_KEY 미설정 → 지오코딩 건너뜀.")
        print("발급(무료): https://developers.kakao.com → REST API 키 → .env 에 KAKAO_REST_API_KEY=... 추가.")
        print("(gazetteer 로컬 매칭 86%는 키 없이 그대로 동작합니다.)")
        return

    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    resolve = make_resolver(json.loads(GAZ.read_text(encoding="utf-8")))
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}

    unresolved = sorted({c["dep"] for c in corpus if not resolve(c["dep"]) and c["dep"] not in cache})
    print(f"미해석 & 미캐시 dep: {len(unresolved)}건 지오코딩 시작")
    got = 0
    for i, dep in enumerate(unresolved, 1):
        cands = candidate_queries(dep)
        region, used = "", ""
        try:
            for q in cands:  # 구체→일반 순으로, 처음 잡히면 채택
                region = kakao_search(q, key)
                used = q
                if region:
                    break
                time.sleep(0.2)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code in (401, 403):  # 인증/권한 → 전부 실패할 것이므로 즉시 중단
                print(f"  ✗ 인증 오류 HTTP {e.code}: {body}")
                print("  → 카카오 개발자 콘솔에서 앱의 '카카오맵'(Local) 서비스 활성화 필요. 켜고 재실행.")
                return
            print(f"  ! {dep} HTTP {e.code}: {body} → 건너뜀(다음 실행에 재시도)")
            continue
        except Exception as e:  # noqa: BLE001
            print(f"  ! {dep} 오류: {e} → 건너뜀(다음 실행에 재시도)")
            continue
        cache[dep] = region  # 실패도 ""로 캐시(재호출 방지)
        if region:
            got += 1
        print(f"  [{i}/{len(unresolved)}] {dep!r} → {region or '(없음)'}  (q={used!r})")
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        time.sleep(0.3)

    # 최종 커버리지
    deps = [c["dep"] for c in corpus]
    hit = sum(1 for d in deps if resolve(d) or cache.get(d))
    print(f"\n신규 해석 {got}건. 저장: {CACHE}")
    print(f"전체 커버리지(로컬+지오코딩): {hit}/{len(deps)} ({hit/len(deps)*100:.0f}%)")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    else:
        main()
