"""세션 1개 = JSON 1행. sessions(id TEXT PK, data TEXT)."""
import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent


def _connect() -> sqlite3.Connection:
    # 경로는 호출 시점에 읽는다(테스트에서 DB_PATH를 바꿀 수 있게). 기본 ./data/app.db, 디렉터리가 없으면 만든다.
    path = Path(os.getenv("DB_PATH") or (BASE_DIR / "data" / "app.db"))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
    return conn


def create_session(data: dict) -> str:
    session_id = uuid.uuid4().hex[:12]
    conn = _connect()
    try:
        with conn:
            conn.execute(
                "INSERT INTO sessions (id, data) VALUES (?, ?)",
                (session_id, json.dumps(data, ensure_ascii=False)),
            )
    finally:
        conn.close()
    return session_id


def get_session(session_id: str) -> Optional[dict]:
    conn = _connect()
    try:
        row = conn.execute("SELECT data FROM sessions WHERE id = ?", (session_id,)).fetchone()
    finally:
        conn.close()
    return json.loads(row[0]) if row else None


def save_session(session_id: str, data: dict) -> None:
    conn = _connect()
    try:
        with conn:
            conn.execute(
                "UPDATE sessions SET data = ? WHERE id = ?",
                (json.dumps(data, ensure_ascii=False), session_id),
            )
    finally:
        conn.close()
