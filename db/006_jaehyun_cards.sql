-- 탐색·카드·반응 (재현, CLAUDE.md 7장 반응 · API 명세 1.11). 여러 번 돌려도 된다.
-- 000_soyun_record.sql (experience) 뒤에. 004·005 와는 순서 상관없음

-- 반응: 카드 하나에 사람당 하나. 다시 보내면 교체. 작성자에게 안 보이고 추천에만 쓴다.
-- 질문 생성·분석 코드는 이 테이블을 읽지 않는다 (POL-07)
create table if not exists reaction (
  experience_id uuid not null references experience(id) on delete cascade,
  user_id       uuid not null references auth.users(id) on delete cascade,
  chip          text not null check (chip in ('나도 그랬어', '나는 반대였어', '다른 상황이었어')),
  note          text check (note is null or char_length(note) between 1 and 80),   -- TODO(기획): 길이. constants.REACTION_NOTE_MAX 와 같아야 함
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  primary key (experience_id, user_id)
);
create index if not exists reaction_user_idx on reaction (user_id);

-- 앱은 직접 못 읽고 못 쓴다. 쓰기는 POST /reactions, 내 반응 읽기는 GET /cards/{id} 의 my_reaction
alter table reaction enable row level security;
revoke all on reaction from anon, authenticated;
