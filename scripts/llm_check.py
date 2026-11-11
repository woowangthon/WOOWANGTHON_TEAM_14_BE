"""LLM 연결 진단. .env의 설정으로 가장 단순한 호출 2번(일반 / JSON 모드)을 보내고 원문 오류를 그대로 보여준다.
사용법: python scripts/llm_check.py
키 값은 출력하지 않는다(길이·접두 4자리만)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import llm  # noqa: E402  (.env 로드)
from openai import OpenAI  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

key, model, base = llm.api_key(), llm.model_name(), llm.base_url()
print(f"model     = {model}")
print(f"base_url  = {base or '(없음 → OpenAI 기본)'}")
print(f"api_key   = {'없음' if not key else f'{key[:4]}… (길이 {len(key)})'}")
if not key:
    sys.exit(".env에 LLM_API_KEY가 없습니다.")

client = OpenAI(api_key=key, base_url=base, timeout=60, max_retries=0)

# 0. 이 키로 쓸 수 있는 모델 목록 (flash/lite/mini 포함된 것만)
print("\n[0. 사용 가능한 모델]")
try:
    ids = sorted(m.id for m in client.models.list().data)
    pick = [i for i in ids if any(k in i.lower() for k in ("flash", "lite", "mini", "llama", "gpt"))]
    for i in pick[:40]:
        print("  ", i.replace("models/", ""))
    if len(ids) > len(pick):
        print(f"   … 외 {len(ids) - len(pick)}개")
except Exception as e:
    print("  목록 조회 실패:", type(e).__name__, str(e)[:200])


def attempt(label, **kw):
    print(f"\n[{label}]")
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": "JSON으로만 답한다."},
                      {"role": "user", "content": '{"q":"1+1은?"} → {"answer": 숫자} 형태로'}],
            temperature=0, **kw)
        print("OK:", (r.choices[0].message.content or "")[:200])
        return True
    except Exception as e:
        print(f"실패: {type(e).__name__}: {e}")
        resp = getattr(e, "response", None)
        if resp is not None:
            print("HTTP", resp.status_code)
            print("본문:", resp.text[:800])
        body = getattr(e, "body", None)
        if body:
            print("body:", json.dumps(body, ensure_ascii=False)[:800])
        return False


a = attempt("1. 일반 호출")
b = attempt("2. JSON 모드 (response_format json_object)", response_format={"type": "json_object"})
print("\n진단:")
if a and b:
    print("  둘 다 OK → 설정 정상. 400은 다른 원인(프롬프트 길이 등). smoke 다시 돌려보기.")
elif a and not b:
    print("  JSON 모드만 실패 → 이 모델/엔드포인트가 response_format을 지원하지 않음. 모델을 바꾸거나 Claude Code에 'json_object 미지원 시 프롬프트에 JSON 지시만으로 대체' 요청.")
elif not a:
    print("  일반 호출도 실패 → 모델 이름 또는 base_url 또는 키 문제. 위 '본문'의 메시지를 그대로 Claude에게 보여주기.")
