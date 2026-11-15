"""확정 자료(PDF/TXT) -> [{"id": "doc#1", "text": ...}]"""
import io
import re

from pypdf import PdfReader

# 한글(또는 닫는 괄호) 뒤 마침표 + 공백에서 문장을 나눈다. "2026.03", "3/3~3/7" 같은 숫자 사이 마침표는 건드리지 않는다.
_SENT_END = re.compile(r"(?<=[가-힣\)][.!?])\s+")
# PDF는 줄바꿈이 문장 경계가 아니므로, 조항·공지 머리말 앞에서 한 번 더 나눈다.
_HEADING = re.compile(r"\s+(?=제\s?\d+\s?조|공지\s?\()")


def decode_text(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):  # UTF-16은 BOM이 있을 때만 (없으면 cp949가 오인식됨)
        return data.decode("utf-16", errors="replace")
    for enc in ("utf-8-sig", "cp949"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return data.decode("utf-8", errors="replace")


def _split(block: str) -> list[str]:
    return [s.strip() for s in _SENT_END.split(block) if s.strip()]


def parse_doc(filename: str, data: bytes) -> list[dict]:
    """지원하지 않는 형식이거나 텍스트가 없으면 ValueError."""
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(data))
        text = " ".join((page.extract_text() or "") for page in reader.pages)
        text = re.sub(r"\s+", " ", text)
        blocks = _HEADING.split(text)
    elif name.endswith(".txt"):
        blocks = decode_text(data).splitlines()
    else:
        raise ValueError("PDF 또는 TXT 파일만 올릴 수 있습니다.")

    sentences = [s for block in blocks for s in _split(block) if len(s) >= 2]
    if not sentences:
        raise ValueError("문서에서 텍스트를 찾지 못했습니다(스캔본 PDF는 지원하지 않습니다).")
    return [{"id": f"doc#{i}", "text": s} for i, s in enumerate(sentences, start=1)]


def parse_docs(files: list[tuple[str, bytes]]) -> tuple[list[dict], list[str]]:
    """여러 파일을 올린 순서대로 읽어 문장을 이어 붙이고 doc#1부터 다시 번호를 매긴다.

    반환: (sentences, problems). 읽지 못한 파일은 건너뛰고 problems에 "파일명: 이유"로 남긴다.
    """
    texts: list[str] = []
    problems: list[str] = []
    for filename, data in files:
        try:
            texts += [s["text"] for s in parse_doc(filename, data)]
        except ValueError as e:
            problems.append(f"{filename or '(이름 없음)'}: {e}")
        except Exception as e:  # noqa: BLE001 — 깨진 PDF 등
            problems.append(f"{filename or '(이름 없음)'}: 문서를 읽지 못했습니다({type(e).__name__})")
    return [{"id": f"doc#{i}", "text": t} for i, t in enumerate(texts, start=1)], problems
