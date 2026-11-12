"""프롬프트 04 + 금지어 필터 + 카드 5장 선정 (CLAUDE.md §6)."""
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from pydantic import BaseModel

from app import llm
from app.models import ELEMENT_KEYS, Question

CARD_COUNT = 5
PER_ITEM_MAX = 2
CONFIRM_MAX = 2  # 카드 5장 중 kind="확인"(추정 fact)의 최대 장수
LLM_ATTEMPTS = 3  # 금지어에 걸리면 재생성, 총 3회

# §6 금지어 정규식 5개 (그대로)
FORBIDDEN = [re.compile(p) for p in (
    r"실수|문제|반려|잘못|힘든",
    r"와\s.+\s중",
    r"인가요\s*아니면",
    r"했을\s*텐데|하셨잖아요",
    r"편법|그냥|빨리\s*처리",
)]

# 프롬프트 04의 요소별 step1 기본형 / step2·3 고정문 (템플릿 폴백용)
DEFAULT_STEP1 = {
    "시기": "이 일은 실제로 언제쯤 시작해서 언제까지 마무리하시나요?",
    "연락경로": "이 일을 진행할 때 함께 일하는 담당자나 기관은 누구인가요? 보통 어떤 방식으로 연락하시나요?",
    "판단기준": "같은 업무라도 상황에 따라 처리가 달라지는 경우가 있나요? 무엇을 기준으로 나누시나요?",
    "자주틀리는점": "이 업무에서 특히 신경 써서 확인하시는 부분이 있나요?",
    "미리할일": "본 작업 전에 미리 준비해 두시는 것이 있나요?",
    "배경": "이 업무가 지금 방식으로 운영되는 배경이 있다면 무엇인가요?",
}
STEP2 = "그 과정에서 평소와 다르게 처리해야 했던 경우가 있었나요?"
STEP3 = "이 업무를 최근에 처리한 사례를 하나 들어주실 수 있나요? 특별한 상황이 없었다면 일반적인 처리 사례도 괜찮습니다."


class _LLMQuestion(BaseModel):
    step1: str
    step2: str
    step3: str


def has_forbidden(step1: str, step2: str, step3: str) -> bool:
    joined = " ".join((step1, step2, step3))
    return any(p.search(joined) for p in FORBIDDEN)


def template(title: str, element: str) -> dict:
    return {"step1": f"[{title}] {DEFAULT_STEP1[element]}", "step2": STEP2, "step3": STEP3}


def _evidence_text(item: dict, doc_sentences: list[dict], evidence: Optional[str]) -> str:
    if not evidence:
        return ""
    for f in item.get("facts", []):
        if f["id"] == evidence:
            return f["text"]
    for s in doc_sentences:
        if s["id"] == evidence:
            return s["text"]
    return evidence  # 문장을 직접 적은 경우


def build_candidates(items: list[dict], doc_sentences: list[dict]) -> list[dict]:
    """문서에없음(0,빈칸) → 모호함(1,빈칸) → 추가확인권장(2,보완) → 추정 fact(1,확인,시기).
    같은 우선순위는 item score 내림차순. 해당없음 요소는 제외. 같은 (item, element)는 우선순위 높은 것 하나만."""
    cands = []
    for item in items:
        for key in ELEMENT_KEYS:
            el = item.get("elements", {}).get(key)
            if not el:
                continue
            if el["state"] == "문서에없음":
                cands.append(dict(item=item, element=key, kind="빈칸", priority=0, context={}))
            elif el["state"] == "모호함":
                cands.append(dict(item=item, element=key, kind="빈칸", priority=1, context={}))
            elif el["state"] == "있음" and el.get("fill") == "추가확인권장":
                cands.append(dict(item=item, element=key, kind="보완", priority=2,
                                  context={"evidence_text": _evidence_text(item, doc_sentences, el.get("evidence"))}))
        for f in item.get("facts", []):
            if f.get("status") == "추정":
                cands.append(dict(item=item, element="시기", kind="확인", priority=1,
                                  context={"fact_text": f["text"], "date": f.get("date")}))

    cands.sort(key=lambda c: (c["priority"], -c["item"].get("score", 0)))
    seen, out = set(), []
    for c in cands:
        k = (c["item"]["id"], c["element"])
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


def select_cards(cands: list[dict]) -> tuple[list[dict], list[dict]]:
    """후보 순서대로 rank 1~5(항목당 최대 2장), 나머지는 rank 6부터.

    kind="확인"(추정 fact) 카드는 5장 중 최대 2장. 넘치는 확인 후보는 more로 보낸다(우선순위는 그대로).
    추정 사실이 많은 실제 대화에서 카드가 전부 '시기/확인'으로 채워지는 것을 막는다.
    """
    cards, more, per_item, confirms = [], [], {}, 0
    for c in cands:
        item_id = c["item"]["id"]
        is_confirm = c["kind"] == "확인"
        if (len(cards) < CARD_COUNT and per_item.get(item_id, 0) < PER_ITEM_MAX
                and not (is_confirm and confirms >= CONFIRM_MAX)):
            per_item[item_id] = per_item.get(item_id, 0) + 1
            confirms += is_confirm
            cards.append(c)
        else:
            more.append(c)
    return cards, more


def generate_steps(c: dict) -> tuple[dict, bool, Optional[dict]]:
    """LLM으로 질문 생성. 금지어에 걸리면 재생성(총 3회), 실패 시 템플릿. 반환: (steps, fallback, error)."""
    title, element = c["item"]["title"], c["element"]
    payload = {"item": title, "element": element, "kind": c["kind"], "context": c["context"]}
    last_error = None
    for _ in range(LLM_ATTEMPTS):
        obj, error = llm.call_json("04_questions", payload, _LLMQuestion)
        if obj is None:
            last_error = error
            if error["code"] == "llm_no_key":
                break
            continue
        if has_forbidden(obj["step1"], obj["step2"], obj["step3"]):
            last_error = {"code": "forbidden_phrase",
                          "message": f"{title}/{element} 질문에 금지 표현이 있어 기본 템플릿으로 대체했습니다."}
            continue
        return obj, False, None
    return template(title, element), True, last_error


def _question(n: int, c: dict, steps: dict, rank: int, fallback: bool) -> dict:
    q = Question(id=f"q_{n:02d}", item_id=c["item"]["id"], element=c["element"], kind=c["kind"],
                 step1=steps["step1"], step2=steps["step2"], step3=steps["step3"], rank=rank,
                 fallback=True if fallback else None)
    return q.model_dump(exclude_none=True)


def generate_questions(items: list[dict], doc_sentences: list[dict]) -> tuple[list[dict], Optional[dict]]:
    """카드(상위 5)만 LLM 호출, more는 템플릿. 반환: (questions(rank 순), error)."""
    cards, more = select_cards(build_candidates(items, doc_sentences))
    with ThreadPoolExecutor(max_workers=llm.concurrency()) as pool:
        generated = list(pool.map(generate_steps, cards))

    questions, first_error = [], None
    for rank, (c, (steps, fallback, error)) in enumerate(zip(cards, generated), start=1):
        first_error = first_error or error
        questions.append(_question(len(questions) + 1, c, steps, rank, fallback))
    for rank, c in enumerate(more, start=CARD_COUNT + 1):
        questions.append(_question(len(questions) + 1, c, template(c["item"]["title"], c["element"]), rank, True))
    return questions, first_error


def split(questions: list[dict]) -> dict:
    return {"cards": [q for q in questions if 1 <= q["rank"] <= CARD_COUNT],
            "more": [q for q in questions if q["rank"] > CARD_COUNT]}
