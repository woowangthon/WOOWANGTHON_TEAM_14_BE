# Claude Code 인계 프롬프트 — 「빠진 페이지」 백엔드

> 사용법: 이 파일을 저장소의 `be/CLAUDE.md`로 저장하고, 첫 메시지로 맨 아래 "첫 지시"를 붙여 넣는다. Claude Code는 이 파일을 매 세션 자동으로 읽는다.
> 이 파일의 §1~§8은 사양(바꾸지 말 것), §9는 작업 방식, §10은 첫 지시다.
> **경로 메모**: 이 저장소(`WOOWANGTHON_TEAM_14_BE`)는 BE 전용이라 `be/` 하위 폴더가 없다. 아래의 `be/`는 **저장소 루트**를 뜻한다(`be/app/main.py` → `app/main.py`).

---

## 1. 프로젝트 한 줄

동아리 임원 인수인계용 서비스 「빠진 페이지」의 **백엔드(FastAPI)**. 확정 자료(회칙·공지 PDF/TXT)에서 업무 항목·키워드를 뽑고, 단톡방 텍스트에서 그 업무의 사실을 모은 뒤, 항목마다 **업무 수행 정보 6요소** 중 빈 곳을 판정해 **질문 카드 5장**을 만든다. 전임이 답하면 주석으로 정리해 승인받고, 후임의 질문에는 **승인된 기록을 그대로** 찾아 보여준다.

핵심 원칙(코드로 강제):
- AI는 "자료에 **없다**"까지만 말한다. "해당 없음(필요 없음)"은 사람만 정한다 → LLM 출력 스키마에서 `해당없음` 제외.
- 출처 없는 사실·주석은 저장하지 않는다.
- LLM 호출은 전부 한 함수(`llm.call_json`)를 거치고, JSON 스키마 검증 실패 시 1회 재시도, 그래도 실패면 **빈 값 + error**. 라우터는 절대 500을 내지 않는다.
- 질문에 금지 표현(부정 전제·선택지·전제·편법 유도)이 있으면 재생성 최대 2회 → 기본 템플릿.
- 후임 Q&A는 승인 주석 검색만. 기록이 없으면 `none: true`. 생성 답변 금지.

마감: 11/13(금) 기능 동결, 11/15(일) 제출. 오늘은 11/9.

---

## 2. 스택·구조 (이대로 만든다)

- Python 3.11+, FastAPI, pydantic v2, `openai` SDK(OpenAI 호환; 모델·엔드포인트는 env), SQLite(`sqlite3` 표준 라이브러리, ORM 없음), `pypdf`, `python-multipart`, `python-dotenv`.
- 저장소는 모노레포. 이 폴더는 `be/`. 프론트는 `fe/`(건드리지 않는다).

```
be/
  app/
    main.py            # FastAPI 앱, CORS(env FE_ORIGIN + http://localhost:5173), /health
    models.py          # §3 타입
    db.py              # 세션 1개 = JSON 1행 (sessions(id TEXT PK, data TEXT))
    llm.py             # call_json(prompt_name, payload, schema, retries=1) -> (obj|None, error|None)
    parsers/docs.py    # PDF/TXT -> [{"id":"doc#1","text":...}]
    parsers/kakao.py   # 카톡 txt(PC/모바일 두 형식) -> [{"idx","id":"chat#n","date","speaker","text"}]
    services/items.py       # 프롬프트 01
    services/facts.py       # 키워드 필터(앞뒤 2개) + 프롬프트 02
    services/elements.py    # 프롬프트 03 + score + rank_items
    services/questions.py   # 프롬프트 04 + 금지어 필터 + 카드 5장 선정
    services/annotations.py # 프롬프트 05
    services/publish.py
    services/qa.py          # 키워드 검색
    routers/sessions.py     # §4 엔드포인트 11개
  prompts/01_items.md 02_facts.md 03_elements.md 04_questions.md 05_annotations.md   # §5
  fixtures/rules.txt kakao.txt (mock.json은 fe/에 있음, 읽기만)
  tests/test_flow.py      # fixture로 1→10 스모크
  scripts/dump.py         # 측정용: fixture 세트 순회, elements/facts JSON 덤프
  requirements.txt  .env.example  README.md
```
`.env.example`: `LLM_API_KEY=`, `LLM_MODEL=gpt-4o-mini`, `LLM_BASE_URL=`, `FE_ORIGIN=http://localhost:5173`

