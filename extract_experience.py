"""
transcripts/{video_id}.txt (유튜브 자막) 를 Claude 로 읽어 경험 카드(템플릿 필드) 추출.

- 범위(7차 회의): 화자가 직접 참여하고 역할을 맡은 활동만. 소비만 한 경험·연애·공포·남의 사연·광고는 제외
- ② "내가 한 일" 이 비어 있으면 저장하지 않음 (status=none)
- 결과는 experiences.csv 에 추가:
    video_id, title, activity_name, activity_type, role, situation, action, feeling, embedding_text
  embedding_text = 활동명 + 활동 유형 + 역할 + ② + ③ 을 한 문단으로 합친 것
- 처리한 행은 videos.csv 의 status 를 done / none(범위 밖) / error 로 갱신
- 429(속도 제한)·5xx 는 SDK 가 자동 재시도. 그래도 실패하면 status 는 pending 으로 두고 중단
- 토큰 사용량을 usage_log.csv 에 기록 (실제 단가 확인용)

실행:  python extract_experience.py --limit 30 --shuffle
환경변수: ANTHROPIC_API_KEY (.env 도 읽음), (선택) CLAUDE_MODEL 기본 claude-sonnet-5-5, CLAUDE_EFFORT 기본 medium
먼저 fetch_transcripts.py 로 자막을 받아 두어야 합니다.
"""
import argparse
import csv
import os
import random
import sys
from pathlib import Path
from typing import Literal

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
EFFORT = os.environ.get("CLAUDE_EFFORT", "medium")

HERE = Path(__file__).parent
VIDEOS_CSV = HERE / "videos.csv"
TRANSCRIPT_DIR = HERE / "transcripts"
OUT_CSV = HERE / "experiences.csv"
USAGE_CSV = HERE / "usage_log.csv"
VIDEO_FIELDS = ["video_id", "title", "duration", "status"]
OUT_FIELDS = [
    "video_id", "title",
    "activity_name", "activity_type", "role",
    "situation", "action", "feeling",
    "embedding_text",
]
USAGE_FIELDS = ["video_id", "model", "input_tokens", "output_tokens", "transcript_chars", "result"]

ActivityType = Literal[
    "동아리·학회", "팀플·공모전", "아르바이트", "대외활동·서포터즈",
    "학생회·행사 운영", "봉사·멘토링", "학과 전시", "개인 프로젝트", "인턴",
]


class ExperienceCard(BaseModel):
    """경험 카드. in_scope 가 False 면 나머지 필드는 비워 둠."""
    in_scope: bool = Field(description="화자가 직접 참여하고 역할을 맡은 활동이 있고, 그 안에서 '한 일'을 구체적으로 말했으면 true")
    reason_if_out: str = Field(description="in_scope 가 false 인 이유 한 줄. true 면 빈 문자열")
    activity_name: str = Field(description="활동명. 가능하면 공식 명칭(예: '2024 OO 공모전', 'OO카페 아르바이트')")
    activity_type: ActivityType | None = Field(description="활동 유형. 현장실습·인턴십은 '인턴', 수업 팀프로젝트·해커톤·공모전은 '팀플·공모전'")
    role: str = Field(description="화자의 역할 (예: 팀장, 홀 서빙, 멘토, 홍보팀 부원)")
    situation: str = Field(description="① 어떤 상황이었나. 1~2문장, 1인칭")
    action: str = Field(description="② 내가 한 일. 2~3문장, 1인칭, 구체적 행동. 없으면 빈 문자열")
    feeling: str = Field(description="③ 어땠나. 감정 단어 1~2개 + 설명 1~2문장, 1인칭")


SYSTEM = """당신은 대학생의 경험을 '경험 카드'로 정리하는 도우미입니다. 유튜브 영상의 자동 생성 자막(오타·띄어쓰기 오류 있음)을 읽고, 화자가 직접 참여하고 역할을 맡았던 활동 하나를 골라 카드 필드를 채웁니다.

포함 기준: 알바, 동아리·학회, 팀플·공모전·해커톤, 대외활동·서포터즈·기자단, 학생회·축제 등 행사 운영, 봉사·멘토링, 학과 전시, 개인 프로젝트, 인턴·현장실습처럼 화자가 그 안에서 '한 일'이 있는 활동.
제외 기준 (in_scope=false):
- 단순히 보고, 먹고, 구경하고, 당한 이야기 (관광, 관람, 연애, 공포 썰, 지인·남의 사연)
- 정보 전달·강의·광고·제품 소개 위주로 화자의 행동 서술이 없는 영상
- 기업·방송이 제작한 콩트·예능 형식의 연출된 '알바/인턴' 콘텐츠
- 활동 소개나 모집 안내만 있고 화자가 실제로 한 일이 없는 경우

여러 활동이 나오면 화자가 가장 구체적으로 '한 일'을 말한 활동 하나만 고릅니다. 자막에 없는 내용은 만들어 넣지 않습니다. 모든 서술은 화자의 1인칭으로, 한국어로 씁니다."""


