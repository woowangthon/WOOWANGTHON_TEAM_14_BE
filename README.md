# WOOWANGTHON_TEAM_14_BE — 「빠진 페이지」 백엔드

동아리 임원 인수인계 서비스의 백엔드(FastAPI). 확정 자료(회칙·공지 PDF/TXT)에서 업무 항목을 뽑고, 단톡방 대화에서 그 업무의 사실을 모은 뒤, 항목마다 **업무 수행 정보 6요소**(시기·연락경로·판단기준·자주틀리는점·미리할일·배경) 중 빈 곳을 판정해 **질문 카드 5장**을 만든다. 전임이 답하면 주석으로 정리해 승인받고, 후임의 질문에는 **승인된 기록을 그대로** 찾아 보여준다.

핵심 원칙(코드로 강제): AI는 "자료에 없다"까지만 말하고 "해당 없음"은 사람만 정한다 · 출처 없는 사실·주석은 저장하지 않는다 · 모든 LLM 호출은 `llm.call_json` 하나를 거친다 · 라우터는 500을 내지 않는다 · 후임 Q&A는 승인 주석 검색만(생성 답변 없음).

사양 전문은 [CLAUDE.md](CLAUDE.md). 이 저장소는 BE 전용이므로 사양의 `be/`는 **저장소 루트**다.

## 실행

Python 3.11 이상.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   /   macOS·Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # LLM_API_KEY를 채운다. .env는 커밋하지 않는다.
uvicorn app.main:app --reload --port 8000
```

- 확인: `GET http://localhost:8000/health` → `{"ok": true, "prompt_version": "<40자>", "model": "..."}`, API 문서 `http://localhost:8000/docs`
- **pytest**(키 없이, CI 기본): `pytest` — LLM은 가짜 응답과 error 경로로 검사. 실제 LLM: `pytest -m llm`(키 있을 때만).
- **smoke**(사람이 보는 끝-끝 확인, 서버가 떠 있어야 함):
  ```bash
  python scripts/smoke.py --base http://localhost:8000 --rules fixtures/demo/rules.txt --kakao fixtures/demo/kakao.txt --save out/smoke_demo.json
  ```
  API 1→11(+forwarded)을 실제로 호출해 단계별 OK/WARN/FAIL과 §7 완료 기준 ✔/✖를 출력한다. `--base`에 배포 URL을 넣으면 배포 확인용. 응답에 `error`가 있으면 WARN으로 계속, 500·연결 실패면 FAIL로 멈춘다.
- **dump**(B의 골드셋 비교용, API 없이 services 직접 호출):
  ```bash
  python scripts/dump.py fixtures/demo -o out/demo.json
  python scripts/dump.py "fixtures/set*" -o out/sets.json     # 세트 디렉터리 일괄
  ```
  items(keywords) → facts(source id) → elements(evidence)/score → cards+more를 한 JSON에. 맨 위에 `prompt_version`과 `model`. 실패 세트는 `error`를 남기고 계속.

### 환경 변수 (`.env`, `.env.example` 참고)

| 이름 | 기본값 | 설명 |
|---|---|---|
| `LLM_API_KEY` | (없음) | 없으면 LLM 단계가 `error: llm_no_key`로 떨어지고 응답 구조는 유지된다 |
| `LLM_MODEL` | `gpt-4o-mini` | |
| `LLM_BASE_URL` | (없음) | OpenAI 호환 엔드포인트를 쓸 때만 |
| `LLM_CONCURRENCY` | `4` | LLM 동시 호출 수. 무료 티어는 `1` 권장(429 방지). 429·503·타임아웃은 2→4→8초 대기 후 최대 3회 자동 재시도 |
| `FE_ORIGIN` | `http://localhost:5173` | CORS 허용 출처, 쉼표로 여러 개. `*`는 무시 |
| `DB_PATH` | `./data/app.db` | SQLite 경로. 디렉터리가 없으면 만든다 |

(별칭 `OPENAI_API_KEY`·`OPENAI_BASE_URL`·`MODEL_NAME`·`FRONTEND_ORIGINS`도 읽히지만 정식 이름은 위의 것.)

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
| + | GET | `/sessions/{id}/forwarded` | — | `{questions:[{question, asked_at}]}` — **계약 외 추가**(전임 질문함 화면용) |

