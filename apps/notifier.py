"""알림(Notifier) — 채널 독립 인터페이스. 비즈니스 로직은 이 인터페이스로만 알림.

헌장: 코어에 카카오/웹 의존성 금지. v1은 ConsoleNotifier(로그), 추후 KakaoNotifier 등을 같은
인터페이스로 주입(코어 무변경). 되돌릴 수 없는 공식 신고 제출은 별도 HITL(여기선 통보만).
"""

from __future__ import annotations

import html as _html
import re
import smtplib
import sys
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Protocol

from apps.api import gemini
from apps.api.config import settings

TOP_OTHERS = 5  # 상위 매칭 외에 함께 보여줄 "비슷한 후보" 수
_LOGO = Path(__file__).resolve().parent / "web" / "static" / "logo.jpg"  # 메일 인라인 로고(CID)


def _emit(msg: str) -> None:
    """콘솔 인코딩(Windows cp949 등)이 이모지를 못 찍어도 요청을 죽이지 않도록 안전 출력."""
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        buf = getattr(sys.stdout, "buffer", None)
        if buf is not None:
            buf.write((msg + "\n").encode("utf-8", "replace"))
            buf.flush()


class Notifier(Protocol):
    def notify(self, lost: dict, matches: list[dict], weak: bool = False) -> None:
        """matches: grade 내림차순 후보 리스트(matches[0]=최고 매칭).
        weak=True: 임계 미만 '약한 후보' 참고 알림(신고자 옵트인 시에만) — 문구를 낮춰 기대치 조정."""
        ...


def _top(matches: list[dict]) -> dict:
    return matches[0] if matches else {}


class ConsoleNotifier:
    """개발/데모용 — 콘솔로 매칭 알림 출력."""

    def notify(self, lost: dict, matches: list[dict], weak: bool = False) -> None:
        m = _top(matches)
        who = lost.get("user_id", "anon")
        text = (lost.get("text") or "")[:30]
        extra = max(len(matches) - 1, 0)
        head = "비슷한 후보(참고, 신뢰도 낮음)" if weak else "유력 후보 발견(확인 필요)!"
        _emit(
            f"🔔 [알림→{who}] 분실물 '{text}' {head}\n"
            f"    → {m.get('name')}/{m.get('color')} "
            f"(grade {m.get('grade')}) | 보관:{m.get('dep_place')} "
            f"지역:{m.get('region') or '-'} | 습득일:{m.get('found_at')}"
            + (f"\n    (외 비슷한 후보 {extra}건)" if extra else "")
        )


def _clean_desc(s) -> str:
    """특이사항(uniq) 앞머리 '내용' 라벨·줄바꿈 잡음 정리."""
    return re.sub(r"\s+", " ", re.sub(r"^\s*내용\s*", "", str(s or ""))).strip()


def _valid_img(url: str) -> str:
    """실제 이미지 URL만 통과(경찰청 no_img 플레이스홀더·빈값 제외)."""
    url = (url or "").strip()
    return url if url.startswith("http") and "no_img" not in url else ""


def _grade_color(g) -> str:
    if g is None:
        return "#9ca3af"
    return "#16a34a" if g >= 80 else "#d97706" if g >= 50 else "#9ca3af"


# 메일 고정 문구(한국어 원본). 외국어 사용자에겐 언어별로 한 번 번역해 캐시(_ui).
# {t}{n}{url}{lost112} 자리표시자는 번역 후에도 유지돼야 함(검증 실패 시 영어 → 한국어 폴백).
KO = {
    "tagline": "분실물 매칭 AI",
    "head_strong": "🔔 유력 후보를 찾았어요 — 확인해보세요",
    "desc_strong": "등록하신 분실물 '{t}' 과(와) 일치할 가능성이 높은 습득물을 찾았어요. "
                   "본인 물건이면 아래 보관기관으로 수령을 문의하세요.",
    "head_weak": "🔎 비슷한 후보가 있어요 — 참고용",
    "desc_weak": "등록하신 분실물 '{t}' 과(와) 정확히 일치하진 않지만(신뢰도 낮음) 비슷한 습득물이에요. "
                 "'약한 후보도 알림 받기'를 켜두셔서 보내드려요. 더 정확한 후보는 계속 찾고 있어요.",
    "subj_strong": "분실물 매칭 알림", "subj_weak": "비슷한 후보 알림(참고)",
    "item": "물품", "category": "분류", "held": "보관장소", "region": "지역", "found": "습득일",
    "desc": "특이사항", "office": "보관기관", "contact": "연락처", "ask_office": "보관기관 문의",
    "grade": "신뢰도(grade)", "reason": "판단 근거", "held_short": "보관",
    "others_text": "비슷한 후보 {n}건도 확인해보세요:", "others_html": "비슷한 후보 {n}건",
    "claim": "본인 물건이면 위 보관기관 연락처로 수령을 문의하세요.",
    "cta": "FindIt에서 전체 결과 보기 →", "cta_text": "FindIt에서 전체 결과 보기: {url}",
    "or_type": "또는 주소창에 {url} 입력",
    "official": "공식 조회: {lost112} (경찰청 유실물 통합포털)",
    "footer": "최종 확인·수령은 본인이 진행하세요. 본 메일은 FindIt 자동 알림입니다.",
}
_PH = re.compile(r"\{(\w+)\}")
_UI_CACHE: dict[str, dict] = {}


