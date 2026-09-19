"""테스트 공통 설정 — 외부 서비스(OpenSearch/Gemini/SMTP) 없이 순수 로직을 검증.

apps.* 임포트 전에 환경을 고정한다:
- GEMINI_API_KEY: 더미(모든 Gemini 호출은 테스트에서 목킹, 실제 호출 없음).
- DATABASE_URL: 비움 → store는 SQLite 백엔드.
- DB_PATH: 임시 파일 → 개발용 실제 DB(data/findit.db)를 건드리지 않음.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.pop("DATABASE_URL", None)  # SQLite 강제
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="findit-test-"), "test.db")
