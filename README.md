# WOOWANGTHON_TEAM_14_BE — 「빠진 페이지」 백엔드

동아리 임원 인수인계 서비스의 백엔드(FastAPI). 확정 자료(회칙·공지 PDF/TXT)에서 업무 항목을 뽑고, 단톡방 대화에서 그 업무의 사실을 모은 뒤, 항목마다 **업무 수행 정보 6요소**(시기·연락경로·판단기준·자주틀리는점·미리할일·배경) 중 빈 곳을 판정해 **질문 카드 5장**을 만든다. 전임이 답하면 주석으로 정리해 승인받고, 후임의 질문에는 **승인된 기록을 그대로** 찾아 보여준다.

핵심 원칙(코드로 강제): AI는 "자료에 없다"까지만 말하고 "해당 없음"은 사람만 정한다 · 출처 없는 사실·주석은 저장하지 않는다 · 모든 LLM 호출은 `llm.call_json` 하나를 거친다 · 라우터는 500을 내지 않는다 · 후임 Q&A는 승인 주석 검색만(생성 답변 없음).

사양 전문은 [CLAUDE.md](CLAUDE.md). 이 저장소는 BE 전용이므로 사양의 `be/`는 저장소 루트다.

## 실행

Python 3.11 이상.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   /   macOS·Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # OPENAI_API_KEY를 채운다. .env는 커밋하지 않는다.
uvicorn app.main:app --reload --port 8000
```

- 확인: `GET http://localhost:8000/health` → `{"ok": true, "version": "<prompt_version>", ...}`, API 문서 `http://localhost:8000/docs`
- 테스트(키 없이, CI 기본): `pytest` — LLM은 가짜 응답과 error 경로로 검사한다.
- 테스트(실제 LLM): `pytest -m llm` — `.env`에 키가 있을 때만 돈다.
- 측정용 덤프(B의 골드셋 비교용):
  ```bash
  python scripts/dump.py fixtures/rules.txt fixtures/kakao.txt -o out/fixtures.json
  python scripts/dump.py "fixtures/set*" -o out/sets.json     # 세트 디렉터리 일괄(rules.txt|pdf + kakao.txt)
  ```
  API를 거치지 않고 services를 직접 불러 items(keywords) → facts(source id) → elements(evidence)/score → cards+more를 한 JSON에 담는다. 맨 위에 `prompt_version`(prompts/ 5개 sha256 앞 8자리 연결)과 `model`을 기록하고, 실패한 세트는 `error`를 남기고 계속 간다.

### 환경 변수 (`.env`, `.env.example` 참고)

| 이름 | 기본값 | 설명 |
|---|---|---|
| `OPENAI_API_KEY` | (없음) | 없으면 LLM 단계가 `error: llm_no_key`로 떨어지고 응답 구조는 유지된다 |
| `OPENAI_BASE_URL` | (없음) | OpenAI 호환 엔드포인트를 쓸 때만 |
| `MODEL_NAME` | `gpt-4o-mini` | |
| `FRONTEND_ORIGINS` | `http://localhost:5173` | CORS 허용 출처, 쉼표 구분. `*`는 무시 |
| `DB_PATH` | `./data/app.db` | SQLite 경로. 디렉터리가 없으면 만든다 |

(옛 이름 `LLM_API_KEY`·`LLM_BASE_URL`·`LLM_MODEL`·`FE_ORIGIN`도 그대로 읽힌다.)

## 엔드포인트

흐름: 1 → 2 → (3) → 4 → (5) → 6 → 7 → 8 → 9 → 10 / 11

