"""Gemini 호출 — 질의 임베딩 + LLM grading. urllib(stdlib)만 사용."""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request

from .config import settings

_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# 프롬프트는 스캐폴드 단계라 여기 둠 — 앱 확장 시 apps/agent/prompts/ 로 이동 예정.
_GRADE_PROMPT = """당신은 분실물↔습득물 매칭 심판입니다.
사용자가 잃어버린 물건 설명과, 검색이 좁힌 습득물 후보 목록이 주어집니다.
각 후보가 '사용자가 잃어버린 바로 그 물건'일 가능성을 0~100으로 채점하세요.

원칙:
- 브랜드·모델·물품종류·색상 일치를 중시. 한글↔영문 브랜드(닥스=DAKS)는 같게 봄.
- 후보에 정보가 적으면 과신하지 말 것.
- 지역·날짜는 참고만. 근거는 한 줄로 짧게.

반드시 아래 JSON 배열로만 답하세요(설명 문장 금지):
[{{"id": <후보번호>, "score": <0~100 정수>, "reason": "<한 줄>"}}, ...]

잃어버린 물건: "{query}"

후보:
{candidates}
"""


def _post(url: str, body: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        url, json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def embed_query(text: str) -> list[float]:
    """질의 → 정규화된 768-dim 벡터 (batchEmbedContents 1건)."""
    url = f"{_BASE}/{settings.gemini_embed_model}:batchEmbedContents?key={settings.gemini_api_key}"
    body = {
        "requests": [{
            "model": f"models/{settings.gemini_embed_model}",
            "content": {"parts": [{"text": text}]},
            "outputDimensionality": settings.gemini_embed_dim,
        }]
    }
    v = _post(url, body)["embeddings"][0]["values"]
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def generate_json(prompt: str, timeout: int = 60):
    """gemini-3.6-flash로 JSON 응답 생성(추출·라우팅 등 재사용). 실패 시 None."""
    url = f"{_BASE}/{settings.gemini_grade_model}:generateContent?key={settings.gemini_api_key}"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }
    try:
        text = _post(url, body, timeout)["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except (KeyError, IndexError, json.JSONDecodeError, urllib.error.URLError):
        return None


def embed_texts(texts: list[str], chunk: int = 50) -> list[list[float]]:
    """여러 문서를 배치 임베딩(정규화). 수집기 색인용. batchEmbedContents 청크."""
    out: list[list[float]] = []
    for i in range(0, len(texts), chunk):
        part = texts[i : i + chunk]
        url = f"{_BASE}/{settings.gemini_embed_model}:batchEmbedContents?key={settings.gemini_api_key}"
        body = {"requests": [{
            "model": f"models/{settings.gemini_embed_model}",
            "content": {"parts": [{"text": t}]},
            "outputDimensionality": settings.gemini_embed_dim,
        } for t in part]}
        for e in _post(url, body, timeout=120)["embeddings"]:
            v = e["values"]
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
    return out


def grade(query: str, candidates: list[dict]) -> list[dict]:
    """top-K 후보를 채점. 반환: [{"id":n, "score":0~100, "reason":".."}]. 실패 시 []."""
    lines = "\n".join(
        f"{n}. 물품명:{c.get('name','')} / 제목:{c.get('subject','')} / "
        f"분류:{c.get('category','')} / 색상:{c.get('color','')} / "
        f"지역:{c.get('region') or '미상'} / 습득일:{c.get('found_at','')}"
        for n, c in enumerate(candidates)
    )
    url = f"{_BASE}/{settings.gemini_grade_model}:generateContent?key={settings.gemini_api_key}"
    body = {
        "contents": [{"parts": [{"text": _GRADE_PROMPT.format(query=query, candidates=lines)}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }
    try:
        text = _post(url, body)["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except (KeyError, IndexError, json.JSONDecodeError, urllib.error.URLError):
        return []
