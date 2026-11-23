"""탐색·카드·반응. 로컬 Postgres 가 있을 때만.
   로컬 DB 에 다른 카드가 남아 있어도 안 섞이게, 목록 테스트는 테스트마다 고유한 역할(tok)과 role_filter=same 으로 본다."""
import json, uuid
import pytest

from test_integration_embedding import _reachable
from test_integration_related import env, run, _sql, card   # noqa: F401  (env 는 fixture)

pytestmark = pytest.mark.skipif(not _reachable(), reason="로컬 DB 없음")


def tok():
    return uuid.uuid4().hex[:8]


def me_with_role(role):
    """보는 사람. 가장 최근 경험의 역할이 role."""
    uid = uuid.uuid4()
    async def go():
        await _sql("insert into auth.users (id) values ($1) on conflict do nothing", uid)
        await _sql("insert into experience (user_id, activity_name, genre, role, period_start, did_chips) "
                   "values ($1,'내 활동','서비스·응대',$2,'2026-01',$3)", uid, role, ["칩"])
    run(go())
    return uid


def h(uid):
    return {"Authorization": f"Bearer dev:{uid}"}


def get_cards(client, uid, **params):
    r = client.get("/v1/cards", headers=h(uid), params=params)
    assert r.status_code == 200, r.text
    return r.json()


def block(a, b):
    run(_sql("insert into block (user_id, blocked_id) values ($1, $2)", a, b))


def test_list_is_newest_first_and_pages_by_cursor(env):
    client, _ = env
    t = tok(); role = f"역할{t}"
    me = me_with_role(role)
    made = [card(f"{t}{i:02d}", role=role)[0] for i in range(22)]   # 만든 순서 = 공개 순서

    first = get_cards(client, me, role_filter="same")
    assert [c["experience_id"] for c in first["items"]] == [str(e) for e in reversed(made)][:20]
    assert first["next_cursor"]
    second = get_cards(client, me, role_filter="same", cursor=first["next_cursor"])
    assert [c["experience_id"] for c in second["items"]] == [str(made[1]), str(made[0])]
    assert second["next_cursor"] is None

    c = first["items"][0]   # PublicCard 모양 (명세 0번)
    assert c["activity_name"] == f"활동{t}21" and c["role"] == role and c["is_mine"] is False
    assert c["accept_questions"] is True and c["qa_count"] == 0 and c["official_activity"] is None


def test_list_hides_mine_private_and_blocked_both_ways(env):
    client, _ = env
    t = tok(); role = f"역할{t}"
    me = me_with_role(role)
    seen, _u = card(f"{t}a", role=role)
    card(f"{t}b", role=role, uid=me)                      # 내 카드
    card(f"{t}c", role=role, visibility="private")        # 나만 보기
    _, blocked_by_me = card(f"{t}d", role=role)
    _, blocked_me = card(f"{t}e", role=role)
    block(me, blocked_by_me); block(blocked_me, me)

    assert [c["experience_id"] for c in get_cards(client, me, role_filter="same")["items"]] == [str(seen)]


