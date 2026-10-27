"""
YouTube Data API v3 search.list 로 경험담 영상 링크 수집 → videos.csv

- 검색어 리스트 순회, type=video / regionCode=KR / relevanceLanguage=ko / videoDuration=medium
- 제목이 EXCLUDE_TITLE(공포·연애·남의 사연·광고)에 걸리면 저장하지 않음
- 저장 컬럼: video_id, title, duration, status   (채널명 등 작성자 정보는 저장하지 않음)
- videoId 중복 제거, 이미 CSV에 있는 영상은 건너뜀
- 403 quotaExceeded 발생 시 즉시 중단, 진행 상태(state.json)에 저장 → 다음 실행 때 이어서 함

실행:  python collect_youtube.py
환경변수: YOUTUBE_API_KEY  (.env 파일도 읽음)
"""
import csv
import html
import json
import os
import re
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ.get("YOUTUBE_API_KEY")
BASE = "https://www.googleapis.com/youtube/v3"

# 활동 유형(7차 회의 분류표) × 서술 표현(후기 > 브이로그 > 회고) 조합
QUERIES = [
    # 아르바이트
    "알바 후기", "첫 알바 브이로그", "알바 하면서 배운 점", "카페 알바 일주일",
    # 동아리·학회
    "대학 동아리 후기", "학회 활동 브이로그", "동아리 운영진 후기",
    # 팀플·공모전
    "공모전 준비 과정", "공모전 수상 후기", "팀플 브이로그", "해커톤 후기",
    # 대외활동·서포터즈
    "대외활동 후기", "서포터즈 활동 후기", "기자단 활동 브이로그",
    # 학생회·행사 운영
    "축제 기획단 브이로그", "학생회 활동 후기", "행사 스태프 알바 후기",
    # 봉사·멘토링
    "봉사활동 후기 대학생", "해외봉사 브이로그", "멘토링 활동 후기",
    # 학과 전시·개인 프로젝트
    "졸업전시 준비 브이로그", "개인 프로젝트 후기", "사이드 프로젝트 회고",
    # 인턴
    "인턴 후기", "현장실습 브이로그", "인턴 첫 출근",
]

# 1단 필터: 제목만 보고 범위 밖(공포·연애·남의 사연·광고)인 영상은 저장하지 않음
EXCLUDE_TITLE = re.compile(
    r"소름|무서|살인|공포|괴담|귀신|심령"            # 공포·범죄
    r"|남친|여친|썸|전남|전여|손절|바람|소개팅|연애"    # 연애·인간관계
    r"|쪽팔|수치|현타|메이크업|화장하면서|빌런"        # 잡담형 썰
    r"|남의 썰|남의썰|구독자 사연|사연 모음|사연읽"     # 화자 본인 경험 아님
    r"|협찬|광고(?!\s*아님)|\[AD\]|\bAD\b|알바천국|알바몬"  # 광고·구인 플랫폼 제작 콘텐츠
)
MAX_PAGES_PER_QUERY = 2          # search.list 1페이지 = 100 유닛. 하루 기본 할당량 10,000
PAGE_SIZE = 50

HERE = Path(__file__).parent
CSV_PATH = HERE / "videos.csv"
STATE_PATH = HERE / "state.json"
FIELDS = ["video_id", "title", "duration", "status"]


class QuotaExceeded(Exception):
    pass


def yt_get(endpoint: str, **params) -> dict:
    params["key"] = API_KEY
    r = requests.get(f"{BASE}/{endpoint}", params=params, timeout=30)
    if r.status_code == 403:
        try:
            reasons = [e.get("reason") for e in r.json()["error"]["errors"]]
        except Exception:
            reasons = []
        if "quotaExceeded" in reasons or "dailyLimitExceeded" in reasons:
            raise QuotaExceeded(r.text)
    r.raise_for_status()
    return r.json()


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"query_index": 0, "page": 0, "page_token": None}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_existing_ids() -> set[str]:
    if not CSV_PATH.exists():
        return set()
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        return {row["video_id"] for row in csv.DictReader(f)}


def append_rows(rows: list[dict]) -> None:
    new_file = not CSV_PATH.exists()
    with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        w.writerows(rows)


def search_page(query: str, page_token: str | None) -> dict:
    params = dict(
        part="snippet",
        q=query,
        type="video",
        regionCode="KR",
        relevanceLanguage="ko",
        videoDuration="medium",
        maxResults=PAGE_SIZE,
        safeSearch="moderate",
    )
    if page_token:
        params["pageToken"] = page_token
    return yt_get("search", **params)


def fetch_durations(video_ids: list[str]) -> dict[str, str]:
    """videos.list(contentDetails) 로 ISO8601 길이 조회. 50개당 1유닛."""
    out: dict[str, str] = {}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i : i + 50]
        data = yt_get("videos", part="contentDetails", id=",".join(chunk), maxResults=50)
        for item in data.get("items", []):
            out[item["id"]] = item["contentDetails"]["duration"]
    return out


def main() -> int:
    if not API_KEY:
        print("YOUTUBE_API_KEY 가 없습니다. .env 에 추가하세요.", file=sys.stderr)
        return 1

    state = load_state()
    seen = load_existing_ids()
    print(f"기존 저장 영상 {len(seen)}개, 재시작 지점: query_index={state['query_index']} page={state['page']}")

    added_total = 0
    try:
        while state["query_index"] < len(QUERIES):
            query = QUERIES[state["query_index"]]
            while state["page"] < MAX_PAGES_PER_QUERY:
                data = search_page(query, state["page_token"])
                items = [it for it in data.get("items", []) if it["id"].get("kind") == "youtube#video"]
                for it in items:
                    it["snippet"]["title"] = html.unescape(it["snippet"]["title"])
                skipped = [it["snippet"]["title"] for it in items if EXCLUDE_TITLE.search(it["snippet"]["title"])]
                ids = [
                    it["id"]["videoId"]
                    for it in items
                    if it["id"]["videoId"] not in seen and not EXCLUDE_TITLE.search(it["snippet"]["title"])
                ]
                # 같은 페이지 안의 중복 제거(순서 유지)
                ids = list(dict.fromkeys(ids))
                titles = {it["id"]["videoId"]: it["snippet"]["title"] for it in items}
                durations = fetch_durations(ids) if ids else {}

                rows = [
                    {"video_id": vid, "title": titles[vid], "duration": durations.get(vid, ""), "status": "pending"}
                    for vid in ids
                ]
                if rows:
                    append_rows(rows)
                    seen.update(ids)
                    added_total += len(rows)
                print(f"[{query}] page {state['page'] + 1}: 신규 {len(rows)}개, 제목 필터 제외 {len(skipped)}개")

                # 진행 상태 갱신 (이 페이지는 끝났음)
                state["page"] += 1
                state["page_token"] = data.get("nextPageToken")
                save_state(state)
                if not state["page_token"]:
                    break

            # 다음 검색어로
            state["query_index"] += 1
            state["page"] = 0
            state["page_token"] = None
            save_state(state)

        print(f"완료. 이번 실행 신규 {added_total}개, 총 {len(seen)}개 → {CSV_PATH}")
        # 전부 끝났으면 state 초기화 (다음 실행은 처음부터, 중복은 CSV로 걸러짐)
        STATE_PATH.unlink(missing_ok=True)
        return 0

    except QuotaExceeded:
        save_state(state)
        print(
            f"할당량 초과(403 quotaExceeded). 중단합니다. 이번 실행 신규 {added_total}개.\n"
            f"진행 상태를 {STATE_PATH} 에 저장했으니 다음 실행 때 이어서 합니다.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
