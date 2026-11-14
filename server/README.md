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

## 폴더
```
app/config.py        환경변수        app/llm/client.py           LLM 호출·재시도·실패 분류
app/constants.py     숫자 전부       app/llm/prompts/question_v1.md  프롬프트 (버전 붙임)
app/errors.py        에러 형식       app/questions/prompt.py     경험 → 입력 문단·해시
app/auth.py          JWT·웹훅 비밀   app/questions/filters.py    응답 후 걸러내기
app/db.py            asyncpg 풀      app/questions/service.py    batch·저장
                                     app/questions/router.py     엔드포인트
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
