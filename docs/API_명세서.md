# API 명세서 v0.3

v0.1 (11/06) → v0.2 (11/09) · 개발팀 회의 + 하영 "앱 ↔ 서버 경계 정리" 반영 → v0.3 (11/14) · 소윤 검토 9건 반영

> **v0.2 대비 바뀐 곳 표시** — 줄 끝의 `[수정 #N]`은 PR #1 설명 표의 N번 항목. 지운 내용은 ~~취소선~~으로 남김. 리뷰가 끝나면 표시는 지움

구조가 두 갈래라 명세도 두 부분입니다.

- **1부 — 앱이 Supabase에 직접 하는 것** — **내 데이터만**. 내 경험, 내 회차, 내 담기·메모, 내 알림 읽음
- **2부 — 앱이 FastAPI에 요청하는 것** — 남이 관련되거나 규칙(개수 제한·익명·스냅샷)이 붙은 것 전부 + AI

경계 원칙 (11/09 합의): 내 데이터는 앱 직접 / 남이 관련되거나 규칙 붙으면 서버 / 상태가 바뀌는 순간은 서버가 확정.

---

## 0. 공통

**인증** — 모든 요청에 Supabase JWT. 앱은 `supabase.auth.getSession()`으로 꺼내서 FastAPI 호출 시 `Authorization: Bearer <token>`. FastAPI는 Supabase JWT secret으로 검증하고 `user_id`를 꺼냄.

**앱에 넣는 키** — `anon`만. `service_role`은 FastAPI 환경변수에만.

**에러 형식**
```json
{ "error": { "code": "QA_DAILY_LIMIT", "message": "디버깅용 설명", "details": { "field": "..." } } }
```
앱은 `code`만 보고 문구를 고름. `message`는 사람이 읽는 디버깅용.

| HTTP | code | 언제 |
|---|---|---|
| 401 | `UNAUTHENTICATED` | 로그인 만료 |
| 403 | `FORBIDDEN` | 남의 데이터 수정 시도 |
| 404 | `NOT_FOUND` | 없음, 비공개로 내려감, **차단 관계** (차단 티 안 나게 403 대신 이걸로) |
| 400 | `VALIDATION_FAILED` | 글자 수·칩 개수 위반. `details.field` |
| 409 | `VERSION_CONFLICT` | 저장 후 수정 충돌 (`updated_at` 다름) |
| 409 | `PUBLISH_PREVIEW_STALE` | 미리보기와 확정 결과가 달라짐 |
| 429 | `QUESTION_REGEN_LIMIT` | AI 질문 재생성 하루 1번 초과 |
| 429 | `QUESTION_SAVE_LIMIT` | 질문 담기 20개 초과 |
| 429 | `QA_DAILY_LIMIT` | Q&A 하루 5개 초과 |
| 429 | `QA_PER_CARD_LIMIT` | Q&A 카드당 3개 초과 |
| 409 | `HELPFUL_ALREADY_SENT` | 도움이 된 이유 중복 |
| 403 | `PROFILE_VERIFICATION_REQUIRED` | 학교 인증 없이 프로필 공개 시도 |
| 503 | `AI_UNAVAILABLE` | AI 실패·지연. 앱은 조용히 넘어감 |
| 500 | `INTERNAL` | 그 외 |

**응답 호환 규칙** — 필드 추가 자유. 삭제·이름 변경 금지. enum 값 추가는 미리 공지(앱은 모르는 값 오면 기본 처리). non-null → nullable도 변경으로 침.

**타입** — Supabase `supabase gen types typescript`, FastAPI OpenAPI → `openapi-typescript`. 응답 예시 JSON이 확정되면 앱은 서버 완성 전에 목업으로 진행.

**시간** — 전부 UTC ISO 8601. 기간(월)은 `"2026-03"` 문자열. 리마인드·방해 금지 시간은 **일단 Asia/Seoul 고정** (사용자별 시간대는 후순위).

**ID** — UUID. `experience_log`는 **앱이 생성해서 보냄** (재전송 중복 방지).

