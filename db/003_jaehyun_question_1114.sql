-- 003_jaehyun_question_1114.sql — 질문 생성 11/14 기획 변경 (PRD 7.2·7.2.1, 기능명세서 14.1). API 명세 v0.3.2
-- 001·002 가 이미 적용된 DB 에 더하는 파일이다. 001 은 고치지 않는다 (팀 Supabase 에 이미 들어가 있음).
-- 여러 번 돌려도 안 깨진다.

-- 질문 종류에 더 적어보기 추가
alter table question drop constraint if exists question_type_check;
alter table question add  constraint question_type_check check (type in ('try_next', 'imagine', 'write_more'));

-- 더 적어보기 답. TODO(기획): 답을 기록의 어느 칸에 넣을지 미정 (/summarize 의 follow_up 과 같은 문제).
-- 정해질 때까지 질문 행에 둔다. 앱은 못 쓴다 (002 의 컬럼 권한에 없음) → POST /questions/{id}/answer
alter table question add column if not exists answer      text;         -- 1~80자 (constants.QUESTION_ANSWER_MAX 와 같은 값)
alter table question add column if not exists answered_at timestamptz;  -- 재생성 횟수 계산에 씀 (답 1개당 1회)
alter table question drop constraint if exists question_answer_check;
alter table question add  constraint question_answer_check check (
  (answer is null and answered_at is null)
  or (type = 'write_more' and answered_at is not null and char_length(answer) between 1 and 80));

-- 더 적어보기는 담기 없음 (기능명세서 14.1 '하지 않는 것')
alter table question drop constraint if exists question_write_more_not_saved;
alter table question add  constraint question_write_more_not_saved check (type <> 'write_more' or not saved);

-- 재생성: 이전에 보여준 질문과 안 겹치게 만들고, 새 질문이 없으면 그대로 둔다
alter table question_batch add column if not exists no_new      boolean not null default false;      -- "새로 만들 질문이 없어요"
alter table question_batch add column if not exists shown_texts jsonb   not null default '[]'::jsonb; -- 이 생성에서 보여준 질문 본문

comment on column question.axis is
  'try_next: role/scale/method/autonomy/did/field/decision · imagine: situation/external · write_more: gap_action/gap_result/gap_situation';
comment on column question_batch.trigger is '저장 웹훅 / 사용자 재생성 버튼. 재생성 횟수(24시간 1회 + 답 1개당 1회)는 user 만 셈';
comment on column question_batch.input_hash is '기록 문단의 sha256. 기록으로만 남긴다 (11/14 부터 같은 해시여도 재사용하지 않음)';
