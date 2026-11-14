"""
로컬 LLM 테스트 실행기 — 회의 전 모델 비교용.

  python run_eval.py --base http://<GPU서버>:8000/v1 --model local --tag qwen27
  python run_eval.py --base http://localhost:11434/v1 --model qwen3:8b --tag ollama-qwen8   # Ollama도 동일

cases.jsonl 의 20개 케이스를 돌려서 results/<tag>.md 와 results/<tag>.jsonl 로 저장.
세 과제: question(질문 생성 2+2) / summarize(정리 확인 칩 추출) / related(관련 경험 재정렬+이유)
명세 기준: temperature 0, JSON 출력, 비현실적 가정·할 수 없는 "해볼 것" 금지, 출처(source) 필수.
"""
import argparse, json, time, pathlib, sys
from openai import OpenAI

HERE = pathlib.Path(__file__).parent

DID_CHIPS = ["기획", "운영", "홍보", "콘텐츠 제작", "디자인", "개발", "데이터 정리", "고객 응대",
             "교육·멘토링", "협업 조율", "예산 관리", "현장 진행", "조사·리서치", "글쓰기", "판매"]
SIT_CHIPS = ["시간이 부족했다", "사람이 부족했다", "방향이 자주 바뀌었다", "의견이 갈렸다", "예산이 없었다",
             "처음 해보는 일이었다", "혼자 결정해야 했다", "결과가 숫자로 평가됐다"]
EMO_CHIPS = ["뿌듯함", "막막함", "지침", "재미", "불안", "답답함", "성취감", "아쉬움"]

SYSTEM = (
    "너는 20대 초중반 대학생의 경험 기록을 돕는 보조자야. 한국어로, 담백하게, 평가하지 않고, "
    "점수나 등급을 매기지 않아. 반드시 지정된 JSON 형식으로만 답해. 설명 문장을 JSON 밖에 쓰지 마."
)

PROMPTS = {
    "question": """아래 경험 기록 하나를 보고 질문 4개를 만들어.
- try_next(해볼 것) 2개: 이 사람이 다음에 실제로 해볼 수 있는 구체적 행동을 묻는 질문. 돈·시간·권한상 불가능한 건 금지.
- imagine(상상해보기) 2개: "그때 ~했다면 어땠을까?" 형태. 비현실적 가정("AI 없이 했다면", "시간이 두 배였다면") 금지. 기록에 있는 실제 선택 지점에서만.
- 각 질문마다 source: 기록의 어느 칩·어느 문장에서 나왔는지 한 줄.
- 질문은 한 문장, 60자 이내, 존댓말 아닌 반말체("~해볼까?", "~였다면 어땠을까?").

경험 기록:
{record}

JSON: {{"questions":[{{"type":"try_next"|"imagine","text":"...","source":"..."}} x4]}}""",

    "summarize": """사용자가 쓴 글에서 해당하는 칩만 골라. 글에 근거가 없는 칩은 절대 넣지 마. 없으면 빈 배열.
- did 후보: {did}
- situation 후보: {sit}
- emotion 후보: {emo}
이미 고른 칩: {already}
추가로, 글을 더 구체적으로 만들어줄 짧은 후속 질문 2개(follow_up).

글:
{text}

JSON: {{"suggested_chips":{{"did":[...],"situation":[...],"emotion":[...]}},"follow_up":["...","..."]}}""",

    "related": """사용자의 질문과 다른 사람의 공개 카드 후보들이 있어. 질문에 실제로 답이 될 만한 순서로 정렬하고,
관련 없는 카드는 빼. 각 카드마다 reason: 왜 이 질문에 도움이 되는지 한 줄(40자 이내). 카드 작성자를 평가하지 마.

질문: {question}

후보 카드:
{cards}

JSON: {{"items":[{{"experience_id":"...","reason":"..."}}, ...]}}  (관련 있는 것만, 관련순)""",
}