def _fill(s: str, **kw) -> str:
    """자리표시자 치환(str.format 대신 — 번역문에 다른 중괄호가 있어도 안전)."""
    return _PH.sub(lambda m: str(kw.get(m.group(1), m.group(0))), s)


def _ui(lang: str) -> dict:
    """메일 고정 문구를 사용자 언어로. 실패하면 영어, 그것도 실패하면 한국어. 성공만 캐시."""
    if lang == "ko":
        return KO
    if lang in _UI_CACHE:
        return _UI_CACHE[lang]
    tr = gemini.translate_strings(KO, lang)
    if tr and all(set(_PH.findall(tr[k])) >= set(_PH.findall(v)) for k, v in KO.items()):
        _UI_CACHE[lang] = tr
        return tr
    return _ui("en") if lang != "en" else KO


def _localize(matches: list[dict], lang: str) -> list[dict]:
    """메일에 실을 후보들의 물품·장소 정보를 사용자 언어로(한 번의 번역 호출). 실패 시 원문 유지.

    물품명·설명은 판정 단계가 이미 사용자 언어로 만든 name_en/desc_en이 있으면 그걸 씀.
    장소·기관명은 '한국어 원문 (번역)'으로 — 보관기관에 그대로 보여줄 수 있게.
    """
    top = matches[: 1 + TOP_OTHERS]
    if lang == "ko":
        return top
    payload: dict[str, str] = {}
    for i, m in enumerate(top):
        if not m.get("name_en"):
            payload[f"name_{i}"] = m.get("name") or ""
        payload[f"color_{i}"] = m.get("color") or ""
        payload[f"cat_{i}"] = m.get("category") or ""
        payload[f"place_held_{i}"] = m.get("dep_place") or ""
        payload[f"place_region_{i}"] = m.get("region") or ""
        payload[f"place_org_{i}"] = m.get("org_name") or ""
    if top and not top[0].get("desc_en"):
        payload["desc_0"] = _clean_desc(top[0].get("description"))[:140]
    payload = {k: v for k, v in payload.items() if v}
    tr = gemini.translate_strings(payload, lang) or {}
    out = []
    for i, m in enumerate(top):
        mm = dict(m)
        mm["name"] = m.get("name_en") or tr.get(f"name_{i}", m.get("name"))
        mm["color"] = tr.get(f"color_{i}", m.get("color"))
        mm["category"] = tr.get(f"cat_{i}", m.get("category"))
        mm["dep_place"] = tr.get(f"place_held_{i}", m.get("dep_place"))
        mm["region"] = tr.get(f"place_region_{i}", m.get("region"))
        mm["org_name"] = tr.get(f"place_org_{i}", m.get("org_name"))
        if i == 0:
            mm["description"] = m.get("desc_en") or tr.get("desc_0", m.get("description"))
        out.append(mm)
    return out


def _intro(lost: dict, weak: bool, S: dict = KO) -> tuple[str, str]:
    """(제목줄, 설명) — 유력 후보 vs 약한 후보(참고)."""
    t = (lost.get("text") or "")[:40]
    kind = "weak" if weak else "strong"
    return S[f"head_{kind}"], _fill(S[f"desc_{kind}"], t=t)


