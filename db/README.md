# DB 설계 — 재현 담당 (질문·탐색 / 분석 / 알림)

API 명세 v0.2 + 레포 CLAUDE.md(11/10 회의 반영)를 SQL 로 옮긴 것.

| 파일 | 어디에 | 내용 |
|---|---|---|
| `000_local_supabase.sql` | **로컬 Postgres 만** | auth.users·auth.uid()·역할·기본 권한 흉내 (Supabase 엔 이미 있음) |
| `000_soyun_record.sql` | 로컬 + Supabase | **소윤 실제 테이블**: experience, experience_log, official_activity, block, profile, experience_public 뷰 + 권한·트리거·웹훅. 001 보다 먼저 |
| `000_stub_soyun_tables.sql` | **로컬 Postgres 만** | 소윤 테이블 중 아직 임시인 것 (qa). Supabase 에 적용 금지 |
| `001_jaehyun_schema.sql` | 로컬 + Supabase | 내 테이블 8개 + experience.embedding + match_public_cards() |
| `002_jaehyun_rls.sql` | 로컬 + Supabase | RLS, 컬럼 권한, 트리거 |
| `003_jaehyun_question_1114.sql` | 로컬 + Supabase | 11/14 기획 변경: `question.type` 에 `write_more`, `answer`·`answered_at`, `question_batch.no_new`·`shown_texts`. 여러 번 돌려도 됨. **Supabase 에는 아직 적용 안 함** |
| `004_jaehyun_analysis_1116.sql` | 로컬 + Supabase | 11/16 기획 변경(기능명세서 15장): `analysis_pattern` 에 `feedback_at`·`feedback_evidence_count`·`reask`·`reask_new_count`·`resurfaced`·`excluded`. 여러 번 돌려도 됨. 이미 만든 로컬 DB 에는 이 파일만 따로 적용 (`docker exec -i genz-pg psql -U postgres -d genz < 004_jaehyun_analysis_1116.sql`). **Supabase 에는 아직 적용 안 함** |
| `apply_local.sh` | 맥 | Docker 로 pgvector Postgres 띄우고 위 순서대로 적용 (포트 54329) |

11/10 회의로 바뀐 것: `question.experience_id` 는 nullable + `on delete set null` (경험 삭제돼도 담은 질문 유지, 안 담은 건 트리거로 삭제), `source_activity_name`·`axis`·`done_experience_id`·`done_dismissed` 추가, `question_batch` 에 `trigger`(save/user)·`failure_reason`(connection/model)·`attempts` 추가.

## 테이블 한눈에

| 테이블 | 명세 | 앱 접근 | 용도 |
|---|---|---|---|
| `question` | 1.3 | 읽기 / `saved`·`memo`·`linked_experience_id` 만 쓰기 | AI 질문. 경험 하나당 4개 (11/14: 더 적어보기 0~2 + 해볼 것·상상해보기) |
| `question_batch` | **없음** | 읽기 | 생성 회차·입력 해시·`status`(준비 중 표시용). 재생성 횟수 제한 근거 (11/14: 24시간 1회 + 답 1개당 1회) |
| `related_cache` | /related 설명만 | 없음 | 관련 경험 결과 캐시. 재생성 시 cascade 로 자동 삭제 |
| `analysis_run` | **없음** | 읽기 | 사용자당 1행. 마지막 계산 시각·기준·해시 |
| `analysis_pattern` | /analysis 응답 모양 | 읽기 | 패턴 + 피드백. "아니에요"면 `hidden` |
| `notification` | 1.4 | 읽기 / `read` 만 쓰기 | 알림 10종, 30일 cron 삭제 |
| `notification_setting` | 1.5 | 전부 | 유형별 on/off, 방해 금지, 리마인드 |
| `push_device` | 1.6 | 전부 | 기기별 Expo 토큰 |
| `experience.embedding` | 1.1 | 없음 | **소윤 테이블에 붙이는 컬럼**. HNSW 인덱스 + `match_public_cards()` RPC |

## 명세대로 막은 것 (CLAUDE.md "실제로 막혀 있어야 함")
- `question.type/text/source/is_done` → 컬럼 단위 `REVOKE UPDATE`. 앱 토큰으로는 바꾸려 해도 권한 에러
- `notification` → `read` 만 `GRANT UPDATE (read)`
- 담기 20개 → 앱 1차 + 트리거 2차 (`QUESTION_SAVE_LIMIT` 예외)
- 해봤어요 연결은 내 경험만 → 트리거
- `related_cache` 는 앱이 못 읽음 (asker 같은 남의 정보 섞일 수 있어서 서버가 PublicCard 로 변환)

