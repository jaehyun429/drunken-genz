"""DB 없이 질문 생성만 돌려보는 데모 (11/17 회의용).
   cd server && python -m scripts.demo_questions                 # 가짜 경험 6개 전부
   python -m scripts.demo_questions f02 f04                      # 골라서
   python -m scripts.demo_questions --force-reask f02            # 다시 묻기를 실제 모델로 확인 (아래 force_reask)
   python -m scripts.demo_questions --regen f02                  # 다시 만들기: 방금 낸 질문을 '이전에 보여준 질문'으로 주고 한 번 더
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
    """첫 응답에서 해볼 것·상상해보기를 하나만 남기고 나머지를 버린 것처럼 꾸며 다시 묻기를 실제 모델로 돌린다.
       더 적어보기는 그대로 둔다. 데모 스크립트 안에서만 바꿔 끼우고 서버 코드는 그대로다."""
    first = [True]

    def apply(raw, other_ids, fact_words=frozenset(), **kw):
        kept, dropped = _real_apply(raw, other_ids, fact_words, **kw)
        if first[0]:
            first[0] = False
            plain = [k for k in kept if k.type != "write_more"]
            dropped += [(k.text, FORCED) for k in plain[1:]]
            kept = [k for k in kept if k.type == "write_more"] + plain[:1]
        return kept, dropped
    filters.apply = apply


LABEL = {"write_more": "더 적어보기", "try_next": "해볼 것   ", "imagine": "상상해보기 "}


def show(out):
    for q in out.questions:
        done = f"  [이미 해본 것: {q.done_experience_id}]" if q.done_experience_id else ""
        print(f"  · {LABEL[q.type]}({q.axis}) {q.text}  [{len(q.text)}자]{done}\n      ← {q.source}")
    for text, why in out.dropped:
        print(f"  ✗ 버림: {text}  ({why})")


async def main(ids: list[str], force: bool = False, regen: bool = False):
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
        show(out)
        if regen:
            shown = [q.text for q in out.questions if q.type != "write_more"]
            try:
                again_out = await generate(e, others, previous=shown)
            except (LLMConnectionError, LLMModelError) as err:
                print(f"  ▶ 다시 만들기 실패 — {type(err).__name__}: {err}"); continue
            note = " · 새로 만들 질문이 없어요 (no_new)" if again_out.no_new else ""
            print(f"  ▶ 다시 만들기: {len(again_out.questions)}개 · 다시 물음 {again_out.reasks}회{note}")
            show(again_out)
        if force:
            forced = [t for t, why in out.dropped if why == FORCED]
            repeats = [t for t, why in out.dropped if why == filters.REPEATED]
            new = sum(q.type != "write_more" for q in out.questions) - 1
            verdict = "새 질문을 받음" if new else ("뺀 질문을 말만 바꿔 다시 내서 버림" if repeats else "새 질문을 못 받음")
            print(f"  ▶ 다시 묻기 확인: 뺀 것 {len(forced)}개, 다시 물음 {out.reasks}회, 새로 받은 것 {new}개 → {verdict}")


if __name__ == "__main__":
    args = sys.argv[1:]
    asyncio.run(main([a for a in args if not a.startswith("--")], force="--force-reask" in args, regen="--regen" in args))
