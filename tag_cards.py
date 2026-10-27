"""
경험 카드(experiences.csv)마다 연결용 태그 3~6개와 한 줄 요약을 Claude 로 뽑아 map/tags_raw.json 에 저장 (1차: 자유 태그).
- 7차 회의: 연결은 '같은 태그가 뽑히면 연결' 뿐. 태그는 역량·일의 성격·분야처럼 다른 활동과 공통점이 될 수 있는 말.
- 이미 처리한 카드는 건너뜀 (재실행 안전). 2차 정규화(동의어 병합)는 merge_tags.py.

실행:  python tag_cards.py [--limit N] [--workers 4]
"""
import argparse
import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field

from extract_experience import EFFORT, MODEL, OUT_CSV, build_client

HERE = Path(__file__).parent
RAW_JSON = HERE / "map" / "tags_raw.json"


class CardTags(BaseModel):
    summary: str = Field(description="카드 한 줄 요약. 40자 이내, 1인칭 생략한 명사형 (예: '카페 마감 혼자 맡아 재고·정리까지 처리')")
    tags: list[str] = Field(description="연결용 태그 3~6개. 각 2~8자 명사형")


SYSTEM = """대학생의 경험 카드를 읽고, 다른 경험과 '공통점'으로 이어질 수 있는 태그를 뽑습니다. 이 태그가 같으면 두 경험이 지도에서 선으로 연결됩니다.

태그 종류 (섞어서 3~6개):
- 역량: 커뮤니케이션, 기획, 리더십, 문제 해결, 협업, 꼼꼼함, 책임감, 체력, 끈기, 창의성, 적응력, 시간 관리
- 일의 성격: 고객 응대, 콘텐츠 제작, 영상 편집, 글쓰기, 교육·지도, 행사 운영, 판매·영업, 조리, 재고 관리, 홍보·마케팅, 디자인, 데이터 분석, 개발, 인터뷰·취재, 사진 촬영, 발표, 자료 조사, 번역·외국어
- 분야: 교육, 식음료, 문화·예술, IT, 환경, 지역사회, 아동·청소년, 패션, 스포츠, 미디어, 금융, 해외

규칙:
- 위 목록에 없는 말도 되지만, 위 목록에 맞는 게 있으면 그 표현을 그대로 씁니다 (표기 통일이 중요).
- 활동 유형 자체(아르바이트, 인턴, 동아리 등)와 고유명사(회사명, 학교명, 지명)는 태그로 쓰지 않습니다.
- 카드에 근거가 있는 것만. 감정 단어(뿌듯함 등)는 태그가 아닙니다.
- 한국어, 명사형으로 씁니다."""


def tag_one(client: anthropic.Anthropic, row: dict) -> tuple[str, dict]:
    user = (
        f"활동명: {row['activity_name']}\n활동 유형: {row['activity_type']}\n역할: {row['role']}\n"
        f"① 상황: {row['situation']}\n② 내가 한 일: {row['action']}\n③ 어땠나: {row['feeling']}"
    )
    res = client.messages.parse(
        model=MODEL, max_tokens=1500, system=SYSTEM,
        output_config={"effort": EFFORT},
        messages=[{"role": "user", "content": user}],
        output_format=CardTags,
    )
    out: CardTags = res.parsed_output
    tags = []
    for t in out.tags:
        t = t.strip().strip("#").strip()
        if t and t not in tags:
            tags.append(t)
    return row.get("video_id") or row.get("id", ""), {"summary": out.summary.strip(), "tags": tags,
                             "in": res.usage.input_tokens, "out": res.usage.output_tokens}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10_000)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    rows = list(csv.DictReader(OUT_CSV.open(encoding="utf-8")))
    done: dict = json.loads(RAW_JSON.read_text(encoding="utf-8")) if RAW_JSON.exists() else {}
    todo = [r for r in rows if r["video_id"] not in done][: args.limit]
    print(f"카드 {len(rows)}장, 처리 완료 {len(done)}장, 이번 {len(todo)}장 (모델 {MODEL})")
    if not todo:
        return 0

    client = build_client()
    tot_in = tot_out = n = 0
    stop = False
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(tag_one, client, r): r for r in todo}
        for f in as_completed(futs):
            r = futs[f]
            try:
                vid, info = f.result()
            except (anthropic.RateLimitError, anthropic.APIConnectionError) as e:
                print(f"  중단 사유: {type(e).__name__}. 지금까지 결과는 저장됩니다.", file=sys.stderr)
                stop = True
                continue
            except Exception as e:
                print(f"  {r['video_id']} 실패: {type(e).__name__} {str(e)[:120]}", file=sys.stderr)
                continue
            if stop:
                continue
            done[vid] = {"summary": info["summary"], "tags": info["tags"]}
            tot_in += info["in"]; tot_out += info["out"]; n += 1
            print(f"[{n}/{len(todo)}] {r['activity_name'][:28]} → {', '.join(info['tags'])}")
            if n % 20 == 0:
                RAW_JSON.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")
    RAW_JSON.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n저장 {len(done)}장 → {RAW_JSON}")
    if n:
        print(f"토큰: 입력 {tot_in:,} / 출력 {tot_out:,} (카드당 입력 {tot_in // n:,}, 출력 {tot_out // n:,})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