## 경계 (소윤과 맞출 것)
1. `experience` 에 `embedding` 컬럼 추가 + 소윤이 거는 컬럼 권한 목록에서 `embedding` 제외
2. `match_public_cards()` 가 `block` 테이블을 읽음 → `block(user_id, blocked_id)` 컬럼명 확정
3. `qa` 에 `notify_on_qa` 트리거 붙이기 (새 질문 → `new_question`, 답변 → `answer`)
4. 공개 확정(`/publish/confirm`) 끝에 임베딩 웹훅 호출 → 재현 FastAPI 가 `embedding` 채우고 `saved` 질문과 매칭해 `related_found`

## 임의로 정한 것 — 회의에서 확인
| 항목 | 내가 정한 것 | 다른 선택지 |
|---|---|---|
| 재생성 시 이전 질문 | `saved=true` 만 남기고 나머지 삭제 (cascade 로 캐시도 삭제) | 전부 남기고 `generation` 으로 구분 |
| 하루 1회 제한 기준 | `question_batch.created_at` 이 KST 오늘인 행 존재 여부 | 마지막 생성 후 24시간 |
| 입력 해시 캐시 범위 | 같은 경험 + 같은 입력 해시면 LLM 안 부르고 같은 질문 복사 | 캐시 안 함 (B200이라 비용 0) |
| 관련 경험 캐시 수명 | 무기한. 재생성 때만 삭제 | N일 후 재계산 (새 카드 반영 위해) |
| 유사도 임계값 | 0.35 (BGE-M3 코사인). **실측 후 조정** | — |
| 분석 "아니에요" 재등장 방지 | `hidden` 행 유지, 새 패턴 생성 시 서버가 hidden 텍스트와 유사하면 버림 | 패턴 id 만 기억 |
| `notification.pushed_at` | 추가. 방해 금지로 미룬 푸시를 아침 8시 cron 이 보낼 근거 | 알림함에만 쌓고 푸시 안 함 |
| `grouped_key` 형식 | `"{type}:{experience_id}:{KST 날짜}"` | 서버에서 묶고 컬럼 없음 |
| `/notifications` 앱 직접 vs 서버 | **앱 직접** (grouped_key 로 앱이 묶음). 서버 엔드포인트 안 만듦 | 서버 경유 |
| `/questions` 호출 주체 | **서버 웹훅** (저장 트리거 → FastAPI). 앱은 `question_batch.status` 구독 | 앱이 저장 직후 호출 |

## 아직 못 정한 것 (지은)
- 회차 기록 감정 칩 1개 vs 2개 → 소윤 테이블이라 나와 무관하지만 질문 생성 입력에 들어감
- `follow_up` 답 저장 위치 → 저장하면 질문 생성 입력에 포함할지

## 다음
- 소윤 `experience` 마이그레이션 나오면 001 의 7번 블록과 002 의 트리거 붙이기
- `supabase gen types typescript` 로 타입 뽑아 하영에게

## 지은님께 물어볼 것 (DB 모양이 바뀌는 정책)

각 항목은 **명세에 뭐라고 돼 있는지 → 뭐가 비어 있는지 → 선택지 → 답이 없으면 이렇게 만들겠다** 순서.
답이 "기본값대로"면 그대로 가고, 다르면 테이블을 고친다.

### 질문 (AI가 만드는 해볼 것·상상해보기)

**1. 저장한 뒤 기록을 고치면 질문도 다시 만드나?**
- 명세: 4단계(주로 한 일)까지 쓰면 저장되고, 5~7단계(조건·상황·감정·한 줄)는 나중에 써도 됨. 질문은 저장되면 만들어짐
- 비어 있는 것: 4단계만 쓰고 저장 → 질문 4개 생김 → 다음 날 한 줄을 추가함. 이때 질문이 그대로인지, 새로 만드는지. 테스트해 보니 "한 줄"이 질문 품질에 제일 큰 영향을 줘서, 한 줄 없이 만든 질문과 있고 나서 만든 질문이 많이 다름
- 선택지: (a) 그대로 둠, 사용자가 재생성 버튼 누름 (b) 내용이 바뀌면 자동으로 다시 만듦 (c) 한 줄·보강글처럼 글이 바뀔 때만 자동
- 걸리는 것: (b)(c)면 "재생성 하루 1회"에 이게 포함되는지. 자동 재생성이 하루 1회를 써버리면 사용자가 버튼을 못 누름
- 기본값: (a). 수정해도 질문은 안 바뀜. 앱에서 "기록이 바뀌었어요, 질문 다시 만들까요?" 띄우는 건 가능

