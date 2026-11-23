# server — 재현 담당 FastAPI

질문·탐색, 분석, 알림. 규칙은 레포 루트 `CLAUDE.md`, 테이블은 `../db/`.

## 처음 한 번
```bash
cd server
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env            # 값 채우기
pytest                          # DB·LLM 없이 도는 단위 테스트
```

## 로컬 DB
Docker Desktop 켜고 `bash ../db/apply_local.sh`. 찍히는 `DATABASE_URL` 을 `.env` 에.
(소윤 테이블은 `db/000_stub_soyun_tables.sql` 임시 버전. 실제 Supabase 엔 000 을 적용하지 않는다.)

## 서버
```bash
uvicorn app.main:app --reload --port 8080
curl localhost:8080/v1/health
```

## 질문 생성 흐름
1. 소윤 쪽 DB 웹훅(experience INSERT) → `POST /v1/internal/experience-saved` (헤더 `X-Webhook-Secret`)
2. 서버가 `question_batch` pending 행을 만들고 백그라운드로 생성. 앱은 이 행의 `status` 를 보고 "준비 중"
3. 성공: 안 담은 이전 질문 삭제 → 새 질문 삽입 → `done`. 실패: `failed` + `failure_reason`(`connection` → 앱이 "다시 시도" 버튼 → `POST /v1/questions/retry`, `model` → 안내만)
4. 사용자 재생성 버튼 → `POST /v1/questions/regenerate` (JWT). 기록당 24시간에 1회 + 더 적어보기에 답할 때마다 1회, 초과 429 `QUESTION_REGEN_LIMIT`. 이전에 보여준 질문과 겹치지 않는 것만 새로 만들고, 없으면 아무것도 지우지 않고 `question_batch.no_new` 를 켠다
5. 더 적어보기 답 → `POST /v1/questions/{id}/answer` (JWT, 1~80자). 저장만 하고 다시 만들지는 않는다. 답은 다음 생성 때 기록 원문으로 들어간다

생성 한 번 안에서 하는 일 (`app/questions/generate.py`):
- 11/14 기획 변경(PRD 7.2, 기능명세서 14.1)부터 화면에 나가는 건 **4개 고정**이다. 모델은 후보(더 적어보기 0~2, 해볼 것 2~3, 상상해보기 2~3)를 내고 코드가 4개를 고른다 (`generate.compose`): 더 적어보기를 앞에 최대 2개, 나머지 자리는 해볼 것과 상상해보기를 번갈아. 4자리면 2+2, 3자리면 해볼 것 2 + 상상해보기 1, 2자리면 1+1
- 프롬프트 v6 (11/23): v5 에 "이 기록에서만 나올 질문으로" 절을 더했다. 바꾸는 것을 구체적인 값으로 말하기, 기록에 적힌 어려움을 키우는 해볼 것 금지, 상황 칩을 뒤집기만 한 가정 금지. Qwen 이 낸 질문이 어느 기록에나 붙는 꼴이어서 (재현님 11/23). Haiku 로 7건 확인, Qwen 은 확인 전
- 질문은 40자 이내, 문장 틀은 프롬프트 v6 의 "문장 틀" 절. 틀은 프롬프트로만 맞추고 필터는 길이와 끝("요?")만 본다
- 걸러낸 뒤 4개가 안 되거나 해볼 것·상상해보기 가운데 한 종류가 없으면 **한 번만** 다시 묻는다. 통과한 질문과 버린 질문·이유를 알려주고 모자란 종류와 개수만 받는다 (`question_reask_v2.md`). 재생성 횟수에 세지 않는다. 다시 물어도 모자라면 있는 것만 저장한다. 0개면 `model` 실패
- 다시 받은 질문이 버린 질문과 닮았으면(겹치는 질문과 같은 기준 0.5) 받지 않는다 (`filters.split_repeats`). Qwen 은 버린 질문을 말만 바꿔 다시 낸다. 형식 때문에 버린 것(출처 없음, 축 불일치, 길이)은 고쳐서 다시 내도 받는다
- 질문의 `source` 를 기록 글자와 맞춘다 (`source.py`). 모델이 붙인 항목 이름("한 줄: …", "역할 칩 '…'")을 떼고, 틀리게 옮긴 칩은 가장 닮은 칩으로 바꾼다. 기록에서 못 찾으면 그대로 둔다
- 입력 해시가 같아도 저장된 질문을 재사용하지 않는다 (11/14 부터. 재생성은 이전 질문과 안 겹쳐야 한다). 해시는 기록으로만 남긴다
- 겹침을 잴 때는 문장 틀("다음엔 …해보면 어떨까요?")을 빼고 내용만 본다 (`filters._FRAME`). 틀이 40자의 3분의 1쯤이라 그대로 재면 서로 다른 질문도 닮아 보인다
- 같은 사실을 축만 바꿔 두 번 물으면 하나만 남긴다 (`filters.same_fact`). 종류가 같으면 앞의 것, 다르면 버렸을 때 그 종류가 없어지는 쪽, 둘 다 하나씩이면 상상해보기를 남긴다 (해볼 것은 재료가 많아 다시 받기 쉽다). 조건 값이 상황 칩에도 적혀 있고(규모=혼자, "혼자 결정해야 했다") 두 질문이 다른 축에서 그 낱말을 "~가 아니라·말고·대신"으로 바꾼 꼴만 본다. 그 밖의 겹침은 프롬프트와 글자 겹침 규칙에 맡긴다