**공개 카드 모양 (PublicCard)** — 탐색 목록, 카드 상세, 관련 경험에서 **같은 형태**로 내려줌.
```json
{
  "experience_id": "uuid",
  "activity_name": "...",
  "genre": "...",
  "role": "...",
  "did_chips": ["..."],
  "one_liner": "...",
  "official_activity": { "name": "...", "verified": false } | null,
  "published_at": "...",
  "accept_questions": true,
  "qa_count": 2,
  "is_mine": false
}
```
`activity_name`·`role`·`place` 등은 **가림 적용된 값**. 원본은 절대 안 나감.

---

## 1부. Supabase 테이블과 접근 규칙

### 앱이 직접 접근하는 테이블 — RLS는 `user_id = auth.uid()` 하나

### 1.1 experience — 경험 (소윤)

| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | uuid | PK |
| user_id | uuid | auth.users 참조 |
| status | text | `in_progress` / `done` |
| activity_name | text | 2~40자 |
| official_activity_id | uuid | nullable |
| official_period | smallint | nullable, 1~99. 기수 (공식 활동을 고른 뒤 선택). 공개 카드에도 들어감 — 미리보기에서 가리기 제안 (11/20) |
| genre | text | **분야** 9개 중 1 (화면 말은 "분야", 컬럼 이름은 그대로): `행사 운영` `개발·IT` `디자인·콘텐츠` `음악·공연` `연구·실험` `교육·멘토링` `경영·마케팅` `서비스·응대` `기타` |
| type | text | **유형**, nullable: `동아리·학회` `팀플·공모전` `대외활동` `학생회·행사` `아르바이트` `봉사·멘토링` `수업·캡스톤` |
| role | text | 2~20자 |
| period_start | text | `"2026-03"` |
| period_end | text | nullable (진행 중) |
| place | text | nullable, 30자 |
| did_chips | text[] | 1~3개 |
| did_notes | jsonb | `{ "칩이름": "보강글" }` |
| custom_chips | text[] | 직접 입력한 칩 |
| conditions | jsonb | `{ "scale": [...], "method": [...], "my_role": [...], "autonomy": [...] }` |
| situation_chips | text[] | 최대 2 |
| situation_notes | jsonb | |
| emotion_chips | text[] | 최대 2 |
| one_liner | text | nullable, 100자 |
| ai_tags | text[] | 정리 확인에서 사용자가 켠 것만 |
| photos | text[] | Storage 경로 |
| pinned | bool | 보관소 상단 고정 |
| **visibility** | text | `private` / `anonymous` / `profile`. 기본 `private`. **앱이 못 바꿈** |
| **accept_questions** | bool | 기본 true. 앱이 못 바꿈 — 공개 확정 때(`/publish/confirm`) 고르고, 이후엔 `/publish/settings`로 바꿈 `[수정 #4]` |
| **accept_coffeechat** | bool | 기본 false. 앱이 못 바꿈 |
| **published_snapshot** | jsonb | 공개 시점 복사본. 앱이 못 바꿈 |
| **masked** | jsonb | 가림 처리된 필드. 앱이 못 바꿈 |
| **published_at** | timestamptz | 공개 시각. "다시 공개할까요?" 판단용. 앱이 못 바꿈 |
| **embedding** | vector(1024) | 재현이 웹훅으로 채움. 앱이 못 바꿈 |
| created_at, updated_at | timestamptz | `updated_at`은 버전 체크용. 수정할 때마다 DB 트리거로 자동 갱신 `[수정 #5]` |

*v0.1의 `draft_step` 삭제 — 임시저장은 폰에만.*

**RLS**
- 본인: select 전부. insert·update는 **굵은 컬럼 제외** (컬럼 권한 REVOKE 또는 트리거로 막음)
- 남: **원본 테이블 접근 불가.** 공개 카드는 `experience_public` 뷰 또는 FastAPI로만

**뷰 experience_public** — `visibility != 'private'`인 행의 `published_snapshot`·`masked`만 노출. 뷰는 `security_invoker`로 만들어 RLS가 적용되게 하고, 차단 관계 필터는 FastAPI 쿼리에서 건다 (요청한 사용자 id 기준 — 서버는 `service_role`로 읽어서 DB가 누가 보는지 모름). **앱은 이 뷰도 직접 안 읽음** — FastAPI가 읽어서 PublicCard로 내려줌. `[수정 #2]`

