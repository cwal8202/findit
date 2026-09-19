#!/usr/bin/env bash
# 매일 "어제" 습득물 수집 + 재매칭 (cron에서 호출). 심사 기간 최신 데이터 유지용.
# 설치(서버):
#   chmod +x scripts/daily_collect.sh
#   (crontab -l 2>/dev/null; echo "0 5 * * * /root/findit/scripts/daily_collect.sh >> /root/findit/daily_collect.log 2>&1") | crontab -
set -e
cd "$(dirname "$0")/.."                 # repo 루트(findit/)

YESTERDAY=$(date -d 'yesterday' +%Y%m%d)
echo "==== [$(date '+%F %T')] 어제분($YESTERDAY) 수집 시작 ===="

docker compose -f docker-compose.prod.yml exec -T backend \
  uv run python -m apps.collector.run \
    --source both --start "$YESTERDAY" --end "$YESTERDAY" \
    --max 2000 --enrich --rematch

echo "==== [$(date '+%F %T')] 수집 완료 ===="
