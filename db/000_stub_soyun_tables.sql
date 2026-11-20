-- 로컬 개발용 임시 테이블 (CLAUDE.md 10장: "소윤 테이블이 없으면 같은 모양의 임시 테이블로 개발")
-- Supabase 실제 프로젝트에는 절대 적용하지 않는다. 로컬 Postgres(pgvector 이미지)에서만.
-- experience·experience_log·block 은 000_soyun_record.sql 의 실제 테이블로 바뀜 (소윤 11/16). 남은 qa·experience_public 도 차례로 바뀔 예정

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
