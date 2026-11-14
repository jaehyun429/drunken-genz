-- 재현 담당: 질문·탐색, 분석, 알림 (API 명세 v0.2 1.3~1.6 + 명세에 없는 보조 테이블)
-- supabase/migrations/ 에 넣어서 `supabase db push`. 소윤 테이블(experience, profile 등)이 먼저 있어야 함.
-- 명세에 없어서 임의로 정한 건 "-- TODO(기획)" 또는 "-- 결정:" 으로 표시. db/README.md 에 모아둠.

create extension if not exists vector;
do $$ begin create extension if not exists pg_cron; exception when others then raise notice 'pg_cron 없음 (로컬). 알림 삭제 cron 은 건너뜀'; end $$;

-- =====================================================================
-- 1. question — AI 질문 (명세 1.3)
-- =====================================================================
create table question (
  id                   uuid primary key default gen_random_uuid(),
  experience_id        uuid references experience(id) on delete set null,  -- 경험 삭제 시 담은 질문은 남김 (CLAUDE.md 4장). 안 담은 건 트리거로 삭제
  user_id              uuid not null references auth.users(id) on delete cascade,
  source_activity_name text not null,                       -- 출처 경험이 지워져도 "삭제된 기록: {활동명}" 표시용
  type                 text not null check (type in ('try_next', 'imagine')),
  text                 text not null,
  source               text not null,                       -- "어느 칩·어느 문장에서"
  axis                 text,                                -- 바꾼 변수 하나: role/scale/method/autonomy/did/field (try_next), situation/external (imagine)
  is_done              boolean not null default false,      -- 내 다른 기록과 겹침 (서버 판정)
  done_experience_id   uuid references experience(id) on delete set null,  -- 겹친다고 판정한 내 기록
  done_dismissed       boolean not null default false,      -- 사용자가 "아니에요" (앱이 바꿈). 화면은 is_done and not done_dismissed
  saved                boolean not null default false,      -- 담기 (앱이 바꿈)
  notify_on_related    boolean not null default false,      -- 관련 경험 0개일 때 "새 경험 생기면 알려드릴까요?" 좋아요 (앱이 바꿈). 지은 11/14
  related_new          boolean not null default false,      -- 새 관련 경험이 붙었음 표시. 서버가 켜고 앱이 끔 (앱이 바꿈)
  memo                 text,                                -- imagine 메모 (앱이 바꿈)
  linked_experience_id uuid references experience(id) on delete set null,  -- 해봤어요 직접 연결 (앱이 바꿈)
  generation           int  not null default 1,            -- 재생성 회차
  created_at           timestamptz not null default now()
);
create index question_user_exp_idx on question (user_id, experience_id, generation desc);
create index question_saved_idx    on question (user_id) where saved;

-- 재생성 하루 1회 + 입력 해시 캐시. 경험당 생성 기록 (명세 "입력 해시 캐시", "하루 1번")
create table question_batch (
  id             uuid primary key default gen_random_uuid(),
  experience_id  uuid not null references experience(id) on delete cascade,
  user_id        uuid not null references auth.users(id) on delete cascade,
  generation     int  not null,
  trigger        text not null check (trigger in ('save', 'user')),   -- 저장 웹훅 / 사용자 재생성 버튼. 하루 1회는 'user'만 셈
  input_hash     text not null,          -- 임베딩 입력과 같은 문단의 sha256. 같으면 LLM 안 부르고 재사용
  model          text,                   -- 어떤 모델이 만들었는지 (환경변수 값)
  prompt_version text,                   -- 프롬프트 파일 버전
  status         text not null default 'pending' check (status in ('pending', 'done', 'failed')),  -- 앱이 "준비 중" 띄우는 근거
  failure_reason text check (failure_reason in ('connection', 'model')),  -- connection: 앱이 "다시 시도" 버튼 / model: 안내만
  error_detail   text,                   -- 디버깅용. 사용자 글 원문은 넣지 않음
  attempts       int  not null default 0,  -- 서버 자동 재시도 횟수 (최대 3)
  created_at     timestamptz not null default now(),
  finished_at    timestamptz,
  unique (experience_id, generation)
);
create index question_batch_daily_idx on question_batch (experience_id, created_at desc);

-- =====================================================================
-- 2. related_cache — 관련 경험 결과 캐시 (명세 POST /related "처음 열 때 계산하고 캐시")
-- =====================================================================
create table related_cache (
  question_id uuid not null references question(id) on delete cascade,
  filter      text not null default 'all',   -- all / same_role / different_role / genre:{장르}
  items       jsonb not null,                -- [{experience_id, reason, score}] 최대 10. 비공개·차단은 읽을 때 걸러냄
  -- 재계산 (지은 11/14): items 가 비어 있고 question.notify_on_related 인 질문은 새 공개 카드가 생기면 다시 계산.
  -- 카드가 비공개·삭제되면 그 카드를 담고 있던 캐시도 다시 계산.
  model       text,
  computed_at timestamptz not null default now(),
  primary key (question_id, filter)
);
-- 무효화: 재생성 시 question 행이 cascade 로 지워지므로 자동. 카드 비공개·차단은 서버가 읽을 때 필터.