SCHEMAS = {
    "question": {"type": "object", "properties": {"questions": {"type": "array", "minItems": 4, "maxItems": 4,
                 "items": {"type": "object", "properties": {"type": {"enum": ["try_next", "imagine"]},
                 "text": {"type": "string"}, "source": {"type": "string"}}, "required": ["type", "text", "source"]}}},
                 "required": ["questions"]},
    "summarize": {"type": "object", "properties": {"suggested_chips": {"type": "object", "properties": {
                  "did": {"type": "array", "items": {"enum": DID_CHIPS}}, "situation": {"type": "array", "items": {"enum": SIT_CHIPS}},
                  "emotion": {"type": "array", "items": {"enum": EMO_CHIPS}}}, "required": ["did", "situation", "emotion"]},
                  "follow_up": {"type": "array", "items": {"type": "string"}}}, "required": ["suggested_chips", "follow_up"]},
    "related": {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
                "experience_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["experience_id", "reason"]}}},
                "required": ["items"]},
}


def fmt_record(r):
    lines = [f"활동: {r['activity_name']} ({r['genre']}, {r['status']})", f"역할: {r['role']}", f"기간: {r['period']}",
             f"주로 한 일: {', '.join(r['did_chips'])}"]
    for k, v in r.get("did_notes", {}).items():
        lines.append(f"  - {k}: {v}")
    if r.get("conditions"):
        lines.append("조건: " + ", ".join(f"{k}={v}" for k, v in r["conditions"].items()))
    if r.get("situation_chips"):
        lines.append("상황: " + ", ".join(r["situation_chips"]))
    if r.get("emotion_chips"):
        lines.append("감정: " + ", ".join(r["emotion_chips"]))
    if r.get("one_liner"):
        lines.append(f"한 줄: {r['one_liner']}")
    return "\n".join(lines)


def build_prompt(case):
    t = case["task"]
    if t == "question":
        return PROMPTS[t].format(record=fmt_record(case["record"]))
    if t == "summarize":
        return PROMPTS[t].format(did=", ".join(DID_CHIPS), sit=", ".join(SIT_CHIPS), emo=", ".join(EMO_CHIPS),
                                 already=", ".join(case.get("already_selected", [])) or "없음", text=case["text"])
    if t == "related":
        cards = "\n".join(f"- [{c['experience_id']}] {c['activity_name']} / {c['role']} / {', '.join(c['did_chips'])} / {c['one_liner']}"
                          for c in case["cards"])
        return PROMPTS[t].format(question=case["question"], cards=cards)
    raise ValueError(t)


def call(client, model, prompt, schema, no_think, timeout):
    extra = {}
    if no_think:  # Qwen3·EXAONE 류 reasoning 끄기 (vLLM chat_template_kwargs). Ollama는 무시됨
        extra["chat_template_kwargs"] = {"enable_thinking": False}
    kwargs = dict(model=model, temperature=0, seed=0, max_tokens=1024, timeout=timeout,
                  messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                  extra_body=extra)
    try:
        resp = client.chat.completions.create(
            response_format={"type": "json_schema", "json_schema": {"name": "out", "schema": schema}}, **kwargs)
    except Exception as e:  # 서버가 json_schema 미지원이면 json_object로 재시도
        print(f"   (json_schema 실패: {type(e).__name__}, json_object로 재시도)", file=sys.stderr)
        resp = client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
    return resp


