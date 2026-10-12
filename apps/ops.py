"""운영 점검·알림 — cron 스크립트가 호출. "조용히 죽는" 상태를 관리자에게 메일로 알림.

실행(서버, backend 이미지로):
  docker compose -f docker-compose.prod.yml run --rm -T backend uv run python -m apps.ops alert "제목" "본문"
  docker compose -f docker-compose.prod.yml run --rm -T backend uv run python -m apps.ops freshness --max-age-days 3

- alert: 관리자 주소(NOTIFY_EMAIL_TO, 없으면 SMTP_USER)로 메일. SMTP 미설정이면 콘솔 출력만.
- freshness: 색인의 가장 최근 습득일이 N일보다 오래되면(수집이 멈춘 신호) 알림 + 종료코드 1.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone

from apps.api.config import settings
from apps.notifier import EmailNotifier, _emit

KST = timezone(timedelta(hours=9))


def alert(subject: str, body: str) -> bool:
    to = settings.notify_email_to or settings.smtp_user
    _emit(f"[ops] {subject}\n{body}")
    if not (settings.smtp_user and settings.smtp_password and to):
        return False
    html = "<pre style=\"font-family:monospace;white-space:pre-wrap\">" + \
        body.replace("&", "&amp;").replace("<", "&lt;") + "</pre>"
    return EmailNotifier._send(to, f"[FindIt 운영] {subject}", body, html)


def index_status() -> dict:
    """색인 문서 수 + 가장 최근 습득일."""
    base = f"{settings.opensearch_url}/{settings.opensearch_index}"
    count = json.load(urllib.request.urlopen(f"{base}/_count", timeout=15))["count"]
    req = urllib.request.Request(
        f"{base}/_search",
        json.dumps({"size": 1, "sort": [{"found_at": {"order": "desc", "missing": "_last"}}],
                    "_source": ["found_at"]}).encode(),
        {"Content-Type": "application/json"},
    )
    hits = json.load(urllib.request.urlopen(req, timeout=15))["hits"]["hits"]
    latest = hits[0]["_source"].get("found_at", "") if hits else ""
    return {"count": count, "latest_found_at": latest}


def freshness(max_age_days: int, status: dict | None = None, today: date | None = None) -> bool:
    """최근 습득일이 max_age_days 이내면 True. 아니면 알림."""
    status = status or index_status()
    today = today or datetime.now(KST).date()
    latest = status.get("latest_found_at") or ""
    try:
        age = (today - date.fromisoformat(latest[:10])).days
    except ValueError:
        age = None
    ok = age is not None and age <= max_age_days
    print(f"[ops] 색인 {status.get('count')}건, 최근 습득일 {latest or '-'} ({age if age is not None else '?'}일 전) → "
          f"{'정상' if ok else '정체'}")
    if not ok:
        alert("데이터 정체 — 새 습득물이 들어오지 않아요",
              f"색인 문서 {status.get('count')}건, 가장 최근 습득일 {latest or '없음'}"
              f"({age if age is not None else '?'}일 전, 기준 {max_age_days}일).\n"
              "수집 cron·공공데이터 API·임베딩 쿼터를 확인하세요. 로그: /root/findit/daily_collect.log")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description="FindIt 운영 점검·알림")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("alert")
    a.add_argument("subject")
    a.add_argument("body", nargs="?", default="")
    f = sub.add_parser("freshness")
    f.add_argument("--max-age-days", type=int, default=3)
    args = ap.parse_args()
    if args.cmd == "alert":
        alert(args.subject, args.body)
    elif not freshness(args.max_age_days):
        sys.exit(1)


if __name__ == "__main__":
    main()
