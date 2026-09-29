"""분실물 저장소 — SQLite / PostgreSQL 이중 백엔드.

- `DATABASE_URL`(postgres://...) 있으면 PostgreSQL, 없으면 SQLite(로컬 개발).
- 인터페이스(init/add/get/list_open/all_items/update_match/update_alerts/confirm/dismiss/...)는 동일 → 나머지 코드 무변경.
- status: open(찾는중) | candidate(유력 후보) | confirmed(확인) | expired(알림 기간 만료 — 재매칭 중단).
- JSON 컬럼은 TEXT에 json.dumps로 저장(두 DB 공통). 개인정보 포함 → DB는 git 제외.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

DATABASE_URL = os.environ.get("DATABASE_URL", "")
_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))

if _PG:
    import psycopg
    from psycopg.rows import dict_row
else:
    import sqlite3

    from apps.api.config import settings

_JSON_COLS = ("extracted", "region_set", "queries", "best_match", "dismissed", "weak_notified")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn():
    if _PG:
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _q(sql: str) -> str:
    """SQL은 '?' 플레이스홀더로 작성 → Postgres면 '%s'로 변환."""
    return sql.replace("?", "%s") if _PG else sql


def _run(sql: str, params: tuple = ()) -> None:
    conn = _conn()
    try:
        conn.execute(_q(sql), params)
        conn.commit()
    finally:
        conn.close()


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    conn = _conn()
    try:
        cur = conn.execute(_q(sql), params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _one(sql: str, params: tuple = ()) -> dict | None:
    conn = _conn()
    try:
        cur = conn.execute(_q(sql), params)
        r = cur.fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def init() -> None:
    _run("""
        CREATE TABLE IF NOT EXISTS lost_items (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            email TEXT,
            text TEXT,
            extracted TEXT,
            region_set TEXT,
            queries TEXT,
            status TEXT DEFAULT 'open',
            best_match TEXT,
            best_grade INTEGER,
            dismissed TEXT,
            notify_weak INTEGER DEFAULT 0,
            weak_notified TEXT,
            watch_until TEXT,
            lang TEXT DEFAULT 'ko',
            created_at TEXT,
            matched_at TEXT
        )
    """)
    # 기존 DB 누락 컬럼 마이그레이션 (운영 Postgres도 테이블이 이미 있어 CREATE로는 안 생김)
    added = {"email": "TEXT", "dismissed": "TEXT", "notify_weak": "INTEGER DEFAULT 0",
             "weak_notified": "TEXT", "watch_until": "TEXT", "lang": "TEXT DEFAULT 'ko'"}
    if _PG:
        for col, typ in added.items():
            _run(f"ALTER TABLE lost_items ADD COLUMN IF NOT EXISTS {col} {typ}")
    else:
        conn = _conn()
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(lost_items)")]
            for col, typ in added.items():
                if col not in cols:
                    conn.execute(f"ALTER TABLE lost_items ADD COLUMN {col} {typ}")
            conn.commit()
        finally:
            conn.close()


def _to_dict(d: dict) -> dict:
    for k in _JSON_COLS:
        d[k] = json.loads(d[k]) if d.get(k) else None
    d["notify_weak"] = bool(d.get("notify_weak"))
    return d


def add(text: str, extracted: dict, region_set: list, queries: list,
        best_match: dict | None, best_grade: int | None,
        status: str = "open", user_id: str = "anon", email: str = "",
        notify_weak: bool = False, watch_until: str | None = None, lang: str = "ko") -> str:
    lid = uuid.uuid4().hex[:12]
    _run(
        "INSERT INTO lost_items (id,user_id,email,text,extracted,region_set,queries,"
        "status,best_match,best_grade,notify_weak,watch_until,lang,created_at,matched_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (lid, user_id, email, text, json.dumps(extracted, ensure_ascii=False),
         json.dumps(region_set, ensure_ascii=False), json.dumps(queries, ensure_ascii=False),
         status, json.dumps(best_match, ensure_ascii=False) if best_match else None,
         best_grade, int(notify_weak), watch_until, lang or "ko", _now(), _now() if status == "matched" else None),
    )
    return lid


def list_open() -> list[dict]:
    return [_to_dict(r) for r in _rows("SELECT * FROM lost_items WHERE status='open'")]


def all_items() -> list[dict]:
    return [_to_dict(r) for r in _rows("SELECT * FROM lost_items ORDER BY created_at DESC")]


def get(lid: str) -> dict | None:
    r = _one("SELECT * FROM lost_items WHERE id=?", (lid,))
    return _to_dict(r) if r else None


def update_match(lid: str, best_match: dict, best_grade: int, status: str) -> None:
    _run(
        "UPDATE lost_items SET best_match=?, best_grade=?, status=?, matched_at=? WHERE id=?",
        (json.dumps(best_match, ensure_ascii=False), best_grade, status, _now(), lid),
    )


def set_email(lid: str, email: str) -> None:
    """알림 받을 이메일 갱신. 이후 재매칭 알림도 이 주소로."""
    _run("UPDATE lost_items SET email=? WHERE id=?", (email or "", lid))


def update_alerts(lid: str, email: str, notify_weak: bool, watch_until: str,
                  status: str | None = None) -> None:
    """결과창 '알림 설정' 저장 — 이메일·약한 후보 옵트인·알림 기간(+만료 신고 재개 시 status)."""
    if status is None:
        _run("UPDATE lost_items SET email=?, notify_weak=?, watch_until=? WHERE id=?",
             (email or "", int(notify_weak), watch_until, lid))
    else:
        _run("UPDATE lost_items SET email=?, notify_weak=?, watch_until=?, status=? WHERE id=?",
             (email or "", int(notify_weak), watch_until, status, lid))


def set_status(lid: str, status: str) -> None:
    _run("UPDATE lost_items SET status=? WHERE id=?", (status, lid))


def mark_weak_notified(lid: str, atc_id: str) -> list[str]:
    """약한 후보 알림을 보낸 습득물 기록 → 같은 후보로 재매칭마다 반복 알림 방지."""
    row = get(lid)
    sent = (row.get("weak_notified") if row else None) or []
    if atc_id and atc_id not in sent:
        sent.append(atc_id)
    _run("UPDATE lost_items SET weak_notified=? WHERE id=?",
         (json.dumps(sent, ensure_ascii=False), lid))
    return sent


def confirm(lid: str, match: dict) -> None:
    """사용자가 '내 물건이에요' → 확인됨(수령 단계)."""
    _run(
        "UPDATE lost_items SET status='confirmed', best_match=?, best_grade=?, matched_at=? WHERE id=?",
        (json.dumps(match, ensure_ascii=False), match.get("grade"), _now(), lid),
    )


def dismiss(lid: str, atc_id: str) -> list[str]:
    """사용자가 '아니에요' → 그 후보 제외(재추천 방지), 다시 찾는중(open)."""
    row = get(lid)
    dis = (row.get("dismissed") if row else None) or []
    if atc_id and atc_id not in dis:
        dis.append(atc_id)
    _run("UPDATE lost_items SET status='open', dismissed=? WHERE id=?",
         (json.dumps(dis, ensure_ascii=False), lid))
    return dis
