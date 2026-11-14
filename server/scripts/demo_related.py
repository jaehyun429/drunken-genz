"""관련 경험을 끝까지 돌려보는 데모 (로컬 전용). 가짜 경험 6개에 질문을 하나씩 붙이고,
   로컬 DB 의 평가용 카드(scripts/embed_eval.py load 로 넣은 481장)에서 관련 경험을 찾는다.
   실제 임베딩 모델 + 실제 LLM 재정렬을 탄다. 실제 Supabase 에는 돌리지 않는다.

   cd server && EMBED_BACKEND=local python -m scripts.demo_related           # 6개 전부 (맥은 모델 로드에 1~2분)
   EMBED_BACKEND=local python -m scripts.demo_related f05 f02               # 골라서
   EMBED_BACKEND=local python -m scripts.demo_related --filter different_role f05
   python -m scripts.demo_related clear                                      # 데모용 경험·질문 삭제"""
import asyncio, json, sys, time, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import db
from app.related import service
from scripts.embed_eval import ME, _assert_local

# 가짜 경험 id → 그 경험에서 나왔다고 칠 질문 (scripts/embed_eval.py DEMO_QUESTIONS 와 같은 문장)
QUESTIONS = {
    "f06": ("try_next", "role", "팀장 말고 팀원으로 들어가서 같은 공모전 기획에 참여해보면 어떨까?"),
    "f05": ("try_next", "method", "취객을 응대할 때 말을 거는 방식이나 거리를 다르게 해본다면 어떤 차이가 날까?"),
    "f02": ("imagine", "situation", "결과가 숫자 대신 손님 반응으로 평가됐다면 어땠을까?"),
    "f03": ("try_next", "method", "기능 코드를 짜기 전에 이 기능이 왜 필요한지 한 문장으로 적어볼까?"),
    "f04": ("try_next", "did", "수학을 봐주는 대신, 한 주의 학습 계획을 아이와 같이 짜는 일을 맡아본다면 어떨까?"),
    "f01": ("try_next", "method", "릴스 말고 줄 서 있는 사람들에게 바로 닿는 홍보 방식을 골라볼까?"),
}


async def _clear(conn):
    await conn.execute("delete from question where user_id = $1", ME)
    await conn.execute("delete from experience where user_id = $1", ME)


async def _seed(conn, d: dict) -> tuple:
    """가짜 경험 하나를 ME 의 나만 보기 기록으로 넣고 질문 하나를 붙인다."""
    eid = await conn.fetchval(
        "insert into experience (user_id, status, activity_name, genre, role, period_start, did_chips, did_notes, "
        " conditions, situation_chips, emotion_chips, one_liner) values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) returning id",
        ME, d.get("status", "done"), d["activity_name"], d["genre"], d["role"], d["period_start"], d["did_chips"],
        d.get("did_notes") or {}, d.get("conditions") or {}, d.get("situation_chips") or [],
        d.get("emotion_chips") or [], d.get("one_liner"))
    # 질문 sweep 이 이 경험으로 LLM 을 부르지 않게 '끝난' batch 를 깔아둔다
    await conn.execute(
        "insert into question_batch (experience_id, user_id, generation, trigger, input_hash, status, finished_at) "
        "values ($1, $2, 1, 'save', 'demo', 'done', now())", eid, ME)
    type_, axis, text = QUESTIONS[d["id"]]
    qid = await conn.fetchval(
        "insert into question (experience_id, user_id, type, axis, text, source, source_activity_name) "
        "values ($1,$2,$3,$4,$5,'데모',$6) returning id", eid, ME, type_, axis, text, d["activity_name"])
    return qid, text


async def main(argv: list[str]):
    _assert_local()
    filter_ = "all"
    if "--filter" in argv:
        i = argv.index("--filter"); filter_ = argv[i + 1]; argv = argv[:i] + argv[i + 2:]
    await db.connect()
    try:
        async with db.pool().acquire() as conn:
            await conn.execute("insert into auth.users (id) values ($1) on conflict do nothing", ME)
            await _clear(conn)
            if argv[:1] == ["clear"]:
                print("데모용 경험·질문 삭제"); return
            data = json.loads((pathlib.Path(__file__).parent / "fake_experiences.json").read_text(encoding="utf-8"))
            for d in data:
                if d["id"] not in QUESTIONS or (argv and d["id"] not in argv):
                    continue
                qid, text = await _seed(conn, d)
                print(f"\n{'=' * 78}\n[{d['id']}] {d['activity_name']} / {d['role']}\nQ: {text}   (filter={filter_})")
                t0 = time.time()
                items = await service.get_related(conn, ME, qid, filter_)
                row = await conn.fetchrow("select items, model from related_cache where question_id = $1 and filter = $2", qid, filter_)
                scores = {it["experience_id"]: it["score"] for it in (row["items"] if row else [])}
                note = f"모델 {row['model']}" if row and row["model"] else ("캐시 안 됨(재정렬 실패 → 유사도 순)" if not row else "후보 없음")
                print(f"  {time.time() - t0:.1f}s · {len(items)}개 · {note}")
                for it in items:
                    c = it["card"]
                    print(f"  · {scores.get(c['experience_id'], 0):.3f}  {c['activity_name']} / {c['role']} — {c['one_liner']}")
                    print(f"        ↳ {it['reason']}")
                t1 = time.time()
                again = await service.get_related(conn, ME, qid, filter_)
                assert [x["card"]["experience_id"] for x in again] == [x["card"]["experience_id"] for x in items]
                print(f"  다시 열기(캐시): {(time.time() - t1) * 1000:.0f}ms")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
