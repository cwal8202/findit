# FindIt 배포 (단일 VPS + Docker Compose)

Hetzner CX33(8GB) 등 VPS 한 대에 **OpenSearch + 백엔드(FastAPI+웹)** 를 docker-compose로 띄운다.
프론트는 백엔드가 같이 서빙하므로 별도 배포 불필요 → `http://<서버IP>` 로 접속.

> 시크릿은 서버의 `.env`(git 미포함). OpenSearch는 외부 포트 노출 안 함(백엔드만 80 노출).

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
git clone https://github.com/cwal8202/findit.git
cd findit
```

## 3. 시크릿(.env) 작성
```bash
cp .env.example .env
nano .env      # 아래 키 채우기
```
- `GEMINI_API_KEY` (임베딩·grading·Vision)
- `DATA_GO_KR_SERVICE_KEY_DECODED` (수집기)
- `KAKAO_REST_API_KEY` (지오코딩, 선택)
- 이메일 알림 쓰면: `NOTIFIER_CHANNEL=email`, `SMTP_USER`, `SMTP_PASSWORD`, `NOTIFY_EMAIL_TO`
- (OpenSearch URL은 compose가 자동 주입하므로 건드리지 말 것)

## 4. 기동 (빌드 + 실행)
```bash
docker compose -f docker-compose.prod.yml up -d --build
# OpenSearch가 green 될 때까지 대기(1~2분). 상태:
docker compose -f docker-compose.prod.yml ps
```

## 5. 색인 시드 (캐시된 2,000건 즉시 색인 — 임베딩 비용 0)
```bash
docker compose -f docker-compose.prod.yml exec backend uv run python eval/index_opensearch.py
```
→ 인덱스 자동 생성 + 문서수 출력되면 성공.

## 6. 접속 확인
- 브라우저에서 **http://<서버IP>** → FindIt 화면.
- `curl http://<서버IP>/health` → `{"status":"ok",...}`

## 7. 방화벽 (포트 열기)
- **Hetzner Cloud Firewall**(콘솔) 또는 서버 ufw로 **22(SSH), 80(HTTP)** 인바운드 허용.
```bash
ufw allow 22 && ufw allow 80 && ufw --force enable   # ufw 쓸 경우
```

## 8. (선택) 실데이터 수집 — 최근 1개월 롤링
```bash
# 예: 9월 1~30일 습득물 상세 enrich까지 수집·색인 (Gemini 임베딩 발생, --max로 조절)
docker compose -f docker-compose.prod.yml exec backend \
  uv run python -m apps.collector.run --source both --start 20260901 --end 20260930 --max 5000 --enrich --rematch
```
> data.go.kr 상세는 항목당 1콜(일 10만 한도), 임베딩도 건당 비용 → `--max`로 규모 조절. 처음엔 작게.

## 운영 메모
- 로그: `docker compose -f docker-compose.prod.yml logs -f backend`
- 재시작: `docker compose -f docker-compose.prod.yml restart backend`
- 중지/삭제(과금 종료 전): `docker compose -f docker-compose.prod.yml down`  (데이터 볼륨 유지)
- 완전 삭제: 위 + `docker volume rm findit_findit-os-data findit_findit-data`
- 코드 갱신: `git pull && docker compose -f docker-compose.prod.yml up -d --build`

## 이후 강화(선택)
- **도메인 + HTTPS**: nginx(리버스프록시) + Let's Encrypt(certbot) → `https://도메인`.
- **CORS**: 프론트를 Vercel/Next.js로 분리하면 FastAPI에 CORS 허용 오리진 추가.
- **PostgreSQL**: SQLite → Postgres 컨테이너로 전환(다중 인스턴스/영속 강화).
