"""후임 Q&A: 발행된 승인 주석만 키워드 검색한다(CLAUDE.md §6). 생성 답변·요약·임베딩 없음."""
import re
from datetime import datetime, timezone

STOPWORDS = set("은 는 이 가 을 를 의 에 에서 로 으로 와 과 도 요 어떻게 해요 하나요 뭐 어떤 언제 누구".split())
_PUNCT = re.compile(r"[^\w\s]")  # 구두점 제거 (\w는 한글 포함)
TOP_K = 3


def tokenize(text: str) -> set[str]:
    """구두점 제거 → 공백 토큰화 → 조사·불용어 제거 → 길이 1 토큰 제거."""
    return {t for t in _PUNCT.sub(" ", text or "").split() if t not in STOPWORDS and len(t) > 1}


def search(published: dict, question: str, top_k: int = TOP_K) -> list[dict]:
    """주석 text + 슬롯 값 + 항목 제목과 질문의 토큰 교집합 크기로 점수. 점수 ≥ 1 상위 top_k."""
    q_tokens = tokenize(question)
    hits = []
    for item in published.get("items", []):
        for a in item.get("annotations", []):
            parts = [a["text"], item["title"], *[v for v in (a.get("slots") or {}).values() if v]]
            score = len(q_tokens & tokenize(" ".join(parts)))
            if score >= 1:
                hits.append({
                    "annotation_id": a["id"],
                    "text": a["text"],
                    "source": a["source"],
                    "label": a["label"],
                    "item": item["title"],
                    "score": score,
                })
    hits.sort(key=lambda h: -h["score"])  # 동점은 문서 순서 유지
    return hits[:top_k]


def forward(data: dict, question: str) -> dict:
    """답을 찾지 못한 질문을 전임 질문함(forwarded)에 쌓는다."""
    entry = {"question": question.strip(), "asked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    data["forwarded"].append(entry)
    return entry
