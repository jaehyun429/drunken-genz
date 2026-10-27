# drunken-genz — 경험 지도 1차

대학생 경험 카드를 임베딩해 "섬"으로 나눈 밑지도와, 새 카드를 그 위에 배치하는 로직입니다.
지도 구조는 6차 회의의 대륙 방식, 카드 범위·필드·연결 규칙은 7차 회의 결정을 따릅니다.

## 흐름

| 단계 | 스크립트 | 결과 |
|---|---|---|
| 1. 영상 수집 | `collect_youtube.py` | `videos.csv` (YouTube Data API, 활동 유형별 검색어 + 제목 필터) |
| 2a. 자막 추출 | `fetch_transcripts.py` → `extract_experience.py` | 자막 → Claude 구조화 출력으로 카드 |
| 2b. 영상 직접 추출 | `extract_experience_gemini.py` | 영상 URL → Gemini 로 카드 (자막 방식이 IP 차단되면 이쪽) |
| 3. 태그 | `tag_cards.py` | 카드별 연결용 태그 3~6개 + 한 줄 요약 |
| 4. 밑지도 | `build_map.py` | 임베딩(③ 어땠나) → t-SNE → 섬 8~12개 → 이름 → `map/regions.json`, 뷰어 |
| 5. 새 카드 배치 | `place_card.py` | 카드 CSV → 태그·임베딩 → 섬·좌표·연결 (`sample_cards.csv` 형식) |

`run_pipeline.sh` 는 2a 를 순차 실행하는 껍데기입니다. 두 추출 스크립트는 `videos.csv` 를 통째로 다시 쓰므로 동시에 돌리지 않습니다.

## 설치

```
pip install -r requirements.txt
cp .env.example .env   # 키 채우기
```

`.env`: `YOUTUBE_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, (선택) `GEMINI_MODEL`, `CLAUDE_MODEL`.

## 저장소에 없는 것

유튜브에서 뽑은 텍스트(`transcripts/`, `experiences.csv`, `map/map.json`, 뷰어 HTML)는 팀 결정대로 지도 생성 후 삭제 대상이라 공개 저장소에 올리지 않습니다.
남기는 것은 섬 정보와 익명 좌표(`map/regions.json`), 배치용 익명 벡터(`map/base_vectors.npz`), 태그 어휘(`map/tag_vocab.json`)입니다.
뷰어는 공유 링크로 봅니다.

## 결정 사항·미결

임의로 정한 것 전체 목록과 검토 우선순위는 [docs/DECISIONS.md](docs/DECISIONS.md).

- 임베딩 입력은 ③ 어땠나(후기)만 (`--embed-fields feeling`). 활동명·유형을 넣으면 섬이 활동 종류별로 갈려서 뺐음. `regions.json` 의 `meta.embed_fields` 에 기록됨.
- 섬 개수는 8~12 중 실루엣으로 자동 선택(현재 8). 디자인 결정이 오면 `build_map.py --k N`.
- 섬 이름은 `map/region_names.json` 에 캐시 (멤버 구성이 같으면 다시 짓지 않음).
- 유튜브 삭제 후 `base_vectors.npz` 보관 여부는 팀 결정. 보관하지 않으면 `--placement centroid`.
