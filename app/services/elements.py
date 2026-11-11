"""프롬프트 03: 6요소 판정 + score + rank_items (CLAUDE.md §6 요소 후처리)."""
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from pydantic import BaseModel

from app import llm
from app.models import ELEMENT_KEYS, Element, Fill, LLMState

DOC_SENTENCE_LIMIT = 60  # 문서가 이보다 길면 근거·키워드 문장만 넘긴다


class _LLMElement(BaseModel):
    state: LLMState  # 해당없음은 enum에 없다 → LLM이 내면 스키마 실패
    fill: Optional[Fill] = None
    evidence: Optional[str] = None


class _LLMElements(BaseModel):
    elements: dict[str, _LLMElement]


def _vague() -> dict:
    return Element(state="모호함").model_dump(exclude_none=True)


def postprocess(raw: Optional[dict], previous: Optional[dict] = None) -> dict:
    """6키를 모두 채운다. 빠진 키는 모호함, state≠있음이면 fill 제거. 사람이 닫은 해당없음은 유지."""
    previous = previous or {}
    out: dict = {}
    for key in ELEMENT_KEYS:
        if previous.get(key, {}).get("state") == "해당없음":
            out[key] = previous[key]
            continue
        el = (raw or {}).get(key)
        if not el:
            out[key] = _vague()
            continue
        fill = el.get("fill") if el["state"] == "있음" else None
        out[key] = Element(state=el["state"], fill=fill, evidence=el.get("evidence")).model_dump(exclude_none=True)
    return out


def score(elements: dict) -> int:
    """문서에없음 +1, 모호함 +1, 있음∧추가확인권장 +1. 해당없음 0."""
    n = 0
    for el in elements.values():
        if el["state"] in ("문서에없음", "모호함"):
            n += 1
        elif el["state"] == "있음" and el.get("fill") == "추가확인권장":
            n += 1
    return n


def _count(item: dict, state: str) -> int:
    return sum(1 for el in item.get("elements", {}).values() if el["state"] == state)


def rank_items(items: list[dict]) -> list[dict]:
    """score 내림차순 → 문서에없음 수 → 모호함 수."""
    return sorted(items, key=lambda it: (-it.get("score", 0), -_count(it, "문서에없음"), -_count(it, "모호함")))


def _sentences_for(item: dict, doc_sentences: list[dict], evidence_ids: list[str]) -> list[dict]:
    if len(doc_sentences) <= DOC_SENTENCE_LIMIT:
        return doc_sentences
    keywords = item.get("keywords", [])
    return [s for s in doc_sentences
            if s["id"] in evidence_ids or any(k in s["text"] for k in keywords)]


def judge_one(item: dict, doc_sentences: list[dict], evidence_ids: list[str]) -> tuple[dict, Optional[dict]]:
    payload = {
        "item": item["title"],
        "facts": [{"id": f["id"], "text": f["text"], "date": f.get("date")} for f in item.get("facts", [])],
        "doc_sentences": _sentences_for(item, doc_sentences, evidence_ids),
    }
    obj, error = llm.call_json("03_elements", payload, _LLMElements)
    previous = item.get("elements") or {}
    if obj is None:
        return postprocess(None, previous), error  # 6개 모두 모호함(보류) + error
    return postprocess(obj["elements"], previous), None


def judge_elements(items: list[dict], doc_sentences: list[dict], evidence: dict) -> Optional[dict]:
    """각 item의 elements·score를 채우고(제자리) 정렬은 호출자가 rank_items로 한다."""
    if not items:
        return None
    with ThreadPoolExecutor(max_workers=llm.concurrency()) as pool:
        results = list(pool.map(lambda it: judge_one(it, doc_sentences, evidence.get(it["id"], [])), items))

    failed, first_error = [], None
    for item, (elements, error) in zip(items, results):
        item["elements"] = elements
        item["score"] = score(elements)
        if error:
            failed.append(item["title"])
            first_error = first_error or error
    if first_error:
        return {"code": first_error["code"],
                "message": f"요소 판정 실패 항목(모호함으로 보류): {', '.join(failed)} — {first_error['message']}"[:300]}
    return None


def close_element(item: dict, key: str, closed_by: str) -> None:
    """API 5: 해당 키를 해당없음으로 덮어쓰고 score 재계산."""
    item.setdefault("elements", {})[key] = {"state": "해당없음", "closed_by": closed_by}
    item["score"] = score(item["elements"])
