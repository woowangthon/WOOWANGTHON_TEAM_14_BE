"""발행: 승인(approved)·수정(edited)된 주석만 담은 인수인계 문서. facts(자료)와 annotations(전임답변)는 섞지 않는다."""
from datetime import datetime, timezone

NOTICE = "이 문서는 전임의 경험을 정리한 것이며 회칙·규정이 우선합니다."
NO_APPROVED = "승인된 기록이 없습니다."
PUBLISHABLE = {"approved", "edited"}


def build_document(data: dict) -> dict:
    by_item: dict[str, list[dict]] = {}
    for a in data["annotations"]:
        if a["status"] in PUBLISHABLE:
            by_item.setdefault(a["item_id"], []).append(a)

    items = [{
        "id": it["id"],
        "title": it["title"],
        "elements": it.get("elements", {}),
        "facts": it.get("facts", []),
        "annotations": by_item.get(it["id"], []),
    } for it in data["items"]]

    total = sum(len(it["annotations"]) for it in items)
    notice = NOTICE if total else NOTICE + "\n" + NO_APPROVED
    return {"items": items, "notice": notice}


def publish(data: dict) -> dict:
    """문서를 만들어 세션의 published에 저장(Q&A는 이것만 검색한다)하고 돌려준다."""
    document = build_document(data)
    data["published"] = {**document, "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    return document