def _text_body(lost: dict, matches: list[dict], weak: bool = False, S: dict = KO) -> str:
    m = _top(matches)
    head, desc = _intro(lost, weak, S)
    lines = [
        head,
        desc,
        "",
        f"• {S['item']}: {m.get('name')} / {m.get('color')}",
        f"• {S['category']}: {m.get('category')}",
        f"• {S['held']}: {m.get('dep_place') or '-'}",
        f"• {S['region']}: {m.get('region') or '-'}",
        f"• {S['found']}: {m.get('found_at') or '-'}",
        f"• {S['desc']}: {_clean_desc(m.get('description'))[:140] or '-'}",
        f"• {S['office']}: {m.get('org_name') or '-'}  ({S['contact']}: {m.get('tel') or S['ask_office']})",
        f"• {S['grade']}: {m.get('grade')}",
        f"• {S['reason']}: {m.get('reason') or '-'}",
    ]
    others = matches[1 : 1 + TOP_OTHERS]
    if others:
        lines += ["", "■ " + _fill(S["others_text"], n=len(others))]
        for o in others:
            lines.append(
                f"  - {o.get('name')}/{o.get('color')} (grade {o.get('grade')}) · "
                f"{o.get('region') or '-'} · {S['held_short']}:{o.get('dep_place') or '-'}"
            )
    lines += [
        "",
        S["claim"],
        _fill(S["cta_text"], url=settings.public_base_url),
        _fill(S["official"], lost112="https://www.lost112.go.kr"),
        f"({S['footer']})",
    ]
    return "\n".join(lines)


def _card_html(m: dict, primary: bool = False, S: dict = KO) -> str:
    e = _html.escape
    img = _valid_img(m.get("image_url", ""))
    size = 120 if primary else 60
    imgtag = (
        f'<img src="{e(img)}" alt="" style="width:{size}px;height:{size}px;object-fit:cover;'
        f'border-radius:8px;border:1px solid #eee;margin-right:12px" />'
        if img else ""
    )
    g = m.get("grade")
    badge = (
        f'<span style="background:{_grade_color(g)};color:#fff;border-radius:6px;'
        f'padding:1px 8px;font-weight:700;font-size:12px">grade {e(str(g))}</span>'
    )
    reason = (
        f'<div style="font-size:13px;color:#374151;margin-top:4px">💬 {e(str(m.get("reason") or ""))}</div>'
        if primary and m.get("reason") else ""
    )
    org = (
        f'<div style="font-size:13px;color:#166534;margin-top:4px">'
        f'🏛 {e(str(m.get("org_name") or "-"))} · ☎ {e(str(m.get("tel") or S["ask_office"]))}</div>'
        if (m.get("org_name") or m.get("tel")) else ""
    )
    desc = (
        f'<div style="font-size:13px;color:#374151;margin-top:4px">📝 {e(_clean_desc(m.get("description"))[:140])}</div>'
        if primary and _clean_desc(m.get("description")) else ""
    )
    return (
        f'<div style="display:flex;align-items:flex-start;border:1px solid #eee;border-radius:10px;'
        f'padding:12px;margin:8px 0">{imgtag}<div style="flex:1">'
        f'<div style="font-weight:700">{e(str(m.get("name")))} '
        f'<span style="color:#6b7280;font-weight:400">/ {e(str(m.get("color")))}</span> {badge}</div>'
        f'<div style="font-size:13px;color:#6b7280;margin-top:2px">{e(str(m.get("category") or ""))} · '
        f'{e(S["held_short"])}:{e(str(m.get("dep_place") or "-"))} · {e(str(m.get("region") or "-"))} · '
        f'{e(str(m.get("found_at") or "-"))}</div>{reason}{desc}{org}</div></div>'
    )


