"""data.go.kr 습득물 API 클라이언트 — 경찰청 + 포털기관 (XML).

목록(list) → 항목별 상세(detail) enrich 구조. 상세에만 fdPlace(습득장소)·uniq(특이사항) 존재.
serviceKey는 .env 의 디코딩 키(urlencode로 인코딩됨).

필드 출처: fixtures/responses/*.xml (진실의 원천).
"""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from apps.api.config import settings

BASE = "http://apis.data.go.kr/1320000"

# 소스별 오퍼레이션 (경찰청 vs 포털기관, 스키마 동일)
SOURCES = {
    "police": {
        "list": f"{BASE}/LosfundInfoInqireService/getLosfundInfoAccToClAreaPd",
        "detail": f"{BASE}/LosfundInfoInqireService/getLosfundDetailInfo",
    },
    "portal": {
        "list": f"{BASE}/LosPtfundInfoInqireService/getPtLosfundInfoAccToClAreaPd",
        "detail": f"{BASE}/LosPtfundInfoInqireService/getPtLosfundDetailInfo",
    },
}


def _get(url: str, params: dict, max_retry: int = 4) -> ET.Element:
    params = {**params, "serviceKey": settings.data_go_kr_service_key_decoded}
    full = f"{url}?{urllib.parse.urlencode(params)}"
    for attempt in range(max_retry):
        try:
            with urllib.request.urlopen(full, timeout=30) as r:
                return ET.fromstring(r.read())
        except (urllib.error.URLError, ET.ParseError, TimeoutError) as e:
            if attempt < max_retry - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"data.go.kr 요청 실패: {e}") from e
    raise RuntimeError("재시도 소진")


def _items(root: ET.Element) -> list[dict]:
    out = []
    for item in root.findall(".//item"):
        out.append({c.tag: (c.text or "").strip() for c in item})
    return out


def _header(root: ET.Element) -> tuple[str, str]:
    code = root.findtext(".//resultCode") or root.findtext(".//header/resultCode") or ""
    msg = root.findtext(".//resultMsg") or root.findtext(".//header/resultMsg") or ""
    return code, msg


def get_list(source: str, start_ymd: str, end_ymd: str, page: int = 1, rows: int = 100) -> dict:
    """목록 1페이지. 반환: {items, total, code, msg}."""
    root = _get(SOURCES[source]["list"], {
        "START_YMD": start_ymd, "END_YMD": end_ymd,
        "pageNo": page, "numOfRows": rows,
    })
    code, msg = _header(root)
    total = int(root.findtext(".//totalCount") or 0)
    return {"items": _items(root), "total": total, "code": code, "msg": msg}


def get_detail(source: str, atc_id: str, fd_sn: str) -> dict:
    """상세 1건 (fdPlace 습득장소 · uniq 특이사항 포함)."""
    root = _get(SOURCES[source]["detail"], {"ATC_ID": atc_id, "FD_SN": fd_sn})
    items = _items(root)
    return items[0] if items else {}