---

## 3. 데이터 타입 (API 계약 — 프론트와 합의됨, 필드명 변경 금지)

```python
ElementKey = Literal["시기","연락경로","판단기준","자주틀리는점","미리할일","배경"]
State = Literal["있음","문서에없음","모호함","해당없음"]     # DB 저장용. LLM 출력은 해당없음 제외
Fill  = Literal["확인됨","추가확인권장"]

Source      {type: "자료"|"전임답변", ids: list[str]}            # ["chat#3"] / ["q_01"]
Fact        {id, text, date?, date_estimated: bool=False, source: Source,
             status: "확인됨"|"추정"|"확인대기"="확인됨", label?: "전임경험"|"규정근거"}
Element     {state: State, fill?: Fill(있음일 때만), evidence?: str(fact id|doc id|문장), closed_by?: "전임"|"후임"}
Item        {id: "item_01", title, keywords: list[str], facts: list[Fact]=[],
             elements: dict[ElementKey, Element]={}, score: int=0}
Question    {id: "q_01", item_id, element: ElementKey, kind: "빈칸"|"보완"|"확인",
             step1, step2, step3: str, rank: int}            # 1~5 카드, 6+ 추가 질문, 0 미정
Answer      {question_id, text, audio_url?}
Slots       {절차?, 예외?, 사례?, 연락?, 조건?}
Annotation  {id: "an_001", item_id, element: ElementKey, text, slots: Slots,
             source: Source(type="전임답변"), label: "전임경험"|"규정근거"="전임경험",
             status: "draft"|"approved"|"edited"|"rejected"="draft"}
```
점수: `score = (문서에없음 수) + (모호함 수) + (있음∧추가확인권장 수)`, 해당없음은 제외. 각 1점.
항목 정렬: score 내림차순 → 문서에없음 수 → 모호함 수.

세션 JSON(`db` data): `{"meta", "doc_sentences", "items", "chat", "questions", "answers", "annotations", "published", "forwarded"}`

---

## 4. 엔드포인트 11개

| # | 메서드 | 경로 | 요청 | 응답 |
|---|---|---|---|---|
| 1 | POST | `/sessions` | `{club_name, giver, receiver}` | `{session_id}` |
| 2 | POST | `/sessions/{id}/docs` | multipart `file`(PDF/TXT) | `{items: Item[], error?}` (facts 비어 있음) |
| 3 | PATCH | `/sessions/{id}/items` | `{items:[{id, title?, keywords?, merge_into?, delete?}]}` | `{items}` |
| 4 | POST | `/sessions/{id}/chat` | multipart `file`(txt) | `{items: Item[], chat_count, error?}` (facts·elements·score 채움, 정렬됨) |
| 5 | PATCH | `/sessions/{id}/items/{item_id}/elements/{key}` | `{state:"해당없음", closed_by}` | `{item}` |
| 6 | GET | `/sessions/{id}/questions` | — | `{cards: Question[≤5], more: Question[]}` (최초 호출 시 생성·저장) |
| 7 | POST | `/sessions/{id}/answers` | `{answers: Answer[]}` | `{annotations: Annotation[]}` (draft만) |
| 8 | PATCH | `/sessions/{id}/annotations/{ann_id}` | `{status?, text?, label?}` | `{annotation}` (text 변경 시 status=edited) |
| 9 | POST | `/sessions/{id}/publish` | — | `{document:{items:[{id,title,elements,facts,annotations(approved/edited만)}], notice}}` |
| 10 | POST | `/sessions/{id}/ask` | `{question}` | `{hits:[{annotation_id,text,source,label,item,score}], summary: null, none: bool}` |
| 11 | POST | `/sessions/{id}/ask/forward` | `{question}` | `{forwarded: true}` |

