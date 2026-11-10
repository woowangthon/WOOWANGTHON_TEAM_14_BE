"""fixture로 API 1→4 스모크. CLAUDE.md §7의 1·2·8을 검사한다(5~10은 해당 단계 구현 후 추가).

- 가짜 LLM: 키 없이도 후처리(출처 검증 등)까지 검사
- 키 없음: error 경로에서 응답 구조 유지
- 실제 LLM: .env에 LLM_API_KEY가 있을 때만 실행
"""
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import llm
from app.main import app  # import 시 .env를 읽는다
from app.parsers.kakao import parse_kakao
from app.services.facts import filter_messages

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
HAS_KEY = bool((os.getenv("LLM_API_KEY") or "").strip())
CHAT_IDS = {m["id"] for m in parse_kakao((FIXTURES / "kakao.txt").read_bytes())}

FAKE_ITEMS = {"items": [
    {"title": "회비 정산", "keywords": ["회비", "미납", "정산", "입금"], "evidence": ["doc#1", "doc#2"]},
    {"title": "코트 대관", "keywords": ["코트", "대관", "체육시설과", "코트"], "evidence": ["doc#3"]},
    {"title": "대회 참가", "keywords": ["대회", "참가", "신청서"], "evidence": ["doc#4", "doc#8"]},
    {"title": "신입 모집", "keywords": ["모집", "신입", "지원"], "evidence": ["doc#5", "doc#7"]},
    {"title": "근거 없는 항목", "keywords": ["가", "나", "다"], "evidence": ["doc#999"]},  # 버려져야 함
]}


def fake_call_json(prompt_name, payload, schema, retries=1):
    if prompt_name == "01_items":
        obj = FAKE_ITEMS
    elif prompt_name == "02_facts":
        msgs = payload["messages"]
        facts = [{"text": f"사실: {m['text'][:20]}", "date": m["date"], "source_ids": [m["id"]]} for m in msgs[:4]]
        facts.append({"text": "지어낸 사실", "source_ids": ["chat#9999"]})  # 버려져야 함
        facts.append({"text": "짐작한 날짜", "date": "2026-05-19", "date_estimated": True,
                      "source_ids": [msgs[0]["id"], "chat#9999"]})  # 가짜 id만 지워져야 함
        obj = {"facts": facts}
    elif prompt_name == "03_elements":
        # 배경 누락, 문서에없음에 fill이 붙어 옴(후처리에서 지워져야 함). 항목별로 점수가 다르게 나오도록 한다.
        title = payload["item"]
        if title.startswith("회비"):
            obj = {"elements": {
                "시기": {"state": "있음", "fill": "추가확인권장", "evidence": "doc#1"},
                "연락경로": {"state": "문서에없음", "fill": "확인됨"},
                "판단기준": {"state": "모호함", "evidence": "doc#2"},
                "자주틀리는점": {"state": "문서에없음"},
                "미리할일": {"state": "있음", "fill": "확인됨", "evidence": "fact_001"},
            }}  # score 5 (배경 누락 → 모호함 포함)
        else:
            obj = {"elements": {k: {"state": "있음", "fill": "확인됨", "evidence": "doc#3"} for k in
                                ("시기", "연락경로", "판단기준", "자주틀리는점", "미리할일")}}  # 배경 모호함 → score 1
    elif prompt_name == "04_questions":
        obj = {"step1": f"{payload['item']}의 {payload['element']}은 실제로 어떻게 하시나요?",
               "step2": "평소와 다르게 처리한 경우가 있었나요?", "step3": "최근 사례를 하나 들어주실 수 있나요?"}
    elif prompt_name == "05_annotations":
        ans = payload["answer"]
        one = {"element": payload["element"], "text": f"{payload['item']}: {ans[:30]}",
               "slots": {"절차": ans, "예외": None, "사례": None, "연락": None, "조건": None}}
        obj = {"annotations": [one]}
        if "연락처" in ans:  # 서로 다른 요소가 섞인 답변 → 2개로 나눔
            obj["annotations"].append({"element": "연락경로", "text": "체육시설과 김 주무관에게 메일 후 전화",
                                       "slots": {"절차": None, "예외": None, "사례": None, "연락": "체육시설과 메일+전화", "조건": None}})
    else:
        raise AssertionError(prompt_name)
    return schema.model_validate(obj).model_dump(), None


def schema_dump(schema, obj):
    return schema.model_validate(obj).model_dump()


def llm_no_key(prompt_name, payload, schema, retries=1):
    return None, {"code": "llm_no_key", "message": "no key"}


