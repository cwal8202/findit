#!/usr/bin/env bash
# 매일 "최근 N일" 습득물 수집 + 재매칭 (cron에서 호출). 심사 기간 최신 데이터 유지용.
#
# 왜 '어제분'이 아니라 최근 N일인가: 습득물은 습득일 이후 며칠에 걸쳐 등록된다.
#   실측(2026-09-29): 자정에 '어제분'을 조회하면 5건 → 하루 뒤 같은 날짜는 ~2,800건.
#   그래서 매일 최근 N일 창을 다시 조회하고, 수집기가 이미 색인된 항목은 건너뛴다(상세·임베딩은 새 항목만).
#
# 설치(서버, KST 자정):
#   chmod +x scripts/daily_collect.sh
#   (crontab -l 2>/dev/null; echo "0 0 * * * /root/findit/scripts/daily_collect.sh >> /root/findit/daily_collect.log 2>&1") | crontab -
set -e
cd "$(dirname "$0")/.."                 # repo 루트(findit/)

DAYS=${COLLECT_DAYS:-7}                 # 조회 창(일). 늦게 등록되는 습득물까지 잡도록 넉넉히.
START=$(date -d "$DAYS days ago" +%Y%m%d)
END=$(date -d 'yesterday' +%Y%m%d)
echo "==== [$(date '+%F %T')] 최근 ${DAYS}일($START~$END) 수집 시작 ===="

# --max: 소스별 목록 상한(하루 ~1,700건/소스 × 7일 여유). 상세·임베딩은 새 항목에만 쓰임.
docker compose -f docker-compose.prod.yml exec -T backend \
  uv run python -m apps.collector.run \
    --source both --start "$START" --end "$END" \
    --rows 1000 --max 20000 --enrich --rematch

echo "==== [$(date '+%F %T')] 수집 완료 ===="
