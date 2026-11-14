"""글자 수·개수 제한·임계값은 전부 여기 (CLAUDE.md 12장). 화면·함수마다 숫자를 박지 않는다."""

# 질문 생성
QUESTION_MIN = 2                 # 글이 적으면 2개 (TODO(기획): 보완 질문 추가 여부 검토 중)
QUESTION_MAX = 4
QUESTION_TEXT_MAX = 80           # 한 문장 길이 상한. 넘으면 버림
QUESTION_REGEN_PER_DAY = 1       # 사용자 재생성 하루 1회, 한국 시간 자정 기준 (TODO(기획): "기록할 때마다 한 번"으로 바뀔 수 있음)
QUESTION_SAVE_MAX = 20           # 담기 최대
LLM_MAX_ATTEMPTS = 3             # 서버 자동 재시도. 하루 횟수에 안 셈
LLM_MAX_TOKENS = 4096             # 응답 상한. 사고(thinking) 토큰까지 세는 모델이 있어 1024 는 모자람
LLM_RETRY_BACKOFF_S = (1.0, 2.0) # 1차 실패 후 1초, 2차 실패 후 2초
QUESTION_DEDUPE_SIMILARITY = 0.5 # 문자 2-gram 자카드. 이상이면 겹치는 질문으로 보고 뒤의 것을 버림

# 주기 점검 (DB 웹훅은 실패해도 재전송이 없어서 서버가 빈 곳을 채움)
SWEEP_INTERVAL_S = 300           # 5분마다
SWEEP_MIN_AGE_S = 60             # 저장 직후 웹훅이 오는 중일 수 있으니 1분은 기다림
SWEEP_BATCH_LIMIT = 20           # 한 번에 최대 몇 개

# 임베딩 (공개된 경험만. 모델 BGE-M3 1024차원 코사인 — 바꾸면 전부 다시 돌려야 해서 안 바꿈)
EMBED_DIM = 1024
EMBED_TEXT_MAX_CHARS = 2000      # 한 문단 상한. BGE-M3 는 8192 토큰까지 받지만 카드는 이보다 훨씬 짧다
EMBED_SWEEP_MIN_AGE_S = 60       # 공개 직후 웹훅이 오는 중일 수 있으니 1분은 기다림
EMBED_SWEEP_BATCH_LIMIT = 50

# 관련 경험
RELATED_MAX = 10
RELATED_CANDIDATES = 30
RELATED_MIN_SIMILARITY = 0.35    # BGE-M3 코사인. 실제 데이터로 다시 맞춘다

# 분석
ANALYSIS_MIN_RECORDS = 3         # 미만이면 잠김. 회차 기록도 셈
PATTERN_MIN_EVIDENCE = 2

# 알림
NOTIFICATION_KEEP_DAYS = 30
QUIET_START = "22:00"
QUIET_END = "08:00"
TZ = "Asia/Seoul"
