"""
유튜브 영상 URL 을 Gemini 에 직접 넘겨 경험 카드 추출 (자막 불필요, 내 IP 로 유튜브를 두드리지 않음).

- 대상: videos.csv 에서 status=pending 이고 transcripts/ 에 자막 파일이 없는 영상 (자막 있는 건 extract_experience.py 로)
- 카드 형식·범위 판정 기준·저장 파일은 extract_experience.py 와 동일 (ExperienceCard, SYSTEM 재사용)
- 비용 절감: 기본 10분 이하 영상 우선, media_resolution=LOW
- 429(할당량) 는 즉시 중단하고 status 는 pending 으로 유지. 토큰 사용량은 usage_log.csv 에 기록

실행:  python extract_experience_gemini.py --limit 20 --shuffle [--max-sec 600] [--sleep 4]
환경변수: GEMINI_API_KEY, (선택) GEMINI_MODEL 기본 gemini-3.5-flash-lite
"""
import argparse
import json
import os
import random
import re
import sys
import time

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

load_dotenv()

from extract_experience import (  # noqa: E402
    OUT_CSV, OUT_FIELDS, SYSTEM, TRANSCRIPT_DIR, USAGE_CSV, USAGE_FIELDS,
    ExperienceCard, append_csv, load_rows, save_rows, to_row,
)

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")


def secs(iso: str) -> int:
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    h, mi, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + s


def extract(client: genai.Client, video_id: str, title: str):
    url = f"https://www.youtube.com/watch?v={video_id}"
    return client.models.generate_content(
        model=MODEL,
        contents=[
            types.Part(file_data=types.FileData(file_uri=url)),
            types.Part(text=f"영상 제목: {title}\n\n이 영상을 보고 경험 카드를 채우세요."),
        ],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM,
            response_mime_type="application/json",
            response_schema=ExperienceCard,
            media_resolution="MEDIA_RESOLUTION_LOW",
            temperature=0.2,
        ),
    )


def parse_card(res) -> ExperienceCard:
    if getattr(res, "parsed", None) is not None:
        return res.parsed
    return ExperienceCard.model_validate(json.loads(res.text))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--shuffle", action="store_true")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--max-sec", type=int, default=600, help="이 길이 이하 영상만 (비용 절감). 0이면 제한 없음")
    ap.add_argument("--sleep", type=float, default=4.0, help="요청 간 대기(초). 무료 등급은 분당 요청 제한이 낮음")
    args = ap.parse_args()

    if not os.environ.get("GEMINI_API_KEY"):
        print("GEMINI_API_KEY 가 없습니다. .env 에 추가하세요.", file=sys.stderr)
        return 1

    rows = load_rows()
    pending = [
        r for r in rows
        if r["status"] == "pending"
        and not (TRANSCRIPT_DIR / f"{r['video_id']}.txt").exists()
        and (args.max_sec == 0 or 0 < secs(r["duration"]) <= args.max_sec)
    ]
    if args.shuffle:
        random.Random(args.seed).shuffle(pending)
    targets = pending[: args.limit]
    if not targets:
        print("처리할 pending 영상이 없습니다.")
        return 0
    print(f"모델 {MODEL}, 대상 {len(targets)}개 (후보 {len(pending)}개)")

    client = genai.Client()
    done = none = err = 0
    tot_in = tot_out = 0
    for i, r in enumerate(targets, 1):
        vid, title = r["video_id"], r["title"]
        print(f"\n[{i}/{len(targets)}] {title}  ({r['duration']})")
        try:
            res = extract(client, vid, title)
        except errors.ClientError as e:
            if e.code == 429:
                print("  할당량 초과(429). 중단합니다. (무료 등급이면 내일, 유료 전환했으면 잠시 후 재실행)", file=sys.stderr)
                break
            if e.code == 402:
                print("  선불 크레딧 소진(402). 중단합니다. AI Studio 에서 충전 후 재실행하세요.", file=sys.stderr)
                break
            if e.code == 404:
                print(f"  모델 {MODEL} 을 찾을 수 없음(404). 중단합니다.", file=sys.stderr)
                break
            print(f"  API 오류({e.code}): {str(e)[:160]}", file=sys.stderr)
            r["status"] = "error"
            err += 1
            save_rows(rows)
            time.sleep(args.sleep)
            continue
        except errors.ServerError as e:
            print(f"  서버 오류({e.code}). 이 영상은 pending 으로 두고 넘어갑니다.", file=sys.stderr)
            err += 1
            time.sleep(args.sleep * 3)
            continue

        um = res.usage_metadata
        n_in = um.prompt_token_count or 0
        n_out = (um.candidates_token_count or 0) + (um.thoughts_token_count or 0)
        tot_in += n_in
        tot_out += n_out
        try:
            card = parse_card(res)
        except Exception as e:
            print(f"  응답 파싱 실패: {type(e).__name__} {str(e)[:120]}", file=sys.stderr)
            r["status"] = "error"
            err += 1
            result = "parse_error"
        else:
            if not card.in_scope or not card.action.strip():
                r["status"] = "none"
                none += 1
                result = "none"
                print(f"  → 범위 밖: {card.reason_if_out or '② 비어 있음'}")
            else:
                append_csv(OUT_CSV, OUT_FIELDS, to_row(vid, title, card))
                r["status"] = "done"
                done += 1
                result = "done"
                print(f"  [{card.activity_type}] {card.activity_name} / {card.role}")
                print(f"  ② {card.action}")
                print(f"  ③ {card.feeling}")
        print(f"  토큰 입력 {n_in:,} / 출력 {n_out:,}")
        append_csv(USAGE_CSV, USAGE_FIELDS, {
            "video_id": vid, "model": MODEL, "input_tokens": n_in, "output_tokens": n_out,
            "transcript_chars": secs(r["duration"]), "result": result,
        })
        save_rows(rows)
        time.sleep(args.sleep)

    n = done + none
    print(f"\n저장 {done}개, 제외 {none}개, 오류 {err}개 → {OUT_CSV}")
    if n:
        print(f"토큰: 입력 {tot_in:,} / 출력 {tot_out:,} (영상당 평균 입력 {tot_in // n:,}, 출력 {tot_out // n:,})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
