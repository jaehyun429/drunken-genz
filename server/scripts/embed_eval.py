"""임베딩 품질·임계값 확인용 (로컬 전용). 10월 유튜브 추출본 experiences.csv(481장)을 **로컬 Docker DB** 에
   공개 카드로 넣고, 가짜 경험의 질문으로 match_public_cards() 를 돌려 유사도 분포를 본다.
   실제 Supabase 에는 절대 넣지 않는다 (남의 영상 내용. 11/09 결정: 테스트 데이터는 팀 카드 20장).

   cd server && EMBED_BACKEND=local python -m scripts.embed_eval load     # csv → 로컬 DB (한 번, 맥 CPU 로 수 분)
   EMBED_BACKEND=local python -m scripts.embed_eval query                  # 질문 몇 개로 검색해서 유사도 출력
   EMBED_BACKEND=local python -m scripts.embed_eval query "야간에 혼자 일해본 경험"
   python -m scripts.embed_eval clear                                      # 넣은 카드 삭제"""
import asyncio, csv, json, sys, pathlib, uuid, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import asyncpg
from app.config import settings
from app.embedding.client import embed, to_pg
from app.embedding.text import card_text
from app.constants import RELATED_MIN_SIMILARITY

ROOT = pathlib.Path(__file__).resolve().parents[2]
CSV = ROOT / "experiences.csv"
TAG_USER = uuid.UUID("00000000-0000-0000-0000-00000000e0a1")   # 평가용 카드 주인 (지울 때 식별)
ME = uuid.UUID("00000000-0000-0000-0000-00000000e0a2")         # 검색하는 사람 (내 카드는 빠지니 다른 id)

DEMO_QUESTIONS = [
    "팀장 말고 팀원으로 들어가서 같은 공모전 기획에 참여해보면 어떨까?",
    "취객을 응대할 때 말을 거는 방식이나 거리를 다르게 해본다면 어떤 차이가 날까?",
    "결과가 숫자 대신 손님 반응으로 평가됐다면 어땠을까?",
    "기능 코드를 짜기 전에 이 기능이 왜 필요한지 한 문장으로 적어볼까?",
    "수학을 봐주는 대신, 한 주의 학습 계획을 아이와 같이 짜는 일을 맡아본다면 어떨까?",
    "릴스 말고 줄 서 있는 사람들에게 바로 닿는 홍보 방식을 골라볼까?",
]


def _assert_local():
    url = settings().database_url
    assert "localhost" in url or "127.0.0.1" in url, f"로컬 DB 에만 넣는다: {url}"


async def load():
    _assert_local()
    rows = list(csv.DictReader(CSV.open(encoding="utf-8")))
    conn = await asyncpg.connect(settings().database_url)
    await conn.execute("insert into auth.users (id) values ($1) on conflict do nothing", TAG_USER)
    await conn.execute("insert into auth.users (id) values ($1) on conflict do nothing", ME)
    cards = []
    for r in rows:
        card = {"activity_name": r["activity_name"], "genre": r["activity_type"], "role": r["role"],
                "did_chips": [], "did_notes": {"한 일": r["action"][:300]},
                "situation_chips": [r["situation"][:120]] if r["situation"] else [],
                "emotion_chips": [r["feeling"][:80]] if r["feeling"] else [], "one_liner": r["title"][:100]}
        cards.append(card)
    texts = [card_text(c) for c in cards]
    t0 = time.time()
    vecs = []
    for i in range(0, len(texts), 32):
        vecs += await embed(texts[i:i + 32])
        print(f"  임베딩 {min(i + 32, len(texts))}/{len(texts)}  {time.time() - t0:.0f}s", end="\r")
    print()
    async with conn.transaction():
        for c, v in zip(cards, vecs):
            await conn.execute(
                "insert into experience (user_id, activity_name, genre, role, period_start, did_chips, did_notes, "
                " situation_chips, emotion_chips, one_liner, visibility, published_at, published_snapshot, embedding) "
                "values ($1, $2, $3, $4, '2025-01', '{}', $5::jsonb, $6, $7, $8, 'anonymous', now(), $9::jsonb, $10::vector)",
                TAG_USER, c["activity_name"][:40], c["genre"], c["role"][:20], json.dumps(c["did_notes"]),
                c["situation_chips"], c["emotion_chips"], c["one_liner"], json.dumps(c, ensure_ascii=False), to_pg(v))
    await conn.close()
    print(f"로컬 DB 에 평가용 카드 {len(cards)}장 넣음 ({time.time() - t0:.0f}s)")


async def query(questions: list[str]):
    _assert_local()
    conn = await asyncpg.connect(settings().database_url)
    qvecs = await embed(questions)
    for q, v in zip(questions, qvecs):
        rows = await conn.fetch("select * from match_public_cards($1::vector, $2, 10, 0.0)", to_pg(v), ME)
        print(f"\n{'=' * 70}\nQ: {q}")
        for r in rows:
            e = await conn.fetchrow("select activity_name, role, one_liner from experience where id = $1", r["experience_id"])
            mark = "  " if r["similarity"] >= RELATED_MIN_SIMILARITY else "✗ "
            print(f"  {mark}{r['similarity']:.3f}  {e['activity_name']} / {e['role']} — {e['one_liner']}")
    await conn.close()


async def clear():
    _assert_local()
    conn = await asyncpg.connect(settings().database_url)
    n = await conn.execute("delete from experience where user_id = $1", TAG_USER)
    await conn.close()
    print("삭제:", n)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "query"
    if cmd == "load":
        asyncio.run(load())
    elif cmd == "clear":
        asyncio.run(clear())
    else:
        asyncio.run(query(sys.argv[2:] or DEMO_QUESTIONS))