FORBIDDEN = [re.compile(p) for p in (
    r"실수|문제|반려|잘못|힘든", r"와\s.+\s중", r"인가요\s*아니면", r"했을\s*텐데|하셨잖아요", r"편법|그냥|빨리\s*처리")]


def check_cards(qs):  # §7-4
    cards = qs["cards"]
    assert len(cards) <= 5
    per_item = {}
    for q in cards:
        per_item[q["item_id"]] = per_item.get(q["item_id"], 0) + 1
    assert all(n <= 2 for n in per_item.values()), per_item
    for q in cards + qs["more"]:
        joined = " ".join((q["step1"], q["step2"], q["step3"]))
        assert not any(p.search(joined) for p in FORBIDDEN), joined


ELEMENT_KEYS = ("시기", "연락경로", "판단기준", "자주틀리는점", "미리할일", "배경")


def check_elements(items):  # §7-3
    for it in items:
        assert set(it["elements"]) == set(ELEMENT_KEYS), it["id"]
        assert all(el["state"] != "해당없음" for el in it["elements"].values())
        for el in it["elements"].values():
            if el["state"] != "있음":
                assert "fill" not in el


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    return TestClient(app)  # 처리되지 않은 서버 예외는 테스트 실패로 드러난다


def new_session(client) -> str:
    r = client.post("/sessions", json={"club_name": "테니스 동아리", "giver": "민서", "receiver": "수아"})
    assert r.status_code == 200
    return r.json()["session_id"]


def upload(client, sid: str, kind: str, name: str):
    with open(FIXTURES / name, "rb") as f:
        r = client.post(f"/sessions/{sid}/{kind}", files={"file": (name, f, "text/plain")})
    assert r.status_code == 200, r.text  # §7-8: 500 없음
    return r.json()


def check_items(items):  # §7-1
    assert 4 <= len(items) <= 7
    for it in items:
        assert len(it["keywords"]) >= 3, it


def check_facts(items):  # §7-2
    facts = [f for it in items for f in it["facts"]]
    assert len(facts) >= 10
    for f in facts:
        assert f["source"]["type"] == "자료"
        assert len(f["source"]["ids"]) >= 1
        assert set(f["source"]["ids"]) <= CHAT_IDS, f
    return facts


