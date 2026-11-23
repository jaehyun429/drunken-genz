-- 004 — 분석 응답 처리 (기능명세서 15장, 2026.11.16). 001·002 뒤에 적용. 여러 번 돌려도 된다.
--   아직 모르겠어요 → 문장을 남기고 '확인 중'. 근거가 늘면 다시 묻는다
--   아니에요 → 숨김. 조건이 맞으면 한 번만 다시 보여주고, 또 아니에요면 영구 제외
alter table analysis_pattern
  add column if not exists feedback_at             timestamptz,                      -- 마지막으로 응답한 시각. 숨긴 패턴은 이 뒤에 쓴 기록만 '새 기록'으로 센다
  add column if not exists feedback_evidence_count int,                              -- 응답할 때의 근거 수. 이보다 늘면 '확인 중' 패턴을 다시 묻는다
  add column if not exists reask                   text,                             -- 다시 묻는 카드. more_evidence = 기록이 늘었어요 / repeated = 새 기록 N개에서도
  add column if not exists reask_new_count         int,                              -- reask = repeated 일 때의 N
  add column if not exists resurfaced              boolean not null default false,   -- 아니에요 뒤에 한 번 다시 보여줬다. 두 번은 안 한다
  add column if not exists excluded                boolean not null default false;   -- 다시 보여준 뒤 또 아니에요. 영구 제외

do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'analysis_pattern_reask_check') then
    alter table analysis_pattern add constraint analysis_pattern_reask_check
      check (reask is null or reask in ('more_evidence', 'repeated'));
  end if;
end $$;

-- 004 전에 응답한 패턴: 응답 시각은 마지막 수정 시각으로, 근거 수는 지금 근거 수로 채운다
update analysis_pattern
   set feedback_at = coalesce(feedback_at, updated_at),
       feedback_evidence_count = coalesce(feedback_evidence_count, jsonb_array_length(evidence))
 where feedback is not null and (feedback_at is null or feedback_evidence_count is null);