**앱이 하는 호출**
```js
// 저장 (4단계까지 채운 뒤 한 번. 단계별 upsert 아님)
supabase.from('experience').insert({ ...fields })

// 수정 (버전 체크)
supabase.from('experience').update({ ...fields }).eq('id', id).eq('updated_at', lastSeen)
// 0행 갱신이면 VERSION_CONFLICT 처리

// 내 보관소 (cursor)
supabase.from('experience').select('*').eq('user_id', me)
  .order('created_at', { ascending: false }).lt('created_at', cursor).limit(20)
```

### 1.2 experience_log — 회차 기록 (소윤)

| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | uuid | **앱이 생성** |
| experience_id | uuid | status가 in_progress인 것만 |
| user_id | uuid | |
| log_date | date | 기본 오늘 |
| situation_chips | text[] | 1~2 |
| emotion_chips | text[] | **1개** (11/16 피그마 기준) |
| one_liner | text | |
| extras | jsonb | 접힌 선택 항목 |
| created_at | timestamptz | |

RLS: 본인만. 저장할 때 `experience_id`도 본인 경험이어야 함. 공개된 경험의 회차는 흐름 요약만 `published_snapshot`에. `[수정 #6]`

### 1.3 question — AI 질문 (재현)

| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | uuid | |
| experience_id | uuid | |
| user_id | uuid | |
| **type** | text | `try_next` / `imagine`. 앱이 못 바꿈 |
| **text** | text | 질문 본문. 앱이 못 바꿈 |
| **source** | text | "어느 기록·어느 차이에서". 앱이 못 바꿈 |
| **is_done** | bool | 내 다른 기록과 겹침. 앱이 못 바꿈 |
| saved | bool | 담기 — 앱이 바꿈 |
| memo | text | imagine 메모 — 앱이 바꿈 |
| linked_experience_id | uuid | 해봤어요 연결 — 앱이 바꿈 |
| generation | int | 재생성 회차 |
| created_at | timestamptz | |

RLS: 본인만. `saved`·`memo`·`linked_experience_id`만 앱이 update. 담기 20개 제한은 앱이 먼저 카운트 읽고 막고, DB 트리거는 2차 방어.

### 1.4 notification — 알림 (재현)

| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | uuid | |
| user_id | uuid | |
| type | text | 10종 (new_question / answer / helpful / saved / related_found / reminder / draft / verification / coffeechat_request / coffeechat_result) |
| payload | jsonb | 이동할 화면 정보 |
| grouped_key | text | 하루 묶음용 |
| read | bool | **앱이 바꿀 수 있는 유일한 컬럼** |
| created_at | timestamptz | |

RLS: 본인 select, `read`만 update. 생성은 서버(트리거 + FastAPI). 30일 지난 건 cron 삭제. 앱은 모르는 `type`이 오면 일반 알림으로 표시.

### 1.5 notification_setting (재현)

| 컬럼 | 타입 |
|---|---|
| user_id | uuid PK |
| push_enabled | bool |
| types | jsonb — 유형별 on/off |
| quiet_start, quiet_end | time — 기본 22:00 / 08:00 (Asia/Seoul 고정) |
| reminders | jsonb — `{ experience_id: { days: [...], time: "21:00" } }` |

RLS: 본인 전부. *v0.1의 `expo_push_token` 삭제 → 1.6으로.*

### 1.6 push_device — 기기별 푸시 토큰 (재현) [신규]

| 컬럼 | 타입 |
|---|---|
| user_id, device_id | uuid, text — 복합 PK |
| token | text — Expo push token |
| platform | text — ios / android |
| updated_at | timestamptz |

RLS: 본인 전부. 앱이 시작·포그라운드 복귀 시 upsert, 로그아웃 시 delete. 서버는 Expo가 `DeviceNotRegistered` 주면 삭제.

### 1.7 profile (소윤)

| 컬럼 | 타입 |
|---|---|
| user_id | uuid PK |
| nickname | text, 필수 |
| school, major_field, grade | text, nullable |
| show_school, show_major, show_grade | bool |
| show_qa | bool, 기본 false |
| school_verified | bool — 앱이 못 바꿈 |