- 에러는 항상 `{"error": {"code": str, "message": str}}` 형태를 응답 본문에 포함(HTTP 200 유지, 세션 없음만 404).
- `notice` 고정 문구: "이 문서는 전임의 경험을 정리한 것이며 회칙·규정이 우선합니다."

---

## 5. 프롬프트 5개 (prompts/*.md에 그대로 저장)

### 01_items.md
```
너는 동아리 운영 문서를 읽고 "업무 항목"을 뽑는 도우미다.
입력: {"sentences":[{"id":"doc#1","text":"..."}]}
할 일: 운영 업무를 4~7개 항목으로 나눈다. 각 항목에 제목(5자 내외), 그 업무를 가리키는 키워드 3~8개(카톡에서 쓸 법한 구어체 포함), 근거 문장 id를 적는다.
규칙: 문서에 근거가 없는 항목은 만들지 않는다. 키워드는 명사 위주, 중복 금지.
출력(JSON만, 설명 금지): {"items":[{"title":"회비 정산","keywords":["회비","미납","정산","입금"],"evidence":["doc#1","doc#2"]}]}
```

### 02_facts.md
```
너는 단톡방 메시지에서 특정 업무에 관한 "사실"만 뽑는다.
입력: {"item":"코트 대관","messages":[{"id":"chat#4","date":"2026-04-20","speaker":"회장","text":"..."}]}
할 일: 업무 수행과 관련된 사실(한 일, 결정, 날짜, 담당, 금액, 연락처 언급)을 한 줄씩 뽑는다.
규칙: 반드시 근거 메시지 id를 source_ids에 넣는다. 메시지에 없는 내용은 만들지 않는다. 날짜가 메시지에 명시되지 않고 문맥으로 짐작한 것이면 date_estimated를 true로 한다. 잡담·감정 표현은 제외한다.
출력(JSON만): {"facts":[{"text":"6/14(토) 오후 코트 확정","date":"2026-04-20","date_estimated":false,"source_ids":["chat#4"]}]}
```

### 03_elements.md
```
너는 업무 항목에 대해 "업무 수행 정보 6요소"가 자료에서 확인되는지 판정한다.
6요소: 시기(언제까지·주기·늦으면), 연락경로(누구에게·어떤 방식), 판단기준(어떤 조건에서 어떻게), 자주틀리는점(실수·누락이 잦은 지점), 미리할일(사전 준비), 배경(왜 이렇게 하는지).
입력: {"item":"코트 대관","facts":[{"id":"...","text":"...","date":"..."}],"doc_sentences":[{"id":"doc#3","text":"..."}]}
각 요소의 state는 다음 셋 중 하나만: "있음" | "문서에없음" | "모호함". ("해당없음"은 절대 출력하지 않는다. 필요 여부는 사람이 정한다.)
state가 "있음"이면 fill을 정한다: "확인됨"(후임이 일반적인 상황에서 그대로 수행·판단할 수 있을 만큼 구체적) | "추가확인권장"(언급은 있으나 구체성 부족). 키워드가 있다는 것만으로 "확인됨"을 주지 않는다.
"있음"·"모호함"이면 evidence에 근거 fact id 또는 doc id를 적는다.
예시:
- 시기: "매월 25일까지 정산, 미납 시 다음 달 공지" → 있음/확인됨 ; "매월 말 정산" → 있음/추가확인권장
- 연락경로: "체육시설과 김○○에게 메일 후 전화" → 있음/확인됨 ; "학교에 신청" → 있음/추가확인권장
- 판단기준: "3개월 미납 시 공지, 그래도 미납이면 운영진 논의" → 확인됨 ; "미납 시 논의" → 추가확인권장
- 자주틀리는점: "메일만 보내면 누락됨, 전화 확인 필요" → 확인됨 ; "주의 필요" → 추가확인권장
- 미리할일: "대회 2주 전 참가자 명단 확정" → 확인됨 ; "미리 준비" → 추가확인권장
- 배경: "학교 규정상 동아리는 시설과를 통해서만 예약 가능" → 확인됨 ; "원래 그렇게 함" → 추가확인권장
출력(JSON만): {"elements":{"시기":{"state":"있음","fill":"추가확인권장","evidence":"doc#1"},"연락경로":{"state":"문서에없음"},"판단기준":{...},"자주틀리는점":{...},"미리할일":{...},"배경":{...}}}  (6개 모두 포함)
```