| # | 메서드 | 경로 | 요청 | 응답 |
|---|---|---|---|---|
| 1 | POST | `/sessions` | `{club_name, giver, receiver}` | `{session_id}` |
| 2 | POST | `/sessions/{id}/docs` | multipart `file`(PDF/TXT) | `{items: Item[], error?}` (facts 비어 있음) |
| 3 | PATCH | `/sessions/{id}/items` | `{items:[{id, title?, keywords?, merge_into?, delete?}]}` | `{items, error?}` |
| 4 | POST | `/sessions/{id}/chat` | multipart `file`(카톡 txt) | `{items: Item[], chat_count, error?}` (facts·elements·score 채움, score 내림차순 정렬) |
| 5 | PATCH | `/sessions/{id}/items/{item_id}/elements/{key}` | `{state:"해당없음", closed_by:"전임"\|"후임"}` | `{item, error?}` (score 재계산) |
| 6 | GET | `/sessions/{id}/questions` | — | `{cards: Question[≤5], more: Question[], error?}` (최초 호출 시 생성·저장) |
| 7 | POST | `/sessions/{id}/answers` | `{answers: Answer[]}` | `{annotations: Annotation[](draft만), skipped: question_id[], error?}` |
| 8 | PATCH | `/sessions/{id}/annotations/{ann_id}` | `{status?, text?, label?}` | `{annotation, error?}` (text 변경 시 status=edited) |
| 9 | POST | `/sessions/{id}/publish` | — | `{document:{items:[{id,title,elements,facts,annotations(approved/edited만)}], notice}}` |
| 10 | POST | `/sessions/{id}/ask` | `{question}` | `{hits:[{annotation_id,text,source,label,item,score}](≤3), summary: null, none: bool, error?}` |
| 11 | POST | `/sessions/{id}/ask/forward` | `{question}` | `{forwarded: true}` |
| + | GET | `/sessions/{id}/forwarded` | — | `{forwarded:[{question, asked_at}]}` — **계약 외 추가**(전임 질문함 화면용) |

규칙·세부:
- 에러는 항상 본문의 `{"error": {"code": str, "message": str}}`. HTTP는 200 유지, **세션 없음만 404**. 예상 못 한 예외도 `code: "INTERNAL"`로 200.
- 주요 error code: `llm_no_key`, `llm_call_failed`, `llm_schema_invalid`, `doc_parse_failed`, `no_file`, `no_items`, `item_not_found`, `invalid_element_key`, `annotation_not_found`, `answers_partial`, `forbidden_phrase`, `NOT_PUBLISHED`, `invalid_request`, `INTERNAL`.
- 값이 없는 선택 필드(`date`, `label`, `fill`, `evidence`, `closed_by`, `fallback`)는 `null`이 아니라 **키가 빠진다**. 단 `slots`의 빈 슬롯은 `null`로 들어간다.
- id 형식: 항목 `item_01`, 사실 `fact_001`, 질문 `q_01`, 주석 `an_001`(전역 증가, 재사용 없음), 문서 문장 `doc#1`, 대화 메시지 `chat#1`.
- `Question.fallback?: true` — **optional 추가 필드**. LLM 생성에 실패했거나 금지어가 남아 기본 템플릿으로 채운 질문. `more`는 전부 템플릿.
- API 4를 다시 올리면 facts·elements를 다시 만든다. 사람이 닫은 `해당없음`은 유지된다.
- API 7: 답변이 10자 미만이면 LLM을 부르지 않고 `skipped`에 넣는다(error 아님). 한 답변에 요소가 섞여 있으면 주석 2~3개로 나뉜다.
- API 9: 승인 주석이 0개면 error 없이 빈 배열로 발행하고 `notice` 뒤에 `"승인된 기록이 없습니다."` 한 줄을 붙인다. `notice` 고정 문구: "이 문서는 전임의 경험을 정리한 것이며 회칙·규정이 우선합니다."
- API 10: 발행 전이면 `error.code = "NOT_PUBLISHED"`(hits 빈 배열, none true). `summary`는 항상 `null`.

### 호출 예

```bash
curl -X POST localhost:8000/sessions -H "Content-Type: application/json" -d '{"club_name":"테니스 동아리","giver":"민서","receiver":"수아"}'
curl -X POST localhost:8000/sessions/{id}/docs -F "file=@fixtures/rules.txt"
curl -X POST localhost:8000/sessions/{id}/chat -F "file=@fixtures/kakao.txt"
curl localhost:8000/sessions/{id}/questions
curl -X POST localhost:8000/sessions/{id}/answers -H "Content-Type: application/json" -d '{"answers":[{"question_id":"q_01","text":"매월 25일까지 계좌로 받고 미납자는 개별로 연락합니다."}]}'
curl -X PATCH localhost:8000/sessions/{id}/annotations/an_001 -H "Content-Type: application/json" -d '{"status":"approved"}'
curl -X POST localhost:8000/sessions/{id}/publish
curl -X POST localhost:8000/sessions/{id}/ask -H "Content-Type: application/json" -d '{"question":"회비 미납자는 어떻게 해요?"}'
```

## 직접 구현 / 외부 AI 구분

