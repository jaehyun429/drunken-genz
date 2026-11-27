"""질문 품질 실험 (이슈 #22). 같은 기록을 프롬프트 판 여러 개로 돌려 나란히 본다. DB 없이 돈다. 서비스 코드는 안 바뀐다.
   cd server && python -m scripts.question_lab                  # 가짜 경험 전부 × 판 전부 → question_lab_<모델>.md
   python -m scripts.question_lab f01 f07                       # 기록 골라서
   python -m scripts.question_lab --only v6,free f07            # 판 골라서
   python -m scripts.question_lab --dry                         # 모델 없이 배선만 확인 (가짜 응답)
   .env 의 LLM_BASE_URL 이 모델을 가리켜야 한다 (GPU 서버의 Qwen, 또는 README 의 Anthropic 호환 주소).

   판 (무엇을 하나씩 풀어 보는가)
   - v6    : 지금 서비스가 쓰는 것. 40자, 정해진 문장 틀
   - think : v6 + 질문을 쓰기 전에 '붙잡은 대목(anchor)'과 '바꿀 구체적인 값(change)'을 먼저 적게 함. 40자, 틀 그대로
   - long  : think + 길이 60자
   - free  : think + 60자 + 정해진 문장 틀 없음 (존댓말, "요?"로 끝나는 것만 지킴)"""
import asyncio, copy, json, os, pathlib, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.questions import filters, generate as gen, prompt
from app.questions.schemas import ExperienceInput, OtherExperience, OUTPUT_SCHEMA
from app.llm.client import LLMConnectionError, LLMModelError

VARIANTS = {            # 이름: (프롬프트 파일, 길이 상한, 먼저 적는 칸이 있는가)
    "v6": ("question_v6", 40, False),
    "think": ("lab_question_think", 40, True),
    "long": ("lab_question_think", 60, True),
    "free": ("lab_question_free", 60, True),
}
LABEL = {"write_more": "더 적어보기", "try_next": "해볼 것", "imagine": "상상해보기"}
THOUGHTS: list[dict] = []     # 모델이 먼저 적은 칸 (화면에는 안 나간다. 보고서에만 싣는다)


def think_schema() -> dict:
    s = copy.deepcopy(OUTPUT_SCHEMA)
    item = s["properties"]["questions"]["items"]
    p = item["properties"]
    item["properties"] = {"type": p["type"], "axis": p["axis"], "anchor": {"type": "string"}, "change": {"type": "string"},
                          "text": p["text"], "source": p["source"], "done_experience_id": p["done_experience_id"]}
    item["required"] = list(item["properties"])
    return s


def use(name: str):
    """이 판의 프롬프트·길이·출력 모양으로 바꿔 끼운다. 이 프로세스 안에서만."""
    file, text_max, think = VARIANTS[name]
    prompt.QUESTION_PROMPT = file
    prompt.QUESTION_TEXT_MAX = filters.QUESTION_TEXT_MAX = text_max
    gen.OUTPUT_SCHEMA = think_schema() if think else OUTPUT_SCHEMA


_real_complete = gen.complete_json


async def recording_complete(system, user, schema):
    """실제 호출을 그대로 하고, 먼저 적은 칸만 따로 모아 둔다."""
    res = await _real_complete(system, user, schema)
    THOUGHTS.extend(q for q in (res.data.get("questions") or []) if isinstance(q, dict) and q.get("change"))
    return res


async def dry_complete(system, user, schema):
    """모델 없이 배선만 본다. 출력 모양에 맞는 가짜 질문 넷."""
    from types import SimpleNamespace
    extra = {"anchor": "가짜 대목", "change": "가짜 값"} if "anchor" in schema["properties"]["questions"]["items"]["properties"] else {}
    qs = [{"type": t, "axis": a, **extra, "text": x, "source": "가짜", "done_experience_id": None} for t, a, x in (
        ("try_next", "scale", "다음엔 두세 명이 하는 모임으로 해보면 어떨까요?"), ("try_next", "did", "다음엔 진행 대본도 직접 써보면 어떨까요?"),
        ("imagine", "external", "만약 마감이 한 달 뒤였다면 어땠을까요?"), ("imagine", "situation", "만약 장소가 강당이었다면 어땠을까요?"))]
    return SimpleNamespace(data={"questions": qs}, attempts=1, model="dry")