RLS: 본인 전부. **남의 프로필은 FastAPI로만** (열 단위 공개라 RLS로 안 됨).

### 앱이 직접 접근하지 않는 테이블 — FastAPI만 읽고 씀

### 1.8 official_activity (소윤)

| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | uuid | |
| name | text | 공식명 |
| organizer | text | nullable |
| period | text | `"2026-1"` 등, 회차마다 별도 행 |
| aliases | text[] | 최대 3 |
| status | text | `pending` / `verified` |
| created_by | uuid | |
| record_count | int | 5 미만이면 숨김 |

검색·등록은 FastAPI `/activities/*`로. 앱 직접 접근 없음 (pending 노출 제어 때문).

### 1.9 qa (소윤)

| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | uuid | |
| experience_id | uuid | 공개 카드만 |
| asker_id | uuid | **앱에 절대 안 내려줌**. 서버가 `is_mine`으로 변환 |
| type | text | 조건 / 결정 이유 / 해본 소감 |
| question | text | 10~100자 |
| answer | text | nullable |
| status | text | `waiting`(답변 대기) / `answered`(답변 완료) / `no_answer`(답변 없음). 기본 `waiting` `[수정 #3]` |
| answered_at | timestamptz | |
| created_at | timestamptz | |

*v0.1의 `helpful_count` 삭제 — 숫자 노출 여부 지은 확인 전까지 서버 집계만.*

답변 없이 14일 지나면 **삭제하지 않고** cron이 `status`를 `no_answer`로 바꿈 → 앱은 "답변이 없었어요"로 접어서 표시하고, 질문자에게 비슷한 경험을 추천 (기능명세서 POL-12, QA-01-②). `TODO(기획)`: 추천을 어디에 어떻게 보여줄지. `[수정 #3]`

### 1.10 helpful_reason (소윤)

| 컬럼 | 타입 |
|---|---|
| id, experience_id, qa_id, user_id | uuid `[수정 #9]` |
| chip | text — 기준이 생겼어요 / 조건을 알게 됐어요 / 다음 행동이 보여요 |
| text | text, 80자 |
| hidden_by_owner | bool |

유니크 키는 **(experience_id, user_id)** — 1카드 1회, 점수·별점 없음, 부정 평가 불가 (기능명세서 POL-14). `[수정 #9]`

### 1.11 reaction (재현) `[수정 #1]`

| 컬럼 | 타입 |
|---|---|
| experience_id, user_id | uuid, 유니크 |
| chip | text — 나도 그랬어 / 나는 반대였어 / 다른 상황이었어 |
| note | text, nullable |

작성자에게 안 보임. 추천에만.

### 1.12 block, report (소윤)

`block(user_id, blocked_id)` · `report(id, reporter_id, target_type, target_id, reason, note)`
`target_type`에 `qa` 포함 — 익명 질문자는 앱이 `qa_id`로 신고하고 서버가 사람을 찾음.

### 1.13 Storage

- `photos/{user_id}/{experience_id}/…` — **앱이 직접 업로드**(리사이즈 후). 본인만 읽기. 공개 시에도 안 보임. 내용 필터링 없음
- `evidence/{user_id}/…` — 나중에

---

## 2부. FastAPI 엔드포인트

베이스 URL `https://api.<도메인>/v1`. 전부 `Authorization: Bearer <supabase jwt>`.

### 기록 (소윤)

#### GET /activities/search?q=
2자 이상, 최대 5건, `verified`만. 별칭·띄어쓰기 무시. 초성은 후순위.
```json
{ "items": [ { "id": "uuid", "name": "...", "organizer": "...", "period": "2026-1", "record_count": 12 | null } ] }
```
`record_count`는 5 미만이면 null.

#### POST /activities
새 공식 활동 등록. `pending`으로 저장, 내 기록에만 연결.
```json
{ "name": "...", "organizer": "...", "period": "2026-1" }
```

