-- 소윤 담당: 기록 (experience, experience_log, official_activity) + 차단(block). 로컬 + Supabase.
-- 재현 001·002 가 experience 에 컬럼·트리거를 붙이므로 그보다 먼저 실행한다.
-- 컬럼 이름은 재현이 쓰던 임시 테이블(000_stub) 그대로 — 질문·분석·임베딩 코드가 이 이름을 읽는다.
-- 글자 수·개수 숫자는 server/app/constants.py 와 같아야 한다. 바꾸면 둘 다.
create extension if not exists pg_trgm;
do $$ begin create extension if not exists pg_net; exception when others then raise notice 'pg_net 없음 (로컬). 웹훅은 pg_notify 로 대신'; end $$;


-- ---------- 공식 활동 ----------
-- 앱은 직접 못 읽음 (pending 노출 제어). FastAPI /activities/* 만.
create table if not exists official_activity (
  id          uuid primary key default gen_random_uuid(),
  name        text not null check (char_length(name) between 2 and 40),
  organizer   text,
  aliases     text[] not null default '{}' check (cardinality(aliases) <= 3),
  status      text not null default 'pending' check (status in ('pending', 'verified')),
  created_by  uuid references auth.users(id) on delete set null,
  created_at  timestamptz not null default now()
);
-- 명세 1.8 의 record_count 는 컬럼으로 두지 않고 검색할 때 experience 를 센다 (값이 어긋날 일이 없게)

-- 11/18 추가 (결정 01 7장). add column if not exists 라 이미 만든 Supabase 에 다시 돌려도 됨
alter table official_activity
  add column if not exists genre text check (genre in ('행사 운영', '개발·IT', '디자인·콘텐츠', '음악·공연', '연구·실험',
                                                        '교육·멘토링', '경영·마케팅', '서비스·응대', '기타')),
  add column if not exists type text check (type in ('동아리·학회', '팀플·공모전', '대외활동', '학생회·행사', '아르바이트',
                                                      '봉사·멘토링', '수업·캡스톤'));
-- 11/20: 기수는 목록에 안 넣는다. 활동마다 한 줄이고, 사용자가 기록할 때 experience.official_period 로 고른다
-- (처음 만든 테이블엔 기수 칸이 있었음. 다시 돌리면 지움 — 그 칸을 쓰던 인덱스 먼저)
drop index if exists official_activity_name_period_key;
alter table official_activity drop column if exists period, drop column if exists series_key;

-- 검색용 글자: 이름 + 별칭, 소문자, 띄어쓰기 없앰. "취업 박람회" 로 "취업박람회" 를 찾게
create or replace function activity_search_text(name text, aliases text[]) returns text
  language sql immutable as $$
  select lower(regexp_replace(name || ' ' || array_to_string(aliases, ' '), '\s', '', 'g'))
$$;
-- 초성 검색용: 한글 음절은 초성으로, 나머지 글자는 그대로. "취업박람회" → "ㅊㅇㅂㄹㅎ" (11/18, 피그마 REC-01-③)
create or replace function hangul_initials(s text) returns text
  language plpgsql immutable as $$
declare
  cho constant text[] := array['ㄱ','ㄲ','ㄴ','ㄷ','ㄸ','ㄹ','ㅁ','ㅂ','ㅃ','ㅅ','ㅆ','ㅇ','ㅈ','ㅉ','ㅊ','ㅋ','ㅌ','ㅍ','ㅎ'];
  out text := '';
  ch  text;
  c   int;
begin
  foreach ch in array regexp_split_to_array(coalesce(s, ''), '') loop
    c := ascii(ch);                                   -- UTF8 DB 에선 유니코드 번호
    if c between 44032 and 55203 then                 -- 가(AC00) ~ 힣(D7A3)
      out := out || cho[(c - 44032) / 588 + 1];
    else
      out := out || ch;
    end if;
  end loop;
  return out;
end $$;

-- 같은 이름(띄어쓰기·대소문자 무시)은 한 행만. 목록 넣기 스크립트가 이걸로 덮어쓴다 (server/scripts/import_activities.py)
-- activity_search_text 를 쓰므로 그 함수 뒤에 있어야 함 (빈 DB 에서 순서대로 돌 때)
create unique index if not exists official_activity_name_key
  on official_activity (activity_search_text(name, '{}'));
create index if not exists official_activity_search_idx on official_activity
  using gin (activity_search_text(name, aliases) gin_trgm_ops) where status = 'verified';

alter table official_activity enable row level security;   -- 정책 없음 = 앱은 아무것도 못 함
revoke all on official_activity from anon, authenticated;


-- ---------- 경험 ----------
-- 보강글 jsonb({칩: 글}) 의 값이 전부 글자이고 max 자 이하인지
create or replace function jsonb_notes_ok(notes jsonb, max int) returns boolean
  language sql immutable as $$
  select jsonb_typeof(notes) = 'object'
     and coalesce((select bool_and(jsonb_typeof(v) = 'string' and char_length(v #>> '{}') <= max)
                   from jsonb_each(notes) as t(k, v)), true)
$$;

create table if not exists experience (
  id                   uuid primary key default gen_random_uuid(),
  user_id              uuid not null default auth.uid() references auth.users(id) on delete cascade,
  status               text not null default 'done' check (status in ('in_progress', 'done')),
  activity_name        text not null check (char_length(activity_name) between 2 and 40),
  official_activity_id uuid references official_activity(id) on delete set null,
  official_period      smallint check (official_period between 1 and 99),   -- 기수 (선택). 공식 활동마다 1~99 중 사용자가 고름 (11/20)
  -- 분야 (화면 말은 "분야", 코드 이름은 genre 유지 — 재현 코드·PublicCard 가 genre 로 읽음). 11/16 REC-02 시안
  genre                text not null check (genre in ('행사 운영', '개발·IT', '디자인·콘텐츠', '음악·공연', '연구·실험',
                                                       '교육·멘토링', '경영·마케팅', '서비스·응대', '기타')),
  -- 유형 (선택): 어떤 자리에서 한 활동인지. 분야와 별개
  type                 text check (type in ('동아리·학회', '팀플·공모전', '대외활동', '학생회·행사', '아르바이트',
                                            '봉사·멘토링', '수업·캡스톤')),
  role                 text not null check (char_length(role) between 2 and 20),
  period_start         text not null check (period_start ~ '^\d{4}-(0[1-9]|1[0-2])$'),   -- "2026-03"
  period_end           text check (period_end ~ '^\d{4}-(0[1-9]|1[0-2])$' and period_end >= period_start),
  place                text check (char_length(place) <= 30),
  did_chips            text[] not null default '{}' check (cardinality(did_chips) between 1 and 3),  -- 4단계, 1개는 있어야 저장
  did_notes            jsonb  not null default '{}' check (jsonb_notes_ok(did_notes, 80)),
  custom_chips         text[] not null default '{}',
  conditions           jsonb  not null default '{}' check (jsonb_typeof(conditions) = 'object'),
  situation_chips      text[] not null default '{}' check (cardinality(situation_chips) <= 2),
  situation_notes      jsonb  not null default '{}' check (jsonb_notes_ok(situation_notes, 80)),  -- TODO(기획): 상황 보강 길이. 한 일 보강과 같게 80
  emotion_chips        text[] not null default '{}' check (cardinality(emotion_chips) <= 2),
  one_liner            text check (char_length(one_liner) <= 100),
  ai_tags              text[] not null default '{}',
  photos               text[] not null default '{}',   -- Storage 경로 photos/{user_id}/{experience_id}/...
  pinned               boolean not null default false,
  -- 아래는 앱이 못 바꿈 (컬럼 권한). 공개 API 가 service_role 로 바꾼다
  visibility           text not null default 'private' check (visibility in ('private', 'anonymous', 'profile')),
  accept_questions     boolean not null default true,
  accept_coffeechat    boolean not null default false,
  published_snapshot   jsonb,
  masked               jsonb,
  published_at         timestamptz,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now()
);
-- 11/20 추가. 이미 만든 Supabase 에 다시 돌려도 됨
alter table experience add column if not exists official_period smallint check (official_period between 1 and 99);
create index if not exists experience_user_created_idx on experience (user_id, created_at desc);   -- 보관소 최신순 cursor

-- updated_at = 저장 후 수정의 버전. 앱은 .eq('updated_at', 받은 값 그대로) 로 수정하고 0행이면 VERSION_CONFLICT
-- 재현 서버가 embedding 만 채울 때는 올리지 않는다 (공개 직후 사용자가 고치려다 충돌 나지 않게)
create or replace function experience_touch_updated_at() returns trigger language plpgsql as $$
begin
  if (to_jsonb(new) - 'embedding' - 'updated_at') is distinct from (to_jsonb(old) - 'embedding' - 'updated_at') then
    new.updated_at := now();
  end if;
  return new;
end $$;
drop trigger if exists experience_touch_updated_at_trg on experience;
create trigger experience_touch_updated_at_trg before update on experience
  for each row execute function experience_touch_updated_at();

alter table experience enable row level security;
drop policy if exists experience_own on experience;
create policy experience_own on experience for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

-- 앱이 바꿀 수 있는 컬럼만 열어 둔다. 여기 없는 컬럼(visibility, published_*, masked, accept_*, embedding 등)은 권한 에러
revoke all on experience from anon;
revoke insert, update on experience from authenticated;
grant select, delete on experience to authenticated;
grant insert (user_id, status, activity_name, official_activity_id, official_period, genre, type, role, period_start, period_end, place,
              did_chips, did_notes, custom_chips, conditions, situation_chips, situation_notes, emotion_chips,
              one_liner, ai_tags, photos, pinned) on experience to authenticated;
grant update (status, activity_name, official_activity_id, official_period, genre, type, role, period_start, period_end, place,
              did_chips, did_notes, custom_chips, conditions, situation_chips, situation_notes, emotion_chips,
              one_liner, ai_tags, photos, pinned) on experience to authenticated;


-- ---------- 회차 기록 ----------
create table if not exists experience_log (
  id              uuid primary key,                -- 앱이 만듦. 재전송돼도 같은 id 라 중복이 안 생김
  experience_id   uuid not null references experience(id) on delete cascade,
  user_id         uuid not null default auth.uid() references auth.users(id) on delete cascade,
  log_date        date not null default current_date,
  situation_chips text[] not null default '{}' check (cardinality(situation_chips) between 1 and 2),
  emotion_chips   text[] not null default '{}' check (cardinality(emotion_chips) = 1),  -- 1개 (11/16 피그마 기준)
  one_liner       text check (char_length(one_liner) <= 100),
  extras          jsonb not null default '{}' check (jsonb_typeof(extras) = 'object'),   -- 접힌 선택 항목
  created_at      timestamptz not null default now()
);
create index if not exists experience_log_exp_idx on experience_log (experience_id, log_date);

alter table experience_log enable row level security;
drop policy if exists experience_log_own on experience_log;
-- 쓸 때는 내 경험이고 진행 중이어야 함 (명세 1.2)
create policy experience_log_own on experience_log for all to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid() and exists (
    select 1 from experience e where e.id = experience_id and e.user_id = auth.uid() and e.status = 'in_progress'));

revoke all on experience_log from anon;
revoke insert, update on experience_log from authenticated;
grant select, delete on experience_log to authenticated;
grant insert (id, experience_id, user_id, log_date, situation_chips, emotion_chips, one_liner, extras) on experience_log to authenticated;
grant update (log_date, situation_chips, emotion_chips, one_liner, extras) on experience_log to authenticated;


-- ---------- 차단 ----------
-- 재현 001 의 match_public_cards() 가 읽어서 001 보다 먼저 있어야 함. 신고(report)는 신고·차단 기능 때 같이 만든다
-- 차단은 서버(/reports)로만. 앱은 접근 못 함
create table if not exists block (
  user_id    uuid not null references auth.users(id) on delete cascade,
  blocked_id uuid not null references auth.users(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (user_id, blocked_id),
  check (user_id <> blocked_id)
);
create index if not exists block_blocked_idx on block (blocked_id);   -- "나를 차단한 사람" 쪽 조회
alter table block enable row level security;
revoke all on block from anon, authenticated;


-- ---------- 재현 서버로 보내는 DB 웹훅 ----------
-- Supabase 기본 웹훅 모양({type, table, schema, record, old_record})으로 보낸다. embedding 은 빼고.
-- Supabase: pg_net 으로 보냄. 주소·비밀값은 Vault 에 (db/README.md "소윤 웹훅 설정").
--   pg_net 은 트랜잭션이 커밋돼야 보내고, 실패해도 다시 안 보냄 → 재현 sweep 이 메움
-- 로컬: pg_net 이 없으니 pg_notify('soyun_webhook', {path, type, id}) 로 대신 (테스트가 LISTEN 으로 확인)
create or replace function soyun_post_webhook(path text, body jsonb) returns void
  language plpgsql security definer set search_path = public as $$
declare base text; secret text;
begin
  if to_regnamespace('net') is null or to_regnamespace('vault') is null then
    perform pg_notify('soyun_webhook', jsonb_build_object('path', path, 'type', body->>'type',
      'id', coalesce(body->'record'->>'id', body->'old_record'->>'id'))::text);
    return;
  end if;
  select decrypted_secret into base from vault.decrypted_secrets where name = 'server_base_url';
  select decrypted_secret into secret from vault.decrypted_secrets where name = 'webhook_secret';
  if base is null or secret is null then
    raise warning 'Vault 에 server_base_url / webhook_secret 없음. 웹훅 안 보냄 (%)', path;
    return;
  end if;
  perform net.http_post(url := base || path, body := body,
    headers := jsonb_build_object('Content-Type', 'application/json', 'X-Webhook-Secret', secret));
end $$;
revoke all on function soyun_post_webhook(text, jsonb) from public, anon, authenticated;   -- 앱이 RPC 로 못 부르게

create or replace function experience_webhook() returns trigger
  language plpgsql security definer set search_path = public as $$
declare
  rec jsonb := case when tg_op = 'DELETE' then null else to_jsonb(new) - 'embedding' end;
  old_rec jsonb := case when tg_op = 'INSERT' then null else to_jsonb(old) - 'embedding' end;
  body jsonb := jsonb_build_object('type', tg_op, 'table', 'experience', 'schema', 'public', 'record', rec, 'old_record', old_rec);
begin
  if tg_op = 'INSERT' then
    perform soyun_post_webhook('/v1/internal/experience-saved', body);       -- 질문 생성
  else
    perform soyun_post_webhook('/v1/internal/experience-published', body);   -- 임베딩·관련 경험 정리
  end if;
  return null;
end $$;

drop trigger if exists experience_saved_webhook on experience;
create trigger experience_saved_webhook after insert on experience
  for each row execute function experience_webhook();
-- 공개·다시 공개·비공개 내림: published_at 이나 visibility 가 바뀔 때만
drop trigger if exists experience_published_webhook on experience;
create trigger experience_published_webhook after update of published_at, visibility on experience
  for each row when (old.published_at is distinct from new.published_at or old.visibility is distinct from new.visibility)
  execute function experience_webhook();
-- 공개돼 있던 경험을 지울 때만
drop trigger if exists experience_deleted_webhook on experience;
create trigger experience_deleted_webhook after delete on experience
  for each row when (old.visibility <> 'private')
  execute function experience_webhook();


-- ---------- 프로필 (11/20) ----------
-- 연락처처럼 보이는 글자: 전화번호(숫자 9자리 이상, 사이 - . 공백 허용)·이메일. 닉네임에 못 쓰게 (PRF-⑨)
-- 글에 섞인 연락처 "경고"는 서버 /pii-check 가 따로 한다 (막지는 않음). 여기는 닉네임만 막는다
create or replace function contact_like(s text) returns boolean language sql immutable as $$
  select coalesce(s ~ '\d([\s.-]?\d){8,}' or s ~* '[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}', false)
$$;
-- 앱이 가입 때 자기 행을 직접 만들고 고친다. 남의 프로필은 FastAPI /profiles/{id} 로만 (칸마다 공개 여부가 달라 RLS 로 못 거름)
-- 글자 수는 server/app/constants.py PROFILE_* 와 같아야 한다
create table if not exists profile (
  user_id         uuid primary key default auth.uid() references auth.users(id) on delete cascade,
  -- 2~12자 (피그마 PRF-⑨). 실명은 기계로 못 거르고, 연락처 모양만 DB 가 막는다 (앱이 서버 없이 직접 저장하므로)
  nickname        text not null check (char_length(btrim(nickname)) between 2 and 12),
  school          text check (char_length(school) <= 40),        -- 남에게는 "서울 소재 대학"처럼 넓혀서 (서버가 바꿔 보냄)
  major_field     text check (char_length(major_field) <= 40),   -- 전공 계열 ("인문계열"). TODO(기획): 고르는 값 목록
  grade           text check (char_length(grade) <= 10),         -- TODO(기획): 학년 고르는 값 목록
  bio             text check (char_length(bio) <= 100),          -- 공개용 한 문장 (PRF-⑩). TODO(기획): 길이. 기록 한 줄과 같게 100
  -- 칸마다 남에게 보일지. 처음엔 전부 비공개 (11/20 소윤 확인. 피그마 PRF-⑩ [제안]의 학교·전공 공개 기본값은 안 따름)
  show_school     boolean not null default false,
  show_major      boolean not null default false,
  show_grade      boolean not null default false,
  show_bio        boolean not null default false,
  show_qa         boolean not null default false,
  school_verified boolean not null default false,   -- 학교 메일 인증. 앱이 못 바꿈 (인증은 나중, 명세 13장)
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  constraint profile_nickname_no_contact check (not contact_like(nickname))
);

create or replace function profile_touch_updated_at() returns trigger language plpgsql as $$
begin new.updated_at := now(); return new; end $$;
drop trigger if exists profile_touch_updated_at_trg on profile;
create trigger profile_touch_updated_at_trg before update on profile
  for each row execute function profile_touch_updated_at();

alter table profile enable row level security;
drop policy if exists profile_own on profile;
create policy profile_own on profile for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

revoke all on profile from anon;
revoke insert, update on profile from authenticated;
grant select, delete on profile to authenticated;
grant insert (user_id, nickname, school, major_field, grade, bio, show_school, show_major, show_grade, show_bio, show_qa) on profile to authenticated;
grant update (nickname, school, major_field, grade, bio, show_school, show_major, show_grade, show_bio, show_qa) on profile to authenticated;


-- ---------- 공개 카드 뷰 (11/20, 재현 임시 뷰를 대신함) ----------
-- 공개된(private 아님) + 스냅샷이 있는 경험만. 원본 칸은 하나도 안 내보낸다 — 카드 내용은 published_snapshot 안에만
-- 읽는 곳: 재현 탐색·카드 상세·관련 경험(server/app/cards/public.py), 소윤 프로필 카드. 전부 FastAPI. 앱은 못 읽음
-- 차단 관계는 뷰가 모름 (서버가 service_role 로 읽음) → 읽는 쿼리에서 block 으로 거른다
-- 칸을 늘릴 땐 맨 뒤에만 (create or replace view 는 칸 순서·이름을 못 바꿈)
create or replace view experience_public with (security_invoker = true) as
  select e.id as experience_id, e.user_id, e.published_snapshot, e.masked, e.published_at, e.accept_questions,
         e.visibility                                    -- anonymous / profile. 프로필 화면은 profile 만
  from experience e
  where e.visibility <> 'private' and e.published_snapshot is not null;
revoke all on experience_public from anon, authenticated;