def test_full_flow_fake_llm(client, monkeypatch):
    """§7의 1~8 전부를 한 흐름(1→2→3→4→5→6→7→8→9→10→11)으로. LLM은 가짜 응답."""
    monkeypatch.setattr(llm, "call_json", fake_call_json)
    sid = new_session(client)

    body = upload(client, sid, "docs", "rules.txt")
    assert "error" not in body
    check_items(body["items"])
    assert [it["title"] for it in body["items"]] == ["회비 정산", "코트 대관", "대회 참가", "신입 모집"]
    assert body["items"][1]["keywords"] == ["코트", "대관", "체육시설과"]  # 중복 제거
    assert all(it["facts"] == [] for it in body["items"])

    r = client.patch(f"/sessions/{sid}/items", json={"items": [{"id": "item_04", "title": "신입 회원 모집"}]})
    assert r.status_code == 200 and r.json()["items"][3]["title"] == "신입 회원 모집"

    body = upload(client, sid, "chat", "kakao.txt")
    assert "error" not in body
    assert body["chat_count"] == len(CHAT_IDS) == 55
    facts = check_facts(body["items"])
    assert all(f["text"] != "지어낸 사실" for f in facts)
    assert len({f["id"] for f in facts}) == len(facts)
    estimated = [f for f in facts if f["date_estimated"]]
    assert estimated and all(f["status"] == "추정" and len(f["source"]["ids"]) == 1 for f in estimated)
    assert all(f["status"] == "확인됨" for f in facts if not f["date_estimated"])

    # §7-3 요소 판정 + 정렬
    check_elements(body["items"])
    assert [it["id"] for it in body["items"]] == ["item_01", "item_02", "item_03", "item_04"]
    assert body["items"][0]["score"] == 5 and body["items"][0]["elements"]["배경"] == {"state": "모호함"}
    assert body["items"][0]["elements"]["연락경로"] == {"state": "문서에없음"}
    assert all(it["score"] == 1 for it in body["items"][1:])

    # API 5: 해당없음 닫기 → 그 키만 바뀌고 score가 1 줄어든다
    r = client.patch(f"/sessions/{sid}/items/item_01/elements/연락경로", json={"state": "해당없음", "closed_by": "전임"})
    assert r.status_code == 200
    item = r.json()["item"]
    assert item["elements"]["연락경로"] == {"state": "해당없음", "closed_by": "전임"} and item["score"] == 4
    assert sum(1 for el in item["elements"].values() if el["state"] == "해당없음") == 1
    r = client.patch(f"/sessions/{sid}/items/item_01/elements/기타", json={"state": "해당없음", "closed_by": "전임"})
    assert r.status_code == 200 and r.json()["error"]["code"] == "invalid_element_key"
    r = client.patch(f"/sessions/{sid}/items/item_01/elements/시기", json={"state": "있음", "closed_by": "전임"})
    assert r.status_code == 200 and r.json()["error"]["code"] == "invalid_request"  # 해당없음만 허용

    # §7-4 질문 카드
    r = client.get(f"/sessions/{sid}/questions")
    assert r.status_code == 200
    qs = r.json()
    assert "error" not in qs
    check_cards(qs)
    assert [q["rank"] for q in qs["cards"]] == [1, 2, 3, 4, 5]
    assert all("fallback" not in q for q in qs["cards"]) and all(q["fallback"] for q in qs["more"])
    # 후보 순서: 회비(score 4)의 문서에없음(자주틀리는점) → 모호함(판단기준). 연락경로는 위에서 해당없음으로 닫혔으니 제외.
    # 항목당 2장이므로 회비는 거기까지, 나머지 항목은 배경(모호함)만 비어 있음
    assert [(q["item_id"], q["element"], q["kind"]) for q in qs["cards"]][:2] == [
        ("item_01", "자주틀리는점", "빈칸"), ("item_01", "판단기준", "빈칸")]
    assert all(q["element"] != "연락경로" for q in qs["cards"] + qs["more"] if q["item_id"] == "item_01")
    # 다른 항목(score 1)은 배경(모호함, 빈칸)과 추정 fact(시기, 확인)만 후보 — 둘 다 우선순위 1
    assert all((q["element"], q["kind"]) in {("배경", "빈칸"), ("시기", "확인")} for q in qs["cards"][2:])
    everything = qs["cards"] + qs["more"]
    # 회비/시기는 추가확인권장(보완, 우선 2)과 추정 fact(확인, 우선 1)가 겹치므로 확인 하나만 남는다
    assert {q["kind"] for q in everything} == {"빈칸", "확인"}
    assert sum(1 for q in everything if q["item_id"] == "item_01" and q["element"] == "시기") == 1
    assert len({(q["item_id"], q["element"]) for q in everything}) == len(everything)
    assert [q["rank"] for q in qs["more"]] == list(range(6, 6 + len(qs["more"])))
    assert client.get(f"/sessions/{sid}/questions").json() == qs  # 두 번째 호출은 저장된 것

    # §7-5 답변 3개 → 주석 ≥ 3 (모두 draft, source.type=전임답변). 짧은 답변은 skip, 없는 질문은 error.
    q1, q2, q3 = (q["id"] for q in qs["cards"][:3])
    r = client.post(f"/sessions/{sid}/answers", json={"answers": [
        {"question_id": q1, "text": "매월 25일까지 계좌로 받고, 미납자는 개별로 연락합니다."},
        {"question_id": q2, "text": "체육시설과 연락처는 메일로 먼저 보내고 다음 날 전화합니다."},
        {"question_id": q3, "text": "대회 2주 전에 참가자 명단을 확정해 둡니다.", "audio_url": "https://x/a.m4a"},
        {"question_id": qs["cards"][3]["id"], "text": "네"},
        {"question_id": "q_99", "text": "없는 질문에 대한 긴 답변입니다."},
    ]})
    assert r.status_code == 200
    ab = r.json()
    anns = ab["annotations"]
    assert len(anns) == 4  # 3개 + 요소가 섞인 답변 1개는 2개로 나뉨
    assert [a["id"] for a in anns] == ["an_001", "an_002", "an_003", "an_004"]
    assert all(a["status"] == "draft" and a["source"]["type"] == "전임답변" and a["label"] == "전임경험" for a in anns)
    assert {a["source"]["ids"][0] for a in anns} == {q1, q2, q3}
    assert all(set(a["slots"]) == {"절차", "예외", "사례", "연락", "조건"} for a in anns)
    assert any(a["slots"]["사례"] is None for a in anns)  # 사례 null이어도 저장
    assert ab["skipped"] == [qs["cards"][3]["id"]]
    assert ab["error"]["code"] == "answers_partial" and "q_99" in ab["error"]["message"]

    # API 8: approve 2개, 1개는 text 수정 → edited, 1개 reject
    for ann_id, status in ((anns[0]["id"], "approved"), (anns[1]["id"], "approved"), (anns[3]["id"], "rejected")):
        r = client.patch(f"/sessions/{sid}/annotations/{ann_id}", json={"status": status})
        assert r.status_code == 200 and r.json()["annotation"]["status"] == status
    r = client.patch(f"/sessions/{sid}/annotations/{anns[2]['id']}", json={"text": "대회 2주 전 명단 확정, 참가비는 회비 계좌로."})
    assert r.json()["annotation"]["status"] == "edited" and r.json()["annotation"]["text"].startswith("대회 2주 전 명단")
    r = client.patch(f"/sessions/{sid}/annotations/{anns[0]['id']}", json={"text": anns[0]["text"]})  # 같은 text면 그대로
    assert r.json()["annotation"]["status"] == "approved"
    assert client.patch(f"/sessions/{sid}/annotations/an_999", json={"status": "approved"}).json()["error"]["code"] == "annotation_not_found"
    assert client.patch(f"/sessions/{sid}/annotations/{anns[0]['id']}", json={"status": "published"}).json()["error"]["code"] == "invalid_request"

    # §7-6 발행: approved 2 + edited 1 → 3개. an_003을 reject하면 정확히 2개. draft·rejected는 새지 않는다.
    NOTICE = "이 문서는 전임의 경험을 정리한 것이며 회칙·규정이 우선합니다."
    r = client.post(f"/sessions/{sid}/publish")
    assert r.status_code == 200
    doc = r.json()["document"]
    published = [a for it in doc["items"] for a in it["annotations"]]
    assert {a["id"] for a in published} == {"an_001", "an_002", "an_003"} and doc["notice"] == NOTICE
    client.patch(f"/sessions/{sid}/annotations/an_003", json={"status": "rejected"})
    doc = client.post(f"/sessions/{sid}/publish").json()["document"]
    published = [a for it in doc["items"] for a in it["annotations"]]
    assert [a["id"] for a in published] == ["an_001", "an_002"]
    assert all(a["status"] == "approved" for a in published)
    assert set(doc["items"][0]) == {"id", "title", "elements", "facts", "annotations"}
    assert [it["id"] for it in doc["items"]] == [it["id"] for it in body["items"]]
    for it in doc["items"]:  # facts(자료)와 annotations(전임답변) 분리
        assert all(f["source"]["type"] == "자료" for f in it["facts"])
        assert all(a["source"]["type"] == "전임답변" for a in it["annotations"])

    # §7-7 Q&A: 승인 주석의 단어 → hits ≥ 1, 무관한 질문 → none. rejected 주석(주무관)은 검색되지 않는다.
    r = client.post(f"/sessions/{sid}/ask", json={"question": "회비 미납자는 어떻게 해요?"})
    assert r.status_code == 200
    qa = r.json()
    assert qa["none"] is False and qa["summary"] is None and len(qa["hits"]) >= 1
    assert qa["hits"][0]["annotation_id"] == "an_001" and qa["hits"][0]["score"] >= 2  # 회비 + 미납자는
    assert set(qa["hits"][0]) == {"annotation_id", "text", "source", "label", "item", "score"}
    assert qa["hits"][0]["item"] == "회비 정산" and qa["hits"][0]["source"]["type"] == "전임답변"
    assert all(h["annotation_id"] in {"an_001", "an_002"} for h in qa["hits"])
    for q in ("우주선 발사 일정", "주무관에게 연락"):
        qa = client.post(f"/sessions/{sid}/ask", json={"question": q}).json()
        assert qa == {"hits": [], "summary": None, "none": True}, q

    # API 11 + GET /forwarded
    r = client.post(f"/sessions/{sid}/ask/forward", json={"question": "우주선 발사 일정"})
    assert r.status_code == 200 and r.json() == {"forwarded": True}
    fw = client.get(f"/sessions/{sid}/forwarded").json()["forwarded"]
    assert len(fw) == 1 and fw[0]["question"] == "우주선 발사 일정" and fw[0]["asked_at"]
    assert client.post(f"/sessions/{sid}/ask/forward", json={"question": "  "}).json()["error"]["code"] == "empty_question"

    # 병합·삭제
    by_id = {it["id"]: it for it in body["items"]}
    n1, n4 = len(by_id["item_01"]["facts"]), len(by_id["item_04"]["facts"])
    r = client.patch(f"/sessions/{sid}/items", json={"items": [
        {"id": "item_04", "merge_into": "item_01"},
        {"id": "item_03", "delete": True},
        {"id": "item_99", "title": "없음"},
    ]})
    assert r.status_code == 200
    body = r.json()
    assert [it["id"] for it in body["items"]] == ["item_01", "item_02"]
    assert len(body["items"][0]["facts"]) == n1 + n4
    assert "모집" in body["items"][0]["keywords"]
    assert body["error"]["code"] == "item_patch_partial"