**2. 재생성하면 이전 질문은 어떻게 되나?**
- 명세: "재생성은 하루 1번. saved=true 유지"
- 비어 있는 것: 담지 않은 질문은 사라지는지. 메모를 썼거나 "해봤어요"로 내 다른 기록에 연결한 질문은 담지 않았어도 남겨야 하는지
- 선택지: (a) 담은 것만 남기고 나머지 삭제 (b) 담은 것 + 메모·연결 있는 것 유지 (c) 전부 남기고 "지난 질문"으로 접어 보여줌
- 기본값: (a). (c)면 질문 화면에 "이전 세대" 영역이 생겨서 디자인도 바뀜

**3. "하루 1회"의 하루는 뭔가**
- 선택지: (a) 한국 시간 자정 기준 — 밤 11시에 하고 0시 넘으면 또 됨 (b) 마지막 재생성 후 24시간
- 기본값: (a). 구현도 쉽고 사용자가 이해하기도 쉬움

**4. 경험을 삭제하면 질문은?**
- 명세: 보관소에 "담은 질문" 영역이 따로 있음
- 비어 있는 것: 경험 A를 삭제하면 A에서 나온 질문 중 담아둔 것도 "담은 질문"에서 사라지는지
- 선택지: (a) 같이 삭제 (b) 담은 질문은 남김 (출처 경험은 "삭제된 기록"으로 표시)
- 기본값: (a). 명세의 "출처 필수"와 맞음

**5. AI 생성이 실패하면 앱에 뭘 보여주나**
- 명세: AI 실패 시 503, "앱은 조용히 넘어감". 질문은 저장 직후 "준비 중" 표시 (제안)
- 비어 있는 것: 준비 중이 몇 초 지나도 안 끝나면? 실패하면 "다시 시도" 버튼이 있는지, 서버가 알아서 재시도하는지, 그동안 질문 자리는 비어 있는지
- 기본값: 서버가 3번까지 자동 재시도, 그래도 실패면 "질문을 못 만들었어요. 다시 시도" 버튼. 이 재시도는 하루 1회에 안 셈

### 관련 경험 (질문에 붙는 남의 카드)

**6. 처음 열 때 계산하고 고정 — 그 뒤엔 영원히 안 바뀌나?** ✅ 답 받음 (11/14)
- 관련 경험 0개인 질문에 "새 경험이 생기면 알려드릴까요?" (좋아요/괜찮아요) 를 기능명세에 추가. 좋아요한 질문만 새 경험이 올라올 때 재계산 + 알림
- 작성자가 카드를 비공개·삭제하면 그 카드를 담고 있던 캐시도 재계산
- → `question.notify_on_related` 컬럼 추가. 재계산 대상 = 캐시 결과 0개 + 이 플래그

**7. related_found 알림 범위** ✅ 답 받음 (11/14)
- 경험 하나가 담은 질문 여러 개에 맞아도 경험 수는 한 번만 센다
- 하루 한 번 정해진 시각, **오전 9시** (방해 금지 22~08 피해서)
- 알림을 누르면 보관소 › 담은 질문. 새 경험 생긴 질문에 표시(태그). 질문이 하나뿐이면 그 질문으로 바로
- 내 경험이 내 질문에 맞는 경우는 제외
- → `question.related_new` 컬럼 추가(서버가 켜고 앱이 끔), 알림 payload 에 `question_ids`

### 분석 (반복되는 패턴)

**8. "아니에요"는 얼마나 강한가**
- 명세: "사용자가 아니에요 하면 그 패턴은 숨기고 다시 안 만듦"
- 비어 있는 것: "다시 안 만듦"이 똑같은 문장만인지, 비슷한 내용("팀에서 조율 역할을 맡는다" vs "사람 사이를 조정한다")도 막는지. 기록이 10개 더 쌓여서 진짜 그 패턴이 맞아 보여도 영원히 안 나오는지
- 피드백 "나중에"는 다음 분석 때 다시 보여주는지, 며칠 뒤인지
- 기본값: 비슷한 내용도 막음(임베딩 유사도), 영원히. "나중에"는 다음 분석 때 다시

**9. 사용자가 고친 문장(edit)은 어디에 쓰나**
- 명세: feedback에 "edit" + edited_text 있음
- 비어 있는 것: 고친 문장을 그냥 보여주기만 하는지, 다음 분석에 "이 사람은 이렇게 표현함"으로 넣는지
- 기본값: 보여주기만. 분석 입력엔 안 넣음 (POL-07 "AI가 볼 수 있는 것" 범위를 늘리지 않으려고)

