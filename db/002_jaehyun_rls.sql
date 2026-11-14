-- 재현 담당 테이블 RLS + 컬럼 권한 + 트리거
-- 원칙 (CLAUDE.md 3번): 앱이 못 바꾸는 컬럼은 "약속"이 아니라 실제로 막혀 있어야 함 → 컬럼 단위 GRANT 로 막음

-- ---------- question ----------
alter table question enable row level security;
create policy question_select on question for select to authenticated using (user_id = auth.uid());
create policy question_update on question for update to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());
-- insert/delete 는 서버(service_role)만. 정책 없음 = 거부.

-- 앱은 saved·memo·linked_experience_id·done_dismissed·notify_on_related·related_new 만 update 가능. type/text/source/is_done 은 컬럼 권한으로 차단.
revoke update on question from authenticated;
grant  update (saved, memo, linked_experience_id, done_dismissed, notify_on_related, related_new) on question to authenticated;

-- 담기 20개 제한 2차 방어 (앱이 먼저 세고 막음. 여기는 뚫렸을 때용)
create or replace function question_saved_limit() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.saved and not old.saved then
    if (select count(*) from question where user_id = new.user_id and saved) >= 20 then
      raise exception 'QUESTION_SAVE_LIMIT' using errcode = 'P0001';
    end if;
  end if;
  return new;
end $$;
create trigger question_saved_limit_trg before update of saved on question
  for each row execute function question_saved_limit();

-- linked_experience_id 는 내 경험만 가리킬 수 있음
create or replace function question_link_own_experience() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.linked_experience_id is not null and not exists
     (select 1 from experience where id = new.linked_experience_id and user_id = new.user_id) then
    raise exception 'FORBIDDEN' using errcode = 'P0001';
  end if;
  return new;
end $$;
create trigger question_link_own_trg before update of linked_experience_id on question
  for each row execute function question_link_own_experience();

-- ---------- question_batch / related_cache / analysis_* : 앱은 읽기만 ----------
alter table question_batch   enable row level security;
alter table related_cache    enable row level security;
alter table analysis_run     enable row level security;
alter table analysis_pattern enable row level security;

create policy question_batch_select on question_batch for select to authenticated using (user_id = auth.uid());
-- 앱이 "준비 중"을 알 때 이걸 Realtime 구독하거나 폴링. status 만 보면 됨.

create policy analysis_run_select     on analysis_run     for select to authenticated using (user_id = auth.uid());
create policy analysis_pattern_select on analysis_pattern for select to authenticated using (user_id = auth.uid());
-- related_cache 는 앱 접근 없음 (PublicCard 로 바꿔서 서버가 내려줌). 정책 없음 = 전부 거부.
revoke all on related_cache from anon, authenticated;

-- ---------- notification ----------
alter table notification enable row level security;
create policy notification_select on notification for select to authenticated using (user_id = auth.uid());
create policy notification_update on notification for update to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());
revoke update on notification from authenticated;
grant  update (read) on notification to authenticated;     -- 앱이 바꿀 수 있는 유일한 컬럼
-- insert 는 서버·트리거만.

-- ---------- notification_setting / push_device : 본인 전부 ----------
alter table notification_setting enable row level security;
create policy notification_setting_all on notification_setting for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

alter table push_device enable row level security;
create policy push_device_all on push_device for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

-- ---------- 경계: experience.embedding 은 앱이 못 씀 ----------
-- 소윤이 experience 에 거는 "굵은 컬럼 제외" 컬럼 권한 목록에 embedding 을 추가해달라고 요청.
-- 예: revoke update on experience from authenticated;
--     grant update (status, activity_name, ..., pinned) on experience to authenticated;   -- embedding 제외

-- ---------- 알림 생성 트리거 (경계: 소윤 qa 테이블에 재현이 거는 트리거) ----------
-- Q&A 새 질문 → 카드 주인에게 new_question. 답변 → 질문자에게 answer.
create or replace function notify_on_qa() returns trigger
language plpgsql security definer set search_path = public as $$
declare owner uuid;
begin
  if tg_op = 'INSERT' then
    select user_id into owner from experience where id = new.experience_id;
    insert into notification (user_id, type, payload, grouped_key)
    values (owner, 'new_question',
            jsonb_build_object('experience_id', new.experience_id, 'qa_id', new.id),
            'new_question:' || new.experience_id || ':' || to_char(now() at time zone 'Asia/Seoul', 'YYYY-MM-DD'));
  elsif tg_op = 'UPDATE' and new.answer is not null and old.answer is null then
    insert into notification (user_id, type, payload)
    values (new.asker_id, 'answer', jsonb_build_object('experience_id', new.experience_id, 'qa_id', new.id));
  end if;
  return new;
end $$;
-- qa 테이블이 생긴 뒤 소윤과 합의해서 붙임:
-- create trigger qa_notify_trg after insert or update of answer on qa for each row execute function notify_on_qa();
