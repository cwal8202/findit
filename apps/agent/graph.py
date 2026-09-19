"""매칭 에이전트 (LangGraph) — 신고 자연어 → 매칭 결과.

extract → route → fanout → search(병합) → grade. 노드는 평범한 함수, LangGraph가 그래프로 엮음.
v1은 선형. 추후 분기/루프(예: grade 낮으면 fanout 확장 후 재검색), DB 저장, 지속 재매칭 추가.
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from apps.agent import extract as extract_mod
from apps.agent import fanout
from apps.agent import route
from apps.api import gemini
from apps.api import search as search_mod

SEARCH_PER_QUERY = 10
MERGED_TOPK = 12


class AgentState(TypedDict, total=False):
    text: str
    lost_date: str
    extracted: dict
    region_set: list
    queries: list
    candidates: list
    matches: list


def n_extract(state: AgentState) -> dict:
    return {"extracted": extract_mod.extract(state["text"], state.get("lost_date") or None)}


def n_route(state: AgentState) -> dict:
    return {"region_set": route.resolver.expand(state["extracted"].get("place_context", ""))}


def n_fanout(state: AgentState) -> dict:
    return {"queries": fanout.build_queries(state["extracted"])}


def n_search(state: AgentState) -> dict:
    ex = state["extracted"]
    region_set = state.get("region_set") or []
    lost_date = ex.get("lost_date") or None
    merged: dict[str, dict] = {}
    for q in state["queries"]:
        for it in search_mod.search(
            q, region_set=region_set, lost_date=lost_date, do_grade=False,
            size=SEARCH_PER_QUERY, candidates=100,
        ):
            key = f"{it['atc_id']}_{it['fd_sn']}"
            if key not in merged or it["score"] > merged[key]["score"]:
                merged[key] = it
    cands = sorted(merged.values(), key=lambda x: -x["score"])[:MERGED_TOPK]
    return {"candidates": cands}


def n_grade(state: AgentState) -> dict:
    cands = state.get("candidates") or []
    if not cands:
        return {"matches": []}
    gmap = {int(x["id"]): x for x in gemini.grade(state["text"], cands) if "id" in x}
    for i, it in enumerate(cands):
        g = gmap.get(i)
        it["grade"] = g.get("score") if g else None
        it["reason"] = g.get("reason") if g else None
    cands.sort(key=lambda it: -(it["grade"] if it["grade"] is not None else -1))
    return {"matches": cands}


def _build():
    g = StateGraph(AgentState)
    g.add_node("extract", n_extract)
    g.add_node("route", n_route)
    g.add_node("fanout", n_fanout)
    g.add_node("search", n_search)
    g.add_node("grade", n_grade)
    g.add_edge(START, "extract")
    g.add_edge("extract", "route")
    g.add_edge("route", "fanout")
    g.add_edge("fanout", "search")
    g.add_edge("search", "grade")
    g.add_edge("grade", END)
    return g.compile()


GRAPH = _build()


def run(text: str, lost_date: str | None = None) -> dict:
    return GRAPH.invoke({"text": text, "lost_date": lost_date or ""})


def _synth_text(ex: dict, note: str = "") -> str:
    """추출 필드 → 사람이 읽을 한 줄(설명/grade/저장/표시용). 사진 신고는 문장이 없으므로 합성."""
    core = " ".join(x for x in (ex.get("color", ""), ex.get("brand", ""), ex.get("item", "")) if x)
    parts = [p for p in (core, ex.get("features", ""), note.strip()) if p]
    return " / ".join(parts) or (ex.get("category", "") or "사진 신고")


def run_image(image_b64: str, mime: str = "image/jpeg",
              note: str = "", lost_date: str | None = None) -> dict:
    """사진(+선택 메모) 신고 → Vision 추출 → 텍스트와 동일 파이프라인(라우팅~grade).

    사진에는 문장이 없으므로 추출 필드로 검색용 text를 합성해 grade/저장/표시에 재사용.
    """
    ex = extract_mod.extract_image(image_b64, mime, note=note, lost_date=lost_date)
    text = _synth_text(ex, note)
    state: AgentState = {"text": text, "extracted": ex}
    state.update(n_route(state))
    state.update(n_fanout(state))
    state.update(n_search(state))
    state.update(n_grade(state))
    return state


def match_stored(text: str, extracted: dict, region_set: list, queries: list) -> list[dict]:
    """저장된 분실물 재매칭 — 추출·라우팅 생략(이미 있음), 검색+grade만.
    지속 재매칭이 open 항목마다 호출(LLM 추출/라우팅 재호출 없이 저렴)."""
    state: AgentState = {"text": text, "extracted": extracted,
                         "region_set": region_set, "queries": queries}
    state.update(n_search(state))
    state.update(n_grade(state))
    return state.get("matches", [])
