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
        """resolved 지역이 region_set의 어느 항목이라도 포함하면 True (넓은 구/시 집합)."""
        return any(r and r in region for r in region_set)


resolver = RegionResolver(settings.data_dir)
