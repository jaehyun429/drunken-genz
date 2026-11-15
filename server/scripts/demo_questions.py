"""DB 없이 질문 생성만 돌려보는 데모 (11/17 회의용).
   cd server && python -m scripts.demo_questions                 # 가짜 경험 6개 전부
   python -m scripts.demo_questions f02 f04                      # 골라서
   .env 의 LLM_BASE_URL 이 GPU 서버를 가리켜야 함 (컨테이너 안에서 돌리거나 터널)."""
import asyncio, json, sys, time, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.questions.generate import generate
from app.questions.schemas import ExperienceInput, OtherExperience
from app.llm.client import LLMConnectionError, LLMModelError


async def main(ids: list[str]):
    data = json.loads((pathlib.Path(__file__).parent / "fake_experiences.json").read_text(encoding="utf-8"))
    exps = [ExperienceInput(**d) for d in data if not ids or d["id"] in ids]
    all_others = [OtherExperience(id=d["id"], activity_name=d["activity_name"], genre=d["genre"], role=d["role"],
                                  did_chips=d["did_chips"]) for d in data]
    for e in exps:
        others = [o for o in all_others if o.id != e.id]   # 같은 사람의 다른 기록이라고 가정
        print(f"\n{'='*70}\n[{e.id}] {e.activity_name} / {e.role}\n  한 줄: {e.one_liner}")
        t0 = time.time()
        try:
            out = await generate(e, others)
        except LLMConnectionError as err:
            print(f"  ✗ 연결 문제 (앱: 다시 시도 버튼) — {err}"); continue
        except LLMModelError as err:
            print(f"  ✗ 모델 문제 (앱: 안내만) — {err}"); continue
        again = f" · 다시 물음 {out.reasks}회" if out.reasks else ""
        print(f"  {time.time()-t0:.1f}s · {len(out.questions)}개 · 시도 {out.attempts}{again} · 프롬프트 {out.prompt_version}")
        for q in out.questions:
            tag = "해볼 것 " if q.type == "try_next" else "상상해보기"
            done = f"  [이미 해본 것: {q.done_experience_id}]" if q.done_experience_id else ""
            print(f"  · {tag}({q.axis}) {q.text}{done}\n      ← {q.source}")
        for text, why in out.dropped:
            print(f"  ✗ 버림: {text}  ({why})")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
