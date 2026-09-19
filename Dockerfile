# FindIt 백엔드(FastAPI + 웹) 이미지. OpenSearch·Postgres는 compose로 분리.
FROM python:3.12-slim

# curl: 색인 시드/헬스체크 편의
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

WORKDIR /app

# 의존성 먼저 설치(레이어 캐시). pyproject/uv.lock만 복사 후 sync.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen

# 앱 코드 + 런타임 데이터(gazetteer·프롬프트·웹·시드 코퍼스/임베딩)
COPY . .

ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
