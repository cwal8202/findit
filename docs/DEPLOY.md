# FindIt 배포 (단일 VPS + Docker Compose + HTTPS + 자동배포)

Hetzner CX33(8GB) 등 VPS 한 대에 **OpenSearch(nori) + PostgreSQL + 백엔드(FastAPI+웹) + Caddy**를
`docker-compose.prod.yml`로 함께 띄운다. 프론트는 백엔드가 같이 서빙하므로 별도 배포 불필요.

- **HTTPS**: DuckDNS(무료 서브도메인) + **Caddy**(리버스프록시)가 Let's Encrypt 인증서 자동 발급·갱신.
  `https://<도메인>` 접속. http→https 자동 리다이렉트. (도메인 미설정 시 `http://<서버IP>`로도 동작.)
- **자동배포(CI/CD)**: `main`에 push → GitHub Actions가 서버 SSH → `git pull` + 재빌드. (아래 9절)
- **최신 데이터**: cron이 매일 어제분 습득물 수집·색인·재매칭. (아래 8절)

> 시크릿은 서버의 `.env`(git 미포함). **OpenSearch·PostgreSQL은 외부 포트 노출 안 함** — Caddy(80/443)만 외부에 열림.

라이브(심사 기간 한시): **https://findit-lost.duckdns.org**

---

## 0. 서버 접속
```bash
ssh root@<서버IP>       # 콘솔이 준 IP / (SSH키 없으면 이메일로 온 root 비번)
```

## 1. Docker 설치 (Ubuntu)
```bash
curl -fsSL https://get.docker.com | sh
docker --version && docker compose version
```

## 2. 코드 받기
```bash
apt-get update && apt-get install -y git
git clone https://github.com/cwal8202/findit.git /root/findit
cd /root/findit
```
> 자동배포 워크플로가 `/root/findit` 경로를 가정하므로 이 위치에 클론한다.

## 3. 시크릿(.env) 작성
```bash
cp .env.example .env
nano .env      # 아래 키 채우기
```
- `GEMINI_API_KEY` (임베딩·grading·Vision)
- `DATA_GO_KR_SERVICE_KEY_DECODED` (수집기)
- `KAKAO_REST_API_KEY` (지오코딩, 선택)
- `POSTGRES_PASSWORD` (배포 DB 비밀번호 — 강한 값 권장)
- **`DOMAIN`** (HTTPS용 DuckDNS 도메인, 예: `findit-lost.duckdns.org`) — 비우면 http만.
- 이메일 알림 쓰면: `NOTIFIER_CHANNEL=email`, `SMTP_USER`, `SMTP_PASSWORD`, `NOTIFY_EMAIL_TO`
- (`OPENSEARCH_URL`·`DATABASE_URL`은 compose가 자동 주입하므로 건드리지 말 것)

> **DuckDNS 준비**: duckdns.org에서 서브도메인 생성 → 서버 IP로 A레코드(현재 IP) 지정 → 그 도메인을 `.env` `DOMAIN`에.

## 4. 기동 (빌드 + 실행)
```bash
docker compose -f docker-compose.prod.yml up -d --build
# OpenSearch가 healthy 될 때까지 대기(1~2분). 상태:
docker compose -f docker-compose.prod.yml ps
```
컨테이너 4종이 뜬다: `findit-opensearch` · `findit-postgres` · `findit-backend` · `findit-caddy`.

## 5. 색인 시드 (캐시된 2,000건 즉시 색인 — 임베딩 비용 0)
```bash
docker compose -f docker-compose.prod.yml exec backend uv run python eval/index_opensearch.py
```
→ 인덱스 자동 생성 + 문서수 출력되면 성공.

## 6. 접속 확인
- 브라우저에서 **https://<도메인>** → FindIt 화면 (🔒 유효 인증서 확인).
- `curl https://<도메인>/health` → `{"status":"ok",...}`
- (도메인 미설정 시 `http://<서버IP>`)

## 7. 방화벽 (포트 열기)
- **Hetzner Cloud Firewall**(콘솔) 또는 서버 ufw로 **22(SSH), 80(HTTP), 443(HTTPS)** 인바운드 허용.
  (Caddy가 80으로 인증서 검증 + https 리다이렉트, 443으로 실서비스. OpenSearch/Postgres 포트는 열지 않는다.)
```bash
ufw allow 22 && ufw allow 80 && ufw allow 443 && ufw --force enable   # ufw 쓸 경우
```

## 8. 최신 데이터 유지 — 매일 자동 수집 (cron)
`scripts/daily_collect.sh`가 "어제분" 습득물을 수집·색인·재매칭한다. cron에 등록:
```bash
chmod +x scripts/daily_collect.sh
(crontab -l 2>/dev/null; echo "0 5 * * * /root/findit/scripts/daily_collect.sh >> /root/findit/daily_collect.log 2>&1") | crontab -
```
- 매일 05:00(서버 시각) 실행 → 어제분 `--enrich --rematch`(신규 습득물 유입 시 open 신고 자동 재매칭 → 알림).
- 비용: data.go.kr 상세는 항목당 1콜(일 10만 한도), 임베딩도 건당 비용 → 스크립트의 `--max 2000`으로 규모 조절.
- 즉시 한 번 돌려보려면: `bash scripts/daily_collect.sh`

## 9. 자동배포 (GitHub Actions)
`.github/workflows/deploy.yml` — **`main`에 push되면** GitHub Actions가 서버에 SSH 접속해 자동 재배포한다.
```
main push → git pull --ff-only → docker compose up -d --build backend caddy → image prune
```
- 접속정보는 **레포 Settings > Secrets**에 저장(코드에 노출 안 함):
  - `SSH_HOST` : 서버 IP
  - `SSH_KEY`  : 서버 접속용 SSH 개인키 전체(`-----BEGIN ...-----` 포함)
- 공개 레포라 Actions **무료·무제한**. Docker 레이어 캐시로 재배포 ~십수 초.
- 수동 트리거도 가능(Actions 탭 > Deploy > Run workflow / `workflow_dispatch`).

> 자동배포는 `backend`·`caddy`만 재빌드(코드/웹 변경분). OpenSearch·Postgres는 데이터 보존 위해 건드리지 않음.

---

## 운영 메모
- 로그: `docker compose -f docker-compose.prod.yml logs -f backend`
- 재시작: `docker compose -f docker-compose.prod.yml restart backend`
- 수동 코드 갱신(자동배포 대신): `git pull && docker compose -f docker-compose.prod.yml up -d --build backend caddy`
- 중지(과금 종료 전, 데이터 볼륨 유지): `docker compose -f docker-compose.prod.yml down`
- 완전 삭제: 위 + `docker volume rm findit_findit-os-data findit_findit-pg-data findit_findit-caddy-data findit_findit-caddy-config`
- 인증서 갱신은 Caddy가 자동. `findit-caddy-data` 볼륨에 인증서 영속(재기동해도 재발급 안 함).

## 이후 강화(선택)
- **React/Vercel 프론트 분리**: 분리 시 FastAPI에 CORS 허용 오리진 추가.
- **관측성**: 로그 집계·헬스 알림, OpenSearch 스냅샷 백업.
- **이미지 임베딩(CLIP) 레인**: RAM 여유 있는 인스턴스에서 image-to-image KNN 추가(현재는 Gemini 사진 대조로 대체).