#### POST /summarize — 정리 확인
```json
{ "experience_id": "uuid" }
```
```json
{
  "suggested_chips": { "did": ["..."], "situation": ["..."], "emotion": ["..."] },
  "already_selected": ["..."],
  "follow_up": ["...", "..."]
}
```
5초 초과·실패 → 503 `AI_UNAVAILABLE`. `follow_up` 답은 **어디에 저장하는지 미정** (지은 확인).

#### POST /flow-summary — 흐름 요약
```json
{ "experience_id": "uuid" }
```
```json
{ "summary": "..." | null, "log_count": 4 }
```

### 공개 (소윤)

#### POST /publish/preview — 미리보기. **DB 변경 없음**
```json
{ "experience_id": "uuid", "visibility": "anonymous" }
```
```json
{
  "preview": { ...PublicCard 형태... },
  "mask_suggestions": [
    { "field": "place", "original": "학생회관 302호", "suggested": "교내 행사장", "reason": "건물명" },
    { "field": "role", "original": "행사기획팀 팀장", "suggested": "행사 운영 담당", "reason": "같은 활동·역할 5명 미만" }
  ],
  "pii_warnings": [ { "field": "one_liner", "pattern": "email" } ],
  "preview_hash": "..."
}
```

#### POST /publish/confirm — 확정
```json
{ "experience_id": "uuid", "visibility": "anonymous", "mask_decisions": { "place": "accept", "role": "keep" }, "accept_questions": true, "preview_hash": "..." }
```
- `accept_questions`: 질문 받기 스위치. 생략하면 true (기능명세서 PUB-01-②) `[수정 #4]`
- 서버가 익명 규칙 **재검사**. 결과가 preview와 다르면 409 `PUBLISH_PREVIEW_STALE` + 새 preview 반환
- 통과하면 `published_snapshot`·`masked`·`visibility`·`published_at` 갱신, 임베딩 웹훅 트리거
- 멱등키는 안 함 (11/09). 앱은 재진입 시 서버 상태로 화면을 그림
- `profile`로 올릴 때 `school_verified = false`면 403 `PROFILE_VERIFICATION_REQUIRED`

#### POST /publish/settings — 공개 후 설정 변경 `[수정 #4]`
```json
{ "experience_id": "uuid", "accept_questions": false }
```
공개된 카드만. 질문 받기 ON/OFF (기능명세서 POL-12). 끄면 새 질문만 막고 기존 Q&A는 그대로.

#### POST /publish/unpublish — 하향
```json
{ "experience_id": "uuid" }
```
preview 없이 바로 `private`. 카드·Q&A 비노출. Q&A 데이터 보존 여부는 지은 확인.

#### POST /pii-check — 연락처 감지
```json
{ "text": "..." }
```
```json
{ "found": [ { "type": "phone" | "email" | "sns", "span": [12, 25] } ] }
```
정규식. LLM 안 씀.

### 탐색·카드 (재현) [신규 대부분] `[수정 #1]`

#### GET /cards?genre=&role_filter=&cursor=
**공개 카드 목록.** 탐색 탭 두 번째 흐름 — v0.1에 없던 것.
```json
{ "items": [ PublicCard, ... ], "next_cursor": "..." | null }
```
- `experience_public` 뷰에서. 차단·본인 제외 (차단은 FastAPI 쿼리에서 요청자 id로 거름) `[수정 #2]`
- `role_filter`: `same` / `different` — 내 최근 경험 역할 기준
- cursor 방식, 20개씩

#### GET /cards/{experience_id}
**카드 상세** — 카드 + Q&A + 도움 이유 한 번에.
```json
{
  "card": PublicCard,
  "detail": { "situation_chips": [...], "emotion_chips": [...], "conditions": {...}, "did_notes": {...}, "flow_summary": "..." | null },
  "qa": [ { "id": "uuid", "type": "...", "question": "...", "answer": "..." | null, "status": "waiting" | "answered" | "no_answer", "answered_at": "...", "is_mine": false, "helpful_reasons": [ { "chip": "...", "text": "..." } ] } ],
  "my_reaction": "..." | null
}
```
비공개·차단이면 404 `NOT_FOUND`.
- 위 `qa[]`에 `status` 추가 `[수정 #3]`