### 04_questions.md
```
너는 인수인계 미팅에서 후임이 전임에게 물어볼 질문을 만든다.
입력: {"item":"코트 대관","element":"연락경로","kind":"빈칸"|"보완"|"확인","context":{"evidence_text":"...","fact_text":"...","date":"..."}}
질문은 세 단계다.
- step1(사실): 실제 절차를 묻는다. 요소별 기본형:
  시기 "이 일은 실제로 언제쯤 시작해서 언제까지 마무리하시나요?" / 연락경로 "이 일을 진행할 때 함께 일하는 담당자나 기관은 누구인가요? 보통 어떤 방식으로 연락하시나요?" / 판단기준 "같은 업무라도 상황에 따라 처리가 달라지는 경우가 있나요? 무엇을 기준으로 나누시나요?" / 자주틀리는점 "이 업무에서 특히 신경 써서 확인하시는 부분이 있나요?" / 미리할일 "본 작업 전에 미리 준비해 두시는 것이 있나요?" / 배경 "이 업무가 지금 방식으로 운영되는 배경이 있다면 무엇인가요?"
  kind가 "보완"이면 evidence_text를 인용하며 부족한 부분만 묻는다(예: "'매월 말 정산'이라고 돼 있는데, 정확히 며칠까지인가요? 늦으면 어떻게 되나요?").
  kind가 "확인"이면 fact_text의 날짜·사실이 맞는지 확인한다(예: "5/12에 대회 신청서를 낸 것으로 보이는데 맞나요? 마감일은 언제였나요?").
- step2(예외): "그 과정에서 평소와 다르게 처리해야 했던 경우가 있었나요?"
- step3(사례): "이 업무를 최근에 처리한 사례를 하나 들어주실 수 있나요? 특별한 상황이 없었다면 일반적인 처리 사례도 괜찮습니다."
금지: 실수·문제·반려·잘못·힘든 같은 부정 전제어, "A와 B 중" 선택지, "~했을 텐데/~하셨잖아요" 전제, 편법·그냥·빨리 처리하는 법.
항목 이름을 자연스럽게 넣어 문장을 다듬되 의미는 유지한다.
출력(JSON만): {"step1":"...","step2":"...","step3":"..."}
```

### 05_annotations.md
```
너는 전임의 답변을 인수인계 기록으로 정리한다.
입력: {"item":"코트 대관","element":"연락경로","question":"...","answer":"..."}
할 일: 답변을 슬롯으로 나눈다. 절차(어떻게 하는가), 예외(어떤 경우에 달라지는가), 사례(언제 어떤 일이 있었는가 — 시점·상황·조치 중 2개 이상이 있을 때만), 연락(대상·방식), 조건(언제 적용되는가). 없는 슬롯은 null.
text에는 후임이 읽을 한두 문장 요약을 쓴다. 답변에 없는 내용은 넣지 않는다. "반드시·항상·무조건"은 답변에 그 말이 있을 때만 쓴다.
한 답변에 서로 다른 요소의 내용이 섞여 있으면 annotations를 2~3개로 나눈다(각각 element 지정).
출력(JSON만): {"annotations":[{"element":"연락경로","text":"...","slots":{"절차":"...","예외":null,"사례":"...","연락":"...","조건":null}}]}
```

---

## 6. 알고리즘 규칙 (프롬프트 밖에서 코드로)

