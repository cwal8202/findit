"""지속 재매칭 — 저장된 미해결(open) 분실물을 현재 인덱스와 다시 대조.

수집기가 새 습득물을 색인한 뒤 호출(또는 스케줄러가 수집 후 실행).
저장된 추출·지역집합·쿼리를 재사용 → LLM 추출/라우팅 재호출 없이 검색+grade만(저렴).
임계 grade 이상이면 매칭 성립 → status=matched, 알림 대상 반환(알림은 Notifier 단계).

실행:  uv run python -m apps.agent.rematch
"""

from __future__ import annotations

from apps import store
from apps.agent import graph
from apps.api.config import settings
from apps.notifier import notifier


def rematch_open(verbose: bool = True) -> list[dict]:
    store.init()
    opens = store.list_open()
    if verbose:
        print(f"재매칭 대상(open): {len(opens)}건 / 임계 grade {settings.rematch_grade_threshold}")
    hits: list[dict] = []
    for row in opens:
        matches = graph.match_stored(
            row["text"], row["extracted"] or {}, row["region_set"] or [], row["queries"] or []
        )
        dismissed = set(row.get("dismissed") or [])  # '아니에요' 한 후보 제외
        matches = [m for m in matches if m.get("atc_id") not in dismissed]
        top = matches[0] if matches else None
        g = top.get("grade") if top else None
        if top and g is not None and g >= settings.rematch_grade_threshold:
            store.update_match(row["id"], top, g, "candidate")  # 확정 아닌 '유력 후보'
            hits.append({"lost": row, "match": top})
            notifier.notify(row, matches)  # 채널 독립 알림(최고 후보 + 비슷한 후보 목록)
        elif verbose:
            print(f"  · {row['id']} '{row['text'][:24]}' 최고 grade={g} (임계 미달, open 유지)")
    if verbose:
        print(f"신규 매칭 {len(hits)}건")
    return hits


if __name__ == "__main__":
    rematch_open()
