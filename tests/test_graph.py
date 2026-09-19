"""매칭 에이전트 — 사진 신고용 검색 텍스트 합성(추출 필드 → 사람이 읽을 한 줄)."""

from apps.agent.graph import _synth_text


def test_synth_from_fields_and_note():
    t = _synth_text({"color": "검정", "brand": "닥스", "item": "지갑", "features": "가죽"}, "2호선")
    assert "검정 닥스 지갑" in t and "가죽" in t and "2호선" in t


def test_synth_category_fallback():
    assert _synth_text({"category": "전자기기"}, "") == "전자기기"


def test_synth_empty_fallback():
    assert _synth_text({}, "") == "사진 신고"
