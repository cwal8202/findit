"""API 스키마 (pydantic). 정규화된 습득물 결과."""

from __future__ import annotations

from pydantic import BaseModel, Field

MAX_TEXT = 500  # 신고 문장·메모 길이 상한(화면 maxlength와 동일) — 서버에서도 강제(토큰 절약·남용 방지)


class FoundItem(BaseModel):
    atc_id: str
    fd_sn: str = "1"
    name: str = ""
    subject: str = ""        # 게시제목(자동생성)
    description: str = ""     # 특이사항/상세내용(uniq) — 상세 enrich 시 채워짐
    category: str = ""
    color: str = ""
    dep_place: str = ""      # 보관장소(원본)
    region: str = ""         # 해석된 구/시
    found_at: str = ""       # 습득일
    org_name: str = ""       # 보관기관(수령 문의)
    tel: str = ""            # 보관기관 연락처
    source: str = ""
    image_url: str = ""
    score: float             # 임베딩 + 가점 후 점수
    grade: int | None = None    # LLM 판정 0~100 (grade=true일 때만)
    reason: str | None = None   # LLM 근거
    visual_score: int | None = None   # 사진 대조 0~100 (사진 검색 + 후보에 사진 있을 때만)
    visual_reason: str | None = None  # 사진 대조 근거
    name_en: str | None = None        # 물품명 번역 (외국인 모드, grade가 함께 생성)
    desc_en: str | None = None        # 설명 한 줄 번역 (외국인 모드)


class SearchResponse(BaseModel):
    query: str
    count: int
    region_set: list[str] = []
    graded: bool = False
    results: list[FoundItem]


class LostItemRequest(BaseModel):
    text: str = Field(max_length=MAX_TEXT)  # 자연어 분실 신고
    lost_date: str | None = None    # 명시하면 우선(없으면 LLM이 문장에서 환산)
    email: str | None = None        # 매칭 시 알림 받을 이메일(신고자별)
    notify_weak: bool = False       # 임계 미만 '약한 후보'도 참고 알림 받기(옵트인, 이메일 있을 때만 유효)
    lang: str = "ko"                # 판정 근거 언어(외국인: "en" 등). 검색은 언어 무관.


class LostItemImageRequest(BaseModel):
    image_b64: str                  # 사진(base64, 데이터URL 접두사 제외)
    mime: str = "image/jpeg"        # image/jpeg | image/png ...
    note: str = Field("", max_length=MAX_TEXT)  # 선택 메모(장소·날짜 등 사진이 모르는 정보)
    lost_date: str | None = None
    email: str | None = None
    notify_weak: bool = False
    lang: str = "ko"


class AlertSettingsRequest(BaseModel):
    """결과창 '알림 설정' — 이메일·약한 후보 옵트인·알림 기간. 새 이메일이면 현재 결과도 발송."""
    email: str = ""                 # 알림 받을 이메일(비우면 알림 끔)
    notify_weak: bool = False       # 임계 미만 '약한 후보'도 참고 알림(이메일 있을 때만 유효)
    watch_days: int = 30            # 지금부터 며칠간 새 습득물과 재매칭·알림(1~watch_days_max)
    matches: list[dict] = []        # 화면에 보인 후보들(새 이메일이면 그대로 메일에 담음)


class ConfirmRequest(BaseModel):
    match: dict                     # 사용자가 '내 물건이에요' 한 습득물(기록·수령안내용)


class DismissRequest(BaseModel):
    atc_id: str                     # 사용자가 '아니에요' 한 후보


class MatchResponse(BaseModel):
    """에이전트 매칭 결과 — 추출·지역집합·fan-out 쿼리까지 투명하게 노출."""
    id: str = ""                    # 저장된 분실물 id
    status: str = "open"            # open(찾는중) | candidate(유력후보) | confirmed | expired
    watch_until: str | None = None  # 지속 재매칭·알림 마감(ISO)
    query: str
    extracted: dict = {}
    region_set: list[str] = []
    queries: list[str] = []
    count: int = 0
    matches: list[FoundItem] = []