def test_list_filters_by_genre_and_role(env):
    client, _ = env
    t = tok(); role = f"역할{t}"
    me = me_with_role(f" {role.upper()} ")                 # 띄어쓰기·대소문자는 무시
    a, _u = card(f"{t}a", role=role, genre="개발·IT")
    b, _u = card(f"{t}b", role=role, genre="서비스·응대")
    other, _u = card(f"{t}c", role=f"다른{t}")

    same = [c["experience_id"] for c in get_cards(client, me, role_filter="same")["items"]]
    assert same == [str(b), str(a)]
    assert [c["experience_id"] for c in get_cards(client, me, role_filter="same", genre="개발·IT")["items"]] == [str(a)]

    # different: 같은 역할 카드는 없고 다른 역할 카드는 있다 (다른 카드가 섞여 있을 수 있어 끝까지 넘겨 본다)
    got, cursor = [], None
    while True:
        page = get_cards(client, me, role_filter="different", **({"cursor": cursor} if cursor else {}))
        got += [c["experience_id"] for c in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert str(other) in got and str(a) not in got and str(b) not in got


def test_role_filter_without_my_experience_and_bad_params(env):
    client, _ = env
    nobody = uuid.uuid4()
    assert get_cards(client, nobody, role_filter="same") == {"items": [], "next_cursor": None}
    for params, field in (({"role_filter": "similar"}, "role_filter"), ({"cursor": "엉망"}, "cursor"),
                          ({"genre": "가" * 21}, "genre")):
        r = client.get("/v1/cards", headers=h(nobody), params=params)
        assert r.status_code == 400 and r.json()["error"]["details"]["field"] == field, r.text


def test_detail_shows_snapshot_qa_and_my_reaction(env):
    client, _ = env
    t = tok()
    me, asker, blocked_asker = me_with_role("보는 사람"), uuid.uuid4(), uuid.uuid4()
    eid, owner = card(t)
    snap = {"activity_name": f"가린 활동{t}", "genre": "서비스·응대", "role": "운영 담당", "did_chips": ["응대"],
            "one_liner": "한 줄", "situation_chips": ["바빴다"], "situation_notes": "주말 저녁", "emotion_chips": ["뿌듯"],
            "conditions": {"규모": "혼자"}, "did_notes": {"응대": "단골 응대"}, "period_start": "2026-01", "place": None,
            "official_activity": {"name": "공식 활동", "verified": True}, "official_period": 3}
    async def seed():
        for u in (asker, blocked_asker):
            await _sql("insert into auth.users (id) values ($1) on conflict do nothing", u)
        await _sql("update experience set published_snapshot = $2::jsonb, accept_questions = false where id = $1",
                   eid, json.dumps(snap, ensure_ascii=False))
        await _sql("insert into qa (experience_id, asker_id, type, question, answer, answered_at) "
                   "values ($1,$2,'조건','혼자 하기 힘들지 않았나요?','익숙해지니 괜찮았어요', now())", eid, asker)
        await _sql("insert into qa (experience_id, asker_id, type, question) values ($1,$2,'조건','내가 남긴 질문이에요 맞나요?')", eid, me)
        await _sql("insert into qa (experience_id, asker_id, type, question) values ($1,$2,'조건','차단한 사람의 질문이에요')", eid, blocked_asker)
    run(seed()); block(me, blocked_asker)

    r = client.get(f"/v1/cards/{eid}", headers=h(me))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["card"]["activity_name"] == f"가린 활동{t}" and d["card"]["accept_questions"] is False
    assert d["card"]["official_activity"] == {"name": "공식 활동", "verified": True} and d["card"]["official_period"] == 3
    assert d["card"]["qa_count"] == 3 and d["card"]["is_mine"] is False
    assert d["detail"]["situation_chips"] == ["바빴다"] and d["detail"]["conditions"] == {"규모": "혼자"}
    assert d["detail"]["did_notes"] == {"응대": "단골 응대"} and d["detail"]["flow_summary"] is None
    assert [(q["status"], q["is_mine"]) for q in d["qa"]] == [("answered", False), ("waiting", True)]
    assert all("asker_id" not in q for q in d["qa"])       # 질문자는 익명
    assert d["my_reaction"] is None

    # 카드 주인이 보면 내 카드
    assert client.get(f"/v1/cards/{eid}", headers=h(owner)).json()["card"]["is_mine"] is True


def test_detail_is_404_for_missing_private_and_blocked(env):
    client, _ = env
    t = tok(); me = me_with_role("보는 사람")
    private, _u = card(f"{t}a", visibility="private")
    eid, owner = card(f"{t}b")
    block(owner, me)                                        # 상대가 나를 차단
    for target in (uuid.uuid4(), private, eid):
        r = client.get(f"/v1/cards/{target}", headers=h(me))
        assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND", r.text
        r = client.post("/v1/reactions", headers=h(me), json={"experience_id": str(target), "chip": "나도 그랬어"})
        assert r.status_code == 404, r.text


def test_reaction_is_replaced_and_validated(env):
    client, _ = env
    t = tok(); me = me_with_role("보는 사람")
    eid, owner = card(t)

    def post(uid, **body):
        return client.post("/v1/reactions", headers=h(uid), json={"experience_id": str(eid)} | body)

    assert post(me, chip="나도 그랬어").json() == {"experience_id": str(eid), "chip": "나도 그랬어", "note": None}
    r = post(me, chip="다른 상황이었어", note="  저는 팀으로 했어요 ")
    assert r.status_code == 200 and r.json()["note"] == "저는 팀으로 했어요"
    rows = run(_sql("select chip, note from reaction where experience_id = $1", eid, fetch="all"))
    assert [(x["chip"], x["note"]) for x in rows] == [("다른 상황이었어", "저는 팀으로 했어요")]   # 한 줄만, 교체됨
    d = client.get(f"/v1/cards/{eid}", headers=h(me)).json()
    assert d["my_reaction"] == "다른 상황이었어" and d["my_reaction_note"] == "저는 팀으로 했어요"
    assert client.get(f"/v1/cards/{eid}", headers=h(owner)).json()["my_reaction"] is None   # 작성자에게는 안 보임

    for body, field in (({"chip": "별로였어"}, "chip"), ({"chip": "나도 그랬어", "note": "가" * 81}, "note")):
        r = post(me, **body)
        assert r.status_code == 400 and r.json()["error"]["details"]["field"] == field, r.text
    r = post(owner, chip="나도 그랬어")                      # 내 카드
    assert r.status_code == 400 and r.json()["error"]["details"]["field"] == "experience_id"


def test_app_roles_cannot_touch_reaction_table(env):
    for role in ("anon", "authenticated"):
        for priv in ("select", "insert", "update", "delete"):
            assert run(_sql("select has_table_privilege($1, 'reaction', $2)", role, priv, fetch="val")) is False