| 직접 구현(결정적, 코드) | 외부 AI(LLM, `prompts/`) |
|---|---|
| PDF/TXT 문장 분리 (`parsers/docs.py`) | 01 항목 추출: 문서 → 업무 항목 제목·키워드·근거 문장 id |
| 카톡 PC/모바일 파싱·노이즈 제거 (`parsers/kakao.py`) | 02 사실 추출: 키워드로 거른 메시지 → 한 줄 사실 + 출처 메시지 id |
| 키워드 필터(앞뒤 2개), 출처 id 검증·폐기 (`services/facts.py`) | 03 공백 판정: 6요소 있음/문서에없음/모호함 + 확인됨/추가확인권장 |
| 요소 후처리(빠진 키=모호함, 해당없음 금지), score, 정렬 (`services/elements.py`) | 04 질문 문장: 요소·종류별 3단계 질문(사실→예외→사례) |
| 후보 순서, 카드 5장/항목당 2장, 금지어 정규식 5개·재생성·템플릿 (`services/questions.py`) | 05 답변 슬롯: 전임 답변 → 절차·예외·사례·연락·조건 + 한두 문장 요약 |
| 주석 id·출처 부여, 발행 필터(approved/edited) (`services/annotations.py`, `publish.py`) | |
| Q&A 토큰 교집합 검색, 불용어 제거 (`services/qa.py`) | |

프롬프트 5개(`prompts/01~05.md`)의 문구는 팀 문서·검증 계획과 맞춘 것이므로 **수정하지 않는다**. `/health`의 `version`이 그 파일들의 해시다.

## Render 배포 순서

1. Render → **New Web Service** → 이 저장소(`WOOWANGTHON_TEAM_14_BE`) 선택.
2. **Root Directory는 비워 둔다**(이 저장소는 BE 전용이라 `be/` 폴더가 없다. `render.yaml`도 루트에 있다).
3. `render.yaml`이 자동 인식되는지 확인(Blueprint). 수동이면 Build `pip install -r requirements.txt`, Start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, Health Check Path `/health`.
4. 환경변수 입력: `OPENAI_API_KEY`(필수), `MODEL_NAME`, `OPENAI_BASE_URL`(비워도 됨), `FRONTEND_ORIGINS`, `DB_PATH`.
5. 배포 후 `https://<서비스>.onrender.com/health`가 `{"ok": true, "version": ...}`를 돌려주는지 확인.
6. 프론트가 Vercel에 올라가면 `FRONTEND_ORIGINS`에 그 도메인을 쉼표로 추가(`https://xxx.vercel.app,http://localhost:5173`)하고 재배포.
7. 슬립 방지: cron-job.org 등에서 **10분마다 `/health`를 핑**(무료 플랜은 15분 유휴 시 잠듦).

## 구조

```
app/
  main.py            FastAPI 앱, CORS(FRONTEND_ORIGINS), /health, 공통 에러 응답
  models.py          API 타입(§3) + 요청 본문
  db.py              세션 1개 = JSON 1행 (SQLite sessions(id, data))
  llm.py             call_json: 모든 LLM 호출의 단일 통로(JSON 강제, 스키마 검증, 1회 재시도, 실패 시 (None, error)), prompt_version
  parsers/           docs.py(PDF/TXT → 문장), kakao.py(카톡 PC·모바일 → 메시지)
  services/          items, facts, elements, questions, annotations, publish, qa
  routers/sessions.py  엔드포인트 11개 + GET /forwarded, 공통 예외 가드
prompts/             LLM 프롬프트 5개 (수정 금지)
fixtures/            임시 테스트 자료 rules.txt, kakao.txt (B의 세트는 fixtures/set1~5/)
scripts/dump.py      측정용 덤프
tests/test_flow.py   통합 흐름(가짜 LLM / 키 없음 / 실제 LLM -m llm) + 파서·필터·검색 단위 테스트
render.yaml          Render Blueprint
```

## 알려진 한계

- `summary`는 항상 `null`(AI 요약 미구현, 범위 밖). 임베딩 검색 없음 — Q&A는 **토큰 완전 일치** 교집합이라 "회비"와 "회비를"은 다른 토큰이다.
- 세션 1개가 JSON 1행이라 동시 수정에 약하다(해커톤 범위). Render 무료 플랜은 재배포 시 디스크가 초기화되어 SQLite가 비워진다.
- 인증 없음: `session_id`를 아는 사람은 누구나 읽고 쓸 수 있다.
- 다중 파일 업로드·HWP·음성 STT 없음. 스캔본 PDF(텍스트 없는 PDF)는 `doc_parse_failed`.
- 기능 동결 11/13 이후에는 버그 수정과 에러 응답 통일만 한다.
