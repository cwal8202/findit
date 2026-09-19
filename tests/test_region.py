"""지역 해석기 — dep(보관장소)→구/시, 그리고 넓은 구/시 집합과의 매칭(가점 판정 핵심)."""

from apps.api.region import RegionResolver, resolver


def test_match_substring_broad():
    # region_set의 짧은이름이 해석된 지역 문자열에 포함되면 매칭(넓게 잡기)
    assert RegionResolver.match("서울특별시 강남구", ["강남"]) is True
    assert RegionResolver.match("서울특별시 강남구", ["서초", "강남"]) is True
    assert RegionResolver.match("부산광역시 해운대구", ["강남"]) is False


def test_match_empty_never_true():
    assert RegionResolver.match("서울특별시 강남구", []) is False
    assert RegionResolver.match("", ["강남"]) is False
    assert RegionResolver.match("서울특별시 강남구", [""]) is False  # 빈 항목은 무시


def test_resolver_loaded_and_safe():
    # gazetteer가 로드되어야 하고, 미상 입력은 빈 문자열(크래시 없음)
    assert resolver.stations and resolver.tokens
    assert resolver.resolve("") == ""
    assert isinstance(resolver.resolve("존재하지않는장소_xyz_123"), str)


def test_resolve_token_hit():
    # 지역 토큰(구/시 짧은이름)이 dep 문자열에 들어있으면 그 토큰으로 해석
    tok = resolver.tokens[0]
    assert resolver.resolve(f"XYZ-{tok}-QWE") == tok  # ASCII 필러라 tok만 토큰 매칭
