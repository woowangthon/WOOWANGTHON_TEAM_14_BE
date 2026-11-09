"""fixture로 API 1→4 스모크. CLAUDE.md §7의 1·2·8을 검사한다(5~10은 해당 단계 구현 후 추가).

- 가짜 LLM: 키 없이도 후처리(출처 검증 등)까지 검사
- 키 없음: error 경로에서 응답 구조 유지
- 실제 LLM: .env에 LLM_API_KEY가 있을 때만 실행
"""
import os
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
    else:
        raise AssertionError(prompt_name)
    return schema.model_validate(obj).model_dump(), None


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


def test_flow_fake_llm(client, monkeypatch):
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

    # 병합·삭제
    n1, n4 = len(body["items"][0]["facts"]), len(body["items"][3]["facts"])
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


@pytest.mark.skipif(not HAS_KEY, reason="LLM_API_KEY 없음: 실제 호출 검사는 건너뜀")
def test_flow_real_llm(client):  # §7-1, §7-2
    sid = new_session(client)
    body = upload(client, sid, "docs", "rules.txt")
    assert "error" not in body, body.get("error")
    check_items(body["items"])
    body = upload(client, sid, "chat", "kakao.txt")
    assert "error" not in body, body.get("error")
    check_facts(body["items"])


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


def test_keyword_filter_window():
    chat = [{"id": f"chat#{i}", "text": t} for i, t in enumerate(
        ["a", "b", "c", "회비 냈어요", "e", "f", "g", "h", "코트 예약", "j"], start=1)]
    ids = [m["id"] for m in filter_messages(chat, ["회비"])]
    assert ids == ["chat#2", "chat#3", "chat#4", "chat#5", "chat#6"]
    ids = [m["id"] for m in filter_messages(chat, ["회비", "코트"])]
    assert ids == [f"chat#{i}" for i in (2, 3, 4, 5, 6, 7, 8, 9, 10)]
    assert filter_messages(chat, ["없는말"]) == []
