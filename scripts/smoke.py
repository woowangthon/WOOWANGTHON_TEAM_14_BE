"""사람이 읽는 스모크 테스트. 서버가 떠 있는 상태에서 API 1→11(+forwarded)을 실제로 호출하고 결과를 표/목록으로 보여준다.

사용법:
  python scripts/smoke.py --base http://localhost:8000 --rules fixtures/rules.txt --kakao fixtures/kakao.txt
  python scripts/smoke.py --base https://<배포URL> --rules fixtures/demo/rules.txt --kakao fixtures/demo/kakao.txt --save out/smoke.json

- 응답에 error가 있으면 그 단계를 WARN으로 표시하고 계속 간다.
- HTTP 500이나 연결 실패면 FAIL로 표시하고 멈춘다.
- 마지막에 CLAUDE.md §7 완료 기준 1~8을 ✔/✖로 요약한다.
표준 라이브러리 + httpx만 쓴다.
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:  # 저장소 안에서 돌리면 파서를 빌려 "문장 수", "필터 후 메시지 수"를 계산한다. 없으면 생략.
    from app.parsers.docs import parse_doc
    from app.parsers.kakao import parse_kakao
    from app.services.facts import filter_messages
except Exception:  # noqa: BLE001
    parse_doc = parse_kakao = filter_messages = None

ELEMENT_KEYS = ("시기", "연락경로", "판단기준", "자주틀리는점", "미리할일", "배경")
FORBIDDEN = [re.compile(p) for p in (
    r"실수|문제|반려|잘못|힘든", r"와\s.+\s중", r"인가요\s*아니면", r"했을\s*텐데|하셨잖아요", r"편법|그냥|빨리\s*처리")]
STOPWORDS = set("은 는 이 가 을 를 의 에 에서 로 으로 와 과 도 요 어떻게 해요 하나요 뭐 어떤 언제 누구".split())
NOTICE = "이 문서는 전임의 경험을 정리한 것이며 회칙·규정이 우선합니다."

# 7단계 고정 답변: 카드의 요소에 맞는 것을 고른다(코트 연락경로 · 회비 시기 · 대회 미리할일이 대표).
ANSWERS = {
    "연락경로": "코트 대관은 학교 체육시설과 김 주무관님께 메일로 먼저 신청서를 보내고, 다음 날 전화로 접수됐는지 확인합니다. 메일만 보내면 누락된 적이 있어서 전화 확인은 꼭 합니다.",
    "시기": "회비는 매월 25일까지 동아리 계좌로 받습니다. 20일쯤 단톡에 공지를 올리고, 말일까지 안 낸 사람은 총무가 개별 톡으로 다시 알립니다.",
    "미리할일": "교내 대회는 신청 마감 2주 전에 참가자 명단과 복식 조를 먼저 확정해 둡니다. 참가비는 회비 계좌로 같이 받아서 신청서 낼 때 한 번에 냅니다.",
    "판단기준": "두 달 연속 미납이면 운영진 회의에 올리고, 사정이 있는 경우는 총무가 먼저 개별로 연락해 분납을 안내합니다. 세 달 넘으면 회장이 직접 연락합니다.",
    "자주틀리는점": "코트 대관 신청서에 동아리 등록번호를 빼먹는 경우가 많아서 보내기 전에 꼭 확인합니다. 체육시설과 담당자가 바뀌면 연락처도 다시 확인해야 합니다.",
    "배경": "학교 규정상 동아리는 체육시설과를 통해서만 코트를 예약할 수 있어서 개인 예약은 안 됩니다. 그래서 운영진이 한 달 전에 일괄로 신청합니다.",
}
STATE_MARK = {("있음", "확인됨"): "✔확인됨", ("있음", "추가확인권장"): "✔?추가확인", ("문서에없음", None): "✖없음",
              ("모호함", None): "~모호", ("해당없음", None): "–해당없음"}


class Fail(Exception):
    pass


class Smoke:
    def __init__(self, base: str, rules: Path, kakao: Path, save: Path | None):
        self.base = base.rstrip("/")
        self.rules, self.kakao, self.save = rules, kakao, save
        self.http = httpx.Client(timeout=300)
        self.raw: dict[str, object] = {}
        self.ok = self.warn = 0
        self.errors: list[str] = []
        self.warn_notes: list[str] = []
        self.checks: dict[int, tuple[bool, str]] = {}
        self.sid = ""

    # ---- 출력 ----
    @staticmethod
    def line(step: str, label: str, status: str, detail: str = ""):
        dots = "." * max(2, 22 - len(label) - sum(1 for ch in label if ord(ch) > 0x2E7F))  # 한글은 2칸
        print(f"[{step}] {label} {dots} {status}  {detail}".rstrip())

    @staticmethod
    def sub(text: str, indent: int = 9):
        print(" " * indent + text)

    # ---- 호출 ----
    def call(self, method: str, path: str, **kw) -> dict:
        url = self.base + path
        try:
            r = self.http.request(method, url, **kw)
        except httpx.HTTPError as e:
            raise Fail(f"연결 실패 {method} {url}: {type(e).__name__}: {e}")
        if r.status_code >= 500:
            raise Fail(f"HTTP {r.status_code} {method} {path}: {r.text[:200]}")
        if r.status_code == 404:
            raise Fail(f"HTTP 404 {method} {path}: {r.text[:200]}")
        try:
            body = r.json()
        except ValueError:
            raise Fail(f"JSON 아님 {method} {path}: {r.text[:200]}")
        self.raw.setdefault(path.split("/")[-1] or "root", []).append({"method": method, "path": path, "body": body})
        return body

    def step(self, n: str, label: str, body: dict, detail: str, note: str = ""):
        """error 필드가 있으면 WARN, 아니면 OK."""
        err = body.get("error") if isinstance(body, dict) else None
        if err:
            self.warn += 1
            self.warn_notes.append(f"[{n}] {err.get('code')}: {err.get('message')}")
            self.line(n, label, "WARN", detail)
            self.sub(f"error {err.get('code')}: {err.get('message')}")
        else:
            self.ok += 1
            self.line(n, label, "OK", detail)
        if note:
            self.sub(note)

    # ---- 단계 ----
    def run(self) -> int:
        t0 = time.time()
        print(f"base={self.base}  rules={self.rules}  kakao={self.kakao}")
        try:
            health = self.call("GET", "/health")
            print(f"health: ok={health.get('ok')} prompt_version={health.get('prompt_version')} model={health.get('model')}")
            self.s1(); items = self.s2(); self.s3(items); items = self.s4(items); self.s5(items)
            qs = self.s6(); anns = self.s7(qs); approved = self.s8(anns); self.s9(approved); self.s10(approved); self.s11()
        except Fail as e:
            self.errors.append(str(e))
            print(f"FAIL  {e}")
            print("멈춤: 서버 오류 또는 연결 실패.")
        elapsed = time.time() - t0

        print("─" * 45)
        print(f"결과: {self.ok}/11 통과, 경고 {self.warn} , error {len(self.errors)}, 소요 {elapsed:.0f}초")
        for w in self.warn_notes:
            print(f"  WARN {w}")
        for e in self.errors:
            print(f"  FAIL {e}")
        print("§7 완료 기준:")
        names = {1: "items 4~7개, keywords ≥3", 2: "facts ≥10, 모두 source.ids ≥1·실제 chat id", 3: "elements 6키, 해당없음 0(닫기 전)",
                 4: "cards ≤5, 항목당 ≤2, 금지어 0", 5: "답변 3 → 주석 ≥3, 모두 draft·전임답변", 6: "approve 2 → 발행 주석 정확히 2",
                 7: "승인 단어 ask → hits ≥1 / 무관 질문 → none", 8: "HTTP 500 없음, error여도 응답 구조 유지"}
        for k in range(1, 9):
            passed, why = self.checks.get(k, (False, "실행 안 됨"))
            print(f"  {'✔' if passed else '✖'} {k}. {names[k]}" + (f" — {why}" if why else ""))
        if self.save:
            self.save.parent.mkdir(parents=True, exist_ok=True)
            self.save.write_text(json.dumps({"base": self.base, "session_id": self.sid, "at": datetime.now().isoformat(timespec="seconds"),
                                             "responses": self.raw}, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"응답 원본 저장: {self.save}")
        return 1 if self.errors else 0

    def s1(self):
        body = self.call("POST", "/sessions", json={"club_name": "테니스 동아리", "giver": "전임 회장", "receiver": "후임 회장"})
        self.sid = body.get("session_id", "")
        if not self.sid:
            raise Fail(f"session_id 없음: {body}")
        self.step("1/11", "세션 생성", body, f"session_id={self.sid}")

    def s2(self) -> list[dict]:
        n_sent = "?"
        if parse_doc:
            try:
                n_sent = len(parse_doc(self.rules.name, self.rules.read_bytes()))
            except Exception:  # noqa: BLE001
                pass
        with open(self.rules, "rb") as f:
            body = self.call("POST", f"/sessions/{self.sid}/docs", files={"file": (self.rules.name, f)})
        items = body.get("items", [])
        self.step("2/11", "자료 업로드", body, f"문장 {n_sent}개 → 항목 {len(items)}개")
        for it in items:
            self.sub(f"- {it['id']} {it['title']:<10} 키워드: {', '.join(it['keywords'])}")
        ok = 4 <= len(items) <= 7 and all(len(it["keywords"]) >= 3 for it in items)
        self.checks[1] = (ok, f"항목 {len(items)}개, 키워드 최소 {min((len(it['keywords']) for it in items), default=0)}개")
        return items

    def s3(self, items: list[dict]):
        if not items:
            self.line("3/11", "항목 수정", "SKIP", "항목이 없어 건너뜀")
            return
        first = items[0]
        body = self.call("PATCH", f"/sessions/{self.sid}/items", json={"items": [{"id": first["id"], "keywords": first["keywords"]}]})
        self.step("3/11", "항목 수정", body, f"{first['id']} 키워드 재저장 → 항목 {len(body.get('items', []))}개 유지")

    def s4(self, items: list[dict]) -> list[dict]:
        filtered = "?"
        valid_ids: set[str] = set()
        if parse_kakao and filter_messages:
            try:
                chat = parse_kakao(self.kakao.read_bytes())
                valid_ids = {m["id"] for m in chat}
                kws = [k for it in items for k in it["keywords"]]
                filtered = len(filter_messages(chat, kws)) if kws else 0
            except Exception:  # noqa: BLE001
                pass
        with open(self.kakao, "rb") as f:
            body = self.call("POST", f"/sessions/{self.sid}/chat", files={"file": (self.kakao.name, f)})
        items = body.get("items", [])
        facts = [fa for it in items for fa in it.get("facts", [])]
        no_src = [fa for fa in facts if not fa.get("source", {}).get("ids")]
        bad_src = [fa for fa in facts if valid_ids and not set(fa["source"]["ids"]) <= valid_ids]
        self.step("4/11", "카톡 분석", body,
                  f"메시지 {body.get('chat_count', '?')}개 → 필터 후 {filtered}개 → 사실 {len(facts)}개 (출처 없는 사실 {len(no_src)}, 가짜 출처 {len(bad_src)})")
        for it in items:
            marks = " ".join(f"[{k} {STATE_MARK.get((el.get('state'), el.get('fill') if el.get('state') == '있음' else None), el.get('state'))}]"
                             for k, el in ((k, it.get("elements", {}).get(k, {})) for k in ELEMENT_KEYS))
            self.sub(f"- {it['id']} {it['title']:<10} score {it.get('score', 0)}   {marks}")
            for fa in it.get("facts", [])[:3]:
                est = " (추정)" if fa.get("status") == "추정" else ""
                self.sub(f"· 사실: \"{fa['text'][:40]}\" ({', '.join(fa['source']['ids'])}){est}", indent=11)
            if len(it.get("facts", [])) > 3:
                self.sub(f"· … 외 {len(it['facts']) - 3}개", indent=11)
        self.checks[2] = (len(facts) >= 10 and not no_src and not bad_src, f"사실 {len(facts)}개, 출처 없음 {len(no_src)}, 가짜 출처 {len(bad_src)}")
        six = all(set(it.get("elements", {})) == set(ELEMENT_KEYS) for it in items) and bool(items)
        na = sum(1 for it in items for el in it.get("elements", {}).values() if el.get("state") == "해당없음")
        self.checks[3] = (six and na == 0, f"6키 {'전부' if six else '누락'}, 해당없음 {na}개")
        return items

    def s5(self, items: list[dict]):
        if not items:
            self.line("5/11", "해당없음 닫기", "SKIP", "항목이 없어 건너뜀")
            return
        target = items[-1]  # score가 가장 낮은 항목에서 비어 있는 요소 하나를 사람이 닫는 상황
        key = next((k for k in ELEMENT_KEYS if target.get("elements", {}).get(k, {}).get("state") in ("문서에없음", "모호함")), ELEMENT_KEYS[-1])
        before = target.get("score", 0)
        body = self.call("PATCH", f"/sessions/{self.sid}/items/{target['id']}/elements/{key}", json={"state": "해당없음", "closed_by": "전임"})
        item = body.get("item") or {}
        after = item.get("score", "?")
        state = item.get("elements", {}).get(key, {}).get("state")
        na = sum(1 for el in item.get("elements", {}).values() if el.get("state") == "해당없음")
        mark = "✔" if state == "해당없음" and na == 1 and after == before - 1 else "✖"
        self.step("5/11", "해당없음 닫기", body, f"{target['id']} {target['title']} / {key} → {state}  score {before}→{after} (그 키만 해당없음 {mark})")

    def s6(self) -> dict:
        body = self.call("GET", f"/sessions/{self.sid}/questions")
        cards, more = body.get("cards", []), body.get("more", [])
        per_item: dict[str, int] = {}
        for q in cards:
            per_item[q["item_id"]] = per_item.get(q["item_id"], 0) + 1
        per_ok = all(n <= 2 for n in per_item.values())
        forb = [q["id"] for q in cards + more if any(p.search(" ".join((q["step1"], q["step2"], q["step3"]))) for p in FORBIDDEN)]
        templ = sum(1 for q in cards if q.get("fallback"))
        self.step("6/11", "질문 카드", body,
                  f"카드 {len(cards)}장, 추가 {len(more)}개 (항목당 ≤2 {'✔' if per_ok else '✖'}, 금지어 {len(forb)} {'✔' if not forb else '✖'}, 템플릿 사용 {templ}장)")
        titles = {}
        for q in cards:
            titles.setdefault(q["item_id"], "")
        for i, q in enumerate(cards, start=1):
            self.sub(f"{i}. [{q['item_id']} / {q['element']} / {q['kind']}]{' (템플릿)' if q.get('fallback') else ''} \"{q['step1'][:60]}\"")
            self.sub(f"step2: {q['step2'][:40]}…  step3: {q['step3'][:40]}…", indent=12)
        self.checks[4] = (len(cards) <= 5 and per_ok and not forb, f"카드 {len(cards)}장, 항목당 최대 {max(per_item.values(), default=0)}장, 금지어 {len(forb)}")
        if templ == len(cards) and cards:
            self.warn_notes.append("[6/11] 카드 5장이 전부 템플릿 → LLM 호출 실패(키 확인)")
        return body

    def s7(self, qs: dict) -> list[dict]:
        cards = qs.get("cards", [])[:3]
        if not cards:
            self.line("7/11", "답변 3개 → 주석", "SKIP", "카드가 없어 건너뜀")
            self.checks[5] = (False, "카드 없음")
            return []
        answers = [{"question_id": q["id"], "text": ANSWERS[q["element"]]} for q in cards]
        body = self.call("POST", f"/sessions/{self.sid}/answers", json={"answers": answers})
        anns = body.get("annotations", [])
        all_draft = all(a["status"] == "draft" and a["source"]["type"] == "전임답변" for a in anns)
        self.step("7/11", "답변 3개 → 주석", body,
                  f"주석 {len(anns)}개 ({'모두 draft ✔' if all_draft and anns else '✖'}, skipped {len(body.get('skipped', []))})")
        for a in anns:
            slots = ", ".join(k for k, v in a["slots"].items() if v)
            self.sub(f"- {a['id']} [{a['item_id']} / {a['element']} / {a['label']}] \"{a['text'][:50]}\"  슬롯: {slots or '없음'}")
        self.checks[5] = (len(anns) >= 3 and all_draft, f"주석 {len(anns)}개")
        return anns

    def s8(self, anns: list[dict]) -> list[dict]:
        targets = anns[:2]
        if len(targets) < 2:
            self.line("8/11", "승인 2개", "WARN", f"주석이 {len(anns)}개라 {len(targets)}개만 승인")
            self.warn += 1
        approved = []
        last = {}
        for a in targets:
            last = self.call("PATCH", f"/sessions/{self.sid}/annotations/{a['id']}", json={"status": "approved"})
            if (last.get("annotation") or {}).get("status") == "approved":
                approved.append(last["annotation"])
        if len(targets) >= 2:
            self.step("8/11", "승인 2개", last, f"{', '.join(a['id'] for a in approved)} → approved")
        return approved

    def s9(self, approved: list[dict]):
        body = self.call("POST", f"/sessions/{self.sid}/publish")
        doc = body.get("document", {})
        pub = [a for it in doc.get("items", []) for a in it.get("annotations", [])]
        leaked = [a["id"] for a in pub if a["status"] not in ("approved", "edited")]
        exact = {a["id"] for a in pub} == {a["id"] for a in approved}
        self.step("9/11", "발행", body, f"발행 주석 {len(pub)}개 (승인 {len(approved)}개와 일치 {'✔' if exact else '✖'}, draft 제외 {'✔' if not leaked else '✖'})")
        self.sub(f"notice: \"{doc.get('notice', '')[:60]}\"")
        self.checks[6] = (exact and not leaked and doc.get("notice", "").startswith(NOTICE), f"발행 {len(pub)} / 승인 {len(approved)}")

    def s10(self, approved: list[dict]):
        word = ""
        if approved:
            toks = [t for t in re.sub(r"[^\w\s]", " ", approved[0]["text"]).split() if t not in STOPWORDS and len(t) > 1]
            word = toks[0] if toks else ""
        q1 = f"{word} 어떻게 해요?" if word else "회비 어떻게 해요?"
        b1 = self.call("POST", f"/sessions/{self.sid}/ask", json={"question": q1})
        b2 = self.call("POST", f"/sessions/{self.sid}/ask", json={"question": "우주선 발사 일정"})
        hits = b1.get("hits", [])
        none_ok = b2.get("none") is True and b2.get("hits") == []
        self.step("10/11", "후임 Q&A", b1 if b1.get("error") else b2, f"질문 \"{q1}\" → hits {len(hits)}")
        for h in hits:
            self.sub(f"· {h['annotation_id']} ({h['item']} / {h['label']} / score {h['score']}) \"{h['text'][:50]}\"")
        self.sub(f"질문 \"우주선 발사 일정\" → none={b2.get('none')} {'✔' if none_ok else '✖'}")
        self.checks[7] = (len(hits) >= 1 and none_ok, f"hits {len(hits)}, none={b2.get('none')}")

    def s11(self):
        b1 = self.call("POST", f"/sessions/{self.sid}/ask/forward", json={"question": "우주선 발사 일정"})
        b2 = self.call("GET", f"/sessions/{self.sid}/forwarded")
        n = len(b2.get("questions", []))
        self.step("11/11", "전달", b1 if b1.get("error") else b2, f"forwarded={b1.get('forwarded')} → 질문함 {n}개")
        self.checks[8] = (True, f"500 없음, 경고 {self.warn}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--rules", default="fixtures/rules.txt")
    ap.add_argument("--kakao", default="fixtures/kakao.txt")
    ap.add_argument("--save", help="전체 응답 원본을 저장할 JSON 경로 (예: out/smoke_20261112_1900.json)")
    ns = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return Smoke(ns.base, Path(ns.rules), Path(ns.kakao), Path(ns.save) if ns.save else None).run()


if __name__ == "__main__":
    sys.exit(main())
