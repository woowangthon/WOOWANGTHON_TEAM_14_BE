"""카톡 내보내기 txt -> [{"idx", "id": "chat#n", "date", "speaker", "text"}]

PC:     --------------- 2026년 4월 2일 목요일 ---------------
        [이름] [오후 7:45] 메시지
모바일: 2026년 4월 2일 오후 7:45, 이름 : 메시지
        2026. 4. 2. 오후 7:45, 이름 : 메시지
매치되지 않는 줄은 직전 메시지에 이어붙인다.
"""
import re

from app.parsers.docs import decode_text

_PC_DATE = re.compile(r"^-+\s*(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일.*?-+$")
_PC_MSG = re.compile(r"^\[(.+?)\] \[(?:오전|오후)?\s*\d{1,2}:\d{2}\] ?(.*)$")

_MO_STAMP = r"(\d{4})(?:년|\.)\s*(\d{1,2})(?:월|\.)\s*(\d{1,2})(?:일|\.)\s*(?:오전|오후)?\s*\d{1,2}:\d{2}"
_MO_MSG = re.compile(rf"^{_MO_STAMP},\s*(.+?) : ?(.*)$")
_MO_SYSTEM = re.compile(rf"^{_MO_STAMP}(,|$)")  # 시각은 있는데 "이름 : "이 없는 줄(입장·퇴장 등)
_MO_DATE = re.compile(r"^\d{4}년\s*\d{1,2}월\s*\d{1,2}일\s*\S요일$")  # 모바일 날짜 구분줄

_NOISE_EXACT = {"사진", "이모티콘", "동영상", "삭제된 메시지입니다", "삭제된 메시지입니다."}
_NOISE_PHOTOS = re.compile(r"^사진 \d+장$")
_JOIN_LEAVE = re.compile(r"님이 (들어왔습니다|나갔습니다)")


def _iso(y: str, m: str, d: str) -> str:
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


def _is_noise(text: str) -> bool:
    return (
        len(text) < 2
        or text in _NOISE_EXACT
        or bool(_NOISE_PHOTOS.match(text))
        or bool(_JOIN_LEAVE.search(text))
    )


def parse_kakao(data: bytes) -> list[dict]:
    raw: list[dict] = []  # {"date", "speaker", "text"}
    current_date = None

    for line in decode_text(data).splitlines():
        line = line.rstrip()
        if not line.strip():
            continue

        m = _PC_DATE.match(line)
        if m:
            current_date = _iso(*m.groups())
            continue

        m = _PC_MSG.match(line)
        if m:
            raw.append({"date": current_date, "speaker": m.group(1).strip(), "text": m.group(2).strip()})
            continue

        m = _MO_MSG.match(line)
        if m:
            y, mo, d, speaker, text = m.groups()
            current_date = _iso(y, mo, d)
            raw.append({"date": current_date, "speaker": speaker.strip(), "text": text.strip()})
            continue

        if _MO_SYSTEM.match(line) or _MO_DATE.match(line) or _JOIN_LEAVE.search(line):
            continue  # 시스템 줄은 메시지도 아니고 직전 메시지의 일부도 아니다

        if raw:  # 여러 줄 메시지: 직전 메시지에 이어붙임 (첫 메시지 전의 머리말은 버림)
            raw[-1]["text"] = (raw[-1]["text"] + "\n" + line.strip()).strip()

    messages = []
    for msg in raw:
        if _is_noise(msg["text"]):
            continue
        idx = len(messages) + 1
        messages.append({"idx": idx, "id": f"chat#{idx}", **msg})
    return messages
