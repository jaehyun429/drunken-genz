"""
videos.csv 의 pending 영상에서 유튜브 자막(한국어, 자동 생성 포함)을 받아 transcripts/{video_id}.txt 로 저장.

- API 키 불필요 (youtube-transcript-api)
- 자막이 없거나 비공개/삭제된 영상은 videos.csv status 를 no_caption 으로 갱신
- 이미 transcripts/ 에 파일이 있으면 건너뜀
- 너무 짧은 자막(기본 300자 미만: 음악만 깔린 브이로그 등)도 no_caption 처리

실행:  python fetch_transcripts.py --limit 100 [--shuffle --seed 1] [--sleep 3]
- 짧은 간격으로 수십 개 연속 요청하면 유튜브가 IP 를 잠시 차단함(IpBlocked). 기본 3초 간격
"""
import argparse
import csv
import random
import sys
import time
from pathlib import Path

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    AgeRestricted,
    InvalidVideoId,
    IpBlocked,
    NoTranscriptFound,
    PoTokenRequired,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    VideoUnplayable,
)

# 자막을 영영 못 받는 경우 → no_caption 처리
NO_CAPTION_ERRORS = (NoTranscriptFound, TranscriptsDisabled, VideoUnavailable, VideoUnplayable, AgeRestricted, InvalidVideoId)
# 유튜브가 이 IP 의 요청을 막는 경우 → 즉시 중단 (보통 수십 분~몇 시간 뒤 풀림)
BLOCKED_ERRORS = (IpBlocked, RequestBlocked, PoTokenRequired)

HERE = Path(__file__).parent
VIDEOS_CSV = HERE / "videos.csv"
TRANSCRIPT_DIR = HERE / "transcripts"
VIDEO_FIELDS = ["video_id", "title", "duration", "status"]
LANGS = ["ko", "ko-KR"]
MIN_CHARS = 300


def load_rows() -> list[dict]:
    with VIDEOS_CSV.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_rows(rows: list[dict]) -> None:
    tmp = VIDEOS_CSV.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=VIDEO_FIELDS)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(VIDEOS_CSV)


def fetch_transcript(api: YouTubeTranscriptApi, video_id: str) -> str | None:
    """한국어 자막(수동 우선, 없으면 자동 생성)을 한 덩어리 텍스트로. 없으면 None."""
    try:
        fetched = api.fetch(video_id, languages=LANGS)
    except NO_CAPTION_ERRORS:
        return None
    text = " ".join(s.text.replace("\n", " ").strip() for s in fetched if s.text.strip())
    return text or None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--shuffle", action="store_true")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--sleep", type=float, default=3.0, help="요청 간 대기(초). 0.5초로 43개 연속 요청 시 IP 차단됨")
    args = ap.parse_args()

    TRANSCRIPT_DIR.mkdir(exist_ok=True)
    rows = load_rows()
    pending = [r for r in rows if r["status"] == "pending" and not (TRANSCRIPT_DIR / f"{r['video_id']}.txt").exists()]
    if args.shuffle:
        random.Random(args.seed).shuffle(pending)
    targets = pending[: args.limit]
    if not targets:
        print("자막을 받을 pending 영상이 없습니다.")
        return 0

    api = YouTubeTranscriptApi()
    ok = none = err = 0
    blocked = False
    for i, r in enumerate(targets, 1):
        vid = r["video_id"]
        try:
            text = fetch_transcript(api, vid)
        except BLOCKED_ERRORS as e:
            print(f"[{i}/{len(targets)}] {vid} 유튜브가 이 IP 의 요청을 막고 있습니다({type(e).__name__}). 중단합니다.", file=sys.stderr)
            print("  잠시(30분~수 시간) 후 다시 실행하세요. --sleep 값을 늘리면 덜 걸립니다.", file=sys.stderr)
            err += 1
            blocked = True
            break
        except Exception as e:
            print(f"[{i}/{len(targets)}] {vid} 에러: {type(e).__name__} {str(e)[:120]}", file=sys.stderr)
            err += 1
            continue
        if text is None or len(text) < MIN_CHARS:
            r["status"] = "no_caption"
            none += 1
            print(f"[{i}/{len(targets)}] {vid} 자막 없음/너무 짧음  ← {r['title'][:40]}")
        else:
            (TRANSCRIPT_DIR / f"{vid}.txt").write_text(text, encoding="utf-8")
            ok += 1
            print(f"[{i}/{len(targets)}] {vid} 자막 {len(text):,}자  ← {r['title'][:40]}")
        if i % 10 == 0:
            save_rows(rows)
        time.sleep(args.sleep)

    save_rows(rows)
    print(f"\n자막 저장 {ok}개, 자막 없음 {none}개, 에러 {err}개 → {TRANSCRIPT_DIR}/")
    return 2 if blocked else 0   # 2 = IP 차단으로 중단 (run_pipeline.sh 가 이 코드를 보고 대기)


if __name__ == "__main__":
    sys.exit(main())
