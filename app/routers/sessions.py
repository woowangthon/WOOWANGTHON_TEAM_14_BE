"""엔드포인트 (CLAUDE.md §4). 현재 1~4 구현.

에러는 항상 본문의 {"error": {"code", "message"}}로 돌려주고 HTTP 200을 유지한다(세션 없음만 404).
"""
import functools
from typing import Optional

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse

from app import db
from app.models import (ELEMENT_KEYS, AnnotationPatch, AnswersBody, AskBody, ElementClose, ItemsPatch,
                        SessionCreate, new_session_data)
from app.parsers.docs import parse_doc
from app.parsers.kakao import parse_kakao
from app.services import annotations as annotations_service
from app.services import elements as elements_service
from app.services import facts as facts_service
from app.services import items as items_service
from app.services import publish as publish_service
from app.services import qa as qa_service
from app.services import questions as questions_service

router = APIRouter(prefix="/sessions")


def _err(code: str, message: str) -> dict:
    return {"code": code, "message": message[:300]}


def guarded(fn):
    """예상 못 한 예외도 500 대신 200 + {"error": {"code": "INTERNAL"}}로 돌려준다."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            return {"error": _err("INTERNAL", f"{type(e).__name__}: {e}")}
    return wrapper


def _not_found(session_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": _err("session_not_found", f"세션을 찾을 수 없습니다: {session_id}")},
    )


def _with_error(body: dict, error: Optional[dict]) -> dict:
    if error:
        body["error"] = error
    return body


# 1
@router.post("")
@guarded
def create_session(body: SessionCreate):
    session_id = db.create_session(new_session_data(body.model_dump()))
    return {"session_id": session_id}


# 2
@router.post("/{session_id}/docs")
@guarded
def upload_docs(session_id: str, file: Optional[UploadFile] = File(None)):
    data = db.get_session(session_id)
    if data is None:
        return _not_found(session_id)
    if file is None:
        return {"items": data["items"], "error": _err("no_file", "file 필드에 PDF 또는 TXT를 올려주세요.")}

    try:
        sentences = parse_doc(file.filename or "", file.file.read())
    except ValueError as e:
        return {"items": data["items"], "error": _err("doc_parse_failed", str(e))}
    except Exception as e:  # 깨진 PDF 등
        return {"items": data["items"], "error": _err("doc_parse_failed", f"문서를 읽지 못했습니다: {type(e).__name__}")}

    try:
        items, evidence, error = items_service.extract_items(sentences)
    except Exception as e:
        items, evidence, error = [], {}, _err("items_failed", f"{type(e).__name__}: {e}")

    # 문서를 새로 올리면 항목부터 다시 만든다.
    data["doc_sentences"] = sentences
    data["items"] = items
    data["meta"]["item_evidence"] = evidence
    db.save_session(session_id, data)
    return _with_error({"items": items}, error)


# 3
@router.patch("/{session_id}/items")
@guarded
def patch_items(session_id: str, body: ItemsPatch):
    data = db.get_session(session_id)
    if data is None:
        return _not_found(session_id)

    by_id = {it["id"]: it for it in data["items"]}
    evidence = data["meta"].setdefault("item_evidence", {})
    problems: list[str] = []
    removed: set[str] = set()

    # 수정 → 병합 → 삭제 순서로 적용한다.
    for p in body.items:
        item = by_id.get(p.id)
        if item is None:
            problems.append(f"{p.id}: 없는 항목")
            continue
        if p.title is not None and p.title.strip():
            item["title"] = p.title.strip()
        if p.keywords is not None:
            item["keywords"] = items_service.clean_keywords(p.keywords)

    for p in body.items:
        if not p.merge_into or p.id not in by_id:
            continue
        target = by_id.get(p.merge_into)
        if target is None or p.merge_into == p.id or p.merge_into in removed or p.id in removed:
            problems.append(f"{p.id}: {p.merge_into}(으)로 병합할 수 없음")
            continue
        source = by_id[p.id]
        target["keywords"] = items_service.clean_keywords(target["keywords"] + source["keywords"])
        seen = {f["id"] for f in target["facts"]}
        target["facts"] += [f for f in source["facts"] if f["id"] not in seen]
        evidence[target["id"]] = list(dict.fromkeys(evidence.get(target["id"], []) + evidence.get(p.id, [])))
        removed.add(p.id)

    for p in body.items:
        if p.delete and p.id in by_id:
            removed.add(p.id)

    data["items"] = [it for it in data["items"] if it["id"] not in removed]
    for item_id in removed:
        evidence.pop(item_id, None)
    db.save_session(session_id, data)

    error = _err("item_patch_partial", "; ".join(problems)) if problems else None
    return _with_error({"items": data["items"]}, error)


# 4
@router.post("/{session_id}/chat")
@guarded
def upload_chat(session_id: str, file: Optional[UploadFile] = File(None)):
    data = db.get_session(session_id)
    if data is None:
        return _not_found(session_id)
    items = data["items"]
    if file is None:
        return {"items": items, "chat_count": len(data["chat"]),
                "error": _err("no_file", "file 필드에 카톡 txt를 올려주세요.")}

    try:
        chat = parse_kakao(file.file.read())
    except Exception as e:
        return {"items": items, "chat_count": 0,
                "error": _err("chat_parse_failed", f"대화 파일을 읽지 못했습니다: {type(e).__name__}")}

    data["chat"] = chat
    if not chat:
        error = _err("chat_empty", "대화에서 메시지를 찾지 못했습니다. 카카오톡 '대화 내보내기' txt인지 확인해주세요.")
    elif not items:
        error = _err("no_items", "업무 항목이 없습니다. 먼저 문서를 올려 항목을 만들어주세요.")
    else:
        error = facts_service.extract_facts(items, chat)
        error = elements_service.judge_elements(items, data["doc_sentences"], data["meta"].get("item_evidence", {})) or error
        items = data["items"] = elements_service.rank_items(items)

    db.save_session(session_id, data)
    return _with_error({"items": items, "chat_count": len(chat)}, error)


# 5
@router.patch("/{session_id}/items/{item_id}/elements/{key}")
@guarded
def close_element(session_id: str, item_id: str, key: str, body: ElementClose):
    data = db.get_session(session_id)
    if data is None:
        return _not_found(session_id)
    item = next((it for it in data["items"] if it["id"] == item_id), None)
    if item is None:
        return {"item": None, "error": _err("item_not_found", f"없는 항목: {item_id}")}
    if key not in ELEMENT_KEYS:
        return {"item": item, "error": _err("invalid_element_key", f"요소 키는 {', '.join(ELEMENT_KEYS)} 중 하나여야 합니다: {key}")}

    elements_service.close_element(item, key, body.closed_by)
    data["items"] = elements_service.rank_items(data["items"])
    db.save_session(session_id, data)
    return {"item": item}


# 6
@router.get("/{session_id}/questions")
@guarded
def get_questions(session_id: str):
    data = db.get_session(session_id)
    if data is None:
        return _not_found(session_id)
    if data["questions"]:  # 최초 호출 때 만든 것을 그대로 돌려준다
        return questions_service.split(data["questions"])
    if not data["items"]:
        return {"cards": [], "more": [], "error": _err("no_items", "업무 항목이 없습니다. 문서와 대화를 먼저 올려주세요.")}

    questions, error = questions_service.generate_questions(data["items"], data["doc_sentences"])
    data["questions"] = questions
    db.save_session(session_id, data)
    return _with_error(questions_service.split(questions), error)


# 7
@router.post("/{session_id}/answers")
@guarded
def post_answers(session_id: str, body: AnswersBody):
    data = db.get_session(session_id)
    if data is None:
        return _not_found(session_id)
    created, skipped, error = annotations_service.create_annotations(data, [a.model_dump() for a in body.answers])
    db.save_session(session_id, data)
    return _with_error({"annotations": created, "skipped": skipped}, error)


# 8
@router.patch("/{session_id}/annotations/{ann_id}")
@guarded
def patch_annotation(session_id: str, ann_id: str, body: AnnotationPatch):
    data = db.get_session(session_id)
    if data is None:
        return _not_found(session_id)
    ann = next((a for a in data["annotations"] if a["id"] == ann_id), None)
    if ann is None:
        return {"annotation": None, "error": _err("annotation_not_found", f"없는 주석: {ann_id}")}
    annotations_service.patch_annotation(ann, body.status, body.text, body.label)
    db.save_session(session_id, data)
    return {"annotation": ann}


# 9
@router.post("/{session_id}/publish")
@guarded
def publish(session_id: str):
    data = db.get_session(session_id)
    if data is None:
        return _not_found(session_id)
    document = publish_service.publish(data)
    db.save_session(session_id, data)
    return {"document": document}


# 10
@router.post("/{session_id}/ask")
@guarded
def ask(session_id: str, body: AskBody):
    data = db.get_session(session_id)
    if data is None:
        return _not_found(session_id)
    if not data.get("published"):
        return {"hits": [], "summary": None, "none": True,
                "error": _err("NOT_PUBLISHED", "발행된 기록이 없습니다. 전임이 주석을 승인하고 발행해야 검색할 수 있습니다.")}
    hits = qa_service.search(data["published"], body.question)
    return {"hits": hits, "summary": None, "none": not hits}


# 11
@router.post("/{session_id}/ask/forward")
@guarded
def ask_forward(session_id: str, body: AskBody):
    data = db.get_session(session_id)
    if data is None:
        return _not_found(session_id)
    if not body.question.strip():
        return {"forwarded": False, "error": _err("empty_question", "질문이 비어 있습니다.")}
    qa_service.forward(data, body.question)
    db.save_session(session_id, data)
    return {"forwarded": True}


# 계약 외 1개: 전임 질문함 화면용 (B에게 알릴 것)
@router.get("/{session_id}/forwarded")
@guarded
def list_forwarded(session_id: str):
    data = db.get_session(session_id)
    if data is None:
        return _not_found(session_id)
    return {"questions": data["forwarded"]}
