-- 로컬 개발용 임시 테이블 (CLAUDE.md 10장: "소윤 테이블이 없으면 같은 모양의 임시 테이블로 개발")
-- Supabase 실제 프로젝트에는 절대 적용하지 않는다. 로컬 Postgres(pgvector 이미지)에서만.
-- experience·experience_log·block 은 000_soyun_record.sql 의 실제 테이블로 바뀜 (소윤 11/16). experience_public 도 000_soyun_record.sql 로 옮김 (11/20). 남은 qa 도 차례로 바뀔 예정

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