def test_flow_no_key_keeps_structure(client, monkeypatch):  # §7-8
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    sid = new_session(client)

    body = upload(client, sid, "docs", "rules.txt")
    assert body["items"] == []
    assert body["error"]["code"] == "llm_no_key" and body["error"]["message"]

    body = upload(client, sid, "chat", "kakao.txt")
    assert body["items"] == [] and body["chat_count"] == 55
    assert set(body["error"]) == {"code", "message"}

    body = client.get(f"/sessions/{sid}/questions").json()
    assert body["cards"] == [] and body["more"] == [] and body["error"]["code"] == "no_items"

    # 발행 전 ask → NOT_PUBLISHED (구조 유지)
    qa = client.post(f"/sessions/{sid}/ask", json={"question": "회비"}).json()
    assert qa["hits"] == [] and qa["summary"] is None and qa["none"] is True and qa["error"]["code"] == "NOT_PUBLISHED"

    # 나머지 엔드포인트도 항목이 없는 상태에서 전부 200 (§7-8: 어떤 단계도 500 없음)
    r = client.patch(f"/sessions/{sid}/items/item_01/elements/시기", json={"state": "해당없음", "closed_by": "전임"})
    assert r.status_code == 200 and r.json()["error"]["code"] == "item_not_found"
    r = client.post(f"/sessions/{sid}/answers", json={"answers": [{"question_id": "q_01", "text": "충분히 긴 답변입니다."}]})
    assert r.status_code == 200 and r.json()["annotations"] == [] and "q_01" in r.json()["error"]["message"]
    assert client.patch(f"/sessions/{sid}/annotations/an_001", json={"status": "approved"}).json()["error"]["code"] == "annotation_not_found"
    doc = client.post(f"/sessions/{sid}/publish").json()["document"]
    assert doc["items"] == [] and doc["notice"].endswith("승인된 기록이 없습니다.")
    assert client.post(f"/sessions/{sid}/ask", json={"question": "회비"}).json() == {"hits": [], "summary": None, "none": True}
    assert client.post(f"/sessions/{sid}/ask/forward", json={"question": "회비는요"}).json() == {"forwarded": True}
    assert len(client.get(f"/sessions/{sid}/forwarded").json()["forwarded"]) == 1


