"""DB 없이 분석(패턴 찾기)만 돌려보는 데모. 가짜 경험 6개를 한 사람의 기록이라고 가정한다.
   cd server && python -m scripts.demo_analysis                  # 6개 전부 (회차 기록 2개 포함 기록 8개)
   python -m scripts.demo_analysis f01 f02 f06                   # 골라서
   python -m scripts.demo_analysis --feedback                    # 첫 패턴에 "아니에요", 둘째에 "맞아요"를 누른 것처럼 꾸며 다시 분석
   .env 의 LLM_BASE_URL 이 GPU 서버를 가리켜야 함 (컨테이너 안에서 돌리거나 터널)."""
import asyncio, json, sys, time, pathlib
from collections import defaultdict
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.analysis.generate import find_patterns
from app.analysis.prompt import experience_text, log_text
from app.analysis.schemas import Record
from app.constants import ANALYSIS_MIN_RECORDS, ANALYSIS_PATTERN_MAX
from app.llm.client import LLMConnectionError, LLMModelError


def to_records(data: list[dict]) -> list[Record]:
    """service.load_records 와 같은 번호 규칙 (e1, e1.l1 …). id 자리에 f01 같은 가짜 id 를 그대로 쓴다."""
    out = []
    for i, d in enumerate(data, 1):
        row = defaultdict(lambda: None, d)
        out.append(Record(key=f"e{i}", experience_id=d["id"], activity_name=d["activity_name"], text=experience_text(row)))
        for j, lg in enumerate(sorted(d.get("logs") or [], key=lambda x: x["log_date"]), 1):
            out.append(Record(key=f"e{i}.l{j}", experience_id=d["id"], activity_name=d["activity_name"],
                              text=log_text(d["activity_name"], defaultdict(lambda: None, lg)),
                              log_id=f"{d['id']}.l{j}", log_date=lg["log_date"]))
    return out


async def run(records, existing, blocked, need, title):
    by_key = {r.key: r for r in records}
    print(f"\n{'=' * 70}\n{title}")
    t0 = time.time()
    try:
        out = await find_patterns(records, existing, blocked, need)
    except LLMConnectionError as err:
        print(f"  ✗ 연결 문제 (앱: AI_UNAVAILABLE, reason=connection) — {err}"); return None
    except LLMModelError as err:
        print(f"  ✗ 모델 문제 (앱: AI_UNAVAILABLE, reason=model) — {err}"); return None
    print(f"  {time.time() - t0:.1f}s · 패턴 {len(out.patterns)}개 · 시도 {out.attempts} · 프롬프트 {out.prompt_version} · {out.model}")
    for p in out.patterns:
        where = ", ".join(by_key[k].activity_name + (f" {by_key[k].log_date}" if by_key[k].log_date else "") for k in p.keys)
        print(f"  · {p.text}\n      ← {where}")
    for text, why in out.dropped:
        print(f"  ✗ 버림: {text}  ({why})")
    return out


async def main(ids: list[str], feedback: bool):
    data = json.loads((pathlib.Path(__file__).parent / "fake_experiences.json").read_text(encoding="utf-8"))
    records = to_records([d for d in data if not ids or d["id"] in ids])
    if len(records) < ANALYSIS_MIN_RECORDS:
        print(f"기록 {len(records)}개: 잠김 ({ANALYSIS_MIN_RECORDS}개부터 열림)"); return
    out = await run(records, [], [], ANALYSIS_PATTERN_MAX, f"기록 {len(records)}개 (경험 {sum(1 for r in records if not r.log_id)}개 + 회차 기록)")
    if not feedback or out is None or len(out.patterns) < 2:
        return
    no, yes = out.patterns[0].text, out.patterns[1].text
    again = await run(records, [yes], [no], ANALYSIS_PATTERN_MAX - 1,
                      f"다시 분석: 아니에요 = \"{no}\" / 맞아요 = \"{yes}\"")
    if again is not None:
        print(f"  ▶ 새로 받은 것 {len(again.patterns)}개. 위 두 문장과 같은 내용이 다시 나왔는지 눈으로 본다 (필터는 낱말이 거의 같은 것만 잡는다)")


if __name__ == "__main__":
    args = sys.argv[1:]
    asyncio.run(main([a for a in args if not a.startswith("--")], feedback="--feedback" in args))
