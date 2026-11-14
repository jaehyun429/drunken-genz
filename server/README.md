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
4. 사용자 재생성 버튼 → `POST /v1/questions/regenerate` (JWT). 하루 1회 KST, 초과 429 `QUESTION_REGEN_LIMIT`

## DB 없이 데모
```bash
python -m scripts.demo_questions          # 가짜 경험 6개 → 질문·버린 것·이유 출력
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
- 공개 확정 → 소윤 DB 웹훅 `POST /v1/internal/experience-published` → `experience.embedding` 채움. 비공개로 내려오면 비우고, 그 카드를 담은 `related_cache` 를 지움(다음 조회 때 재계산)
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

## 폴더
```
app/config.py        환경변수        app/llm/client.py           LLM 호출·재시도·실패 분류
app/constants.py     숫자 전부       app/llm/prompts/question_v1.md  프롬프트 (버전 붙임)
app/errors.py        에러 형식       app/questions/prompt.py     경험 → 입력 문단·해시
app/auth.py          JWT·웹훅 비밀   app/questions/filters.py    응답 후 걸러내기
app/db.py            asyncpg 풀      app/questions/service.py    batch·저장
                                     app/questions/router.py     엔드포인트
app/cards/public.py  PublicCard 조립   app/related/rerank.py       LLM 재정렬·이유 걸러내기
app/embedding/       임베딩·웹훅·sweep  app/related/service.py      후보·캐시·새 카드 재계산
```

## Supabase 에 붙일 때 (소윤과 함께)
1. Supabase SQL Editor 에서 `db/001_jaehyun_schema.sql` → `002_jaehyun_rls.sql` 적용 (000 은 적용하지 않음. 소윤 테이블이 먼저 있어야 함)
2. Database → Webhooks → 새 웹훅: 테이블 `experience`, 이벤트 `INSERT`, HTTP POST `https://<내 서버>/v1/internal/experience-saved`, 헤더 `X-Webhook-Secret: <WEBHOOK_SECRET>`
3. `.env` 의 `SUPABASE_JWT_SECRET` 은 Settings → API → JWT Secret. `AUTH_DEV_BYPASS=0`
4. `experience_log` 테이블은 진행 중 활동의 회차 기록을 질문 입력에 넣을 때 읽음 (소윤 테이블, 읽기만)

## GPU 서버에서 데모 돌리기 (11/17)
컨테이너 안 `jhjeong/genz` 에 이 폴더를 올린 뒤:
```bash
cd /NHNHOME/WORKSPACE/26moe002_H/jhjeong/genz/server
uv venv .venv && uv pip install --python .venv/bin/python -e .
cp .env.example .env            # LLM_BASE_URL=http://localhost:18000/v1 그대로
.venv/bin/python -m scripts.demo_questions
```
vLLM 이 떠 있어야 함: `bash ../llm_test/serve.sh qwen27` (PORT=18000).

기록을 어디까지 채웠느냐에 따른 질문 개수 비교 (DB 필요 없음. Qwen 은 temperature 0 이라 반복은 1번이면 됨):
```bash
LLM_TIMEOUT_S=60 .venv/bin/python -m scripts.eval_record_depth 1 depth_qwen.json
```
11/14 Haiku 3회 평균: 필수만 2.06개 → 전부 2.89개. 개수보다 질문 출처(기간·활동명 vs 상황·한 줄) 차이가 큼.
