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
  period      text,                                   -- "2026-1" 등, 회차마다 다른 행
  aliases     text[] not null default '{}' check (cardinality(aliases) <= 3),
  status      text not null default 'pending' check (status in ('pending', 'verified')),
  created_by  uuid references auth.users(id) on delete set null,
  created_at  timestamptz not null default now()
);
-- 명세 1.8 의 record_count 는 컬럼으로 두지 않고 검색할 때 experience 를 센다 (값이 어긋날 일이 없게)

-- 검색용 글자: 이름 + 별칭, 소문자, 띄어쓰기 없앰. "취업 박람회" 로 "취업박람회" 를 찾게
create or replace function activity_search_text(name text, aliases text[]) returns text
  language sql immutable as $$
  select lower(regexp_replace(name || ' ' || array_to_string(aliases, ' '), '\s', '', 'g'))
$$;
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
grant insert (user_id, status, activity_name, official_activity_id, genre, type, role, period_start, period_end, place,
              did_chips, did_notes, custom_chips, conditions, situation_chips, situation_notes, emotion_chips,
              one_liner, ai_tags, photos, pinned) on experience to authenticated;
grant update (status, activity_name, official_activity_id, genre, type, role, period_start, period_end, place,
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
