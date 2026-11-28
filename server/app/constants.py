"""글자 수·개수 제한·임계값은 전부 여기 (CLAUDE.md 12장). 화면·함수마다 숫자를 박지 않는다."""

# 인증 (app/auth.py). 명세에 없는 값이라 재현이 정함 (11/16)
AUTH_JWT_AUDIENCE = "authenticated"   # Supabase 로그인 사용자 토큰의 aud. anon·service_role 키는 aud 가 없어 걸러진다
AUTH_JWKS_ALGS = ("ES256", "RS256")   # 공개키로 검증하는 방식. Supabase 가 낼 수 있는 비대칭 두 가지
AUTH_JWKS_TTL_S = 600                 # 공개키를 담아 두는 시간. Supabase 쪽 캐시도 10분
AUTH_JWKS_REFETCH_MIN_S = 30          # 모르는 kid 가 와도 이 간격보다 자주 다시 받지 않는다
AUTH_JWKS_TIMEOUT_S = 5.0
AUTH_JWT_LEEWAY_S = 30                # 서버 시계가 조금 달라도 방금 받은 토큰을 거절하지 않게

# 질문 생성 (11/14 기획 변경: PRD 7.2·7.2.1, 기능명세서 14장. API 명세 v0.3.2)
QUESTION_COUNT = 4               # 경험 하나당 4개 고정. 더 적어보기 0~2 + 나머지는 해볼 것·상상해보기 (각 1개 이상)
                                 # TODO(기획): 모델이 4개를 못 채우면 있는 만큼만 저장한다
QUESTION_WRITE_MORE_MAX = 2      # 더 적어보기 최대. 빈 칸 우선순위는 schemas.AXES_WRITE_MORE 순서
QUESTION_CANDIDATE_MAX = 10      # 모델이 낸 후보 가운데 걸러낸 뒤 들고 있는 수. 여기서 4개를 고른다 (generate.compose)
QUESTION_TEXT_MAX = 40           # 한 문장 길이 상한. 넘으면 버림. TODO(기획): PRD 7.2 의 [제안 수치]. 확정 전
QUESTION_ANSWER_MAX = 80         # 더 적어보기 답 한 줄 (기능명세서 14.1). db/003 의 check 와 같은 값
QUESTION_REGEN_PER_WINDOW = 1    # 사용자 재생성: 기록당 24시간에 1번
QUESTION_REGEN_WINDOW_H = 24     #   + 그 24시간 안에 더 적어보기에 답한 수만큼 더.
                                 # TODO(기획): 답으로 생긴 1번이 언제까지 유효한지, 새 질문이 없던 시도도 세는지 미정 (지금은 24시간, 센다)
QUESTION_SAVE_MAX = 20           # 담기 최대
# 상황 칩 가운데 '내가 내린 결정'(PRD 6.6, 6개). 이 칩은 상상해보기가 아니라 해볼 것(axis=decision)의 재료다.
# 띄어쓰기를 뺀 글자가 같을 때만 맞는 것으로 본다. 직접 입력한 상황 칩은 전부 '고를 수 없음'.
# TODO(기획): 앱이 쓰는 칩 표가 레포에 없어 PRD 글자 그대로 넣었다. 칩 표가 나오면 거기서 읽는다
QUESTION_DECISION_CHIPS = ("범위 축소", "피드백 반영", "새 형식 시도", "재실험", "원인 추적", "수업 방식 변경")
LLM_MAX_ATTEMPTS = 3             # 서버 자동 재시도. 재생성 횟수에 안 셈
LLM_MAX_TOKENS = 4096             # 응답 상한. 사고(thinking) 토큰까지 세는 모델이 있어 1024 는 모자람
LLM_RETRY_BACKOFF_S = (1.0, 2.0) # 1차 실패 후 1초, 2차 실패 후 2초
QUESTION_DEDUPE_SIMILARITY = 0.5 # 문자 2-gram 자카드. 이상이면 겹치는 질문으로 보고 뒤의 것을 버림. 이전에 보여준 질문과 견줄 때도 같은 값
QUESTION_REASK_MAX = 1           # 걸러낸 뒤 4개가 안 되거나 해볼 것·상상해보기 중 하나가 없으면 다시 묻는 횟수. 재생성 횟수에 안 셈
QUESTION_REASK_PROMPT = "question_reask_v2"   # 다시 물을 때 사용자 메시지 뒤에 붙이는 글
QUESTION_SOURCE_SNAP_SIMILARITY = 0.6  # 출처가 기록에 없는 글자면(오타 등) 이 이상 닮은 칩·문장으로 바꾼다. 문자 2-gram 자카드
QUESTION_PROMPT = "question_v7"  # app/llm/prompts/<이름>.md
# 질문 끝맺음. 프롬프트의 문장 틀과 짝이다. PRD 7.2 문장 틀이 전부 "요?"로 끝난다 (v3 로 되돌리면 r"\?$").
# 문장 틀 자체("다음엔 ~해보면 어떨까요?")는 프롬프트로만 맞춘다. 필터는 끝맺음만 본다
QUESTION_ENDING = r"요\?$"

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

# 분석 (CLAUDE.local.md 6장). 아래에서 ANALYSIS_MIN_RECORDS·PATTERN_MIN_EVIDENCE 말고는 명세에 없어 11/15 구현하면서 정한 값
ANALYSIS_MIN_RECORDS = 3         # 미만이면 잠김. 회차 기록도 셈
PATTERN_MIN_EVIDENCE = 2         # 패턴 하나의 근거 기록 수 하한. 회차 기록도 기록 하나로 센다
ANALYSIS_PROMPT = "analysis_v2"  # app/llm/prompts/<이름>.md. v2 (11/22): 남긴 문장·숨긴 문장의 근거를 다시 센다 (기능명세서 15장)
ANALYSIS_PATTERN_MAX = 5         # 내려주는 패턴 수 상한. 근거 수 많은 순 (기능명세서 15장: 처음 3개, 더보기로 5개. 3개에서 자르는 것은 앱)
ANALYSIS_RESURFACE_MIN_RECORDS = 5   # "아니에요" 패턴을 다시 보여줄 수 있는 전체 기록 수 (15장 "기록 5개(처음 3 + 추가 2) 이후").
                                     # TODO(기획): 전체 기록 수로 읽었다. "아니에요" 뒤로 2개 더인지 확인
