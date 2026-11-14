"""글자 수·개수 제한·임계값은 전부 여기 (CLAUDE.md 12장). 화면·함수마다 숫자를 박지 않는다."""

# 질문 생성
QUESTION_MIN = 2                 # 글이 적으면 2개 (TODO(기획): 보완 질문 추가 여부 검토 중)
QUESTION_MAX = 4
QUESTION_TEXT_MAX = 80           # 한 문장 길이 상한. 넘으면 버림
QUESTION_REGEN_PER_DAY = 1       # 사용자 재생성 하루 1회, 한국 시간 자정 기준 (TODO(기획): "기록할 때마다 한 번"으로 바뀔 수 있음)
QUESTION_SAVE_MAX = 20           # 담기 최대
LLM_MAX_ATTEMPTS = 3             # 서버 자동 재시도. 하루 횟수에 안 셈
LLM_RETRY_BACKOFF_S = (1.0, 2.0) # 1차 실패 후 1초, 2차 실패 후 2초
QUESTION_DEDUPE_SIMILARITY = 0.5 # 문자 2-gram 자카드. 이상이면 겹치는 질문으로 보고 뒤의 것을 버림

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