#### POST /related — 관련 경험 (재현)
```json
{ "question_id": "uuid", "filter": "all" | "same_role" | "different_role" | "genre:{장르}" }
```
```json
{ "items": [ { "card": PublicCard, "reason": "..." | null } ] }
```
- 임베딩 유사도 후보 → **LLM 재정렬 유지** + `reason` 생성. `reason`은 nullable
- 임계값 밑이면 `items: []`
- **처음 열 때 계산하고 캐시.** 무효화: 해당 질문 재생성 시 / 캐시된 카드가 비공개·차단되면 결과에서 즉시 제외(읽을 때 필터)
- 페이지 없음 — 최대 10개로 끝 (v0.1의 `next_cursor` 삭제)

#### POST /reactions
```json
{ "experience_id": "uuid", "chip": "나도 그랬어", "note": "..." | null }
```
같은 카드 다시 보내면 교체. 작성자 알림 없음.

### Q&A (소윤)

#### POST /qa
```json
{ "experience_id": "uuid", "type": "조건", "question": "..." }
```
- 카드 `accept_questions = false` → 403. 하루 5·카드당 3 초과 → 429
- 연락처 패턴 있으면 400 `VALIDATION_FAILED` + `details.pii`
- 성공 시 작성자에게 `new_question` 알림 (트리거)

#### POST /qa/{id}/answer — 카드 주인만
```json
{ "answer": "..." }
```
~~500자. 24시간 내 수정 가능.~~ 질문자에게 `answer` 알림. 성공 시 `status = answered`. `[수정 #8]`
`TODO(기획)`: 답변 500자·24시간 내 수정 가능 규칙은 기능명세서(POL-12, QA-01, QA-02)에 없음 — 지은 확인 전까지 구현 보류. `[수정 #8]`

#### POST /qa/{id}/helpful
```json
{ "chip": "기준이 생겼어요", "text": "..." | null }
```
중복 → 409 `HELPFUL_ALREADY_SENT`.

#### POST /reports
```json
{ "target_type": "experience" | "qa" | "user", "target_id": "uuid", "reason": "...", "note": "...", "block": true }
```

### 질문·분석 (재현)

#### POST /questions — 생성
**호출 주체 결정 필요**: 앱이 저장 직후 부르면 앱 꺼졌을 때 안 생김 / 서버가 저장 웹훅으로 부르면 앱이 "생성 중"을 알아야 함. 제안: **서버 웹훅**으로 생성하고, `question` 테이블에 행이 없으면 앱이 "준비 중" 표시 + 폴링 또는 Realtime 구독.
```json
{ "experience_id": "uuid" }
```
```json
{ "questions": [ { "id": "uuid", "type": "try_next", "text": "...", "source": "...", "is_done": false }, ... ] }
```
try_next 2 + imagine 2. 입력 해시 캐시.

#### POST /questions/regenerate
하루 1회. 초과 → 429 `QUESTION_REGEN_LIMIT`. `saved = true` 유지.

#### POST /analysis
```json
{ "force": false }
```
```json
{
  "locked": false, "record_count": 5,
  "patterns": [ { "id": "uuid", "text": "...", "evidence": [ { "experience_id": "uuid", "activity_name": "..." } ], "feedback": null } ],
  "criteria": { "private": "...", "public": "..." },
  "saved_questions": [ ... ],
  "last_updated": "...", "has_new_records": false
}
```
기록 3개 미만 → `locked: true`. qa·reaction 안 읽음.

#### POST /analysis/feedback
```json
{ "pattern_id": "uuid", "feedback": "yes" | "edit" | "no" | "later", "edited_text": "..." }
```

### 프로필 (소윤)

#### GET /profiles/{user_id} — 남의 프로필
```json
{ "nickname": "...", "school": "서울 소재 대학", "grade": null, "cards": [ PublicCard ], "qa_visible": false }
```
- 비공개 항목은 **키 자체를 뺌** (`major_field` 없음). 미입력은 `null`
- 차단 관계 → 404 `NOT_FOUND`
- `cards`는 `visibility = 'profile'`인 것만

### 알림 (재현)

#### GET /notifications?cursor=
앱이 Supabase 직접 읽어도 되지만 묶음 표시를 서버에서 하려면 여기로. 결정은 재현.

