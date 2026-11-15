"""기록을 어디까지 채웠느냐에 따라 질문이 몇 개 나오는지 비교 (DB 없이, 가짜 경험 6개).
   지은님께 "어느 칸이 질문에 얼마나 영향을 주는지" 숫자로 보여주려고 만든 것.

   cd server && python -m scripts.eval_record_depth            # 판마다 3번씩
   python -m scripts.eval_record_depth 1 out.json              # 반복 횟수, 원본 저장 경로"""
import asyncio, json, sys, pathlib, statistics
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.questions.generate import generate
from app.questions.schemas import ExperienceInput, OtherExperience
from app.llm.client import LLMConnectionError, LLMModelError

OPTIONAL = ("did_notes", "custom_chips", "conditions", "situation_chips", "situation_notes", "emotion_chips",
            "one_liner", "ai_tags", "logs")
# 판 이름 → 필수(1~4단계 칩까지)에 더해 남겨둘 칸
VARIANTS = {
    "필수만 (4단계 칩까지)": (),
    "+ 칩 보강글": ("did_notes",),
    "+ 조건 (5단계)": ("conditions",),
    "+ 상황·감정 (6단계)": ("situation_chips", "situation_notes", "emotion_chips"),
    "+ 한 줄 (7단계)": ("one_liner",),
    "전부": OPTIONAL,
}


def strip(d: dict, keep: tuple) -> dict | None:
    out = {k: v for k, v in d.items() if k not in OPTIONAL or k in keep}
    if keep and keep != OPTIONAL and not any(d.get(k) for k in keep):
        return None            # 원본에 그 칸이 비어 있으면 '필수만'과 같은 판이라 건너뜀
    return out


async def one(sem, variant, d, others, rep):
    async with sem:
        e = ExperienceInput(**d)
        try:
            out = await generate(e, others)
            qs = [q.model_dump() for q in out.questions]
            return {"variant": variant, "id": d["id"], "rep": rep, "n": len(qs), "questions": qs,
                    "dropped": [list(x) for x in out.dropped], "reasks": out.reasks, "error": None}
        except LLMModelError as err:      # 걸러낸 뒤 0개도 여기로 온다
            return {"variant": variant, "id": d["id"], "rep": rep, "n": 0, "questions": [], "dropped": [], "error": f"model: {err}"}
        except LLMConnectionError as err:
            return {"variant": variant, "id": d["id"], "rep": rep, "n": None, "questions": [], "dropped": [], "error": f"connection: {err}"}


async def main(reps: int, out_path: str | None):
    data = json.loads((pathlib.Path(__file__).parent / "fake_experiences.json").read_text(encoding="utf-8"))
    all_others = [OtherExperience(id=d["id"], activity_name=d["activity_name"], genre=d["genre"], role=d["role"],
                                  did_chips=d["did_chips"]) for d in data]
    sem = asyncio.Semaphore(6)
    jobs = []
    for variant, keep in VARIANTS.items():
        for d in data:
            s = strip(d, keep)
            if s is None:
                continue
            others = [o for o in all_others if o.id != d["id"]]
            jobs += [one(sem, variant, s, others, r) for r in range(reps)]
    results = await asyncio.gather(*jobs)
    if out_path:
        pathlib.Path(out_path).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    ids = [d["id"] for d in data]
    print(f"\n판마다 {reps}번씩. 칸 = 질문 개수 평균\n")
    print(f"{'판':<24}" + "".join(f"{i:>6}" for i in ids) + f"{'평균':>7}{'최소':>5}{'2개미만':>8}{'해볼것':>7}{'상상':>6}{'버림':>6}{'다시물음':>8}{'연결실패':>8}")
    for variant in VARIANTS:
        rs = [r for r in results if r["variant"] == variant]
        ok = [r for r in rs if r["n"] is not None]
        cells = ""
        for i in ids:
            ns = [r["n"] for r in ok if r["id"] == i]
            cells += f"{statistics.mean(ns):>6.1f}" if ns else f"{'-':>6}"
        ns = [r["n"] for r in ok]
        tn = statistics.mean(sum(q["type"] == "try_next" for q in r["questions"]) for r in ok)
        im = statistics.mean(sum(q["type"] == "imagine" for q in r["questions"]) for r in ok)
        dr = statistics.mean(len(r["dropped"]) for r in ok)
        few = sum(n < 2 for n in ns)
        ra = sum(1 for r in ok if r.get("reasks"))
        print(f"{variant:<24}{cells}{statistics.mean(ns):>7.2f}{min(ns):>5}{f'{few}/{len(ns)}':>8}{tn:>7.2f}{im:>6.2f}{dr:>6.2f}{ra:>8}{len(rs) - len(ok):>8}")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 3, sys.argv[2] if len(sys.argv) > 2 else None))