## DB 없이 데모
```bash
python -m scripts.demo_questions          # 가짜 경험 6개 → 질문·버린 것·이유 출력
python -m scripts.demo_questions --force-reask f02   # 첫 응답에서 하나만 남긴 것처럼 꾸며 다시 묻기를 실제 모델로 확인
python -m scripts.demo_questions --regen f02         # 다시 만들기: 방금 낸 질문을 이전에 보여준 질문으로 주고 한 번 더
```
`.env` 의 `LLM_BASE_URL` 이 GPU 서버의 vLLM 을 가리켜야 함. 맥에서는 안 닿으니 컨테이너 안에서 돌리거나 ssh 터널.

**GPU 없이 (밖에서) 돌릴 때** — Anthropic 의 OpenAI 호환 엔드포인트로 같은 코드를 돌릴 수 있다. 모델이 다르니 프롬프트 점검용이고, 회의 데모·실서비스는 GPU 서버의 Qwen3.8-27B 로.
```bash
set -a; . ../.env; set +a      # 루트 .env 의 ANTHROPIC_API_KEY
LLM_BASE_URL=https://api.anthropic.com/v1/ LLM_MODEL=claude-haiku-5-5 LLM_API_KEY="$ANTHROPIC_API_KEY" \
LLM_DISABLE_THINKING=0 LLM_DETERMINISTIC=0 LLM_TIMEOUT_S=90 .venv/bin/python -m scripts.demo_questions
```
`LLM_DISABLE_THINKING=0`(vLLM 전용 `chat_template_kwargs` 안 보냄), `LLM_DETERMINISTIC=0`(`temperature`·`seed` 를 거부함) 둘 다 필요. Gemini 호환 엔드포인트도 같은 플래그로 붙지만 무료 등급은 크레딧이 없다고 402 를 낸다.

