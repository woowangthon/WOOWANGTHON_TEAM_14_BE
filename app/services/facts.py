"""키워드 필터(앞뒤 2개) + 프롬프트 02 + 출처 검증."""
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from pydantic import BaseModel, Field

from app import llm
from app.models import Fact, Source

CONTEXT = 2  # 키워드가 걸린 메시지의 앞뒤 몇 개까지 함께 넘길지


class _LLMFact(BaseModel):
    text: str
    date: Optional[str] = None
    date_estimated: bool = False
    source_ids: list[str] = Field(default_factory=list)


class _LLMFacts(BaseModel):
    facts: list[_LLMFact]


def filter_messages(chat: list[dict], keywords: list[str]) -> list[dict]:
    """키워드가 포함된 메시지 인덱스 ±2 범위만 남긴다. 중복 없이, 원래 순서대로."""
    keywords = [k for k in keywords if k]
    keep: set[int] = set()
    for i, msg in enumerate(chat):
        if any(k in msg["text"] for k in keywords):
            keep.update(range(max(0, i - CONTEXT), min(len(chat), i + CONTEXT + 1)))
    return [chat[i] for i in sorted(keep)]


def postprocess(raw_facts: list[dict], sent_ids: set[str]) -> list[dict]:
    """실제로 넘긴 메시지 id에 없는 출처는 지우고, 출처가 하나도 안 남는 fact는 버린다."""
    kept = []
    for raw in raw_facts:
        ids = [i for i in dict.fromkeys(raw.get("source_ids") or []) if i in sent_ids]
        text = (raw.get("text") or "").strip()
        if not ids or not text:
            continue
        estimated = bool(raw.get("date_estimated"))
        kept.append({
            "text": text,
            "date": raw.get("date") or None,
            "date_estimated": estimated,
            "source_ids": ids,
            "status": "추정" if estimated else "확인됨",
        })
    return kept


def _extract_one(item: dict, chat: list[dict]) -> tuple[list[dict], Optional[dict]]:
    messages = filter_messages(chat, item.get("keywords", []))
    if not messages:
        return [], None  # 관련 메시지가 없으면 LLM을 부르지 않는다
    payload = {
        "item": item["title"],
        "messages": [{k: m[k] for k in ("id", "date", "speaker", "text")} for m in messages],
    }
    obj, error = llm.call_json("02_facts", payload, _LLMFacts)
    if obj is None:
        return [], error
    return postprocess(obj["facts"], {m["id"] for m in messages}), None


def extract_facts(items: list[dict], chat: list[dict]) -> Optional[dict]:
    """각 item의 facts를 채운다(제자리 수정). 일부 항목이 실패해도 나머지는 채우고 error를 돌려준다."""
    if not items:
        return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda it: _extract_one(it, chat), items))

    failed: list[str] = []
    first_error: Optional[dict] = None
    n = 0
    for item, (facts, error) in zip(items, results):
        if error:
            failed.append(item["title"])
            first_error = first_error or error
        item["facts"] = []
        for f in facts:
            n += 1
            fact = Fact(
                id=f"fact_{n:03d}",
                text=f["text"],
                date=f["date"],
                date_estimated=f["date_estimated"],
                source=Source(type="자료", ids=f["source_ids"]),
                status=f["status"],
            )
            item["facts"].append(fact.model_dump(exclude_none=True))

    if first_error:
        return {
            "code": first_error["code"],
            "message": f"사실 추출 실패 항목: {', '.join(failed)} — {first_error['message']}"[:300],
        }
    return None