async def main(ids: list[str], only: list[str], dry: bool):
    gen.complete_json = dry_complete if dry else recording_complete
    data = json.loads((pathlib.Path(__file__).parent / "fake_experiences.json").read_text(encoding="utf-8"))
    exps = [ExperienceInput(**d) for d in data if not ids or d["id"] in ids]
    others_all = [OtherExperience(id=d["id"], activity_name=d["activity_name"], genre=d["genre"], role=d["role"],
                                  did_chips=d["did_chips"]) for d in data]
    names = [n for n in VARIANTS if not only or n in only]
    model = "dry" if dry else os.environ.get("LLM_MODEL", "model")
    md = [f"# 질문 품질 실험 — 모델 `{model}`", "",
          "같은 기록을 프롬프트 판마다 돌린 결과. 판 설명은 `scripts/question_lab.py` 맨 위.", ""]
    stat = {n: {"q": 0, "plain": 0, "len": 0, "drop": 0, "sec": 0.0, "fail": 0} for n in names}
    for e in exps:
        others = [o for o in others_all if o.id != e.id]
        md += [f"## [{e.id}] {e.activity_name} / {e.role}", "", "```", prompt.record_text(e), "```", ""]
        for n in names:
            use(n); THOUGHTS.clear()
            t0 = time.time()
            try:
                out = await gen.generate(e, others)
            except (LLMConnectionError, LLMModelError) as err:
                stat[n]["fail"] += 1
                md += [f"**{n}** — 실패: {type(err).__name__} {str(err)[:120]}", ""]
                print(f"[{e.id}] {n}: 실패 {type(err).__name__}"); continue
            sec = time.time() - t0
            st = stat[n]; st["sec"] += sec; st["drop"] += len(out.dropped)
            md.append(f"**{n}** ({sec:.1f}초, 다시 묻기 {out.reasks}번)")
            for q in out.questions:
                st["q"] += 1
                if q.type != "write_more":
                    st["plain"] += 1; st["len"] += len(q.text)
                md.append(f"- {LABEL[q.type]} · {q.text} ({len(q.text)}자) ← {q.source}")
            for text, why in out.dropped:
                md.append(f"- ~~{text}~~ (버림: {why})")
            shown = {q.text for q in out.questions}
            for t in THOUGHTS:
                if t.get("text") in shown:
                    md.append(f"  - 먼저 적은 것: 「{t.get('anchor')}」 → {t.get('change')}")
            md.append("")
            print(f"[{e.id}] {n}: {len(out.questions)}개, 버림 {len(out.dropped)}, {sec:.1f}초")
    md += ["## 요약", "", "| 판 | 질문 수 | 해볼 것·상상해보기 평균 길이 | 버린 수 | 평균 시간 | 실패 |", "|---|---|---|---|---|---|"]
    for n in names:
        st = stat[n]; runs = max(1, len(exps) - st["fail"])
        md.append(f"| {n} | {st['q']} | {st['len'] / max(1, st['plain']):.0f}자 | {st['drop']} | {st['sec'] / runs:.1f}초 | {st['fail']} |")
    path = pathlib.Path(f"question_lab_{model.replace('/', '_')}.md")
    path.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\n결과: {path.resolve()}")


if __name__ == "__main__":
    args = sys.argv[1:]
    only = []
    if "--only" in args:
        i = args.index("--only"); only = args[i + 1].split(","); del args[i:i + 2]
    dry = "--dry" in args
    asyncio.run(main([a for a in args if not a.startswith("--")], only, dry))