규칙·세부:
- 에러는 항상 본문의 `{"error": {"code": str, "message": str}}`. HTTP는 200 유지, **세션 없음만 404**. 예상 못 한 예외도 `code: "INTERNAL"`로 200.
- 주요 error code: `llm_no_key`, `llm_call_failed`, `llm_schema_invalid`, `doc_parse_failed`, `no_file`, `no_items`, `item_not_found`, `invalid_element_key`, `annotation_not_found`, `answers_partial`, `forbidden_phrase`, `NOT_PUBLISHED`, `invalid_request`, `INTERNAL`.
- 값이 없는 선택 필드(`date`, `label`, `fill`, `evidence`, `closed_by`, `fallback`)는 `null`이 아니라 **키가 빠진다**. 단 `slots`의 빈 슬롯은 `null`로 들어간다.
- id 형식: 항목 `item_01`, 사실 `fact_001`, 질문 `q_01`, 주석 `an_001`(전역 증가, 재사용 없음), 문서 문장 `doc#1`, 대화 메시지 `chat#1`.
- **optional 추가 필드(B 전달)**: `Question.fallback?: true`(LLM 실패·금지어로 기본 템플릿을 쓴 질문; `more`는 전부 템플릿), API 7 응답의 `skipped`.
- API 4를 다시 올리면 facts·elements를 다시 만든다. 사람이 닫은 `해당없음`은 유지된다.
- API 7: 답변이 10자 미만이면 LLM을 부르지 않고 `skipped`에 넣는다(error 아님). 한 답변에 요소가 섞여 있으면 주석 2~3개로 나뉜다.
- API 9: 승인 주석이 0개면 error 없이 빈 배열로 발행하고 `notice` 뒤에 `"승인된 기록이 없습니다."` 한 줄을 붙인다. `notice` 고정 문구: "이 문서는 전임의 경험을 정리한 것이며 회칙·규정이 우선합니다."
- API 10: 발행 전이면 `error.code = "NOT_PUBLISHED"`(hits 빈 배열, none true). `summary`는 항상 `null`.

### 호출 예

```bash
curl -X POST localhost:8000/sessions -H "Content-Type: application/json" -d '{"club_name":"테니스 동아리","giver":"민서","receiver":"수아"}'
curl -X POST localhost:8000/sessions/{id}/docs -F "file=@fixtures/demo/rules.txt"
curl -X POST localhost:8000/sessions/{id}/chat -F "file=@fixtures/demo/kakao.txt"
curl localhost:8000/sessions/{id}/questions
curl -X POST localhost:8000/sessions/{id}/answers -H "Content-Type: application/json" -d '{"answers":[{"question_id":"q_01","text":"매월 25일까지 계좌로 받고 미납자는 개별로 연락합니다."}]}'
curl -X PATCH localhost:8000/sessions/{id}/annotations/an_001 -H "Content-Type: application/json" -d '{"status":"approved"}'
curl -X POST localhost:8000/sessions/{id}/publish
curl -X POST localhost:8000/sessions/{id}/ask -H "Content-Type: application/json" -d '{"question":"회비 미납자는 어떻게 해요?"}'
```

## 프롬프트 5개 (`prompts/`, 문구 수정 금지)

| 파일 | 한 줄 |
|---|---|
| `01_items.md` | 문서 문장 → 업무 항목 4~7개(제목·구어체 포함 키워드 3~8개·근거 문장 id) |
| `02_facts.md` | 키워드로 거른 메시지 → 업무 사실 한 줄씩 + 출처 메시지 id, 날짜 짐작이면 `date_estimated` |
| `03_elements.md` | 항목의 facts+문서 → 6요소 있음/문서에없음/모호함 + 확인됨/추가확인권장 (해당없음은 절대 출력 금지) |
| `04_questions.md` | 요소·종류(빈칸/보완/확인)별 3단계 질문(사실→예외→사례), 부정 전제·선택지·편법 금지 |
| `05_annotations.md` | 전임 답변 → 절차·예외·사례·연락·조건 슬롯 + 한두 문장 요약, 답변에 없는 말 금지 |

`/health`의 `prompt_version`은 이 5개 파일 sha256 앞 8자리를 이어 붙인 40자다. 바뀌면 골드셋과 비교할 때 알 수 있다.

## 직접 구현 / 외부 AI 구분

