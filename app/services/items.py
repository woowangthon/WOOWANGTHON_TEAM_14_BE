"""프롬프트 01: 문서 문장 -> 업무 항목(제목·키워드)."""
from typing import Optional

from pydantic import BaseModel, Field

from app import llm
from app.models import Item

MAX_ITEMS = 7
MAX_KEYWORDS = 8


class _LLMItem(BaseModel):
    title: str
    keywords: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class _LLMItems(BaseModel):
    items: list[_LLMItem]


def clean_keywords(keywords: list[str]) -> list[str]:
    """공백 정리, 빈 값·중복 제거, 순서 유지."""
    return list(dict.fromkeys(k.strip() for k in keywords if k and k.strip()))


def extract_items(sentences: list[dict]) -> tuple[list[dict], dict, Optional[dict]]:
    """반환: (items, evidence, error). evidence = {item_id: [doc id]}.

    근거 문장 id가 실제 문서에 하나도 없는 항목은 버린다(문서에 근거 없는 항목 금지).
    """
    obj, error = llm.call_json("01_items", {"sentences": sentences}, _LLMItems)
    if obj is None:
        return [], {}, error

    doc_ids = {s["id"] for s in sentences}
    items: list[dict] = []
    evidence: dict[str, list[str]] = {}
    for raw in obj["items"]:
        title = raw["title"].strip()
        ev = [i for i in dict.fromkeys(raw["evidence"]) if i in doc_ids]
        if not title or not ev:
            continue
        item_id = f"item_{len(items) + 1:02d}"
        item = Item(id=item_id, title=title, keywords=clean_keywords(raw["keywords"])[:MAX_KEYWORDS])
        items.append(item.model_dump(exclude_none=True))
        evidence[item_id] = ev
        if len(items) == MAX_ITEMS:
            break
    return items, evidence, None
