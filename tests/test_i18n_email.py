"""다국어 알림 — 사용자 언어 정리, 메일 번역(문구·물품·장소)·폴백, 신고 언어 저장·재매칭 전달.

외부 서비스 없음: 번역(gemini.translate_strings)·검색·SMTP는 목킹.
"""

import pytest

from apps import notifier, store
from apps.agent import rematch
from apps.api import gemini
from apps.api import main


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    notifier._UI_CACHE.clear()
    store.init()
    store._run("DELETE FROM lost_items")
    yield
    notifier._UI_CACHE.clear()


@pytest.mark.parametrize("raw, expected", [
    (None, "ko"), ("", "ko"), ("ko-KR", "ko"), ("en-US", "en"), ("ja", "ja"),
    ("zh-cn", "zh-CN"), ("zh-TW", "zh-TW"), ("zh-HK", "zh-TW"), ("vi", "vi"),
    ("<script>", "ko"), ("en; drop", "ko"),
])
def test_normalize_lang(raw, expected):
    assert gemini.normalize_lang(raw) == expected


MATCH = {"atc_id": "A_1", "name": "검정 반지갑", "color": "검정", "category": "지갑 > 남성용 지갑",
         "dep_place": "강남경찰서", "region": "서울특별시 강남구", "org_name": "강남경찰서", "tel": "02-000",
         "found_at": "2026-09-28", "grade": 90, "reason": "Brand and color match", "description": "내용 가죽"}


def fake_translate(prefix):
    def tr(strings, lang):
        return {k: f"{prefix}{v}" for k, v in strings.items()}  # 자리표시자({t} 등)는 값 안에 그대로 남음
    return tr


def test_korean_email_needs_no_translation(monkeypatch):
    monkeypatch.setattr(gemini, "translate_strings", lambda *a: (_ for _ in ()).throw(AssertionError("호출 금지")))
    subject, text, html = notifier.build_email({"text": "검정 지갑", "lang": "ko"}, [MATCH])
    assert subject.startswith("[FindIt] 분실물 매칭 알림") and "유력 후보" in text and "보관장소" in text


def test_foreign_email_translates_ui_items_and_places(monkeypatch):
    monkeypatch.setattr(gemini, "translate_strings", fake_translate("JA:"))
    m = dict(MATCH, name_en="黒い財布")                  # 판정 단계가 사용자 언어로 만든 물품명
    subject, text, html = notifier.build_email({"text": "黒い財布をなくした", "lang": "ja"}, [m])
    assert "JA:분실물 매칭 알림" in subject and "黒い財布" in subject   # 문구 번역 + name_en 우선
    assert "JA:강남경찰서" in text and "JA:지갑 > 남성용 지갑" in text  # 장소·분류 번역
    assert "黒い財布をなくした" in text                                  # 사용자 원문은 그대로
    assert "JA:" in html and "{url}" not in html and "{lost112}" not in html  # 자리표시자 치환 완료


def test_translation_failure_falls_back_to_english_then_korean(monkeypatch):
    def only_english(strings, lang):
        return {k: f"EN:{v}" for k, v in strings.items()} if lang == "en" else None
    monkeypatch.setattr(gemini, "translate_strings", only_english)
    subject, _, _ = notifier.build_email({"text": "x", "lang": "vi"}, [MATCH])
    assert "EN:분실물 매칭 알림" in subject                            # vi 실패 → 영어 문구

    notifier._UI_CACHE.clear()
    monkeypatch.setattr(gemini, "translate_strings", lambda *a: None)
    subject, text, _ = notifier.build_email({"text": "x", "lang": "vi"}, [MATCH])
    assert subject.startswith("[FindIt] 분실물 매칭 알림") and "강남경찰서" in text  # 전부 실패 → 한국어


def test_translation_that_breaks_placeholders_is_rejected(monkeypatch):
    def broken(strings, lang):
        return {k: v.replace("{t}", "").replace("{n}", "") for k, v in strings.items()} if lang == "fr" \
            else {k: f"EN:{v}" for k, v in strings.items()}
    monkeypatch.setattr(gemini, "translate_strings", broken)
    assert notifier._ui("fr")["subj_strong"] == "EN:분실물 매칭 알림"
    assert "fr" not in notifier._UI_CACHE


def test_report_language_saved_and_used_in_rematch(monkeypatch):
    sent = []
    monkeypatch.setattr("apps.notifier.notifier",
                        type("N", (), {"notify": lambda self, lost, ms, weak=False: sent.append(lost["lang"])})())
    r = main._persist_and_respond("I lost my black wallet", {"matches": [dict(MATCH, score=0.9)]},
                                  email="me@x.com", lang="en")
    assert store.get(r.id)["lang"] == "en" and sent == ["en"]         # 저장 + 등록 알림 언어

    store._run("UPDATE lost_items SET status='open' WHERE id=?", (r.id,))
    seen = {}
    monkeypatch.setattr(rematch, "notifier", type("N", (), {"notify": lambda self, *a, **k: None})())
    monkeypatch.setattr(rematch.graph, "match_stored", lambda *a, **k: seen.update(k) or [])
    rematch.rematch_open(verbose=False)
    assert seen.get("lang") == "en"                                    # 재매칭 판정 근거도 신고 언어로