| 직접 구현(결정적, 코드) | 외부 AI(LLM) |
|---|---|
| PDF/TXT 문장 분리 (`parsers/docs.py`) | 항목 추출(01) |
| 카톡 PC/모바일 파싱·노이즈 제거 (`parsers/kakao.py`) | 사실 추출(02) |
| 키워드 필터(앞뒤 2개) (`services/facts.py`) | 공백 판정(03) |
| **출처 검증**: 넘기지 않은 메시지 id 제거, 출처 0개 fact 폐기 (`services/facts.py`) | 질문 문장(04) |
| 요소 후처리(빠진 키=모호함, 해당없음 금지), **점수**, 정렬 (`services/elements.py`) | 답변 슬롯(05) |
| 후보 순서, **카드 선정**(5장/항목당 2장), **금지어 필터**(정규식 5개)·재생성·템플릿 (`services/questions.py`) | |
| 주석 id·출처 부여, 발행 필터(approved/edited) (`services/annotations.py`, `publish.py`) | |
| **Q&A 검색**: 토큰 교집합, 불용어 제거 (`services/qa.py`) | |

## Render 배포 순서

1. Render → **New Web Service** → 이 저장소(`WOOWANGTHON_TEAM_14_BE`) 선택.
2. **Root Directory는 비워 둔다**(이 저장소는 BE 전용이라 `be/` 폴더가 없다. `render.yaml`도 루트에 있다).
3. `render.yaml`이 자동 인식되는지 확인(Blueprint). 수동이면 Build `pip install -r requirements.txt`, Start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, Health Check Path `/health`.
4. 환경변수 입력: `LLM_API_KEY`(필수), `LLM_MODEL`, `LLM_BASE_URL`(비워도 됨), `FE_ORIGIN`, `DB_PATH`.
5. 배포 후 `https://<서비스>.onrender.com/health`가 `{"ok": true, "prompt_version": ...}`를 돌려주는지 확인.
6. 프론트가 Vercel에 올라가면 `FE_ORIGIN`에 그 도메인을 쉼표로 추가(`https://xxx.vercel.app,http://localhost:5173`)하고 재배포.
7. 슬립 방지: cron-job.org 등에서 **10분마다 `/health`를 핑**(무료 플랜은 15분 유휴 시 잠듦).
8. 배포 URL로 끝-끝 확인: `python scripts/smoke.py --base https://<서비스>.onrender.com --rules fixtures/demo/rules.txt --kakao fixtures/demo/kakao.txt`

## 구조

```
app/
  main.py            FastAPI 앱, CORS(FE_ORIGIN), /health(prompt_version), 공통 에러 응답
  models.py          API 타입(§3) + 요청 본문
  db.py              세션 1개 = JSON 1행 (SQLite sessions(id, data))
  llm.py             call_json: 모든 LLM 호출의 단일 통로(JSON 강제, 스키마 검증, 1회 재시도, 실패 시 (None, error)), prompt_version
  parsers/           docs.py(PDF/TXT → 문장), kakao.py(카톡 PC·모바일 → 메시지)
  services/          items, facts, elements, questions, annotations, publish, qa
  routers/sessions.py  엔드포인트 11개 + GET /forwarded, 공통 예외 가드
prompts/             LLM 프롬프트 5개 (수정 금지)
fixtures/            rules.txt, kakao.txt(임시 10줄/60개) · demo/(시연용, README에 설계 의도) · set1~5/(B의 골드셋)
scripts/smoke.py     사람이 보는 끝-끝 스모크
scripts/dump.py      측정용 덤프
tests/test_flow.py   통합 흐름(가짜 LLM / 키 없음 / 실제 LLM -m llm) + 파서·필터·검색 단위 테스트
render.yaml          Render Blueprint
```

## 알려진 한계

- `summary`는 항상 `null`(AI 요약 미구현, 범위 밖). 임베딩 검색 없음 — Q&A는 토큰 교집합이다. 토큰 끝의 조사(은·는·까지·한테 등)는 한 번 떼고 비교하지만("회비는"="회비"), 활용형·동의어("예약"↔"대관", "연락해요"↔"연락합니다")는 맞추지 못한다.
- 세션 1개가 JSON 1행이고 **동시 사용자를 고려하지 않았다**(같은 세션을 둘이 동시에 수정하면 늦게 저장한 쪽이 덮어쓴다). Render 무료 플랜은 재배포 시 디스크가 초기화되어 SQLite가 비워진다.
- 인증 없음: `session_id`를 아는 사람은 누구나 읽고 쓸 수 있다.
- 다중 파일 업로드·HWP·음성 STT 없음. 스캔본 PDF(텍스트 없는 PDF)는 `doc_parse_failed`.
- 기능 동결(11/13) 이후에는 버그 수정과 에러 응답 통일만 한다.