def build_client() -> anthropic.Anthropic:
    # ANTHROPIC_API_KEY 환경변수(.env 포함) 사용.
    # Accept-Encoding 에서 brotli 를 빼는 이유: 이 환경(anaconda)의 brotlicffi 1.1.0 이 httpx2 2.16 이 넘기는
    # output_buffer_limit 인자를 몰라 응답 해제 중 TypeError → SDK 가 APIConnectionError 로 보고함.
    return anthropic.Anthropic(default_headers={"Accept-Encoding": "gzip, deflate"})


def extract(client: anthropic.Anthropic, title: str, transcript: str):
    user = f"영상 제목: {title}\n\n자막:\n{transcript}"
    return client.messages.parse(
        model=MODEL,
        max_tokens=4000,
        system=SYSTEM,
        output_config={"effort": EFFORT},
        messages=[{"role": "user", "content": user}],
        output_format=ExperienceCard,
    )


def _clean(v: str) -> str:
    """모델 출력 끝에 가끔 섞이는 따옴표·괄호 찌꾸기 제거."""
    return v.strip().strip('"\'“”}{').strip()


def to_row(video_id: str, title: str, card: ExperienceCard) -> dict:
    for f in ("activity_name", "role", "situation", "action", "feeling"):
        setattr(card, f, _clean(getattr(card, f)))
    embedding_text = " ".join(
        s for s in [card.activity_name, card.activity_type or "", card.role, card.action, card.feeling] if s
    )
    return {
        "video_id": video_id, "title": title,
        "activity_name": card.activity_name, "activity_type": card.activity_type or "",
        "role": card.role, "situation": card.situation, "action": card.action, "feeling": card.feeling,
        "embedding_text": embedding_text,
    }


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


def append_csv(path: Path, fields: list[str], row: dict) -> None:
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new_file:
            w.writeheader()
        w.writerow(row)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=2, help="이번에 처리할 영상 수")
    ap.add_argument("--shuffle", action="store_true", help="자막 있는 pending 중 무작위로 고름")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY 가 없습니다. .env 에 추가하세요.", file=sys.stderr)
        return 1
    if not VIDEOS_CSV.exists():
        print(f"{VIDEOS_CSV} 가 없습니다. 먼저 collect_youtube.py 를 실행하세요.", file=sys.stderr)
        return 1

    rows = load_rows()
    pending = [r for r in rows if r["status"] == "pending" and (TRANSCRIPT_DIR / f"{r['video_id']}.txt").exists()]
    if args.shuffle:
        random.Random(args.seed).shuffle(pending)
    targets = pending[: args.limit]
    if not targets:
        print("자막이 있는 pending 영상이 없습니다. fetch_transcripts.py 를 먼저 실행하세요.")
        return 0

    client = build_client()
    done = none = 0
    tot_in = tot_out = 0
    for r in targets:
        vid, title = r["video_id"], r["title"]
        transcript = (TRANSCRIPT_DIR / f"{vid}.txt").read_text(encoding="utf-8")
        print(f"\n=== {title}  ({r['duration']}, 자막 {len(transcript):,}자)")
        try:
            res = extract(client, title, transcript)
        except anthropic.RateLimitError as e:
            print(f"  속도 제한(429)이 재시도 후에도 계속됩니다. 중단합니다. {e.message}", file=sys.stderr)
            break
        except anthropic.APIStatusError as e:
            if e.status_code >= 500:
                print(f"  서버 오류({e.status_code}). 중단합니다.", file=sys.stderr)
                break
            print(f"  API 오류({e.status_code}): {e.message[:200]}", file=sys.stderr)
            r["status"] = "error"
            save_rows(rows)
            continue
        except anthropic.APIConnectionError as e:
            print(f"  네트워크 오류. 중단합니다. {e}", file=sys.stderr)
            break

        tot_in += res.usage.input_tokens
        tot_out += res.usage.output_tokens
        if res.stop_reason == "refusal":
            print("  모델이 응답을 거부했습니다. status=error")
            r["status"] = "error"
            result = "refusal"
        else:
            card: ExperienceCard = res.parsed_output
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
        append_csv(USAGE_CSV, USAGE_FIELDS, {
            "video_id": vid, "model": MODEL, "input_tokens": res.usage.input_tokens,
            "output_tokens": res.usage.output_tokens, "transcript_chars": len(transcript), "result": result,
        })
        save_rows(rows)

    n = done + none
    if n:
        print(f"\n저장 {done}개, 제외 {none}개 → {OUT_CSV}")
        print(f"토큰: 입력 {tot_in:,} / 출력 {tot_out:,} (영상당 평균 입력 {tot_in // n:,}, 출력 {tot_out // n:,})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
