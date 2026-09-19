"""Gemini 호출 — 질의 임베딩 + LLM grading. urllib(stdlib)만 사용."""

from __future__ import annotations

import json
import math
import time
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


def _post(url: str, body: dict, timeout: int = 60, max_retry: int = 6) -> dict:
    """429/5xx는 지수 백오프로 재시도(대량 임베딩·쿼터 대응)."""
    payload = json.dumps(body).encode("utf-8")
    for attempt in range(max_retry):
        try:
            req = urllib.request.Request(url, payload, {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and attempt < max_retry - 1:
                time.sleep(min(30, 5 * (attempt + 1)))  # 5,10,...,30s
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt < max_retry - 1:
                time.sleep(5)
                continue
            raise
    raise RuntimeError("Gemini _post: 재시도 소진")


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


def generate_json_image(prompt: str, image_b64: str, mime: str = "image/jpeg", timeout: int = 60):
    """사진 + 프롬프트 → JSON (Vision 추출용). gemini-3.6-flash 멀티모달. 실패 시 None."""
    url = f"{_BASE}/{settings.gemini_grade_model}:generateContent?key={settings.gemini_api_key}"
    body = {
        "contents": [{"parts": [
            {"inline_data": {"mime_type": mime, "data": image_b64}},
            {"text": prompt},
        ]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }
    try:
        text = _post(url, body, timeout)["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except (KeyError, IndexError, json.JSONDecodeError, urllib.error.URLError):
        return None


_COMPARE_PROMPT = """두 이미지가 '같은(또는 매우 비슷한) 물건'인지 판단하세요.
첫 번째는 사용자가 잃어버린 물건 사진, 두 번째는 습득물 보관 사진입니다.
색상·형태·재질·브랜드·종류를 종합해, 같은 물건일 가능성을 0~100으로 채점하세요.
습득물 사진은 조명·각도·배경이 다를 수 있으니 그 점은 감안하세요.
반드시 아래 JSON만: {"score": <0~100 정수>, "reason": "<한 줄 근거>"}"""


def compare_images(user_b64: str, cand_b64: str, user_mime: str = "image/jpeg",
                   cand_mime: str = "image/jpeg", lang: str = "ko") -> dict | None:
    """사용자 사진 ↔ 습득물 사진 직접 대조(멀티모달). {"score","reason"} 또는 None(실패)."""
    prompt = _COMPARE_PROMPT
    if lang != "ko":
        prompt += f"\n중요: 'reason'은 {_LANG_NAME.get(lang, 'English')}로 작성하세요."
    url = f"{_BASE}/{settings.gemini_grade_model}:generateContent?key={settings.gemini_api_key}"
    body = {
        "contents": [{"parts": [
            {"text": prompt},
            {"inline_data": {"mime_type": user_mime, "data": user_b64}},
            {"inline_data": {"mime_type": cand_mime, "data": cand_b64}},
        ]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }
    try:
        text = _post(url, body, timeout=60)["candidates"][0]["content"]["parts"][0]["text"]
        r = json.loads(text)
        return {"score": int(r.get("score")), "reason": (r.get("reason") or "").strip()}
    except (KeyError, IndexError, ValueError, TypeError, json.JSONDecodeError, urllib.error.URLError):
        return None


def embed_texts(texts: list[str], chunk: int = 50) -> list[list[float]]:
    """여러 문서를 배치 임베딩(정규화). 수집기 색인용. batchEmbedContents 청크."""
    out: list[list[float]] = []
    url = f"{_BASE}/{settings.gemini_embed_model}:batchEmbedContents?key={settings.gemini_api_key}"
    n_chunks = (len(texts) + chunk - 1) // chunk
    for idx, i in enumerate(range(0, len(texts), chunk)):
        part = texts[i : i + chunk]
        body = {"requests": [{
            "model": f"models/{settings.gemini_embed_model}",
            "content": {"parts": [{"text": t}]},
            "outputDimensionality": settings.gemini_embed_dim,
        } for t in part]}
        for e in _post(url, body, timeout=120)["embeddings"]:
            v = e["values"]
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        if idx < n_chunks - 1:
            time.sleep(0.6)  # 청크 간 페이싱(RPM 한도 여유)
    return out


_LANG_NAME = {"en": "English", "ja": "Japanese", "zh": "Chinese", "ko": "Korean"}


def grade(query: str, candidates: list[dict], lang: str = "ko") -> list[dict]:
    """top-K 후보를 채점. 반환: [{"id":n, "score":0~100, "reason":".."}]. 실패 시 [].

    lang!="ko"이면 'reason'을 해당 언어로 생성(외국인 사용자용). 점수 로직은 언어 무관.
    """
    lines = "\n".join(
        f"{n}. 물품명:{c.get('name','')} / 제목:{c.get('subject','')} / "
        f"분류:{c.get('category','')} / 색상:{c.get('color','')} / "
        f"지역:{c.get('region') or '미상'} / 습득일:{c.get('found_at','')}"
        for n, c in enumerate(candidates)
    )
    prompt = _GRADE_PROMPT.format(query=query, candidates=lines)
    if lang != "ko":  # 근거 + 물품명/설명을 사용자 언어로(원본은 한국어라 얹어서 번역)
        lname = _LANG_NAME.get(lang, "English")
        prompt += (
            f'\n\n중요: 각 항목 JSON에 아래를 모두 포함하고 값은 {lname}로 작성하세요 — '
            f'"reason"(한 줄 근거), "name_en"(물품의 간결한 {lname} 이름, 예: "Black leather wallet"), '
            f'"desc_en"(한 줄 {lname} 설명; 특이사항 있으면 반영). '
            f'즉 각 항목: {{"id":n, "score":0~100, "reason":"..", "name_en":"..", "desc_en":".."}}.'
        )
    url = f"{_BASE}/{settings.gemini_grade_model}:generateContent?key={settings.gemini_api_key}"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }
    try:
        text = _post(url, body)["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except (KeyError, IndexError, json.JSONDecodeError, urllib.error.URLError):
        return []
