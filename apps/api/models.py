"""API 스키마 (pydantic). 정규화된 습득물 결과."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MAX_TEXT = 500  # 신고 문장·메모 길이 상한(화면 maxlength와 동일) — 서버에서도 강제(토큰 절약·남용 방지)
MAX_EMAIL = 254
MAX_IMAGE_B64 = 7_000_000  # 사진 base64 상한(≈5MB 원본). 화면은 업로드 전에 1600px로 줄여 보통 수백 KB
ImageMime = Literal["image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"]


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
    email: str | None = Field(None, max_length=MAX_EMAIL)  # 알림 받을 이메일 — 확인 메일의 링크를 눌러야 알림 활성화
    notify_weak: bool = False       # 임계 미만 '약한 후보'도 참고 알림 받기(옵트인, 이메일 있을 때만 유효)
    lang: str = "ko"                # 판정 근거 언어(외국인: "en" 등). 검색은 언어 무관.


class LostItemImageRequest(BaseModel):
    image_b64: str = Field(max_length=MAX_IMAGE_B64)  # 사진(base64, 데이터URL 접두사 제외)
    mime: ImageMime = "image/jpeg"
    note: str = Field("", max_length=MAX_TEXT)  # 선택 메모(장소·날짜 등 사진이 모르는 정보)
    lost_date: str | None = None
    email: str | None = Field(None, max_length=MAX_EMAIL)
    notify_weak: bool = False
    lang: str = "ko"


class AlertSettingsRequest(BaseModel):
    """결과창 '알림 설정' — 이메일·약한 후보 옵트인·알림 기간. 새 이메일이면 확인 메일 발송(확인 후 알림)."""
    email: str = Field("", max_length=MAX_EMAIL)  # 알림 받을 이메일(비우면 알림 끔)
    notify_weak: bool = False       # 임계 미만 '약한 후보'도 참고 알림(확인된 이메일 있을 때만 유효)
    watch_days: int = 30            # 지금부터 며칠간 새 습득물과 재매칭·알림(1~watch_days_max)


class ConfirmRequest(BaseModel):
    atc_id: str = Field(max_length=64)  # '내 물건이에요' 한 후보 — 서버가 저장한 후보에서 찾음(클라이언트 데이터 불신)


class DismissRequest(BaseModel):
    atc_id: str = Field(max_length=64)  # 사용자가 '아니에요' 한 후보


class VerifyRequest(BaseModel):
    token: str = Field(max_length=128)  # 확인 메일 링크의 토큰


class MineRef(BaseModel):
    id: str = Field(max_length=64)
    token: str = Field(max_length=128)


class MineRequest(BaseModel):
    """'내 신고' — 이 브라우저가 등록 때 받은 (id, 관리 토큰) 목록. 남의 신고는 볼 수 없음."""
    items: list[MineRef] = Field(default_factory=list, max_length=50)


class MatchResponse(BaseModel):
    """에이전트 매칭 결과 — 추출·지역집합·fan-out 쿼리까지 투명하게 노출."""
    id: str = ""                    # 저장된 분실물 id
    token: str = ""                 # 신고 관리 토큰 — 확인·제외·알림 설정에 필요(등록 응답으로만 1회 전달)
    email_pending: bool = False     # 확인 메일을 보냄(링크를 눌러야 알림 활성화)
    status: str = "open"            # open(찾는중) | candidate(유력후보) | confirmed | expired
    watch_until: str | None = None  # 지속 재매칭·알림 마감(ISO)
    query: str
    extracted: dict = {}
    region_set: list[str] = []
    queries: list[str] = []
    count: int = 0
    matches: list[FoundItem] = []