def check(case, out):
    """명세 규칙 자동 검사. 사람이 볼 건 .md에서."""
    issues = []
    t = case["task"]
    if t == "question":
        qs = out.get("questions", [])
        if len(qs) != 4: issues.append(f"질문 {len(qs)}개")
        if sum(q.get("type") == "try_next" for q in qs) != 2: issues.append("try_next≠2")
        if sum(q.get("type") == "imagine" for q in qs) != 2: issues.append("imagine≠2")
        for q in qs:
            if not q.get("source"): issues.append("source 없음")
            if len(q.get("text", "")) > 60: issues.append("60자 초과")
            if any(w in q.get("text", "") for w in ["AI 없이", "두 배", "무한"]): issues.append("비현실 가정 의심")
    elif t == "summarize":
        sc = out.get("suggested_chips", {})
        for k, pool in (("did", DID_CHIPS), ("situation", SIT_CHIPS), ("emotion", EMO_CHIPS)):
            bad = [c for c in sc.get(k, []) if c not in pool]
            if bad: issues.append(f"{k} 후보 밖 칩: {bad}")
        if len(out.get("follow_up", [])) != 2: issues.append("follow_up≠2")
    elif t == "related":
        ids = {c["experience_id"] for c in case["cards"]}
        for it in out.get("items", []):
            if it.get("experience_id") not in ids: issues.append(f"없는 id {it.get('experience_id')}")
            if len(it.get("reason", "")) > 40: issues.append("reason 40자 초과")
        if case.get("expect_top") and out.get("items") and out["items"][0]["experience_id"] != case["expect_top"]:
            issues.append(f"1위 기대 {case['expect_top']} ≠ {out['items'][0]['experience_id']}")
        if case.get("expect_excluded"):
            got = [it["experience_id"] for it in out.get("items", [])]
            leaked = [x for x in case["expect_excluded"] if x in got]
            if leaked: issues.append(f"걸러야 할 카드 포함 {leaked}")
    return issues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000/v1")
    ap.add_argument("--model", default="local")
    ap.add_argument("--tag", required=True, help="결과 파일 이름 (모델 구분용)")
    ap.add_argument("--cases", default=str(HERE / "cases.jsonl"))
    ap.add_argument("--only", help="question|summarize|related 중 하나만")
    ap.add_argument("--think", action="store_true", help="reasoning 켜기 (기본은 끔)")
    ap.add_argument("--timeout", type=float, default=60)
    a = ap.parse_args()

    client = OpenAI(base_url=a.base, api_key="x")
    cases = [json.loads(l) for l in open(a.cases, encoding="utf-8") if l.strip()]
    if a.only: cases = [c for c in cases if c["task"] == a.only]
    outdir = HERE / "results"; outdir.mkdir(exist_ok=True)
    jl = open(outdir / f"{a.tag}.jsonl", "w", encoding="utf-8")
    md = [f"# {a.tag} — {a.model} @ {a.base}", "", f"케이스 {len(cases)}개, temperature 0, think={'on' if a.think else 'off'}", ""]
    total_t, n_ok, n_issue = 0.0, 0, 0

    for c in cases:
        prompt = build_prompt(c)
        t0 = time.time()
        try:
            resp = call(client, a.model, prompt, SCHEMAS[c["task"]], not a.think, a.timeout)
            dt = time.time() - t0
            raw = resp.choices[0].message.content or ""
            usage = resp.usage
            try:
                out = json.loads(raw); parse_ok = True
            except json.JSONDecodeError:
                out = {}; parse_ok = False
            issues = ([] if parse_ok else ["JSON 파싱 실패"]) + check(c, out)
        except Exception as e:
            dt = time.time() - t0; raw = f"ERROR {type(e).__name__}: {e}"; out = {}; usage = None
            issues = ["호출 실패"]
        total_t += dt; n_ok += not issues; n_issue += bool(issues)
        rec = {"id": c["id"], "task": c["task"], "sec": round(dt, 2), "issues": issues, "output": out, "raw": raw,
               "tokens": {"in": usage.prompt_tokens, "out": usage.completion_tokens} if usage else None}
        jl.write(json.dumps(rec, ensure_ascii=False) + "\n"); jl.flush()
        flag = "✅" if not issues else "⚠️"
        print(f"{flag} {c['id']:<14} {c['task']:<9} {dt:5.1f}s  {', '.join(issues)}")
        md += [f"## {flag} {c['id']} ({c['task']}, {dt:.1f}s)", "", f"**검사**: {', '.join(issues) or '통과'}", "",
               "<details><summary>입력</summary>", "", "```", prompt, "```", "</details>", "",
               "```json", json.dumps(out, ensure_ascii=False, indent=2) if out else raw, "```", ""]

    summary = f"**통과 {n_ok} / 경고 {n_issue} / 평균 {total_t/len(cases):.1f}s**"
    md.insert(3, summary); md.insert(4, "")
    (outdir / f"{a.tag}.md").write_text("\n".join(md), encoding="utf-8")
    print(f"\n{summary}\n→ {outdir / (a.tag + '.md')}")


if __name__ == "__main__":
    main()
