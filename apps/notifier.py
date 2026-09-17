"""알림(Notifier) — 채널 독립 인터페이스. 비즈니스 로직은 이 인터페이스로만 알림.

헌장: 코어에 카카오/웹 의존성 금지. v1은 ConsoleNotifier(로그), 추후 KakaoNotifier 등을 같은
인터페이스로 주입(코어 무변경). 되돌릴 수 없는 공식 신고 제출은 별도 HITL(여기선 통보만).
"""

from __future__ import annotations

import smtplib
import sys
from email.message import EmailMessage
from email.utils import formataddr
from typing import Protocol

from apps.api.config import settings


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
    def notify(self, lost: dict, match: dict) -> None:
        ...


class ConsoleNotifier:
    """개발/데모용 — 콘솔로 매칭 알림 출력."""

    def notify(self, lost: dict, match: dict) -> None:
        who = lost.get("user_id", "anon")
        text = (lost.get("text") or "")[:30]
        _emit(
            f"🔔 [알림→{who}] 분실물 '{text}' 매칭!\n"
            f"    → {match.get('name')}/{match.get('color')} "
            f"(grade {match.get('grade')}) | 보관:{match.get('dep_place')} "
            f"지역:{match.get('region') or '-'} | 습득일:{match.get('found_at')}"
        )


def _match_lines(lost: dict, match: dict) -> tuple[str, str]:
    """알림 제목·본문 생성(채널 공통)."""
    subject = f"[FindIt] 분실물 매칭 알림 — {match.get('name')}"
    body = (
        f"등록하신 분실물 '{(lost.get('text') or '')[:40]}' 과(와) 일치하는 습득물을 찾았어요.\n\n"
        f"• 물품: {match.get('name')} / {match.get('color')}\n"
        f"• 분류: {match.get('category')}\n"
        f"• 보관장소: {match.get('dep_place') or '-'}\n"
        f"• 지역: {match.get('region') or '-'}\n"
        f"• 습득일: {match.get('found_at') or '-'}\n"
        f"• 매칭 신뢰도(grade): {match.get('grade')}\n"
        f"• 판단 근거: {match.get('reason') or '-'}\n\n"
        f"자세한 확인·수령 절차는 보관 기관에 문의하세요. (본 메일은 FindIt 자동 알림입니다.)"
    )
    return subject, body


class EmailNotifier:
    """SMTP 이메일 알림. 발송 실패해도 요청을 죽이지 않음(로그 후 진행)."""

    def notify(self, lost: dict, match: dict) -> None:
        to = settings.notify_email_to or settings.smtp_user
        if not (settings.smtp_user and settings.smtp_password and to):
            _emit("[알림] 이메일 설정 미완료(SMTP_USER/PASSWORD/NOTIFY_EMAIL_TO) → 발송 생략")
            return
        subject, body = _match_lines(lost, match)
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = formataddr(("FindIt", settings.smtp_user))
        msg["To"] = to
        msg.set_content(body)
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
                s.starttls()
                s.login(settings.smtp_user, settings.smtp_password)
                s.send_message(msg)
            _emit(f"📧 [이메일 알림→{to}] '{match.get('name')}' 매칭 발송 완료")
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
