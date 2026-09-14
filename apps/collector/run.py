"""수집기 CLI — data.go.kr 목록 페이징 → 상세 enrich → 임베딩 → OpenSearch 색인(upsert).

스케줄과 무관한 멱등 명령. 언제 돌릴지는 외부 스케줄러(cron/작업 스케줄러/클라우드)가 결정.
문서 _id = atcId_fdSn 라 재실행은 upsert(중복 없음).

예:
  # 검증(임베딩·색인 없이 몇 건만 확인)
  uv run python -m apps.collector.run --source police --start 20260831 --end 20260831 --max 5 --dry-run
  # 실제 색인(상세 enrich + 임베딩)
  uv run python -m apps.collector.run --source both --start 20260901 --end 20260901 --max 200 --enrich
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request

from apps.api import gemini
from apps.api.config import settings
from apps.collector import client
from apps.collector.normalize import doc_id, embed_text, normalize


def fetch(source: str, start: str, end: str, rows: int, max_items: int, enrich: bool) -> list[dict]:
    docs: list[dict] = []
    page = 1
    while len(docs) < max_items:
        res = client.get_list(source, start, end, page=page, rows=min(rows, max_items - len(docs)))
        if res["code"] not in ("00", ""):
            print(f"  ⚠ {source} 목록 응답: {res['code']} {res['msg']}")
            break
        items = res["items"]
        if not items:
            break
        for li in items:
            detail = {}
            if enrich:
                try:
                    detail = client.get_detail(source, li["atcId"], li["fdSn"])
                except Exception as e:  # noqa: BLE001
                    print(f"  ! 상세 실패 {li.get('atcId')}: {e}")
                time.sleep(0.1)  # 상세는 항목당 1콜 — 예의상 간격
            docs.append(normalize(source, li, detail))
        print(f"  {source} p{page}: 누적 {len(docs)}/{min(max_items, res['total'])} (total {res['total']})")
        if len(docs) >= res["total"]:
            break
        page += 1
    return docs[:max_items]


def bulk_index(docs: list[dict]) -> dict:
    lines = []
    for d in docs:
        lines.append(json.dumps({"index": {"_index": settings.opensearch_index, "_id": doc_id(d)}}))
        lines.append(json.dumps(d, ensure_ascii=False))
    body = ("\n".join(lines) + "\n").encode("utf-8")
    req = urllib.request.Request(
        f"{settings.opensearch_url}/_bulk", body, {"Content-Type": "application/x-ndjson"}
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        res = json.load(r)
    errors = [i for i in res.get("items", []) if i.get("index", {}).get("status", 200) >= 300]
    return {"took": res.get("took"), "errors": len(errors), "count": len(docs)}


def main() -> None:
    ap = argparse.ArgumentParser(description="FindIt 습득물 수집기")
    ap.add_argument("--source", choices=["police", "portal", "both"], default="both")
    ap.add_argument("--start", required=True, help="START_YMD (YYYYMMDD)")
    ap.add_argument("--end", required=True, help="END_YMD (YYYYMMDD)")
    ap.add_argument("--rows", type=int, default=100, help="페이지당 목록 건수")
    ap.add_argument("--max", type=int, default=100, help="소스별 최대 수집 건수(비용/한도 보호)")
    ap.add_argument("--enrich", action="store_true", help="상세 호출로 습득장소·특이사항 채움(항목당 1콜)")
    ap.add_argument("--no-embed", action="store_true", help="임베딩·색인 생략(정규화까지만)")
    ap.add_argument("--dry-run", action="store_true", help="색인 안 함, 샘플만 출력")
    args = ap.parse_args()

    sources = ["police", "portal"] if args.source == "both" else [args.source]
    all_docs: list[dict] = []
    for src in sources:
        print(f"[{src}] 수집 {args.start}~{args.end} (max {args.max}, enrich={args.enrich})")
        all_docs += fetch(src, args.start, args.end, args.rows, args.max, args.enrich)

    print(f"\n총 정규화 {len(all_docs)}건")
    if all_docs:
        d = all_docs[0]
        print(f"  샘플: {d['name']} / {d['color']} / {d['category']} / 보관:{d['dep_place']} / "
              f"습득장소:{d['found_place'] or '-'} / 특이:{(d['description'] or '-')[:30]}")

    if args.dry_run or args.no_embed:
        print("(dry-run/no-embed — 임베딩·색인 생략)")
        return

    print("임베딩(Gemini)...")
    vecs = gemini.embed_texts([embed_text(d) for d in all_docs])
    for d, v in zip(all_docs, vecs):
        d["embedding"] = v

    print("OpenSearch 색인(_bulk upsert)...")
    res = bulk_index(all_docs)
    print(f"  완료: {res['count']}건 색인, 오류 {res['errors']}건 (took {res['took']}ms)")
    urllib.request.urlopen(urllib.request.Request(  # 색인 즉시 반영
        f"{settings.opensearch_url}/{settings.opensearch_index}/_refresh", method="POST"), timeout=15)
    cnt = json.load(urllib.request.urlopen(
        f"{settings.opensearch_url}/{settings.opensearch_index}/_count", timeout=15))["count"]
    print(f"  현재 인덱스 총 문서: {cnt}")


if __name__ == "__main__":
    main()