-- =====================================================================
-- 3. analysis — 분석 (명세 POST /analysis, /analysis/feedback)
-- =====================================================================
create table analysis_run (
  user_id       uuid primary key references auth.users(id) on delete cascade,
  record_count  int  not null,              -- 계산 당시 기록 수. 3 미만이면 locked
  criteria      jsonb,                      -- { "private": "...", "public": "..." }
  input_hash    text,                       -- 기록 집합 해시. 같으면 has_new_records=false
  model         text,
  last_updated  timestamptz not null default now()
);

create table analysis_pattern (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  text        text not null,
  evidence    jsonb not null,               -- [{experience_id, activity_name}] 2개 이상
  feedback    text check (feedback in ('yes', 'edit', 'no', 'later')),
  edited_text text,
  hidden      boolean not null default false,  -- feedback='no' 면 true. 다시 안 만듦 (text 유사도로 재등장 막기)
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index analysis_pattern_user_idx on analysis_pattern (user_id, hidden);

-- =====================================================================
-- 4. notification — 알림 (명세 1.4)
-- =====================================================================
create table notification (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  type        text not null check (type in (
                'new_question', 'answer', 'helpful', 'saved', 'related_found', 'reminder',
                'draft', 'verification', 'coffeechat_request', 'coffeechat_result')),
  payload     jsonb not null default '{}',   -- 이동할 화면 정보 {experience_id, qa_id, question_id ...}
  grouped_key text,                           -- 하루 묶음용 예: "new_question:{experience_id}:2026-11-10"
  read        boolean not null default false,
  count       int     not null default 1,     -- 묶음 개수. 같은 grouped_key 로 또 생기면 행을 새로 만들지 않고 +1 ("질문 1→3개"). 지은 11/14
  pushed_at   timestamptz,                    -- 실제 푸시 보낸 시각. null = 방해 금지로 아직 안 보냄. 묶음은 첫 건만 푸시
  created_at  timestamptz not null default now()
);
create index notification_user_idx   on notification (user_id, created_at desc);
create index notification_unread_idx on notification (user_id) where not read;
create unique index notification_grouped_uq on notification (user_id, grouped_key) where grouped_key is not null;
create index notification_pending_push_idx on notification (created_at) where pushed_at is null;

-- 30일 지난 알림 삭제 (명세 3장). 매일 04:00 KST = 19:00 UTC
do $$ begin
  perform cron.schedule('notification_purge', '0 19 * * *',
    $job$ delete from notification where created_at < now() - interval '30 days' $job$);
exception when others then raise notice 'pg_cron 없음. notification_purge 건너뜀'; end $$;

-- =====================================================================
-- 5. notification_setting (명세 1.5)
-- =====================================================================
create table notification_setting (
  user_id      uuid primary key references auth.users(id) on delete cascade,
  push_enabled boolean not null default true,
  types        jsonb   not null default '{}',   -- {"new_question": true, "reminder": false, ...} 없는 키는 on
  quiet_start  time    not null default '22:00',
  quiet_end    time    not null default '08:00',
  reminders    jsonb   not null default '{}',   -- { "<experience_id>": { "days": [1,3,5], "time": "21:00" } }
  updated_at   timestamptz not null default now()
);
-- 시간대는 Asia/Seoul 고정 (11/09 결정). 사용자별 시간대는 후순위라 컬럼 없음.

-- =====================================================================
-- 6. push_device — 기기별 푸시 토큰 (명세 1.6)
-- =====================================================================
create table push_device (
  user_id    uuid not null references auth.users(id) on delete cascade,
  device_id  text not null,
  token      text not null,
  platform   text not null check (platform in ('ios', 'android')),
  updated_at timestamptz not null default now(),
  primary key (user_id, device_id)
);

-- =====================================================================
-- 7. 경계: experience.embedding (소윤 테이블에 재현이 붙이는 컬럼)
-- =====================================================================
-- 소윤 마이그레이션에 experience 가 만들어진 뒤 실행. BGE-M3 = 1024차원.
alter table experience add column if not exists embedding vector(1024);
create index if not exists experience_embedding_idx on experience
  using hnsw (embedding vector_cosine_ops) where visibility <> 'private';

-- 경험 삭제 시: 담은 질문은 남기고(experience_id → null), 안 담은 질문은 지움 (CLAUDE.md 4장 "다시 만들기")
create or replace function question_on_experience_delete() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  delete from question where experience_id = old.id and not saved;
  return old;
end $$;
create trigger question_on_experience_delete_trg before delete on experience
  for each row execute function question_on_experience_delete();

-- 관련 경험 후보 뽑기. FastAPI(service_role)만 호출. 비공개·본인·차단 제외는 여기서 1차, 서버에서 2차.
create or replace function match_public_cards(
  query_embedding vector(1024), me uuid, k int default 30, min_sim float default 0.35
) returns table (experience_id uuid, similarity float)
language sql stable security definer set search_path = public as $$
  select e.id, 1 - (e.embedding <=> query_embedding)
  from experience e
  where e.visibility <> 'private'
    and e.user_id <> me
    and e.embedding is not null
    and not exists (select 1 from block b
                    where (b.user_id = me and b.blocked_id = e.user_id)
                       or (b.user_id = e.user_id and b.blocked_id = me))
    and 1 - (e.embedding <=> query_embedding) >= min_sim
  order by e.embedding <=> query_embedding
  limit k
$$;
revoke all on function match_public_cards from public, anon, authenticated;