### 알림

**10. 방해 금지 시간에 생긴 알림은 아침에 보내나**
- 명세: "밤 10시~아침 8시는 안 보내고 알림함에만 쌓음"
- 비어 있는 것: 아침 8시가 되면 밤에 쌓인 걸 푸시로 보내는지, 끝까지 푸시 없이 알림함에만 두는지
- 선택지: (a) 8시에 몰아서 보냄 — 밤사이 질문 3개면 아침에 푸시 3개 (b) 8시에 하나로 묶어서 "밤사이 알림 3개" (c) 푸시 안 함, 앱 열면 알림함에서 봄
- 기본값: (b). (c)면 `pushed_at` 컬럼이 필요 없음

**11. "하루 묶음"의 단위** ✅ 답 받음 (11/14)
- 같은 경험에 같은 날 달린 질문은 첫 질문만 바로 푸시. 그 뒤는 푸시 없이 알림함 항목 숫자만 갱신 ("질문 1→3개")
- 푸시를 덮어써서 숫자를 바꿀 수 있으면 그것도 좋음 → Expo 지원 확인 후
- → `notification.count` 컬럼 + `(user_id, grouped_key)` 유니크. 같은 키면 insert 대신 count+1

**12. 알림 기본값과 리마인드 자동 설정**
- 명세: 종류별 on/off, 활동별 리마인드 요일·시간. 기본값 언급 없음
- 비어 있는 것: 가입 직후 10종이 전부 켜져 있는지. 진행 중 활동을 만들면 리마인드가 자동으로 켜지는지, 켜진다면 기본 요일·시간(예: 매주 일 21:00)
- 기본값: 전부 켬. 진행 중 활동 만들면 리마인드 자동 on, 매주 일요일 21:00. 커피챗 2종은 화면 없으니 꺼둠

**13. 알림이 가리키는 글이 없어졌을 때**
- 비어 있는 것: 카드가 비공개로 내려가거나 Q&A가 14일 지나 삭제됐는데 알림은 남아 있을 때. 눌렀을 때
- 선택지: (a) 알림도 같이 삭제 (b) 알림은 남기고 눌렀을 때 "지금은 볼 수 없는 글이에요"
- 기본값: (b). 알림을 지우면 "분명 알림이 왔는데 사라졌다"가 더 이상함

### 경계·기타

**14. "해봤어요" 자동 판정과 직접 연결이 화면에서 다른가**
- 명세: is_done "내 다른 기록이랑 겹치면 이미 해본 것 표시"(AI 자동) + linked_experience_id "해봤어요(내 다른 기록에 연결)"(사용자 수동)
- 비어 있는 것: 둘이 화면에서 같은 표시인지. 자동 판정이 틀렸을 때 사용자가 끌 수 있는지
- 기본값: 표시는 같음. 자동 판정은 사용자가 "아니에요"로 끌 수 있게 (컬럼 1개 추가)

**15. 탈퇴하면 공개했던 카드는**
- 비어 있는 것: 공개 카드와 거기 달린 남의 Q&A·도움 이유·반응을 전부 지우는지, 카드만 내리고 남이 쓴 건 남기는지
- 기본값: 전부 삭제. 익명 서비스라 "남이 쓴 글을 남긴다"가 더 설명하기 어려움
- (소윤 테이블이 대부분이라 같이 물어보면 됨)

## 소윤 웹훅 설정 (Supabase)

`000_soyun_record.sql` 의 트리거가 pg_net 으로 재현 서버를 부른다. Supabase 대시보드에서 웹훅을 따로 만들지 않는다.
주소와 비밀값은 SQL Editor 에서 Vault 에 한 번 넣는다:

```sql
select vault.create_secret('https://<서버 주소>', 'server_base_url');   -- 끝에 / 없이
select vault.create_secret('<WEBHOOK_SECRET 과 같은 값>', 'webhook_secret');
```

| 언제 | 주소 | type |
|---|---|---|
| experience INSERT | `/v1/internal/experience-saved` | `INSERT` |
| `published_at`·`visibility` 가 바뀐 UPDATE | `/v1/internal/experience-published` | `UPDATE` |
| 공개돼 있던 행 DELETE | `/v1/internal/experience-published` | `DELETE` |

로컬은 pg_net 이 없어서 `pg_notify('soyun_webhook', {path, type, id})` 로 대신한다.