## 임베딩 (3단계)
- 공개 확정 → 소윤 DB 웹훅 `POST /v1/internal/experience-published` → `experience.embedding` 채움. 비공개로 내려오면 비우고, 그 카드를 담은 `related_cache` 를 지움(다음 조회 때 재계산). 공개된 경험이 삭제되면 같은 주소로 `type: "DELETE"` 가 오고(`record` 는 null, id 는 `old_record.id`) 캐시를 같은 방식으로 지움
- 웹훅 재전송이 없어서 sweep 이 `visibility <> 'private' and embedding is null` 을 5분마다 채움. 지금 돌리기: `POST /v1/internal/sweep`
- 넣는 글: `app/embedding/text.py` `card_text()` — 활동명·장르·역할·한 일·상황·감정·한 줄·태그 한 문단. 스냅샷(가림본) 우선
- 백엔드: `EMBED_BACKEND=openai`(GPU 서버 vLLM, `llm_test/serve_embed.sh` 포트 18001) 또는 `local`(맥, sentence-transformers)
- 11/14 실측(481장): 질문만 임베딩하면 맞는 카드도 0.5대, 전체 중앙값 0.4 → 0.35 는 의미 없음. **출처 경험 맥락 + 질문**으로 넣으면 맞는 카드 0.64~0.70 → 임계값 0.55 로 (`constants.py`)
- 임계값 확인: `scripts/embed_eval.py` — 10월 유튜브 추출본 481장을 **로컬 DB 에만** 넣고 질문으로 검색해 유사도 분포를 본다. 실제 Supabase 에는 안 넣음

## 관련 경험 (4단계)
- `POST /v1/related` `{question_id, filter}` → `{items: [{card: PublicCard, reason}]}`. 최대 10개, 없으면 `[]`
- 흐름: 검색어(출처 경험 맥락 + 질문) 임베딩 → `match_public_cards()` 후보 30개(필터가 있으면 100개 뽑아 거름) → LLM 재정렬 + 한 줄 이유(`app/llm/prompts/related_v1.md`) → `related_cache` 저장
- LLM 은 관련 없는 후보를 뺄 수 있다. 이유가 길거나(60자) 평가·연락처가 섞이면 이유만 `null` 로 두고 카드는 남긴다
- LLM 이 실패하면 유사도 순으로 내려주고 **캐시하지 않는다** (다음에 열 때 다시 시도). 임베딩 서버가 죽으면 `AI_UNAVAILABLE`
- 캐시에서 꺼낼 때마다 다시 거른다: 차단·내 것은 빼기만, 뷰에서 사라진 카드(비공개·삭제)가 있으면 다시 계산
- 새 공개 카드가 임베딩되면 `notify_on_related` 켜진 질문 중 결과가 비어 있던 것만 맞춰보고, 생기면 `related_new` 를 켠다 (알림은 6단계에서 오전 9시 묶음)
- 읽는 건 `experience_public` 뷰뿐. PublicCard 는 `app/cards/public.py` 한 곳에서 만든다 (7단계 `/cards` 도 같이 씀)
- 끝까지 돌려보기(로컬 전용, 평가용 카드 481장 필요): `EMBED_BACKEND=local python -m scripts.demo_related [f05 ...] [--filter same_role]`, 정리는 `python -m scripts.demo_related clear`
- 11/14 실측(Haiku 재정렬): 질문 6개에 3~5장씩, 첫 조회 4~6초(모델 로드 제외), 다시 열기 1~4ms

