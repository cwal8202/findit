"""앱 설정 — .env 를 pydantic-settings 로 읽음. 시크릿은 코드에 없음."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT = Path(__file__).resolve().parents[2]  # repo 루트


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ROOT / ".env"), extra="ignore", case_sensitive=False
    )

    gemini_api_key: str
    gemini_embed_model: str = "gemini-embedding-001"
    gemini_embed_dim: int = 768
    gemini_grade_model: str = "gemini-3.6-flash"  # 2.5-flash는 신규차단(404) → 3.6-flash
    kakao_rest_api_key: str = ""
    data_go_kr_service_key_decoded: str = ""  # 경찰청 습득물 API (디코딩 키)

    opensearch_url: str = "http://localhost:9200"
    opensearch_index: str = "found_items"

    # 분실물 저장소(린 v1: SQLite. 추후 PostgreSQL). 런타임 상태 → git 제외.
    db_path: Path = _ROOT / "data" / "findit.db"
    rematch_grade_threshold: int = 80  # 이 점수 이상이면 "매칭 성립"(알림 대상)

    # gazetteer/지오코딩 데이터 위치 (현재 eval/data 재사용 — 추후 공용 data/로 이동 예정)
    data_dir: Path = _ROOT / "eval" / "data"


settings = Settings()
