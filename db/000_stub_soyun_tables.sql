-- 로컬 개발용 임시 테이블 (CLAUDE.md 10장: "소윤 테이블이 없으면 같은 모양의 임시 테이블로 개발")
-- Supabase 실제 프로젝트에는 절대 적용하지 않는다. 로컬 Postgres(pgvector 이미지)에서만.
create extension if not exists vector;

create schema if not exists auth;
create table if not exists auth.users (
  id    uuid primary key default gen_random_uuid(),
  email text
);
-- Supabase 의 auth.uid() 흉내. 테스트에서 set_config('request.jwt.claim.sub', '<uuid>', true) 로 지정
create or replace function auth.uid() returns uuid language sql stable as $$
  select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
$$;
do $$ begin create role authenticated nologin; exception when duplicate_object then null; end $$;
do $$ begin create role anon nologin; exception when duplicate_object then null; end $$;
do $$ begin create role service_role nologin bypassrls; exception when duplicate_object then null; end $$;

-- Supabase 기본 권한 흉내: public 스키마 테이블은 anon/authenticated 가 기본으로 전부 할 수 있고, RLS 와 컬럼 REVOKE 로 좁힌다
grant usage on schema public, auth to anon, authenticated, service_role;
grant execute on function auth.uid() to anon, authenticated, service_role;
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
alter default privileges in schema public grant execute on functions to anon, authenticated, service_role;

-- experience: API 명세 1.1 과 같은 컬럼 (질문 생성·임베딩에 쓰는 것 위주)
create table if not exists experience (
  id                   uuid primary key default gen_random_uuid(),
  user_id              uuid not null references auth.users(id) on delete cascade,
  status               text not null default 'done' check (status in ('in_progress', 'done')),
  activity_name        text not null,
  official_activity_id uuid,
  genre                text not null,
  type                 text,
  role                 text not null,
  period_start         text not null,
  period_end           text,
  place                text,
  did_chips            text[] not null default '{}',
  did_notes            jsonb  not null default '{}',
  custom_chips         text[] not null default '{}',
  conditions           jsonb  not null default '{}',
  situation_chips      text[] not null default '{}',
  situation_notes      jsonb  not null default '{}',
  emotion_chips        text[] not null default '{}',
  one_liner            text,
  ai_tags              text[] not null default '{}',
  photos               text[] not null default '{}',
  pinned               boolean not null default false,
  visibility           text not null default 'private' check (visibility in ('private', 'anonymous', 'profile')),
  accept_questions     boolean not null default true,
  accept_coffeechat    boolean not null default false,
  published_snapshot   jsonb,
  masked               jsonb,
  published_at         timestamptz,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now()
);

create table if not exists experience_log (
  id              uuid primary key,
  experience_id   uuid not null references experience(id) on delete cascade,
  user_id         uuid not null references auth.users(id) on delete cascade,
  log_date        date not null default current_date,
  situation_chips text[] not null default '{}',
  emotion_chips   text[] not null default '{}',
  one_liner       text,
  extras          jsonb not null default '{}',
  created_at      timestamptz not null default now()
);

create table if not exists block (
  user_id    uuid not null references auth.users(id) on delete cascade,
  blocked_id uuid not null references auth.users(id) on delete cascade,
  primary key (user_id, blocked_id)
);

create table if not exists qa (
  id            uuid primary key default gen_random_uuid(),
  experience_id uuid not null references experience(id) on delete cascade,
  asker_id      uuid not null references auth.users(id) on delete cascade,
  type          text not null,
  question      text not null,
  answer        text,
  answered_at   timestamptz,
  created_at    timestamptz not null default now()
);

-- 공개 카드 뷰 (소윤 것의 최소 흉내). 관련 경험은 이 뷰만 읽음
create or replace view experience_public as
  select id as experience_id, user_id, published_snapshot, masked, published_at, accept_questions
  from experience where visibility <> 'private';
