"""프롬프트 05: 전임 답변 -> 주석(draft). 후처리는 CLAUDE.md §6."""
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from pydantic import BaseModel, Field

from app import llm
from app.models import Annotation, ElementKey, Slots, Source

MIN_ANSWER_LEN = 10  # 이보다 짧은 답변은 LLM을 부르지 않고 건너뛴다


class _LLMAnnotation(BaseModel):
    element: ElementKey
    text: str
    slots: Slots = Field(default_factory=Slots)


class _LLMAnnotations(BaseModel):
    annotations: list[_LLMAnnotation]


def _next_id(data: dict) -> str:
    """an_001부터 전역 증가. 삭제·거절돼도 번호를 다시 쓰지 않는다."""
    data["meta"]["annotation_seq"] = data["meta"].get("annotation_seq", 0) + 1
    return f"an_{data['meta']['annotation_seq']:03d}"


def _call(question: dict, item_title: str, answer_text: str) -> tuple[list[dict], Optional[dict]]:
    payload = {
        "item": item_title,
        "element": question["element"],
        "question": "\n".join((question["step1"], question["step2"], question["step3"])),
        "answer": answer_text,
    }
    obj, error = llm.call_json("05_annotations", payload, _LLMAnnotations)
    if obj is None:
        return [], error
    return [a for a in obj["annotations"] if a["text"].strip()], None


def create_annotations(data: dict, answers: list[dict]) -> tuple[list[dict], list[str], Optional[dict]]:
    """답변마다 프롬프트 05를 호출해 draft 주석을 만들고 세션에 추가한다(제자리).

    반환: (새 주석들, skipped question_id들, error). 10자 미만 답변은 error 없이 skip.
    """
    questions = {q["id"]: q for q in data["questions"]}
    titles = {it["id"]: it["title"] for it in data["items"]}

    jobs, skipped, problems = [], [], []
    for ans in answers:
        q = questions.get(ans["question_id"])
        text = (ans.get("text") or "").strip()
        if q is None:
            problems.append(f"{ans['question_id']}: 없는 질문")
            continue
        if len(text) < MIN_ANSWER_LEN:
            skipped.append(ans["question_id"])
            continue
        data["answers"].append({"question_id": ans["question_id"], "text": text, "audio_url": ans.get("audio_url")})
        jobs.append((q, titles.get(q["item_id"], ""), text))

    with ThreadPoolExecutor(max_workers=llm.concurrency()) as pool:
        results = list(pool.map(lambda j: _call(*j), jobs))

    created, first_error = [], None
    for (q, _title, _text), (raws, error) in zip(jobs, results):
        if error:
            problems.append(f"{q['id']}: {error['message']}")
            first_error = first_error or error
        for raw in raws:
            ann = Annotation(
                id=_next_id(data),
                item_id=q["item_id"],
                element=raw["element"],
                text=raw["text"].strip(),
                slots=Slots(**raw["slots"]),  # 사례가 null이어도 저장
                source=Source(type="전임답변", ids=[q["id"]]),
            ).model_dump()  # slots의 null은 그대로 둔다
            data["annotations"].append(ann)
            created.append(ann)

    error = None
    if problems:
        code = first_error["code"] if first_error else "answers_partial"
        error = {"code": code, "message": "; ".join(problems)[:300]}
    return created, skipped, error


def patch_annotation(ann: dict, status: Optional[str], text: Optional[str], label: Optional[str]) -> dict:
    """API 8. text가 바뀌면 status=edited."""
    if status is not None:
        ann["status"] = status
    if label is not None:
        ann["label"] = label
    if text is not None and text.strip() and text.strip() != ann["text"]:
        ann["text"] = text.strip()
        ann["status"] = "edited"
    return ann
