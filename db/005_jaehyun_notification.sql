-- 005 — 알림 만들기·묶기·푸시 (이슈 #18, CLAUDE.md 11장, API 명세 1.4·3장). 001·002 뒤에 적용. 여러 번 돌려도 된다.

-- 푸시를 보낼지 서버가 판단을 끝낸 시각. null = 아직 안 봤음 (방해 금지 시간이라 미뤄 둔 것 포함).
-- pushed_at 은 "실제로 보낸 시각"이라, 종류를 꺼 뒀거나 기기가 없어 안 보낸 알림과 구분하려고 따로 둔다
alter table notification add column if not exists push_checked_at timestamptz;
create index if not exists notification_unchecked_idx on notification (created_at) where push_checked_at is null;
-- 005 전에 만들어진 알림은 이제 와서 푸시하지 않는다
update notification set push_checked_at = coalesce(pushed_at, now()) where push_checked_at is null and created_at < now() - interval '1 day';

-- 새 관련 경험 알림(related_found)에 이미 실어 보낸 질문인지. 오전 9시 작업이 채운다.
-- related_new 는 사용자가 볼 때까지 켜져 있어서, 이 값이 없으면 안 열어 본 질문을 날마다 다시 알리게 된다.
-- 관련 경험이 다시 0개가 됐다가 새로 붙으면 서버가 null 로 되돌린다. 앱은 못 바꾼다 (002 의 컬럼 권한에 없음)
alter table question add column if not exists related_notified_at timestamptz;

-- 알림 한 건 넣기. 트리거와 FastAPI 가 같이 쓴다.
-- 묶음 키가 같으면 행을 새로 만들지 않고 count 만 올린다 ("질문 1→3개", 지은 11/14). p_bump = false 면 이미 있을 때 아무것도 안 한다 (리마인드처럼 하루 한 번만 만드는 것)
create or replace function notification_add(
  p_user uuid, p_type text, p_payload jsonb, p_grouped_key text default null, p_bump boolean default true
) returns uuid
language plpgsql security definer set search_path = public as $$
declare nid uuid;
begin
  if p_grouped_key is null then
    insert into notification (user_id, type, payload) values (p_user, p_type, p_payload) returning id into nid;
    return nid;
  end if;
  insert into notification (user_id, type, payload, grouped_key) values (p_user, p_type, p_payload, p_grouped_key)
  on conflict (user_id, grouped_key) where grouped_key is not null do update
    set count   = notification.count + (case when p_bump then 1 else 0 end),
        payload = case when p_bump then excluded.payload else notification.payload end,   -- 가장 최근 것으로 이동
        read    = case when p_bump then false else notification.read end                    -- 다시 안 읽음으로
  returning id into nid;
  return nid;
end $$;
revoke execute on function notification_add(uuid, text, jsonb, text, boolean) from public, anon, authenticated;

-- Q&A 알림 트리거 함수 (002 의 것을 바꿈). 002 는 insert 만 해서 같은 날 같은 카드의 두 번째 질문이 유니크 충돌로 qa 저장까지 막았다.
--   새 질문 → 카드 주인에게 new_question (같은 카드·같은 날은 한 행, count +1)
--   답변    → 질문자에게 answer (개별)
--   내 카드에 내가 남긴 질문, 서로 차단한 사이는 알림을 만들지 않는다
create or replace function notify_on_qa() returns trigger
language plpgsql security definer set search_path = public as $$
declare owner uuid;
begin
  select user_id into owner from experience where id = new.experience_id;
  if owner is null or owner = new.asker_id
     or exists (select 1 from block b where (b.user_id = owner and b.blocked_id = new.asker_id)
                                         or (b.user_id = new.asker_id and b.blocked_id = owner)) then
    return new;
  end if;
  if tg_op = 'INSERT' then
    perform notification_add(owner, 'new_question',
      jsonb_build_object('experience_id', new.experience_id, 'qa_id', new.id),
      'new_question:' || new.experience_id || ':' || to_char(now() at time zone 'Asia/Seoul', 'YYYY-MM-DD'));
  elsif tg_op = 'UPDATE' and new.answer is not null and old.answer is null then
    perform notification_add(new.asker_id, 'answer',
      jsonb_build_object('experience_id', new.experience_id, 'qa_id', new.id));
  end if;
  return new;
end $$;
-- 트리거는 qa 가 실제 테이블이 된 뒤 소윤과 합의해서 붙인다 (지금 qa 는 로컬 임시 테이블):
-- create trigger qa_notify_trg after insert or update of answer on qa for each row execute function notify_on_qa();