## 분석 (5단계)
- `POST /v1/analysis` `{force}` → `{locked, record_count, patterns, criteria, saved_questions, last_updated, has_new_records, reask_count}`. 기록(경험 + 회차 기록)이 3개 미만이면 `locked: true` 이고 LLM 을 안 부른다
- 기록마다 번호를 붙여 보낸다 (`[e1]` 경험, `[e1.l1]` 그 경험의 회차 기록). 모델은 문장과 근거 번호를 돌려주고, 서버가 번호를 `{experience_id, activity_name, log_id, log_date}` 로 바꿔 저장한다. 근거가 서로 다른 기록 2개 미만이면 버린다
- 저장된 결과: 처음 한 번은 바로 계산한다. 그 뒤로는 저장된 것을 주고, 기록이 바뀌었으면 `has_new_records: true` 만 알린다. `force: true` 이고 기록이 바뀌었을 때만 다시 계산한다 (기록이 그대로면 `force` 여도 LLM 을 안 부른다)
- 다시 계산할 때 (기능명세서 15장, 11/22 에 맞춤): "맞아요"·"수정할게요"·"아직 모르겠어요"를 받은 패턴은 남기고, 피드백이 없는 패턴은 지우고 새로 받는다. "아니에요"는 숨긴 채 남겨 두고 닮은 문장을 막는 데 쓴다. 사용자가 고친 문장(`edited_text`)은 모델에 보내지 않는다
- 근거 다시 세기 (프롬프트 v2): 남긴 문장은 `[p1]`, 숨긴 문장은 `[n1]` 번호를 붙여 보내고, 모델이 문장마다 그 내용이 적힌 기록 번호를 `existing` 으로 돌려준다. 있던 근거는 빼지 않고 새 기록만 더한다
- 순서: 근거 수 많은 순으로 최대 5개. "아직 모르겠어요"(확인 중)는 뒤로 밀리고 새 패턴의 자리를 차지하지 않는다. 응답할 때보다 근거가 늘면 `reask: more_evidence` 가 붙고 순위에 들어온다
- "아니에요" 다시 보여주기: 전체 기록 5개 이상이고 "아니에요" 뒤에 쓴 기록에 같은 내용이 있으면 한 번만 `reask: repeated` 로 다시 보여준다. 또 "아니에요"면 영구 제외 (`constants.ANALYSIS_RESURFACE_*`)
- 걸러내기(`app/analysis/filters.py`): 길이 80자, 끝이 "요.", 평가·사람 규정·조언·앞날 단정·남과 비교·연락처·"-시-" 높임, 근거 부족, 이미 있는 문장이나 숨긴 문장과 닮음. 닮음은 틀 낱말("기록이 두 개 있어요")을 뺀 문자 2-gram 이라 낱말이 거의 같은 것만 잡는다
- `POST /v1/analysis/feedback` `{pattern_id, feedback, edited_text}` → `{pattern}`. `edit` 는 고친 문장(1~60자)이 있어야 하고, `no` 는 숨긴다. 남의 패턴은 404
- LLM 이 실패하면 503 `AI_UNAVAILABLE` (`details.reason` = `connection` | `model`) 이고 아무것도 저장하지 않는다
- 읽는 테이블은 본인의 `experience`, `experience_log`, `question`(담은 것), `analysis_*` 뿐. `qa`·`reaction` 은 안 읽는다 (POL-07, `tests/test_analysis_unit.py` 가 SQL 을 검사)
- DB 없이 돌려보기: `python -m scripts.demo_analysis [f01 ...] [--feedback]` (가짜 경험 6개를 한 사람의 기록으로 가정. `--feedback` 은 첫 패턴에 "아니에요", 둘째에 "맞아요"를 누른 것처럼 꾸며 다시 분석)
- 11/15 실측(Haiku, 가짜 경험): 5번 돌려 패턴 18개, 필터가 버린 것 0개, 전부 "요."로 끝남. 기록 8개에 7~9초. 숨긴 문장과 남긴 문장은 다시 나오지 않았다 (1번 확인). "이유를 모르겠다고 적은 기록" 근거에 뜻이 조금 다른 기록이 하나 섞인 적이 있다 (2번). **Qwen 으로는 아직 안 돌렸다**
- 11/22 실측(Haiku, 프롬프트 v2, `--feedback` 1번): 기록 8개에 9.6초·9.8초, 버린 것 0개. 남긴 문장 `[p1]` 에는 근거 4개(처음 3개 + 회차 기록 1개), 숨긴 문장 `[n1]` 에는 처음과 같은 3개를 돌려줬다. 근거를 다시 세는 부분은 이 한 번만 확인했다


