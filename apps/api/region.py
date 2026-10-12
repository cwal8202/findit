"""dep(보관장소) → 구/시 지역 해석기. gazetteer(로컬) + 지오코딩 캐시.

build_gazetteer.py / geocode_deps.py 산출물(dep_gazetteer.json, dep_geocoded.json)을 로드해
4단계 폴백 + 지오코딩 캐시로 해석. 런타임 API 호출 없음(정적 조회).
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import settings


class RegionResolver:
    def __init__(self, data_dir: Path):
        gaz = json.loads((data_dir / "dep_gazetteer.json").read_text(encoding="utf-8"))
        self.stations = gaz["stations"]
        self.police = gaz["police_stations"]
        self.tokens = gaz["region_tokens"]
        self.names = sorted(gaz.get("name_to_region", {}).items(), key=lambda kv: -len(kv[0]))
        geo = data_dir / "dep_geocoded.json"
        self.geocoded = json.loads(geo.read_text(encoding="utf-8")) if geo.exists() else {}
        self._memo: dict[str, str] = {}

    def resolve(self, dep: str) -> str:
        if dep in self._memo:
            return self._memo[dep]
        r = self._resolve(dep)
        self._memo[dep] = r
        return r

    def _resolve(self, dep: str) -> str:
        if not dep:
            return ""
        if dep in self.stations:
            return self.stations[dep]["region"]
        if dep.endswith("경찰서") and dep[:-3] in self.police:
            return self.police[dep[:-3]]
        for suf in ("지구대", "파출소", "치안센터", "출장소"):
            if dep.endswith(suf) and dep[: -len(suf)] in self.stations:
                return self.stations[dep[: -len(suf)]]["region"]
        for t in self.tokens:
            if t in dep:
                return t
        for short, reg in self.names:
            if short in dep:
                return reg
        return self.geocoded.get(dep, "")

    @staticmethod
    def match(region: str, region_set: list[str]) -> bool:
        return region_matches(region, region_set)


# ---------- 지역 매칭 (앱·eval 공용 — 랭킹 핵심이라 한 곳에서만 정의) ----------
# 예전엔 부분 문자열 비교('중' in '중랑구', '부산광역시 중구' → 모두 True)라 짧은 이름이 전국에 가점을 줬음.
# 이제 토큰의 '핵심 이름'(접미사 제거)끼리 같은지 비교하고, '서울 중구'처럼 시도가 붙으면 시도까지 맞아야 함.
_SIDO = {
    "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천", "광주광역시": "광주",
    "대전광역시": "대전", "울산광역시": "울산", "세종특별자치시": "세종", "경기도": "경기", "강원도": "강원",
    "강원특별자치도": "강원", "충청북도": "충북", "충청남도": "충남", "전라북도": "전북", "전북특별자치도": "전북",
    "전라남도": "전남", "경상북도": "경북", "경상남도": "경남", "제주특별자치도": "제주", "제주도": "제주",
    "서울시": "서울", "전남광주통합특별시": "전남",
}
_SIDO_SHORT = set(_SIDO.values())


def _core(tok: str) -> str:
    """'서울특별시'→'서울', '전라남도'→'전남', '강남구'→'강남', '수원시'→'수원', '강남'→'강남'.

    접미사(시·군·구·도)는 3글자 이상일 때만 뗀다 — '대구'·'청도'·'영도'가 '대'·'청'·'영'이 되지 않게.
    두 글자 구('중구'·'동구')는 그대로 비교 → 시도 없는 '중' 한 글자가 전국 중구에 가점 주지 않음.
    """
    t = tok.strip()
    if t in _SIDO:
        return _SIDO[t]
    if len(t) >= 3 and t[-1] in "시군구도":
        return t[:-1]
    return t


def region_matches(region: str, region_set: list[str]) -> bool:
    """해석된 지역(예 '서울특별시 중구')이 지역집합(예 ['서울 중구', '마포'])에 드는가.

    - 한 단어 항목('마포', '순천', '대구'): 해석 지역 토큰 중 핵심 이름이 같은 게 있으면 True.
    - 시도+구 항목('서울 중구'): 구가 같고, 해석 지역에 시도가 있으면 시도까지 같아야 True.
    """
    if not region:
        return False
    toks = region.split()
    cores = {_core(t) for t in toks}
    has_sido = any(_core(t) in _SIDO_SHORT for t in toks)
    for r in region_set:
        parts = (r or "").split()
        if not parts:
            continue
        if len(parts) == 1:
            if _core(parts[0]) in cores:
                return True
        elif _core(parts[-1]) in cores and (not has_sido or _core(parts[0]) in cores):
            return True
    return False


resolver = RegionResolver(settings.data_dir)