def test_unexpected_exception_is_200_internal(client, monkeypatch):
    """라우터 안에서 터진 예외는 {"error": {"code": "INTERNAL"}} + 200."""
    from app import db

    def boom(session_id):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(db, "get_session", boom)
    r = client.get("/sessions/any/questions")
    assert r.status_code == 200
    assert r.json()["error"]["code"] == "INTERNAL" and "disk on fire" in r.json()["error"]["message"]


def test_facts_llm_failure_keeps_items(client, monkeypatch):  # §7-8
    def items_ok_facts_fail(prompt_name, payload, schema, retries=1):
        if prompt_name == "01_items":
            return fake_call_json(prompt_name, payload, schema)
        return None, {"code": "llm_call_failed", "message": "boom"}

    monkeypatch.setattr(llm, "call_json", items_ok_facts_fail)
    sid = new_session(client)
    upload(client, sid, "docs", "rules.txt")
    body = upload(client, sid, "chat", "kakao.txt")
    assert len(body["items"]) == 4 and all(it["facts"] == [] for it in body["items"])
    assert body["error"]["code"] == "llm_call_failed"
    for it in body["items"]:  # 요소 판정도 실패 → 6개 모두 모호함(보류), score 6
        assert it["elements"] == {k: {"state": "모호함"} for k in ELEMENT_KEYS} and it["score"] == 6

    # 질문도 LLM 실패 → 카드 5장은 전부 기본 템플릿(fallback) + error, 500 없음
    qs = client.get(f"/sessions/{sid}/questions").json()
    check_cards(qs)
    assert len(qs["cards"]) == 5 and all(q["fallback"] for q in qs["cards"]) and qs["error"]["code"] == "llm_call_failed"
    assert len(qs["cards"]) + len(qs["more"]) == 4 * 6  # 4항목 × 6요소 모두 모호함 → 후보 24개

    # 답변 → 주석도 LLM 실패 → annotations=[] + error, 500 없음
    r = client.post(f"/sessions/{sid}/answers", json={"answers": [{"question_id": qs["cards"][0]["id"], "text": "충분히 긴 답변입니다."}]})
    assert r.status_code == 200 and r.json()["annotations"] == [] and r.json()["error"]["code"] == "llm_call_failed"

    # 승인된 주석 0개 → error 없이 빈 배열 + notice 뒤에 한 줄
    doc = client.post(f"/sessions/{sid}/publish").json()["document"]
    assert all(it["annotations"] == [] for it in doc["items"]) and len(doc["items"]) == 4
    assert doc["notice"].endswith("\n승인된 기록이 없습니다.")


