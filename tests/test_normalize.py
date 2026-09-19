"""수집기 정규화 — 공공 API 원본(목록+상세) → 색인 문서(필드명은 매핑과 일치가 진실)."""

from apps.collector.normalize import doc_id, embed_text, normalize


def test_maps_list_and_detail_fields():
    li = {"atcId": "A1", "fdSn": "1", "fdPrdtNm": "지갑", "clrNm": "검정",
          "prdtClNm": "지갑 > 여성용 지갑", "depPlace": "서울역", "fdYmd": "2026-09-01",
          "fdFilePathImg": "http://x/img.jpg"}
    detail = {"fdPlace": "2호선", "uniq": "카드 있음", "orgNm": "서울역파출소", "tel": "02-000-0000"}
    d = normalize("police", li, detail)
    assert d["atc_id"] == "A1" and d["name"] == "지갑" and d["color"] == "검정"
    assert d["category_top"] == "지갑" and d["category_sub"] == "여성용 지갑"
    assert d["found_place"] == "2호선" and d["description"] == "카드 있음"
    assert d["org_name"] == "서울역파출소" and d["tel"] == "02-000-0000"
    assert d["source"] == "police"


def test_detail_optional_defaults():
    d = normalize("portal", {"atcId": "B", "fdPrdtNm": "우산"})
    assert d["fd_sn"] == "1"            # 기본 fdSn
    assert d["description"] == ""        # 상세 없으면 특이사항 빈값
    assert d["found_place"] == "" and d["org_name"] == ""


def test_doc_id_and_embed_text():
    d = normalize("police", {"atcId": "A", "fdSn": "2", "fdPrdtNm": "지갑",
                             "clrNm": "검정", "prdtClNm": "지갑"})
    assert doc_id(d) == "A_2"
    txt = embed_text(d)
    assert "지갑" in txt and "검정" in txt
