"""API 계약 타입 (CLAUDE.md §3). 프론트와 합의된 필드명이므로 바꾸지 않는다."""
from typing import Literal, Optional, get_args

from pydantic import BaseModel, Field

ElementKey = Literal["시기", "연락경로", "판단기준", "자주틀리는점", "미리할일", "배경"]
ELEMENT_KEYS: tuple[str, ...] = get_args(ElementKey)

State = Literal["있음", "문서에없음", "모호함", "해당없음"]  # DB 저장용
LLMState = Literal["있음", "문서에없음", "모호함"]  # LLM 출력용: 해당없음은 사람만 정한다
Fill = Literal["확인됨", "추가확인권장"]
Label = Literal["전임경험", "규정근거"]


class Source(BaseModel):
    type: Literal["자료", "전임답변"]
    ids: list[str]  # ["chat#3"] / ["q_01"]


class Fact(BaseModel):
    id: str
    text: str
    date: Optional[str] = None
    date_estimated: bool = False
    source: Source
    status: Literal["확인됨", "추정", "확인대기"] = "확인됨"
    label: Optional[Label] = None


class Element(BaseModel):
    state: State
    fill: Optional[Fill] = None  # state가 있음일 때만
    evidence: Optional[str] = None  # fact id | doc id | 문장
    closed_by: Optional[Literal["전임", "후임"]] = None


class Item(BaseModel):
    id: str  # "item_01"
    title: str
    keywords: list[str]
    facts: list[Fact] = Field(default_factory=list)
    elements: dict[ElementKey, Element] = Field(default_factory=dict)
    score: int = 0


class Question(BaseModel):
    id: str  # "q_01"
    item_id: str
    element: ElementKey
    kind: Literal["빈칸", "보완", "확인"]
    step1: str
    step2: str
    step3: str
    rank: int = 0  # 1~5 카드, 6+ 추가 질문, 0 미정


class Answer(BaseModel):
    question_id: str
    text: str
    audio_url: Optional[str] = None


class Slots(BaseModel):
    절차: Optional[str] = None
    예외: Optional[str] = None
    사례: Optional[str] = None
    연락: Optional[str] = None
    조건: Optional[str] = None


class Annotation(BaseModel):
    id: str  # "an_001"
    item_id: str
    element: ElementKey
    text: str
    slots: Slots = Field(default_factory=Slots)
    source: Source  # type="전임답변"
    label: Label = "전임경험"
    status: Literal["draft", "approved", "edited", "rejected"] = "draft"


# ---- 요청 본문 ----

class SessionCreate(BaseModel):
    club_name: str
    giver: str
    receiver: str


class ItemPatch(BaseModel):
    id: str
    title: Optional[str] = None
    keywords: Optional[list[str]] = None
    merge_into: Optional[str] = None
    delete: Optional[bool] = None


class ItemsPatch(BaseModel):
    items: list[ItemPatch]


def new_session_data(meta: dict) -> dict:
    """세션 1개 = JSON 1행 (§3)."""
    return {
        "meta": meta,
        "doc_sentences": [],
        "items": [],
        "chat": [],
        "questions": [],
        "answers": [],
        "annotations": [],
        "published": None,
        "forwarded": [],
    }