## 기능 확인용 페이지 (`/demo`)
기획·디자인이 서버 기능을 직접 눌러 보는 페이지다. **실제 앱이 아니고 디자인도 아니다** (색·아이콘 없음). `app/demo/` 에 있고 `DEMO_PASSWORD` 를 넣은 서버에서만 열린다. 넣지 않으면 `/demo`, `/v1/demo/*` 는 전부 404 이고 `demo:` 토큰도 안 통한다. 실제 서비스 서버에는 넣지 않는다.
- 볼 수 있는 것: 기록 쓰기(7단계를 줄인 칸) → 질문 4개, 더 적어보기 답, 담기, 다시 만들기 → 분석(패턴, 응답 버튼 4개, 다시 분석, 다시 묻는 카드, 더보기) → 알림함(묶임)
- 페이지는 AI 가 도는 부분은 실제 엔드포인트(`/v1/questions/*`, `/v1/analysis*`)를 그대로 부른다. 실제 앱이 Supabase 에서 직접 하는 부분(기록 저장, 목록 읽기, 담기)만 `/v1/demo/*` 가 대신한다
- 로그인: 비밀번호 하나 + 확인용 계정 고르기. 계정은 `DEMO_ACCOUNTS="지은:<uuid>,명금:<uuid>"` 로 준다. uuid 는 `auth.users` 에 있는 사용자여야 한다 (Supabase 대시보드 Authentication 에서 만든 사용자의 UID). 계정마다 기록이 따로 쌓인다
- 입력 없이 바로 들어가는 링크: `/demo#key=<비밀번호>&as=<계정 이름>`. 사람마다 자기 이름이 든 링크를 준다. `#` 뒤는 서버로 전송되지 않아 접속 기록에 남지 않는다. `as` 를 빼면 계정 고르기까지만 건너뛴다
- 맥에서: `DEMO_PASSWORD=... DEMO_ACCOUNTS=... uvicorn app.main:app --port 8765` 뒤 `http://localhost:8765/demo`
- 페이지를 다른 곳(Vercel 등)에 올릴 때: `app/demo/page.html` 한 장만 올리고 주소 뒤에 `?api=https://서버주소` 를 붙인다. `DEMO_PASSWORD` 가 있는 서버는 다른 주소에서 오는 요청(CORS)을 받는다
- GPU 서버에서 Qwen 으로 열 때: Supabase 에 `db/005` 까지 적용 → 컨테이너의 `server/.env` 에 `DATABASE_URL`(Supabase 의 Session pooler 또는 Direct 주소. Transaction pooler 6543 은 asyncpg 와 안 맞는다), `LLM_BASE_URL=http://localhost:8001/v1`, `LLM_MODEL=local`, `DEMO_PASSWORD`, `DEMO_ACCOUNTS` → `uvicorn app.main:app --host 0.0.0.0 --port <포트>` → 그 포트를 터널로 밖에 연다. 링크는 `터널주소/demo`

## 알림 (6단계, 진행 중 — 이슈 #18)
- 앱은 알림함을 `notification` 테이블에서 직접 읽는다. 서버는 행을 만들고 푸시를 보낸다. 서버 엔드포인트는 없다
- 만들기: `app/notifications/service.py` `add()` → DB 함수 `notification_add`. 묶음 키(`종류:경험id:한국날짜`)가 같으면 행을 새로 만들지 않고 `count` 만 올린다. Q&A 알림은 DB 트리거 함수 `notify_on_qa` 가 같은 함수를 부른다 (트리거는 `qa` 가 실제 테이블이 되면 붙인다)
- 푸시: `scheduler.run_forever` 가 1분마다 `dispatch_pending()` 을 돌린다. 아직 판단하지 않은 알림(`push_checked_at is null`)을 사람별로 모아, 방해 금지 시간이면 그대로 두고 아니면 Expo Push 로 보낸다. 묶음 행은 처음 만들어질 때만 걸리니 첫 건만 푸시된다
- 방해 금지(기본 22~08시, 한국 시간, 사용자가 바꾼 값 우선)에 생긴 알림이 2개 이상 밀려 있으면 "밤사이 알림 N개가 있어요" 하나로 보낸다
- 푸시를 전체로 껐거나, 그 종류를 껐거나, 기기가 없으면 보내지 않고 본 것으로만 표시한다 (`pushed_at` 은 null). Expo 가 `DeviceNotRegistered` 를 주면 그 토큰을 지운다. Expo 에 못 닿으면 그대로 두고 다음 차례에 다시 본다. 하루 넘게 밀린 것은 보내지 않는다
- 문구는 `text.py` (기획 "1117 수정" 표의 예시 그대로, 닉네임 없음)
- 환경변수: `EXPO_PUSH_URL`(기본 Expo 주소), `EXPO_ACCESS_TOKEN`(Expo 에서 push security 를 켰을 때만)
- 아직 안 한 것: 오전 9시 관련 경험 알림, 리마인드. 실제 Expo 로는 한 번도 안 보내 봤다 (테스트는 가짜 Expo)