def _html_body(lost: dict, matches: list[dict], weak: bool = False, S: dict = KO) -> str:
    e = _html.escape
    site = settings.public_base_url
    head, desc = _intro(lost, weak, S)
    others = matches[1 : 1 + TOP_OTHERS]
    others_html = ""
    if others:
        others_html = (
            f'<h3 style="font-size:14px;color:#374151;margin:16px 0 4px">{e(_fill(S["others_html"], n=len(others)))}</h3>'
            + "".join(_card_html(o, S=S) for o in others)
        )
    logo = ('<img src="cid:findit-logo" width="40" height="40" alt="FindIt" '
            'style="border-radius:9px;display:block" />') if _LOGO.exists() else ""
    site_link = f'<a href="{e(site)}" style="color:#2563eb">{e(site)}</a>'
    lost112 = '<a href="https://www.lost112.go.kr">lost112.go.kr</a>'
    return (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Malgun Gothic\',sans-serif;'
        'max-width:560px;color:#1f2430">'
        # 브랜드 헤더 (로고 + 이름)
        '<div style="display:flex;align-items:center;gap:10px;padding-bottom:14px;'
        'border-bottom:1px solid #eee;margin-bottom:16px">'
        f'{logo}<span style="font-size:20px;font-weight:800;color:#2563eb">FindIt</span>'
        f'<span style="font-size:12px;color:#6b7280">{e(S["tagline"])}</span></div>'
        f'<h2 style="font-size:18px;margin:0 0 8px">{e(head)}</h2>'
        f'<p style="margin:0 0 12px">{e(desc)}</p>'
        f'{_card_html(_top(matches), primary=True, S=S)}{others_html}'
        # 사이트 이동 CTA
        f'<div style="text-align:center;margin:22px 0 6px">'
        f'<a href="{e(site)}" style="display:inline-block;background:#2563eb;color:#fff;'
        'text-decoration:none;padding:13px 30px;border-radius:10px;font-weight:700;font-size:15px">'
        f'{e(S["cta"])}</a></div>'
        f'<p style="text-align:center;font-size:12px;color:#6b7280;margin:0 0 16px">'
        f'{_fill(e(S["or_type"]), url=site_link)}</p>'
        '<p style="color:#888;font-size:12px;margin-top:16px;border-top:1px solid #eee;padding-top:12px">'
        f'{_fill(e(S["official"]), lost112=lost112)} {e(S["footer"])}</p></div>'
    )


def build_email(lost: dict, matches: list[dict], weak: bool = False) -> tuple[str, str, str]:
    """(제목, 텍스트, HTML) — 신고자 언어(lost['lang'])로. 한국어가 아니면 문구·물품 정보를 번역."""
    lang = gemini.normalize_lang(lost.get("lang"))
    S = _ui(lang)
    ms = _localize(matches, lang)
    kind = "weak" if weak else "strong"
    subject = f"[FindIt] {S[f'subj_{kind}']} — {_top(ms).get('name')}"
    return subject, _text_body(lost, ms, weak, S), _html_body(lost, ms, weak, S)


class EmailNotifier:
    """SMTP 이메일 알림(HTML+텍스트). 신고자 언어로 발송. 발송 실패해도 요청을 죽이지 않음(로그 후 진행)."""

    def notify(self, lost: dict, matches: list[dict], weak: bool = False) -> None:
        # 수신자 = 신고자가 등록 시 입력한 이메일(신고별) → 없으면 config 기본값
        to = (lost.get("email") or "").strip() or settings.notify_email_to or settings.smtp_user
        if not (settings.smtp_user and settings.smtp_password and to) or not matches:
            _emit("[알림] 이메일 설정/수신자/후보 미비 → 발송 생략")
            return
        subject, text, html = build_email(lost, matches, weak)
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = formataddr(("FindIt", settings.smtp_user))
        msg["To"] = to
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
        if _LOGO.exists():  # HTML 파트에 로고 인라인 첨부(cid:findit-logo)
            try:
                html_part = msg.get_payload()[-1]
                html_part.add_related(_LOGO.read_bytes(), "image", "jpeg", cid="findit-logo")
            except Exception:  # noqa: BLE001
                pass
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
                s.starttls()
                s.login(settings.smtp_user, settings.smtp_password)
                s.send_message(msg)
            _emit(f"📧 [이메일 알림→{to}, {lost.get('lang') or 'ko'}] '{_top(matches).get('name')}' "
                  f"외 {max(len(matches)-1,0)}건 발송 완료")
        except Exception as e:  # noqa: BLE001
            _emit(f"[알림] 이메일 발송 실패({type(e).__name__}: {e}) → 스킵")


def get_notifier() -> Notifier:
    """config의 notifier_channel로 알림 채널 선택. email 미설정 시 console 폴백."""
    if settings.notifier_channel == "email":
        if settings.smtp_user and settings.smtp_password:
            return EmailNotifier()
        _emit("[알림] NOTIFIER_CHANNEL=email 이지만 SMTP 미설정 → console로 폴백")
    return ConsoleNotifier()


# 주입 지점 — config로 채널 선택(추후 KakaoNotifier 등 추가 가능)
notifier: Notifier = get_notifier()