def test_elements_reject_not_applicable_from_llm(client, monkeypatch):
    """LLM이 해당없음을 내면 스키마 실패 → 6개 모두 모호함 + error. 사람이 닫은 해당없음은 재판정 때 유지."""
    from app.services import elements as es

    def fake(prompt_name, payload, schema, retries=1):
        if prompt_name == "03_elements":
            return None, {"code": "llm_schema_invalid", "message": "해당없음"}
        return fake_call_json(prompt_name, payload, schema)

    with pytest.raises(Exception):
        es._LLMElements.model_validate({"elements": {"시기": {"state": "해당없음"}}})

    item = {"id": "item_01", "title": "회비 정산", "keywords": ["회비"], "facts": [],
            "elements": {"배경": {"state": "해당없음", "closed_by": "후임"}}}
    monkeypatch.setattr(llm, "call_json", fake)
    assert es.judge_elements([item], [], {})["code"] == "llm_schema_invalid"
    assert item["elements"]["배경"] == {"state": "해당없음", "closed_by": "후임"}
    assert all(item["elements"][k] == {"state": "모호함"} for k in ELEMENT_KEYS if k != "배경")
    assert item["score"] == 5
    assert [it["id"] for it in es.rank_items([{"id": "a", "score": 1, "elements": {}}, {"id": "b", "score": 3, "elements": {}}])] == ["b", "a"]


@pytest.mark.llm
@pytest.mark.skipif(not HAS_KEY, reason="LLM_API_KEY 없음: 실제 호출 검사는 건너뜀")
def test_full_flow_real_llm(client):
    """실제 LLM으로 §7의 1~8. `pytest -m llm` 으로만 돈다."""
    sid = new_session(client)
    body = upload(client, sid, "docs", "rules.txt")
    assert "error" not in body, body.get("error")
    check_items(body["items"])

    body = upload(client, sid, "chat", "kakao.txt")
    assert "error" not in body, body.get("error")
    check_facts(body["items"])
    check_elements(body["items"])
    scores = [it["score"] for it in body["items"]]
    assert scores == sorted(scores, reverse=True)

    qs = client.get(f"/sessions/{sid}/questions").json()
    assert "error" not in qs, qs.get("error")
    check_cards(qs)
    assert len(qs["cards"]) >= 1 and all("fallback" not in q for q in qs["cards"]), "카드가 템플릿으로 대체됨"

    answers = [
        "매월 25일까지 동아리 계좌로 받고, 미납자는 총무가 개별 톡으로 연락합니다. 두 달 넘으면 운영진 회의에 올립니다.",
        "체육시설과 담당 주무관에게 메일을 먼저 보내고 다음 날 전화로 접수됐는지 확인합니다. 작년에 메일만 보냈다가 접수가 안 된 적이 있어요.",
        "대회 2주 전에 참가자 명단을 확정하고, 참가비는 회비 계좌로 함께 받습니다. 신청서는 학생지원팀에 운영진이 일괄 제출합니다.",
    ]
    r = client.post(f"/sessions/{sid}/answers", json={"answers": [
        {"question_id": q["id"], "text": a} for q, a in zip(qs["cards"], answers)]})
    anns = r.json()["annotations"]
    assert "error" not in r.json(), r.json().get("error")
    assert len(anns) >= 3 and all(a["status"] == "draft" and a["source"]["type"] == "전임답변" for a in anns)

    for a in anns[:2]:
        client.patch(f"/sessions/{sid}/annotations/{a['id']}", json={"status": "approved"})
    doc = client.post(f"/sessions/{sid}/publish").json()["document"]
    published = [a for it in doc["items"] for a in it["annotations"]]
    assert [a["id"] for a in published] == [a["id"] for a in anns[:2]]

    word = next(t for t in __import__("app.services.qa", fromlist=["tokenize"]).tokenize(published[0]["text"]))
    qa = client.post(f"/sessions/{sid}/ask", json={"question": f"{word} 어떻게 해요?"}).json()
    assert qa["none"] is False and len(qa["hits"]) >= 1
    qa = client.post(f"/sessions/{sid}/ask", json={"question": "우주선 발사 일정"}).json()
    assert qa == {"hits": [], "summary": None, "none": True}