- **카톡 파싱**: PC 형식(`--------------- 2026년 4월 2일 목요일 ---------------` 날짜선 + `[이름] [오후 7:45] 메시지`)과 모바일 형식(`2026년 4월 2일 오후 7:45, 이름 : 메시지`, `2026. 4. 2. 오후 7:45, 이름 : 메시지`) 모두 처리. 매치 안 되는 줄은 직전 메시지에 이어붙임. 노이즈 제거: "사진", "이모티콘", "동영상", "삭제된 메시지입니다", "님이 들어왔습니다/나갔습니다", 길이 2 미만. id는 `chat#{idx}`.
- **키워드 필터**: 항목 키워드가 포함된 메시지 인덱스 ± 2 범위만 남김. 중복 제거, 순서 유지.
- **사실 후처리**: `source_ids` 중 실제 넘긴 메시지 id에 없는 것은 제거, 하나도 안 남으면 그 fact 폐기. `date_estimated`면 `status="추정"`.
- **요소 후처리**: LLM이 6개 중 일부를 빠뜨리면 빠진 키는 `{"state":"모호함"}`. state≠있음이면 fill=None. 스키마 실패 시 6개 모두 `모호함` + error(보류).
- **질문 후보 생성 순서**: 문서에없음(우선 0) → 모호함(1) → 추가확인권장(2, kind=보완) → 추정 fact(1, kind=확인, element=시기). 같은 우선순위 내 항목 score 내림차순.
- **금지어 정규식**: `실수|문제|반려|잘못|힘든`, `와\s.+\s중`, `인가요\s*아니면`, `했을\s*텐데|하셨잖아요`, `편법|그냥|빨리\s*처리`. step1+step2+step3 합쳐 검사. 걸리면 재생성(총 3회 시도) → 실패 시 요소별 기본 템플릿(`[항목명] ` + step1 기본형, step2/3 고정문).
- **카드 선정**: 후보 순서대로 rank 1~5 부여, **항목당 최대 2장**. 나머지는 rank 6부터 순번. 비용 절감: 상위 5 후보만 LLM 호출, 나머지는 기본 템플릿 허용.
- **주석 후처리**: `source={"type":"전임답변","ids":[question_id]}`, id는 `an_{n:03d}` 전역 증가. 사례 슬롯이 null이어도 저장(라벨은 전임경험 유지).
- **발행**: status ∈ {approved, edited}만. facts(자료)와 annotations(전임답변) 분리 유지.
- **Q&A 검색**: 질문·주석 모두 구두점 제거 후 공백 토큰화, 조사·불용어(은 는 이 가 을 를 의 에 에서 로 으로 와 과 도 요 어떻게 해요 하나요 뭐 어떤 언제 누구) 제거, 길이 1 토큰 제거. 주석 텍스트 + 슬롯 값 + 항목 제목을 대상으로 교집합 크기 점수. 점수 ≥ 1인 것 상위 3개. 없으면 `none: true`, `hits: []`. `summary`는 항상 null(해커톤 범위).
- **해당없음 닫기(API 5)**: 해당 키를 `{"state":"해당없음","closed_by":...}`로 덮어쓰고 score 재계산.

---

## 7. 완료 기준 (tests/test_flow.py가 이걸 검사)

fixture(`fixtures/rules.txt`, `fixtures/kakao.txt`)로 1→2→3→4→6→7→8→9→10을 순서대로 호출했을 때:
1. items 4~7개, 각 keywords ≥ 3.
2. facts 총 10개 이상, **모든 fact에 source.ids ≥ 1**, 모든 id가 실제 chat id.
3. 모든 item의 elements에 6키 존재, state 값이 `해당없음`인 것 0개(사람이 닫기 전).
4. cards 길이 ≤ 5, 항목당 ≤ 2, 금지어 정규식 매치 0.
5. answers 3개 → annotations ≥ 3, 모두 status=draft, source.type=전임답변.
6. 2개 approve → publish 결과 annotations 정확히 2개.
7. 승인 주석에 있는 단어로 ask → hits ≥ 1; 전혀 무관한 질문("우주선 발사 일정") → none=true, hits=[].
8. 어떤 단계도 HTTP 500 없음. LLM 키가 없거나 호출 실패해도 `error` 필드로 떨어지고 응답 구조는 유지.

---

## 8. 범위 밖 (하지 말 것)

음성 STT(선택, 1~7 끝난 뒤에만), 임베딩 검색, AI 요약(summary), 사용자 인증, 다중 파일 업로드, HWP 파싱, 프론트 코드 수정, 규정 검색·충돌 판정, 관리자 대시보드. 요청받지 않은 추상화·레이어·테스트 프레임워크 추가 금지.

