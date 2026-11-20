-- 로컬 Postgres 에서 Supabase 를 흉내 내는 부분. Supabase 실제 프로젝트에는 적용하지 않는다 (이미 있음).
-- 원래 000_stub_soyun_tables.sql 맨 앞에 있던 것을 그대로 옮김 (소윤 실제 테이블보다 먼저 돌아야 해서).
create extension if not exists vector;
create extension if not exists pg_trgm;

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