def test_session_not_found(client):
    r = client.patch("/sessions/nope/items", json={"items": []})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "session_not_found"


def test_bad_request_is_200_with_error(client):
    sid = new_session(client)
    r = client.patch(f"/sessions/{sid}/items", json={"items": "x"})
    assert r.status_code == 200 and r.json()["error"]["code"] == "invalid_request"
    r = client.post(f"/sessions/{sid}/docs", files={"file": ("a.hwp", b"x", "application/octet-stream")})
    assert r.status_code == 200 and r.json()["error"]["code"] == "doc_parse_failed"


def test_call_json_retries_once_then_gives_error(monkeypatch):
    """call_json: 스키마 실패 시 1회 재시도, 그래도 실패면 (None, error). 예외를 던지지 않는다."""
    from types import SimpleNamespace

    from pydantic import BaseModel

    class Out(BaseModel):
        items: list[str]

    def install(replies):
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            reply = replies[len(calls) - 1]
            if isinstance(reply, Exception):
                raise reply
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])

        fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        monkeypatch.setattr(llm, "OpenAI", lambda **kw: fake)
        return calls

    monkeypatch.setenv("LLM_API_KEY", "test-key")

    calls = install(['{"wrong": 1}', '```json\n{"items": ["가"]}\n```'])
    assert llm.call_json("01_items", {"sentences": []}, Out) == ({"items": ["가"]}, None)
    assert len(calls) == 2
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert calls[0]["messages"][0]["content"].startswith("너는 동아리 운영 문서를 읽고")

    calls = install(["not json", '{"items": 3}', '{"items": ["늦음"]}'])
    obj, error = llm.call_json("01_items", {"sentences": []}, Out)
    assert obj is None and error["code"] == "llm_schema_invalid" and len(calls) == 2

    calls = install([RuntimeError("network down"), RuntimeError("network down")])
    obj, error = llm.call_json("01_items", {"sentences": []}, Out)
    assert obj is None and error["code"] == "llm_call_failed" and len(calls) == 2


def test_forbidden_phrase_triggers_regeneration_then_template(monkeypatch):
    """금지어가 있는 응답은 재생성(총 3회), 전부 걸리면 요소별 기본 템플릿 + fallback."""
    from app.services import questions as qs

    bad = {"step1": "회비 정산에서 실수한 적 있나요?", "step2": "그냥 처리한 적 있나요?", "step3": "사례요?"}
    good = {"step1": "회비 정산은 언제 하시나요?", "step2": "다르게 처리한 경우가 있었나요?", "step3": "사례를 들어주세요."}
    assert qs.has_forbidden(**bad) and not qs.has_forbidden(**good)
    for s1 in ("A와 B 중 어느 쪽인가요?", "메일인가요 아니면 전화인가요?", "이미 하셨잖아요", "빨리 처리하는 법이 있나요?"):
        assert qs.has_forbidden(s1, "", "")

    cand = {"item": {"id": "item_01", "title": "회비 정산", "score": 3}, "element": "시기", "kind": "빈칸", "context": {}}
    calls = []

    def fake(prompt_name, payload, schema, retries=1):
        calls.append(payload)
        return schema_dump(schema, bad if len(calls) < 3 else good), None

    monkeypatch.setattr(llm, "call_json", fake)
    assert qs.generate_steps(cand) == (good, False, None) and len(calls) == 3  # 두 번 걸리고 세 번째 통과

    monkeypatch.setattr(llm, "call_json", lambda name, payload, schema, retries=1: (schema_dump(schema, bad), None))
    steps, fallback, error = qs.generate_steps(cand)
    assert fallback and error["code"] == "forbidden_phrase"
    assert steps == {"step1": "[회비 정산] 이 일은 실제로 언제쯤 시작해서 언제까지 마무리하시나요?",
                     "step2": qs.STEP2, "step3": qs.STEP3}
    for key in ELEMENT_KEYS:  # 모든 기본 템플릿이 금지어 필터를 통과한다
        assert not qs.has_forbidden(**qs.template("코트 대관", key))

    monkeypatch.setattr(llm, "call_json", llm_no_key)
    steps, fallback, error = qs.generate_steps(cand)
    assert fallback and error["code"] == "llm_no_key"