~~`POST /push/devices`, `DELETE /push/devices/{device_id}`~~ 삭제 — 푸시 토큰 등록·삭제는 FastAPI 엔드포인트 없음 — 앱이 `push_device` 테이블에 직접 (1.6). `[수정 #7]`

---

## 3. FastAPI 백그라운드

- 경험 공개 확정 시 임베딩 생성 (confirm 안에서 동기 처리하거나 웹훅)
- 새 공개 카드 → `saved = true`인 질문들과 매칭 → `related_found` 알림
- 리마인드 푸시 cron (Asia/Seoul 기준, 방해 금지 제외)
- 30일 지난 알림 삭제, 14일 미답변 Q&A를 `no_answer`로 변경 (삭제 안 함) `[수정 #3]`
- Expo 전송 결과 `DeviceNotRegistered` → 토큰 삭제
- *v0.1의 "임시저장 2일째 알림" 삭제 — 앱 로컬 알림으로*

---

## 4. 결정됨 (11/09)

- ~~LLM 재정렬 뺄지~~ → **유지**
- ~~PDF 서버 vs 앱~~ → **앱 (expo-print)**. `/export` 삭제
- ~~임베딩 어디서~~ → **FastAPI 웹훅**
- ~~사진 업로드 방식~~ → **앱이 Storage 직접**
- ~~임시저장 위치~~ → **폰 로컬**
- ~~멱등키~~ → **안 함**
- ~~시간대~~ → **Asia/Seoul 고정, 후순위**
- ~~에러 코드~~ → 0번 목록

## 5. 아직 결정 필요

| 항목 | 누가 |
|---|---|
| `/questions` 호출 주체 (앱 vs 서버 웹훅) | 재현 |
| `/notifications` 앱 직접 vs 서버 경유 | 재현 |
| 초성 검색 — pg_trgm으로 안 되면 | 소윤 |
| 개수 제한을 DB 트리거로도 걸지 (앱 체크 + 서버 체크 외에) | 소윤 |
| `follow_up` 답변 저장 위치 | 지은 |
| 공개 → 비공개 시 Q&A 데이터 보존 | 지은 |
| `helpful_count` 화면 노출 | 지은 |
| ~~회차 기록 감정 칩 개수~~ → 1개 (11/16 피그마 기준) | 지은 |
| ~~helpful_reason 유니크 단위 (카드 vs Q&A)~~ → 카드당 1번 확정 (POL-14) | 지은 `[수정 #9]` |
| 다른 폰 이어쓰기 필요 여부 | 지은 |
| Q&A 답변 500자·24시간 수정 규칙 출처 | 지은 `[수정 #8]` |
| 14일 미답변 Q&A의 비슷한 경험 추천 표시 방식 | 지은 `[수정 #3]` |

## 변경 이력

- v0.3 (11/14) — 소윤 검토 반영. 탐색·카드·반응 담당 재현으로 통일. 차단 필터를 뷰 → FastAPI 쿼리로. 14일 미답변 Q&A 삭제 → `qa.status = no_answer`로 접기 (POL-12). `/publish/confirm`에 `accept_questions` 추가, `/publish/settings` 신설. `updated_at` 트리거 자동 갱신 명시. `experience_log` 저장 시 본인 경험 확인. `/push/devices` 두 개 삭제 (앱 직접). helpful_reason 유니크 (experience_id, user_id) 확정 (POL-14). 답변 500자·24시간 규칙 `TODO(기획)`
- v0.2 (11/09) — 앱·서버 경계 원칙 반영. 앱 직접 접근을 내 데이터로 제한, 앱이 못 바꾸는 컬럼 명시. `draft_step` 삭제, `published_at` 추가, `push_device` 테이블 신설, `helpful_count` 삭제. 탐색 목록 `/cards`·카드 상세 `/cards/{id}`·`/activities/*`·`/qa/*`·`/reactions`·`/reports`·`/profiles/{id}`·`/push/devices` 신설. `/publish` → preview/confirm/unpublish 분리. `/export` 삭제. 에러 코드 14개·형식·호환 규칙. PublicCard 통일
- v0.1 (11/06) 초안
