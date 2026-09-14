# FindIt — 분실물 매칭 AI 에이전트

분실물을 자연어로 등록하면 경찰청·포털기관 **습득물 공개데이터**를 수집해,
**하이브리드 검색(의미 임베딩 + BM25) + LLM grading** 으로 매칭하고 알림하는 서비스.
4주 사이드 프로젝트(포트폴리오) — 코드 품질과 **설계 근거 문서화**를 중시.

> 📄 모든 결정·실험·수치는 [`docs/PROGRESS.md`](docs/PROGRESS.md)에 정리되어 있습니다.

---

## 현재 상태

**✅ 검증된 것 — "검색 두뇌"**
- 공공 API(경찰청·포털기관 습득물) 실응답 확보 → 정규화·색인
- 실제 OpenSearch(nori + 768-dim KNN)에서 하이브리드 검색 동작
- eval로 성능 실측 (골든셋 29문항):

| 방식 | R@1 | R@5 | MRR |
|------|:---:|:---:|:---:|
| BM25 (nori) | 0.517 | 0.793 | 0.633 |
| **의미 임베딩 (KNN)** | **0.931** | **1.000** | **0.960** |
| 하이브리드(0.5:0.5) | 0.759 | 0.897 | 0.821 |

→ **결론: 의미 임베딩이 주력 랭커.** 한글↔영문 브랜드(닥스↔DAKS), 번역(대한항공↔KOREAN AIR),
오타까지 커버. 하이브리드 반반은 오히려 손해(가중치는 튜닝 대상).

**⏳ 미구현**: 사용자 앱/화면, 매일 자동 수집(collector), 카카오 알림, LLM grading, 가점(boosting).

---

## 아키텍처 원칙 (핵심)

- **채널 독립 코어** — 비즈니스 로직에 카카오/웹 의존성 금지. 알림은 `Notifier` 인터페이스로만.
- **장소·시간은 하드필터가 아니라 가점(boosting)** — 조건은 정답을 좁히는 게 아니라 순위를 정하는 점수.
- **되돌릴 수 없는 액션(공식 신고)은 HITL** — 자동화 금지, 사용자 승인 유지.
- **공공 API 필드는 `fixtures/responses/` 샘플이 진실** — 추측으로 필드명 만들지 않음.

## 기술 스택

Python 3.12 · FastAPI · LangGraph · Gemini API(embedding·grading) ·
OpenSearch(nori, 768-dim HNSW KNN) · PostgreSQL · React · Docker Compose · uv · ruff

## 레포 구조

```
eval/        골든셋 + 성능 실험 (BM25/임베딩/하이브리드/스윕, OpenSearch 색인·검색·데모)
infra/       docker-compose + OpenSearch(nori) Dockerfile + 인덱스 매핑
fixtures/    공공 API 실응답 샘플 (진실의 원천)
docs/        PROGRESS.md (설계·실험 기록)
```
> `apps/{api,agent,collector,web,extension}`는 앱 구현 단계에서 추가 예정.

## 실행

**1. 사전 준비**: Docker Desktop, Python, `.env` 작성 ([`.env.example`](.env.example) 참고)

**2. OpenSearch(nori) 기동**
```bash
docker compose -f infra/docker-compose.yml up -d --build
```

**3. 인덱스 생성 + 색인 + 검색 데모**
```bash
# 인덱스 매핑 적용
curl -X PUT localhost:9200/found_items -H "Content-Type: application/json" \
  --data-binary @infra/opensearch/mappings/found_items.json
# 캐시된 임베딩으로 색인 (재임베딩 없음)
python eval/index_opensearch.py
# 성능 채점 / 자유 문장 검색
python eval/run_os.py
python eval/search_demo.py "지하철에 흰색 무선이어폰 두고 내렸어요"
```

---

*설계 결정의 배경과 모든 실험 수치는 [`docs/PROGRESS.md`](docs/PROGRESS.md)를 참고하세요.*