ANALYSIS_RESURFACE_NEW_EVIDENCE = 1  # "아니에요" 뒤에 쓴 기록 가운데 같은 내용이 적힌 기록이 이만큼 있어야 다시 보여준다 (한 번만).
                                     # TODO(기획): 15장은 "새 기록에서 같은 선택이 더 반복됐을 때"라고만 한다
ANALYSIS_TEXT_MAX = 80           # 패턴 한 문장 길이 상한. 넘으면 버림
ANALYSIS_EDIT_MAX = 60           # "수정할게요"로 고친 문장 길이 상한 (기능명세서 ANA-01b-①: 최대 60자)
# 패턴 끝맺음. 프롬프트의 말투와 짝이라 같이 바꾼다. 질문(v4)과 같이 존댓말, 평서문 "~요."
# TODO(기획): 말투는 PRD 7장 톤 규칙이 정한다
ANALYSIS_ENDING = r"요\.$"
ANALYSIS_DEDUPE_SIMILARITY = 0.5 # 틀 낱말을 뺀 문자 2-gram 자카드 (analysis/filters.alike). 이상이면 이미 있는 패턴과 겹친다고 보고 버림
ANALYSIS_BLOCK_SIMILARITY = 0.5  # "아니에요"를 받은 패턴과 이 이상 닮으면 다시 내지 않는다. 낱말이 거의 같은 것만 잡힌다.
                                 # 바꿔 말한 것은 프롬프트의 "만들지 않을 문장"에 맡긴다. TODO(기획): "비슷한 내용"의 범위
ANALYSIS_INPUT_MAX_RECORDS = 60  # LLM 에 넣는 기록 수 상한. 넘으면 최근 것부터. record_count 는 전체를 센다
ANALYSIS_AVOID_PROMPT_MAX = 20   # 프롬프트의 "만들지 않을 문장"에 넣는 개수 상한 (최근 것부터). 필터는 전부와 견준다
ANALYSIS_LLM_ATTEMPTS = 2        # /analysis 는 앱이 기다리는 호출이라 질문 생성(3회)보다 덜 재시도. 실패하면 AI_UNAVAILABLE

# 알림
NOTIFICATION_KEEP_DAYS = 30
TZ = "Asia/Seoul"
# 푸시 (이슈 #18). 아래는 명세에 없어 11/22 구현하면서 정한 값
NOTIFY_TICK_S = 60                # 밀린 푸시를 보는 주기. 정해진 시각 푸시(오전 9시, 저녁 6시)도 이 주기 안에 나간다
NOTIFY_PURGE_EVERY_TICKS = 60     # 오래된 알림 삭제는 한 시간에 한 번
PUSH_BATCH = 500                  # 한 번에 보는 알림 수
PUSH_MAX_AGE_H = 24               # 이보다 오래 밀린 알림은 푸시하지 않는다 (서버가 오래 꺼져 있었을 때 한꺼번에 쏟아지지 않게)
PUSH_CHUNK = 100                  # Expo 가 한 요청에 받는 메시지 수 상한
PUSH_TIMEOUT_S = 10.0
RELATED_NOTIFY_HOUR = 9            # 새 관련 경험 알림을 만드는 시각 (한국 시간). 9시대에 한 번, 서버가 그 시간 내내 꺼져 있었으면 다음 날 (지은 11/14)
# 11/24 회의·기능명세서 16장: 방해 금지 시간은 없앴다. 켜고 끄는 단위는 종류(type)가 아니라 아래 6가지 분류다.
# notification_setting.types 의 키는 분류 이름. 없는 키는 켬. 예전처럼 종류 이름으로 꺼 둔 값도 계속 받는다
NOTIFICATION_CATEGORIES = {
    "qa": ("new_question", "answer"),            # 질문/답변
    "recommend": ("related_found",),              # 추천 경험
    "reach": ("helpful", "saved"),                # 내 경험이 닿은 곳
    "reminder": ("reminder",),                    # 진행 중 활동 리마인드
    "draft": ("draft",),                          # 미뤄둔 경험 (앱 로컬 알림일 수 있다. 저장 위치 미정)
    "verification": ("verification",),            # 증빙 (알림은 아직 안 만든다)
}
PUSH_FIXED_HOUR = {"helpful": 18, "saved": 18}   # 생긴 즉시가 아니라 그 뒤 처음 오는 이 시각(한국 시간)에 푸시. 내 경험이 닿은 곳 = 저녁 6시 (11/24)
PUSH_ANDROID_CHANNELS = False      # True 면 푸시에 channelId(분류 이름)를 싣는다. 앱이 그 채널을 만들기 전에 켜면 안드로이드에서 알림이 안 뜬다 (Expo 문서)
REMINDER_WEEK_START = 0            # 리마인드의 "그 주" 가 시작하는 요일. 0=월 … 6=일 (date.weekday). 명세에 없어 월요일로 잡음 TODO(기획)
REMINDER_LATE_MAX_MIN = 60         # 리마인드 시각을 이만큼 넘기면 그날 것은 건너뛴다 (서버가 꺼져 있다 켜졌을 때 한참 늦은 리마인드를 보내지 않게)