---

## 9. 작업 방식

- **한 번에 한 단계.** §10 순서대로 구현하고, 각 단계마다 실제로 실행(`uvicorn` 또는 pytest)해서 결과를 보여준 뒤 다음으로 간다. 전부 만들고 한 번에 실행하지 않는다.
- 파일을 만들기 전에 기존 파일을 읽는다. 이미 있는 코드는 덮어쓰지 말고 수정한다.
- LLM 호출이 필요한 단계는 `.env`에 키가 있을 때만 실제 호출. 없으면 `call_json`이 error를 돌려주는 경로가 정상 동작하는지 확인하고, 프롬프트와 스키마만 완성한다.
- 외부 패키지는 §2에 적힌 것만. 추가가 꼭 필요하면 이유를 한 줄 말하고 추가.
- 모르는 것(카톡 샘플 형식, fixture 내용)은 추측해서 진행하지 말고 **질문**한다. 단, fixture가 없으면 §10의 임시 fixture를 직접 만들어 진행한다.
- 한국어 문자열은 그대로 사용(인코딩 utf-8). 응답 JSON은 `ensure_ascii=False`.
- 커밋은 단계마다 하나, 메시지 앞에 `[BE]`.

---

## 10. 첫 지시 (Claude Code 첫 메시지로 붙여 넣을 것)

```
be/CLAUDE.md를 읽고 그 사양대로 백엔드를 만든다. 오늘 범위는 아래 1~7이고, 각 단계가 끝날 때마다 실행 결과를 보여준 뒤 다음으로 넘어간다.

1. be/ 아래에 §2 구조를 만들고 requirements.txt, .env.example, .gitignore(.venv, .env, *.db, __pycache__)를 쓴다. 가상환경을 만들고 패키지를 설치한다.
2. app/models.py(§3), app/db.py, app/llm.py(§1·§6 규칙: JSON 강제, 스키마 검증, 1회 재시도, 실패 시 (None, error)), app/main.py(/health, CORS)를 만들고 uvicorn으로 /health가 뜨는 것을 확인한다.
3. prompts/ 5개 파일을 §5 내용 그대로 저장한다.
4. fixtures/rules.txt가 없으면 아래 10줄로 만들고, fixtures/kakao.txt가 없으면 PC 형식으로 60줄(회비·코트·대회 관련 메시지 20개 + 잡담 40개, 날짜선 3개)을 만든다.
   제1조(회비) 회비는 매월 말 정산한다.
   제2조(미납) 회비 미납이 반복되는 회원은 운영진이 논의한다.
   제3조(코트) 코트 대관은 학교 체육시설과에 신청한다.
   제4조(대회) 교내 대회 참가 신청은 운영진이 일괄 접수한다.
   제5조(모집) 신입 회원 모집은 매 학기 첫째 주에 진행한다.
   제6조(행사) 정기 모임은 월 1회 개최한다.
   공지(2026.03) 신입 회원 모집 3/3~3/7, 지원 폼 링크는 단톡 공지 참고.
   공지(2026.05) 교내 테니스 대회 참가 신청 5/15까지 총무에게.
   공지(2026.06) 6월 정기 모임 6/14(토) 오후 코트.
   공지(2026.09) 2학기 회비 납부 안내.
5. parsers/docs.py, parsers/kakao.py(§6 두 형식), services/items.py(프롬프트 01), services/facts.py(키워드 필터 + 프롬프트 02 + 출처 검증), routers/sessions.py의 API 1~4를 만든다.
6. tests/test_flow.py에 §7의 1·2·8 항목을 검사하는 테스트를 쓰고 통과시킨다(LLM 키가 없으면 error 경로로 구조만 검사).
7. README.md에 실행법과 §4 표를 적는다. 여기까지 끝나면 멈추고, 내일(11/10) 할 5·6단계(elements·questions)는 시작하지 않는다.

카톡 fixture 형식이나 사양에서 모호한 점이 있으면 진행 전에 질문해라.
```
