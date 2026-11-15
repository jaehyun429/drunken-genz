"""DB 없이 질문 생성만 돌려보는 데모 (11/17 회의용).
   cd server && python -m scripts.demo_questions                 # 가짜 경험 6개 전부
   python -m scripts.demo_questions f02 f04                      # 골라서
   python -m scripts.demo_questions --force-reask f02            # 다시 묻기를 실제 모델로 확인 (아래 force_reask)
   .env 의 LLM_BASE_URL 이 GPU 서버를 가리켜야 함 (컨테이너 안에서 돌리거나 터널)."""
import asyncio, json, sys, time, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.questions import filters
from app.questions.generate import generate
from app.questions.schemas import ExperienceInput, OtherExperience
from app.llm.client import LLMConnectionError, LLMModelError

FORCED = "확인용으로 뺌 (다른 재료에서 새로 만들 것)"
_real_apply = filters.apply


def force_reask():
    """첫 응답에서 질문을 하나만 남기고 나머지를 버린 것처럼 꾸며 다시 묻기를 실제 모델로 돌린다.
       모델이 2개 미만을 내는 일이 드물어 그냥 돌려서는 이 경로를 볼 수 없다 (11/15 Qwen: 35번 중 0번).
       데모 스크립트 안에서만 바꿔 끼우고 서버 코드는 그대로다."""
    first = [True]

    def apply(raw, other_ids, fact_words=frozenset()):
        kept, dropped = _real_apply(raw, other_ids, fact_words)
        if first[0]:
            first[0] = False
            dropped += [(k.text, FORCED) for k in kept[1:]]
            kept = kept[:1]
        return kept, dropped
    filters.apply = apply


async def main(ids: list[str], force: bool = False):
    data = json.loads((pathlib.Path(__file__).parent / "fake_experiences.json").read_text(encoding="utf-8"))
    exps = [ExperienceInput(**d) for d in data if not ids or d["id"] in ids]
    all_others = [OtherExperience(id=d["id"], activity_name=d["activity_name"], genre=d["genre"], role=d["role"],
                                  did_chips=d["did_chips"]) for d in data]
    for e in exps:
        others = [o for o in all_others if o.id != e.id]   # 같은 사람의 다른 기록이라고 가정
        print(f"\n{'='*70}\n[{e.id}] {e.activity_name} / {e.role}\n  한 줄: {e.one_liner}")
        t0 = time.time()
        if force:
            force_reask()
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
        if force:
            forced = [t for t, why in out.dropped if why == FORCED]
            repeats = [t for t, why in out.dropped if why == filters.REPEATED]
            new = len(out.questions) - 1
            verdict = "새 질문을 받음" if new else ("뺀 질문을 말만 바꿔 다시 내서 버림" if repeats else "새 질문을 못 받음")
            print(f"  ▶ 다시 묻기 확인: 뺀 것 {len(forced)}개, 다시 물음 {out.reasks}회, 새로 받은 것 {new}개 → {verdict}")


if __name__ == "__main__":
    args = sys.argv[1:]
    asyncio.run(main([a for a in args if not a.startswith("--")], force="--force-reask" in args))
