#!/usr/bin/env bash
# PostgreSQL(분실물 신고) 일일 백업 — pg_dump → gzip, 14일 보관. 실패하면 관리자에게 메일.
#
# 설치(서버, 수집이 끝난 뒤 새벽 4시 KST):
#   chmod +x scripts/backup_db.sh
#   (crontab -l 2>/dev/null; echo "0 4 * * * /root/findit/scripts/backup_db.sh >> /root/findit/backup.log 2>&1") | crontab -
#
# 복구: gunzip -c /root/findit-backups/findit_YYYYMMDD_HHMM.sql.gz | \
#        docker compose -f docker-compose.prod.yml exec -T postgres psql -U findit findit
#
# 참고: 백업은 같은 서버에 남는다(서버 자체 장애엔 무력) — 중요해지면 외부 저장소로 복사할 것.
#       OpenSearch 색인은 공공데이터에서 다시 수집할 수 있어 백업하지 않음(.env는 별도로 안전한 곳에 보관).
set -uo pipefail
cd "$(dirname "$0")/.."
COMPOSE="docker compose -f docker-compose.prod.yml"
DIR=${BACKUP_DIR:-/root/findit-backups}
KEEP=${BACKUP_KEEP_DAYS:-14}

mkdir -p "$DIR" && chmod 700 "$DIR"
F="$DIR/findit_$(date +%Y%m%d_%H%M).sql.gz"
if $COMPOSE exec -T postgres pg_dump -U findit findit | gzip > "$F.tmp" && [ -s "$F.tmp" ]; then
  mv "$F.tmp" "$F" && chmod 600 "$F"
  echo "[$(date '+%F %T')] 백업 완료: $F ($(du -h "$F" | cut -f1))"
else
  rm -f "$F.tmp"
  echo "[$(date '+%F %T')] 백업 실패"
  $COMPOSE run --rm -T backend uv run python -m apps.ops alert "DB 백업 실패" \
    "backup_db.sh 실패 ($(date '+%F %T')). 로그: /root/findit/backup.log" || true
  exit 1
fi
find "$DIR" -name 'findit_*.sql.gz' -mtime +"$KEEP" -delete