## 폴더
```
app/config.py        환경변수        app/llm/client.py           LLM 호출·재시도·실패 분류
app/constants.py     숫자 전부       app/llm/prompts/question_v6.md  프롬프트 (버전 붙임. 쓰는 버전은 constants 의 QUESTION_PROMPT)
app/errors.py        에러 형식       app/questions/prompt.py     경험 → 입력 문단·해시
app/auth.py          JWT·웹훅 비밀   app/questions/filters.py    응답 후 걸러내기
app/db.py            asyncpg 풀      app/questions/generate.py   호출 → 걸러내기 → 다시 묻기 → 출처 맞추기
                                     app/questions/source.py     source 를 기록 글자에 맞추기
                                     app/llm/prompts/question_reask_v2.md  다시 물을 때 붙이는 글
                                     app/questions/service.py    batch·저장
                                     app/questions/router.py     엔드포인트
app/cards/public.py  PublicCard 조립   app/related/rerank.py       LLM 재정렬·이유 걸러내기
app/embedding/       임베딩·웹훅·sweep  app/related/service.py      후보·캐시·새 카드 재계산
app/analysis/        분석 (prompt·filters·generate·service·router)   app/llm/prompts/analysis_v2.md  분석 프롬프트
```

## Supabase 에 붙일 때 (소윤과 함께)
1. Supabase SQL Editor 에서 `db/000_soyun_record.sql` → `001_jaehyun_schema.sql` → `002_jaehyun_rls.sql` 적용 (`000_local_supabase`·`000_stub` 은 적용하지 않음)
2. 웹훅은 `000_soyun_record.sql` 트리거가 pg_net 으로 보냄. 대시보드에서 웹훅을 만들면 두 번 가니 만들지 않는다. Vault 에 주소·비밀값만 넣기 (`db/README.md` "소윤 웹훅 설정")
3. `.env` 의 `SUPABASE_JWT_SECRET` 은 Settings → API → JWT Secret. `AUTH_DEV_BYPASS=0`
4. `experience_log` 테이블은 진행 중 활동의 회차 기록을 질문 입력에 넣을 때 읽음 (소윤 테이블, 읽기만)

### 앱 토큰 검증 (`app/auth.py`)
앱이 보내는 Supabase access token 의 서명 방식 두 가지를 다 받는다. 토큰 머리의 `alg` 로 고른다.
- **ES256·RS256**: `.env` 에 `SUPABASE_URL` 만 넣는다. 공개키를 `<SUPABASE_URL>/auth/v1/.well-known/jwks.json` 에서 받아 검증하고 비밀값은 필요 없다. 지금 프로젝트는 Current 키가 ECC(P-256) 라서 이쪽이다 (11/16 확인)
- **HS256**: 예전 방식. `SUPABASE_JWT_SECRET` 을 넣어 둔 서버만 받는다. 위 3번의 값은 이 경우에만 필요하고, 비워 두거나 예시 값 `change-me` 그대로면 HS256 토큰은 거절한다
- 공개키는 10분 담아 두고 요청마다 받지 않는다. 모르는 `kid` 가 오면 다시 받되 30초에 한 번까지만. 그래서 Supabase 에서 키를 돌려도 서버를 다시 띄울 필요가 없다. 숫자는 `constants.py` 의 `AUTH_*`
- `aud` 는 `authenticated`, `iss` 는 `<SUPABASE_URL>/auth/v1` 이어야 한다. 틀리면 401 `UNAUTHENTICATED`. 공개키를 한 번도 못 받았거나 `SUPABASE_URL` 이 비어 있으면 토큰 잘못이 아니므로 500 `INTERNAL`
- 테스트는 `tests/test_auth.py` (DB 없이 돎). 실제 Supabase 토큰으로는 아직 확인하지 못했다

