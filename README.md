# FindIt — 분실물 매칭 AI 에이전트

분실물을 자연어로 등록하면 경찰청·포털기관 **습득물 공개데이터**를 수집해,
**하이브리드 검색(의미 임베딩 + BM25) + LLM grading** 으로 매칭하고 알림하는 서비스.
4주 사이드 프로젝트(포트폴리오) — 코드 품질과 **설계 근거 문서화**를 중시.

> 🚀 **라이브 데모**: https://findit-lost.duckdns.org 🔒 *(심사 기간 한시 운영)*
> — 예: *"어제 2호선에서 검정 닥스 지갑 잃어버렸어요"*, *"동전지갑 잃어버렸어요"* 검색 →
> 유력 후보 + 근거 + (사진 있으면) 썸네일 → *[내 물건이에요]* 시 보관기관·연락처 안내.
>
> 📄 모든 결정·실험·수치는 [`docs/PROGRESS.md`](docs/PROGRESS.md), 배포는 [`docs/DEPLOY.md`](docs/DEPLOY.md).

---

## 현재 상태

**✅ 동작하는 전체 파이프라인** (자연어 신고 → 매칭 → 저장 → 지속 재매칭 → 알림 → 웹)
```
수집기(data.go.kr) → OpenSearch 색인 → 검색+지역가점 → LLM grading → 매칭 에이전트(LangGraph)
                                                                    ↓
              웹 화면 ← 알림(Notifier) ← 지속 재매칭 ← 분실물 DB(SQLite)
```
- **검색 API**: `GET /found-items/search`, **매칭 에이전트**: `POST /lost-items`(자연어 신고 한 줄 → 매칭)
- **웹**: `http://localhost:8000` — 등록 → 매칭 결과(근거 포함) → 내 신고 목록
- **지속 재매칭**: 신고를 저장(open) → 새 습득물 유입마다 재매칭 → 성립 시 알림

**✅ 검색 품질 실측 (eval, 골든셋 29문항)**

| 방식 | R@1 | R@5 | MRR |
|------|:---:|:---:|:---:|
| BM25 (nori) | 0.517 | 0.793 | 0.633 |
| **의미 임베딩 (KNN)** | **0.931** | **1.000** | **0.960** |

→ 의미 임베딩이 주력 랭커(한글↔영문·번역·오타 커버). **지역 가점**은 밀집 군집(검정지갑 193건 등)에서
묻힌 정답을 MRR 0.084→0.857로 끌어올림(덧셈 소프트 w≈0.05, eval 검증). dep→구/시 gazetteer+지오코딩 커버 **95%**.

**⏳ 남은 것**: 카카오 알림톡(현재 콘솔 알림) · 이미지 레인(사진 매칭) · 브라우저 확장 · 수집 스케줄 자동화 ·
API 테스트(pytest) · 인증/PostgreSQL 이전.

---

## 아키텍처 원칙 (핵심)

- **채널 독립 코어** — 비즈니스 로직에 카카오/웹 의존성 금지. 알림은 `Notifier` 인터페이스로만.
- **장소·시간은 하드필터가 아니라 가점(boosting)** — 조건은 정답을 좁히는 게 아니라 순위를 정하는 점수.
- **되돌릴 수 없는 액션(공식 신고)은 HITL** — 자동화 금지, 사용자 승인 유지.
- **공공 API 필드는 `fixtures/responses/` 샘플이 진실** — 추측으로 필드명 만들지 않음.

## 기술 스택

Python 3.12 · FastAPI · LangGraph · Gemini API(embedding·grading) ·
OpenSearch(nori, 768-dim HNSW KNN) · PostgreSQL(배포) / SQLite(로컬) · 바닐라 JS 웹(→React/확장) · Docker Compose · uv · ruff

## 레포 구조

```
eval/        골든셋 + 성능 실험 (BM25/임베딩/하이브리드/스윕, OpenSearch 색인·검색·데모)
infra/       docker-compose + OpenSearch(nori) Dockerfile + 인덱스 매핑
fixtures/    공공 API 실응답 샘플 (진실의 원천)
docs/        PROGRESS.md (설계·실험 기록)
apps/api/       FastAPI 엔드포인트 (GET /found-items/search, POST /lost-items, GET /lost-items, GET /)
apps/agent/     매칭 에이전트 (LangGraph: extract→route→fanout→search→grade) + rematch(지속 재매칭)
apps/collector/ 수집기 (data.go.kr 습득물 → 상세 enrich → 임베딩 → 색인, --rematch)
apps/store.py   분실물 저장소 (PostgreSQL/SQLite 이중 백엔드, DATABASE_URL로 선택)
apps/notifier.py 알림 (Notifier 인터페이스 + ConsoleNotifier, 추후 KakaoNotifier)
apps/web/       웹 화면 (단일 페이지, FastAPI가 GET /로 서빙)
```
> `apps/extension`(브라우저 확장)은 다음 단계 예정.

## 실행

**1. 사전 준비**: Docker Desktop, [uv](https://docs.astral.sh/uv/), `.env` 작성 ([`.env.example`](.env.example) 참고)
```bash
uv sync   # .venv + 의존성(fastapi/uvicorn/langgraph 등)
```

**2. OpenSearch(nori) 기동 + 인덱스**
```bash
docker compose -f infra/docker-compose.yml up -d --build
curl -X PUT localhost:9200/found_items -H "Content-Type: application/json" \
  --data-binary @infra/opensearch/mappings/found_items.json      # 최초 1회
python eval/index_opensearch.py                                   # 캐시 임베딩으로 시드 색인(선택)
```

**3. 앱(API+웹) 실행** → 브라우저에서 http://localhost:8000
```bash
uv run uvicorn apps.api.main:app --reload      # /docs 에 API 문서
```

**4. 수집기 (실 데이터 갱신, 선택)** — 스케줄과 무관한 CLI. 색인 후 지속 재매칭까지:
```bash
uv run python -m apps.collector.run --source both --start 20260901 --end 20260901 --max 200 --enrich --rematch
```

**성능 실험(eval)**: `python eval/run_os.py`(OpenSearch 실측) · `python eval/run_boost.py`(지역 가점) ·
`python eval/run_grade.py`(LLM grading). 상세는 [`docs/PROGRESS.md`](docs/PROGRESS.md).

> Windows에서 콘솔 한글/이모지 깨짐 방지: `PYTHONUTF8=1` 권장.

---

*설계 결정의 배경과 모든 실험 수치는 [`docs/PROGRESS.md`](docs/PROGRESS.md)를 참고하세요.*
