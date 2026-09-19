"""알림 헬퍼 — 특이사항 정리, 유효 이미지 필터(경찰청 no_img 플레이스홀더 제외)."""

from apps.notifier import _clean_desc, _valid_img


def test_clean_desc_strips_label_and_whitespace():
    assert _clean_desc("내용 카드  있음\n") == "카드 있음"
    assert _clean_desc("  줄바꿈\n\n정리  ") == "줄바꿈 정리"
    assert _clean_desc(None) == ""


def test_valid_img_filters_placeholder():
    assert _valid_img("http://x/real.jpg") == "http://x/real.jpg"
    assert _valid_img("https://minwon24.police.go.kr/images/sub/img02_no_img.gif") == ""
    assert _valid_img("") == ""
    assert _valid_img("ftp://x/img.jpg") == ""      # http(s) 아님
