"""지속 재매칭 — 저장된 미해결(open) 분실물을 현재 인덱스와 다시 대조.

수집기가 새 습득물을 색인한 뒤 호출(또는 스케줄러가 수집 후 실행).
저장된 추출·지역집합·쿼리를 재사용 → LLM 추출/라우팅 재호출 없이 검색+grade만(저렴).
임계 grade 이상이면 유력 후보 → status=candidate, 알림. 신고자가 옵트인했으면 약한 후보도 '참고' 알림.

실행:  uv run python -m apps.agent.rematch
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from apps import store
from apps.agent import graph
from apps.api.config import settings
from apps.notifier import notifier


def watch_until_after(days: int | None = None) -> str:
    """지금부터 days일 뒤(ISO). 1~watch_days_max로 클램프, 미지정이면 기본값."""
    d = days or settings.watch_days_default
    d = max(1, min(int(d), settings.watch_days_max))
    return (datetime.now(timezone.utc) + timedelta(days=d)).isoformat()


def watch_deadline(row: dict) -> datetime:
    """신고의 알림 마감 시각. watch_until 없는 기존 신고는 등록일 + 기본 기간으로 간주."""
    if row.get("watch_until"):
        return datetime.fromisoformat(row["watch_until"])
    created = datetime.fromisoformat(row.get("created_at") or datetime.now(timezone.utc).isoformat())
    return created + timedelta(days=settings.watch_days_default)


def alert_level(grade: int | None, notify_weak: bool) -> str | None:
    """알림 등급 — 'strong'(유력 후보, ≥임계) | 'weak'(옵트인 시 [하한, 임계)) | None(알림 없음).

    약한 후보는 확정도 유력도 아니므로 상태를 open으로 두고(더 나은 후보 계속 탐색) 알림만 보낸다.
    """
    if grade is None:
        return None
    if grade >= settings.rematch_grade_threshold:
        return "strong"
    if notify_weak and grade >= settings.weak_grade_threshold:
        return "weak"
    return None


_SLIM_KEYS = ("atc_id", "fd_sn", "name", "subject", "description", "category", "color", "dep_place",
              "region", "found_at", "org_name", "tel", "source", "image_url", "score", "grade", "reason",
              "name_en", "desc_en", "visual_score", "visual_reason")


def slim_matches(matches: list[dict], n: int = 6) -> list[dict]:
    """메일·확인용으로 저장할 후보(서버 판정 결과) — 상위 n개, 필요한 필드만, 설명은 짧게."""
    out = []
    for m in (matches or [])[:n]:
        d = {k: m.get(k) for k in _SLIM_KEYS if m.get(k) is not None}
        if isinstance(d.get("description"), str):
            d["description"] = d["description"][:300]
        out.append(d)
    return out


def can_email(row: dict) -> bool:
    """알림 메일은 신고자가 확인(double opt-in)한 이메일에만."""
    return bool(row.get("email")) and bool(row.get("email_verified"))


def _rematch_one(row: dict, verbose: bool) -> dict | None:
    matches = graph.match_stored(
        row["text"], row["extracted"] or {}, row["region_set"] or [], row["queries"] or [],
        lang=row.get("lang") or "ko",
    )
    dismissed = set(row.get("dismissed") or [])  # '아니에요' 한 후보 제외
    matches = [m for m in matches if m.get("atc_id") not in dismissed]
    top = matches[0] if matches else None
    g = top.get("grade") if top else None
    level = alert_level(g, row.get("notify_weak", False)) if top else None
    if level == "strong":
        store.update_match(row["id"], top, g, "candidate", matches=slim_matches(matches))  # 확정 아닌 '유력 후보'
        if can_email(row):
            notifier.notify(row, matches)  # 채널 독립 알림(최고 후보 + 비슷한 후보 목록)
        return {"lost": row, "match": top}
    if level == "weak" and top.get("atc_id") not in (row.get("weak_notified") or []) and can_email(row):
        store.mark_weak_notified(row["id"], top.get("atc_id"))  # 같은 후보로 반복 알림 방지
        store.set_matches(row["id"], slim_matches(matches))
        notifier.notify(row, matches, weak=True)  # open 유지 — 더 나은 후보 계속 탐색
        if verbose:
            print(f"  · {row['id']} '{row['text'][:24]}' 약한 후보 grade={g} → 참고 알림(open 유지)")
    elif verbose:
        print(f"  · {row['id']} '{row['text'][:24]}' 최고 grade={g} (임계 미달, open 유지)")
    return None


def rematch_open(verbose: bool = True) -> list[dict]:
    store.init()
    opens = store.list_open()
    if verbose:
        print(f"재매칭 대상(open): {len(opens)}건 / 임계 grade {settings.rematch_grade_threshold}"
              f" (약한 후보 옵트인 ≥{settings.weak_grade_threshold})")
    hits: list[dict] = []
    failed = 0
    now = datetime.now(timezone.utc)
    for row in opens:
        if watch_deadline(row) < now:  # 알림 기간 만료 → 재매칭(LLM 비용) 중단. 결과창에서 기간 재설정 시 재개.
            store.set_status(row["id"], "expired")
            if verbose:
                print(f"  · {row['id']} '{row['text'][:24]}' 알림 기간 만료 → expired")
            continue
        try:  # 한 건 실패(LLM·검색 오류)가 나머지 신고 재매칭을 막지 않게
            hit = _rematch_one(row, verbose)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ! {row['id']} 재매칭 실패({type(e).__name__}: {e}) → 다음 신고로")
            continue
        if hit:
            hits.append(hit)
    if verbose:
        print(f"신규 매칭 {len(hits)}건" + (f", 실패 {failed}건" if failed else ""))
    return hits


if __name__ == "__main__":
    rematch_open()
