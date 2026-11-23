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
QUESTION_REASK_MAX = 1           # 걸러낸 뒤 QUESTION_MIN 미만이면 버린 이유를 알려주고 다시 묻는 횟수. 하루 횟수에 안 셈
QUESTION_REASK_PROMPT = "question_reask_v1"   # 다시 물을 때 사용자 메시지 뒤에 붙이는 글
QUESTION_SOURCE_SNAP_SIMILARITY = 0.6  # 출처가 기록에 없는 글자면(오타 등) 이 이상 닮은 칩·문장으로 바꾼다. 문자 2-gram 자카드
QUESTION_PROMPT = "question_v4"  # app/llm/prompts/<이름>.md. 버전을 올리면 예전 버전으로 만든 질문은 재사용하지 않는다
# 질문 끝맺음. 프롬프트의 말투와 짝이라 같이 바꾼다 (v4 부터 존댓말. v3 로 되돌리면 r"\?$").
# TODO(기획): 말투는 PRD 7장 톤 규칙이 정한다. 11/15 재현 결정으로 존댓말 "~해보는 것은 어떨까요?"를 임시값으로 쓴다
QUESTION_ENDING = r"요\?$"
# TODO(기획): v2 는 상황 칩이 없는 기록에 상상해보기를 0개 낼 수 있다 (기록에 없는 상황을 지어내지 않음).
#   두 종류가 꼭 하나씩 있어야 하는지, 앱 화면이 그걸 가정하는지 확인 필요

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
RELATED_MIN_SIMILARITY = 0.55    # BGE-M3 코사인. 11/14 유튜브 추출본 481장 실측: 질문만 넣으면 전체 중앙값 0.36~0.41, 상위 0.5~0.58 로
                                 # 0.35 는 아무것도 못 거름. 질문을 "출처 경험 맥락 + 질문" 으로 넣으면 맞는 카드 0.64~0.70, 중앙값 0.41~0.47
                                 # → 0.55. 팀 카드 20장 들어오면 다시 본다 (scripts/embed_eval.py)
RELATED_QUERY_WITH_CONTEXT = True # 관련 경험 검색어 = 출처 경험(활동/장르/역할/한 일) + 질문. 질문만 넣는 것보다 top1 이 +0.1~0.17
RELATED_FILTER_CANDIDATES = 100  # filter 가 all 이 아니면 걸러낸 뒤에도 30개가 남게 넉넉히 뽑는다
RELATED_REASON_MAX = 60          # "왜 관련 있는지" 한 줄 상한. 넘으면 reason 만 null (카드는 남김). 결정: 명세에 길이 없음
RELATED_LLM_ATTEMPTS = 2         # /related 는 앱이 기다리는 호출이라 질문 생성(3회)보다 덜 재시도. 실패하면 유사도 순으로 내려주고 캐시 안 함
RELATED_NOTIFY_RECHECK_LIMIT = 200  # 새 공개 카드 하나당 맞춰볼 notify_on_related 질문 수 상한

# 공식 활동 검색 (소윤)
ACTIVITY_SEARCH_MIN_CHARS = 2    # 띄어쓰기 빼고 센다
ACTIVITY_SEARCH_LIMIT = 5
ACTIVITY_RECORD_COUNT_MIN = 5    # 기록 수가 이보다 적으면 null (명세 1.8)

# 프로필 (소윤). db/000_soyun_record.sql profile 의 check 와 같아야 함
PROFILE_NICKNAME_MIN = 2         # 피그마 PRF-⑨
PROFILE_NICKNAME_MAX = 12
PROFILE_BIO_MAX = 100            # 공개용 한 문장. TODO(기획): 길이 (기록 한 줄과 같게 둠)
PROFILE_SCHOOL_MAX = 40
PROFILE_MAJOR_MAX = 40
PROFILE_GRADE_MAX = 10

# 공개 스냅샷 (소윤). 모양을 바꾸면 올림 — 읽는 쪽(cards/public.py, embedding/text.py)이 옛 모양을 구분할 수 있게
SNAPSHOT_VERSION = 1

# 분석
ANALYSIS_MIN_RECORDS = 3         # 미만이면 잠김. 회차 기록도 셈
PATTERN_MIN_EVIDENCE = 2

# 알림
NOTIFICATION_KEEP_DAYS = 30
QUIET_START = "22:00"
QUIET_END = "08:00"
TZ = "Asia/Seoul"