def test_kakao_both_formats():
    pc = (
        "방 이름 님과 카카오톡 대화\n"
        "--------------- 2026년 4월 2일 목요일 ---------------\n"
        "[회장] [오후 7:45] 코트 대관 신청했어요\n"
        "둘째 줄\n"
        "[총무] [오후 7:46] 사진\n"
        "홍길동님이 들어왔습니다.\n"
        "[총무] [오후 7:47] ㅋ\n"
        "[총무] [오후 7:48] 회비 입금 확인\n"
    ).encode("utf-8")
    assert parse_kakao(pc) == [
        {"idx": 1, "id": "chat#1", "date": "2026-04-02", "speaker": "회장", "text": "코트 대관 신청했어요\n둘째 줄"},
        {"idx": 2, "id": "chat#2", "date": "2026-04-02", "speaker": "총무", "text": "회비 입금 확인"},
    ]
    mobile = (
        "2026년 4월 2일 목요일\n"
        "2026년 4월 2일 오후 7:45, 회장 : 코트 대관 신청했어요\n"
        "2026년 4월 2일 오후 7:46, 홍길동님이 나갔습니다.\n"
        "2026. 4. 3. 오전 9:01, 총무 : 회비 입금 확인\n"
        "2026. 4. 3. 오전 9:02, 총무 : 삭제된 메시지입니다\n"
    ).encode("utf-8")
    assert [(m["id"], m["date"], m["speaker"], m["text"]) for m in parse_kakao(mobile)] == [
        ("chat#1", "2026-04-02", "회장", "코트 대관 신청했어요"),
        ("chat#2", "2026-04-03", "총무", "회비 입금 확인"),
    ]


def test_qa_tokenize_and_search():
    from app.services import qa

    assert qa.tokenize("회비는 언제, 어떻게 내요?") == {"회비는", "내요"}  # '언제'·'어떻게' 불용어, 구두점 제거
    assert qa.tokenize("a 이 가 을") == set()  # 길이 1 토큰 제거
    published = {"items": [{"title": "코트 대관", "annotations": [
        {"id": "an_001", "text": "체육시설과에 메일을 보낸다", "label": "전임경험", "source": {"type": "전임답변", "ids": ["q_01"]},
         "slots": {"절차": None, "예외": None, "사례": None, "연락": "김 주무관 전화", "조건": None}},
        {"id": "an_002", "text": "대관료는 회비에서 낸다", "label": "전임경험", "source": {"type": "전임답변", "ids": ["q_02"]},
         "slots": {"절차": None, "예외": None, "사례": None, "연락": None, "조건": None}},
    ]}]}
    hits = qa.search(published, "주무관 전화 번호가 뭐예요")  # 슬롯 값으로도 매치
    assert [h["annotation_id"] for h in hits] == ["an_001"] and hits[0]["score"] == 2
    hits = qa.search(published, "코트 대관료는")  # 항목 제목(코트)으로 둘 다, '대관료는'으로 an_002가 앞
    assert [h["annotation_id"] for h in hits] == ["an_002", "an_001"]
    hits = qa.search(published, "코트 대관료")  # 토큰 완전 일치만 본다('대관료'≠'대관료는') → 둘 다 1점, 문서 순서
    assert [(h["annotation_id"], h["score"]) for h in hits] == [("an_001", 1), ("an_002", 1)]
    assert qa.search(published, "우주선 발사 일정") == []


def test_keyword_filter_window():
    chat = [{"id": f"chat#{i}", "text": t} for i, t in enumerate(
        ["a", "b", "c", "회비 냈어요", "e", "f", "g", "h", "코트 예약", "j"], start=1)]
    ids = [m["id"] for m in filter_messages(chat, ["회비"])]
    assert ids == ["chat#2", "chat#3", "chat#4", "chat#5", "chat#6"]
    ids = [m["id"] for m in filter_messages(chat, ["회비", "코트"])]
    assert ids == [f"chat#{i}" for i in (2, 3, 4, 5, 6, 7, 8, 9, 10)]
    assert filter_messages(chat, ["없는말"]) == []