## GPU 서버에서 데모 돌리기 (11/17)
컨테이너 안 `jhjeong/genz` 는 이 레포의 git 체크아웃이다 (11/14 에 바꿈). 새 코드는 `git pull` 로 받는다:
```bash
cd /NHNHOME/WORKSPACE/26moe002_H/jhjeong/genz && git pull
cd server
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e .   # 처음 한 번
cp .env.example .env                                                            # 처음 한 번
pgrep -af 'vllm serve'                      # 이미 떠 있는 Qwen 이 있는지, 포트가 몇 번인지 먼저 본다
export LLM_BASE_URL=http://localhost:8001/v1   # 11/14 기준 Qwen3.8-27B 가 8001 에 떠 있었음
.venv/bin/python -m scripts.demo_questions
```
Qwen 이 이미 떠 있으면 GPU 메모리를 다 쓰고 있어서 새로 띄우면(`bash ../llm_test/serve.sh qwen27`, PORT=18000) 죽는다. 떠 있는 걸 쓴다. 아무것도 없을 때만 새로 띄우고 `LLM_BASE_URL` 을 18000 으로.

기록을 어디까지 채웠느냐에 따른 질문 개수 비교 (DB 필요 없음. Qwen 은 temperature 0 이라 반복은 1번이면 됨):
```bash
LLM_TIMEOUT_S=60 .venv/bin/python -m scripts.eval_record_depth 1 depth_qwen_v3.json
```
실측 (가짜 경험 6개, 경험당 평균 질문 수):

| 판 | v1 Haiku (3회) | v1 Qwen | v2 Haiku (1회) | v2 Qwen (1회) | v2 Qwen, 다시 묻기 넣은 뒤 (1회) |
|---|---|---|---|---|---|
| 필수만 | 2.06 | 2.00 | 2.00 | 1.83 | 2.00 |
| + 칩 보강글 | 2.27 | 2.00 | 2.60 | 2.20 | 2.20 |
| + 조건 | 2.28 | 2.00 | 3.50 | 2.17 | 2.17 |
| + 상황·감정 | 2.39 | 2.00 | 3.17 | 2.17 | 2.17 |
| + 한 줄 | 2.44 | 2.00 | 2.83 | 2.00 | 2.17 |
| 전부 | 2.89 | 1.83 | 3.50 | 2.83 | 2.83 |

