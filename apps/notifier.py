"""알림(Notifier) — 채널 독립 인터페이스. 비즈니스 로직은 이 인터페이스로만 알림.

헌장: 코어에 카카오/웹 의존성 금지. v1은 ConsoleNotifier(로그), 추후 KakaoNotifier 등을 같은
인터페이스로 주입(코어 무변경). 되돌릴 수 없는 공식 신고 제출은 별도 HITL(여기선 통보만).
"""

from __future__ import annotations

import html as _html
import smtplib
import sys
from email.message import EmailMessage
from email.utils import formataddr
from typing import Protocol

from apps.api.config import settings

TOP_OTHERS = 5  # 상위 매칭 외에 함께 보여줄 "비슷한 후보" 수


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
    def notify(self, lost: dict, matches: list[dict]) -> None:
        """matches: grade 내림차순 후보 리스트(matches[0]=최고 매칭)."""
        ...


def _top(matches: list[dict]) -> dict:
    return matches[0] if matches else {}


class ConsoleNotifier:
    """개발/데모용 — 콘솔로 매칭 알림 출력."""

    def notify(self, lost: dict, matches: list[dict]) -> None:
        m = _top(matches)
        who = lost.get("user_id", "anon")
        text = (lost.get("text") or "")[:30]
        extra = max(len(matches) - 1, 0)
        _emit(
            f"🔔 [알림→{who}] 분실물 '{text}' 매칭!\n"
            f"    → {m.get('name')}/{m.get('color')} "
            f"(grade {m.get('grade')}) | 보관:{m.get('dep_place')} "
            f"지역:{m.get('region') or '-'} | 습득일:{m.get('found_at')}"
            + (f"\n    (외 비슷한 후보 {extra}건)" if extra else "")
        )


def _valid_img(url: str) -> str:
    """실제 이미지 URL만 통과(경찰청 no_img 플레이스홀더·빈값 제외)."""
    url = (url or "").strip()
    return url if url.startswith("http") and "no_img" not in url else ""


def _grade_color(g) -> str:
    if g is None:
        return "#9ca3af"
    return "#16a34a" if g >= 80 else "#d97706" if g >= 50 else "#9ca3af"


def _text_body(lost: dict, matches: list[dict]) -> str:
    m = _top(matches)
    lines = [
        f"등록하신 분실물 '{(lost.get('text') or '')[:40]}' 과(와) 일치하는 습득물을 찾았어요.",
        "",
        f"• 물품: {m.get('name')} / {m.get('color')}",
        f"• 분류: {m.get('category')}",
        f"• 보관장소: {m.get('dep_place') or '-'}",
        f"• 지역: {m.get('region') or '-'}",
        f"• 습득일: {m.get('found_at') or '-'}",
        f"• 매칭 신뢰도(grade): {m.get('grade')}",
        f"• 판단 근거: {m.get('reason') or '-'}",
    ]
    others = matches[1 : 1 + TOP_OTHERS]
    if others:
        lines += ["", f"■ 비슷한 후보 {len(others)}건도 확인해보세요:"]
        for o in others:
            lines.append(
                f"  - {o.get('name')}/{o.get('color')} (grade {o.get('grade')}) · "
                f"{o.get('region') or '-'} · 보관:{o.get('dep_place') or '-'}"
            )
    lines += ["", "자세한 확인·수령 절차는 보관 기관에 문의하세요. (본 메일은 FindIt 자동 알림입니다.)"]
    return "\n".join(lines)


def _card_html(m: dict, primary: bool = False) -> str:
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
    return (
        f'<div style="display:flex;align-items:flex-start;border:1px solid #eee;border-radius:10px;'
        f'padding:12px;margin:8px 0">{imgtag}<div style="flex:1">'
        f'<div style="font-weight:700">{e(str(m.get("name")))} '
        f'<span style="color:#6b7280;font-weight:400">/ {e(str(m.get("color")))}</span> {badge}</div>'
        f'<div style="font-size:13px;color:#6b7280;margin-top:2px">{e(str(m.get("category") or ""))} · '
        f'보관:{e(str(m.get("dep_place") or "-"))} · {e(str(m.get("region") or "-"))} · '
        f'{e(str(m.get("found_at") or "-"))}</div>{reason}</div></div>'
    )


def _html_body(lost: dict, matches: list[dict]) -> str:
    e = _html.escape
    others = matches[1 : 1 + TOP_OTHERS]
    others_html = ""
    if others:
        others_html = (
            f'<h3 style="font-size:14px;color:#374151;margin:16px 0 4px">비슷한 후보 {len(others)}건</h3>'
            + "".join(_card_html(o) for o in others)
        )
    return (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Malgun Gothic\',sans-serif;'
        'max-width:560px;color:#1f2430">'
        '<h2 style="font-size:18px">🔔 분실물 매칭 알림</h2>'
        f'<p>등록하신 \'<b>{e((lost.get("text") or "")[:40])}</b>\' 과(와) 일치하는 습득물을 찾았어요.</p>'
        f'{_card_html(_top(matches), primary=True)}{others_html}'
        '<p style="color:#888;font-size:12px;margin-top:16px">자세한 확인·수령 절차는 보관 기관에 '
        '문의하세요. 본 메일은 FindIt 자동 알림입니다.</p></div>'
    )


class EmailNotifier:
    """SMTP 이메일 알림(HTML+텍스트). 발송 실패해도 요청을 죽이지 않음(로그 후 진행)."""

    def notify(self, lost: dict, matches: list[dict]) -> None:
        # 수신자 = 신고자가 등록 시 입력한 이메일(신고별) → 없으면 config 기본값
        to = (lost.get("email") or "").strip() or settings.notify_email_to or settings.smtp_user
        if not (settings.smtp_user and settings.smtp_password and to) or not matches:
            _emit("[알림] 이메일 설정/수신자/후보 미비 → 발송 생략")
            return
        m = _top(matches)
        msg = EmailMessage()
        msg["Subject"] = f"[FindIt] 분실물 매칭 알림 — {m.get('name')}"
        msg["From"] = formataddr(("FindIt", settings.smtp_user))
        msg["To"] = to
        msg.set_content(_text_body(lost, matches))
        msg.add_alternative(_html_body(lost, matches), subtype="html")
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
                s.starttls()
                s.login(settings.smtp_user, settings.smtp_password)
                s.send_message(msg)
            _emit(f"📧 [이메일 알림→{to}] '{m.get('name')}' 외 {max(len(matches)-1,0)}건 발송 완료")
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
