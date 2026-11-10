"""LLM 호출은 전부 call_json 하나를 거친다.

JSON 강제 → pydantic 스키마 검증 → 실패 시 retries회 재시도 → 그래도 실패면 (None, error).
예외를 밖으로 던지지 않는다(라우터가 500을 내지 않게).
"""
import json
import os
from pathlib import Path
from typing import Optional, Type

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, ValidationError

BASE_DIR = Path(__file__).resolve().parent.parent
PROMPTS_DIR = BASE_DIR / "prompts"
load_dotenv(BASE_DIR / ".env")

TIMEOUT_SECONDS = 60
PROMPT_NAMES = ("01_items", "02_facts", "03_elements", "04_questions", "05_annotations")


def prompt_version() -> str:
    """prompts/ 5개 파일 sha256 앞 8자리를 이어 붙인 값. 골드셋 비교 때 프롬프트가 바뀌었는지 확인하는 용도."""
    import hashlib
    parts = []
    for name in PROMPT_NAMES:
        path = PROMPTS_DIR / f"{name}.md"
        parts.append(hashlib.sha256(path.read_bytes()).hexdigest()[:8] if path.exists() else "missing_")
    return "".join(parts)


def _env(*names: str, default: str = "") -> str:
    """여러 이름 중 먼저 값이 있는 것. 정식 이름은 CLAUDE.md §2의 LLM_*; OPENAI_*/MODEL_NAME은 호환용 별칭."""
    for n in names:
        v = (os.getenv(n) or "").strip()
        if v:
            return v
    return default


def api_key() -> str:
    return _env("LLM_API_KEY", "OPENAI_API_KEY")


def model_name() -> str:
    return _env("LLM_MODEL", "MODEL_NAME", default="gpt-4o-mini")


def base_url() -> Optional[str]:
    return _env("LLM_BASE_URL", "OPENAI_BASE_URL") or None


def _error(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def _strip_fence(text: str) -> str:
    """모델이 ```json ... ``` 로 감싸서 돌려주는 경우를 벗긴다."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def call_json(
    prompt_name: str,
    payload: dict,
    schema: Type[BaseModel],
    retries: int = 1,
) -> tuple[Optional[dict], Optional[dict]]:
    """prompts/{prompt_name}.md를 system으로, payload를 user JSON으로 보내고 schema로 검증한 dict를 돌려준다.

    반환: (obj, None) 또는 (None, {"code", "message"}).
    """
    key = api_key()
    if not key:
        return None, _error("llm_no_key", "LLM_API_KEY가 설정되지 않았습니다(.env 확인).")

    try:
        system = (PROMPTS_DIR / f"{prompt_name}.md").read_text(encoding="utf-8")
    except OSError as e:
        return None, _error("llm_prompt_missing", f"프롬프트 파일을 읽지 못했습니다: {prompt_name} ({e})")

    model = model_name()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]

    last = _error("llm_failed", "LLM 호출에 실패했습니다.")
    for _ in range(1 + max(0, retries)):
        try:
            client = OpenAI(api_key=key, base_url=base_url(), timeout=TIMEOUT_SECONDS, max_retries=0)
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0,
            )
            raw = resp.choices[0].message.content or ""
        except Exception as e:  # 네트워크·인증·한도 등 SDK 예외 전부
            last = _error("llm_call_failed", f"{type(e).__name__}: {e}"[:300])
            continue
        try:
            obj = schema.model_validate(json.loads(_strip_fence(raw)))
        except (json.JSONDecodeError, ValidationError) as e:
            last = _error("llm_schema_invalid", f"{prompt_name} 응답이 스키마와 맞지 않습니다: {e}"[:300])
            continue
        return obj.model_dump(), None
    return None, last