- v1 에서 Qwen 은 기록을 얼마나 채우든 2개(해볼 것 1 + 상상해보기 1)만 냈다. 프롬프트의 "역할 하나 + 상황 하나" 예시를 그대로 따른 것. v2 는 재료(보강글·조건·상황·한 줄)마다 후보를 만들게 바꿨다. v2 Qwen 은 전부 채운 기록에서 2.83 까지 올랐지만 중간 단계는 여전히 2개 근처다
- 다시 묻기를 넣기 전 v2 Qwen 에서는 2개 미만이 두 번 있었다. 하나는 필터 오탐("평소보다 더"를 남과 비교로 봄, 고침), 하나는 맞게 버린 것("사장님이 … 꺼냈다면")
- 다시 묻기를 넣은 뒤(11/15) Qwen 은 2개 미만이 0번이다. 다만 35번 모두 버림 0, 다시 물음 0 이었다. "사장님이 …" 질문이 이번에는 나오지 않았다. **그래서 다시 묻기는 Qwen 에서 한 번도 돌지 않았다.** 프롬프트와 입력은 그대로였으니 temperature 0·seed 0 이어도 vLLM 출력이 실행마다 같지는 않은 것으로 보인다
- 다시 묻기 자체는 `--force-reask` 로 Haiku 에서 확인했다 (f02·f05 둘 다 다른 재료에서 새 질문 1개를 받음). Qwen 에서는 같은 명령으로 확인하면 된다
- 같은 사실 겹침 규칙은 위 표를 잰 뒤에 넣었다. 11/15 저녁 Qwen 으로 f05 를 돌리니 "혼자"를 두 번 물은 것 중 하나가 버려지고 다시 묻기가 돌았다 (3.0초, 2개). Qwen 에서 다시 묻기가 실제로 돈 첫 사례
- 그 실행에서 문제 셋이 나왔고 같은 날 고쳤다. ① f02 강제 확인에서 Qwen 이 뺀 질문을 출처만 바꿔 다시 냄 → 버린 질문과 닮으면 받지 않는다. ② f05 에서 뒤에 온 상상해보기가 버려져 해볼 것만 2개가 됨 → 남기는 쪽을 종류로 고른다. ③ 다시 받은 질문이 "오프라인이 아니라 온라인으로 고객 응대를 했다면"(편의점) → 프롬프트 v3 의 15번
- 프롬프트 v3 를 Qwen 으로 6건 돌렸다 (11/15 밤). 3·3·3·3·2·3개(평균 2.83, v2 와 같음), 1.8~3.0초, 버림 0, 시도 1. f05 는 해볼 것 1 + 상상해보기 1 이고 온라인으로 바꾼 질문은 없었다. `--force-reask` 는 f02 에서 새 질문 1개(3.4초), f05 에서 0개. 이 실행에서 ①② 필터는 한 번도 걸리지 않았다 (프롬프트가 먼저 막음). 위 표는 전부 v2 까지다. v3 Haiku 로는 6건을 한 번 돌려 3·4·3·3·2·4개(평균 3.17), 버림 0, 온라인으로 바꾼 질문 0 이었다. Haiku 에서는 ①② 꼴이 나오지 않아 그 둘은 테스트로만 확인했다
- 그 Qwen v3 출력은 말투가 섞여 있었다 ("…해볼까?", "…했을지?", "…궁금해."). 11/15 재현 결정으로 **존댓말**로 맞췄다: 프롬프트 v4 의 "말투" 절 (해볼 것은 "~해보는 것은 어떨까요?", 상상해보기는 "~였다면 어땠을까요?"), 그리고 끝이 "요?"가 아닌 질문은 필터가 버린다 (`constants.QUESTION_ENDING`, 이유 `filters.TONE`). 말투로 버린 질문은 다시 묻기에서 고쳐 내면 받는다. 되돌리려면 `QUESTION_PROMPT = "question_v3"`, `QUESTION_ENDING = r"\?$"`
- **프롬프트 v4 는 Qwen 으로 아직 안 쟀다.** Haiku 로 6건을 두 번 돌려 3·3·2·3·3·4개, 3·3·3·3·3·3개였고 36개 모두 "요?"로 끝났다. 말투로 버린 것 0. 첫 실행에서 읽는 사람에게 높임을 붙인 "더 쓰셨을까요?"가 하나 나와 프롬프트에 예시를 더했고 두 번째 실행에서는 없었다. 필터는 이 꼴을 잡지 않는다
- v2 에서는 상황 칩이 없는 기록(필수만, + 조건)에 상상해보기가 0개다. 기록에 없는 상황을 지어내지 않게 한 결과. 앱이 두 종류가 다 있다고 가정하면 안 된다
- v2 Haiku 에서 2개 미만으로 떨어진 경우는 전부 필터 오탐이었다 ("얘기하는 게 좋다면", "부족하지 않았다면"). 11/15 에 고침
