"""데이터랩 × 습득물 분석 차트(PNG) — 서식4·참고자료용.

실행(그래프 라이브러리는 이번 실행에만 임시 사용, 프로젝트 의존성 아님):
  uv run --with matplotlib python -m eval.datalab.charts --period 20260801_20260830
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).parent
plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False
BLUE, ORANGE, GRAY = "#2563eb", "#ea580c", "#9ca3af"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", required=True)
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args()
    d = json.loads((HERE / "data" / f"analysis_{a.period}.json").read_text(encoding="utf-8"))
    rows, s = d["rows"], d["summary"]
    out = HERE / "figures"
    out.mkdir(exist_ok=True)
    start, end = a.period.split("_")
    span = f"{start[:4]}.{start[4:6]}.{start[6:]}~{end[4:6]}.{end[6:]}"

    # 1) 외국인 방문 상위 N 시군: 외국인 방문(막대) vs 보관 습득물(막대)
    top = rows[: a.top][::-1]
    labels = [f"{r['sido'].replace('특별자치도', '').replace('특별시', '').replace('광역시', '')} {r['name']}" for r in top]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 7.2), sharey=True, gridspec_kw={"wspace": 0.12})
    ax1.barh(labels, [r["foreign"] / 1e4 for r in top], color=BLUE)
    ax1.set_title("외국인 방문자수 (만 명·연인원)", fontsize=11)
    ax2.barh(labels, [r["found"] for r in top], color=ORANGE)
    ax2.set_title("보관 습득물 (건)", fontsize=11)
    for ax in (ax1, ax2):
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(axis="y", length=0)
        ax.grid(axis="x", color="#e5e7eb")
        ax.set_axisbelow(True)
    ax2.spines["left"].set_visible(False)
    ax2.tick_params(axis="y", labelleft=False)
    fig.suptitle(f"외국인 방문 상위 {a.top}개 시군구 — 외국인 방문과 습득물 ({span})", fontsize=13, fontweight="bold")
    fig.text(0.5, 0.01, "출처: 한국관광 데이터랩 지역별 방문자수(외국인) · 경찰청/포털기관 습득물정보(보관장소 기준) · FindIt 분석",
             ha="center", fontsize=8.5, color="#6b7280")
    fig.savefig(out / f"top{a.top}_foreign_vs_found.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    # 2) 집중도: 상위 N 시군 비중 — 시군 수 vs 외국인 방문 vs 습득물
    n_share = a.top / s["regions"]
    vals = [n_share, s[f"top{a.top}_foreign_share"], s[f"top{a.top}_found_share"]]
    cats = [f"시군구 수\n({a.top}/{s['regions']})", "외국인 방문", "보관 습득물"]
    fig, ax = plt.subplots(figsize=(7, 4.2))
    bars = ax.bar(cats, [v * 100 for v in vals], color=[GRAY, BLUE, ORANGE], width=0.55)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1.2, f"{v:.1%}", ha="center", fontsize=12, fontweight="bold")
    ax.set_ylim(0, 75)
    ax.set_ylabel("전국 대비 비중 (%)")
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_title(f"외국인 방문 상위 {a.top}개 시군구의 비중 ({span})", fontsize=12, fontweight="bold")
    fig.text(0.5, -0.02, f"습득물은 시군구 매칭 {s['found_matched']:,}건 기준 · 외국인 방문–습득물 순위상관(스피어만) {s['spearman_foreign_vs_found']:.2f}",
             ha="center", fontsize=8.5, color="#6b7280")
    fig.savefig(out / f"top{a.top}_concentration.png", dpi=180, bbox_inches="tight")
    plt.close(fig)
    print("저장:", *(p.name for p in sorted(out.glob("*.png"))))


if __name__ == "__main__":
    main()
