"""분실물 저장소 — 린 v1은 SQLite(stdlib). 추후 PostgreSQL로 이전.

신고를 저장(status=open)하고, 지속 재매칭이 새 습득물과 대조해 매칭되면 갱신.
개인정보(신고 원문) 포함 → DB 파일은 git 제외(.gitignore).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from apps.api.config import settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS lost_items (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                text TEXT,
                extracted TEXT,       -- JSON
                region_set TEXT,      -- JSON list
                queries TEXT,         -- JSON list
                status TEXT DEFAULT 'open',   -- open | matched | closed
                best_match TEXT,      -- JSON (최고 매칭 습득물)
                best_grade INTEGER,
                created_at TEXT,
                matched_at TEXT
            )
        """)


def _row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("extracted", "region_set", "queries", "best_match"):
        d[k] = json.loads(d[k]) if d.get(k) else None
    return d


def add(text: str, extracted: dict, region_set: list, queries: list,
        best_match: dict | None, best_grade: int | None,
        status: str = "open", user_id: str = "anon") -> str:
    lid = uuid.uuid4().hex[:12]
    with _conn() as c:
        c.execute(
            "INSERT INTO lost_items (id,user_id,text,extracted,region_set,queries,"
            "status,best_match,best_grade,created_at,matched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (lid, user_id, text, json.dumps(extracted, ensure_ascii=False),
             json.dumps(region_set, ensure_ascii=False), json.dumps(queries, ensure_ascii=False),
             status, json.dumps(best_match, ensure_ascii=False) if best_match else None,
             best_grade, _now(), _now() if status == "matched" else None),
        )
    return lid


def list_open() -> list[dict]:
    with _conn() as c:
        return [_row_to_dict(r) for r in c.execute("SELECT * FROM lost_items WHERE status='open'")]


def all_items() -> list[dict]:
    with _conn() as c:
        return [_row_to_dict(r) for r in
                c.execute("SELECT * FROM lost_items ORDER BY created_at DESC")]


def get(lid: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM lost_items WHERE id=?", (lid,)).fetchone()
    return _row_to_dict(r) if r else None


def update_match(lid: str, best_match: dict, best_grade: int, status: str) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE lost_items SET best_match=?, best_grade=?, status=?, matched_at=? WHERE id=?",
            (json.dumps(best_match, ensure_ascii=False), best_grade, status, _now(), lid),
        )
