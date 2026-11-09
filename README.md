# WOOWANGTHON_TEAM_14_BE

동아리 임원 인수인계 서비스 「빠진 페이지」의 백엔드(FastAPI).
확정 자료(회칙·공지)에서 업무 항목을 뽑고, 단톡방 대화에서 그 업무의 사실을 모아, 빈 정보를 질문 카드로 만든다.
사양 전문은 [CLAUDE.md](CLAUDE.md).

## 실행

Python 3.11 이상.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   /   macOS·Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # LLM_API_KEY를 채운다. .env는 커밋하지 않는다.
uvicorn app.main:app --reload --port 8000
```

- 확인: `GET http://localhost:8000/health` → `{"status":"ok"}`, API 문서: `http://localhost:8000/docs`
- 테스트: `pytest` (키가 없으면 실제 LLM 호출 테스트 1개만 건너뛴다)

### 환경 변수 (`.env`)

| 이름 | 기본값 | 설명 |
|---|---|---|
| `LLM_API_KEY` | (없음) | 없으면 LLM 단계가 `error: llm_no_key`로 떨어지고 응답 구조는 유지된다 |
| `LLM_MODEL` | `gpt-4o-mini` | |
| `LLM_BASE_URL` | (없음) | OpenAI 호환 엔드포인트를 쓸 때만 |
| `FE_ORIGIN` | `http://localhost:5173` | CORS 허용 출처 |
| `DB_PATH` | `sessions.db` | SQLite 파일 경로(선택) |

## 엔드포인트

| # | 메서드 | 경로 | 요청 | 응답 | 상태 |
|---|---|---|---|---|---|
| 1 | POST | `/sessions` | `{club_name, giver, receiver}` | `{session_id}` | ✅ |
| 2 | POST | `/sessions/{id}/docs` | multipart `file`(PDF/TXT) | `{items: Item[], error?}` (facts 비어 있음) | ✅ |
| 3 | PATCH | `/sessions/{id}/items` | `{items:[{id, title?, keywords?, merge_into?, delete?}]}` | `{items}` | ✅ |
| 4 | POST | `/sessions/{id}/chat` | multipart `file`(txt) | `{items: Item[], chat_count, error?}` (facts·elements·score 채움, 정렬됨) | ⚠️ facts까지. elements·score·정렬은 예정 |
| 5 | PATCH | `/sessions/{id}/items/{item_id}/elements/{key}` | `{state:"해당없음", closed_by}` | `{item}` | 예정 |
| 6 | GET | `/sessions/{id}/questions` | — | `{cards: Question[≤5], more: Question[]}` (최초 호출 시 생성·저장) | 예정 |
| 7 | POST | `/sessions/{id}/answers` | `{answers: Answer[]}` | `{annotations: Annotation[]}` (draft만) | 예정 |
| 8 | PATCH | `/sessions/{id}/annotations/{ann_id}` | `{status?, text?, label?}` | `{annotation}` (text 변경 시 status=edited) | 예정 |
| 9 | POST | `/sessions/{id}/publish` | — | `{document:{items:[{id,title,elements,facts,annotations(approved/edited만)}], notice}}` | 예정 |
| 10 | POST | `/sessions/{id}/ask` | `{question}` | `{hits:[{annotation_id,text,source,label,item,score}], summary: null, none: bool}` | 예정 |
| 11 | POST | `/sessions/{id}/ask/forward` | `{question}` | `{forwarded: true}` | 예정 |

- 에러는 항상 응답 본문에 `{"error": {"code": str, "message": str}}`로 담긴다. HTTP 상태는 200을 유지하고, 세션이 없을 때만 404다.
- 값이 없는 선택 필드(`date`, `label`, `fill` 등)는 `null`이 아니라 **키 자체가 빠진다**.
- id 형식: 항목 `item_01`, 사실 `fact_001`, 문서 문장 `doc#1`, 대화 메시지 `chat#1`.

### 호출 예

```bash
curl -X POST localhost:8000/sessions -H "Content-Type: application/json" \
  -d '{"club_name":"테니스 동아리","giver":"민서","receiver":"수아"}'
curl -X POST localhost:8000/sessions/{id}/docs -F "file=@fixtures/rules.txt"
curl -X POST localhost:8000/sessions/{id}/chat -F "file=@fixtures/kakao.txt"
```

## 구조

```
app/
  main.py            FastAPI 앱, CORS, /health, 공통 에러 응답
  models.py          API 타입
  db.py              세션 1개 = JSON 1행 (SQLite)
  llm.py             call_json: 모든 LLM 호출의 단일 통로(JSON 강제, 스키마 검증, 1회 재시도)
  parsers/           docs.py(PDF/TXT → 문장), kakao.py(카톡 PC·모바일 → 메시지)
  services/          items.py, facts.py (elements·questions·annotations·publish·qa는 예정)
  routers/sessions.py
prompts/             LLM 프롬프트 5개 (문구는 팀 문서와 맞춘 것이므로 수정 금지)
fixtures/            임시 테스트 자료 rules.txt, kakao.txt
tests/test_flow.py
```
