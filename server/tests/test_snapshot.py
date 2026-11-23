"""공개 스냅샷 모양 (app/publish/snapshot.py). DB 없이."""
import pytest

from app.embedding.text import FIELDS as EMBED_FIELDS
from app.publish.snapshot import SNAPSHOT_FIELDS, make_snapshot

EXP = {"id": "x", "user_id": "u", "activity_name": "축제 운영", "genre": "행사 운영", "role": "팀장",
       "did_chips": ["부스 운영"], "photos": ["photos/a.jpg"], "pinned": True, "place": "학생회관",
       "visibility": "private", "published_at": None}


def test_copies_card_fields_and_leaves_out_private_ones():
    snap, masked = make_snapshot(EXP)
    assert snap["activity_name"] == "축제 운영" and snap["official_activity"] is None and masked == []
    for k in ("id", "user_id", "photos", "pinned", "visibility", "published_at"):
        assert k not in snap
    assert set(EMBED_FIELDS) <= set(SNAPSHOT_FIELDS)        # 재현 임베딩이 읽는 칸은 다 있음


def test_masking_replaces_value_and_is_independent_of_source():
    exp = dict(EXP, did_chips=["부스 운영"])
    snap, masked = make_snapshot(exp, {"name": "축제 연합", "status": "pending"}, {"place": "교내 행사장"})
    exp["did_chips"].append("나중에 추가")
    assert snap["place"] == "교내 행사장" and masked == ["place"]
    assert snap["did_chips"] == ["부스 운영"]
    assert snap["official_activity"] == {"name": "축제 연합", "verified": False}


def test_period_is_kept_unless_hidden():
    exp = dict(EXP, official_period=13)
    assert make_snapshot(exp)[0]["official_period"] == 13
    snap, masked = make_snapshot(exp, replaced={"official_period": None})
    assert snap["official_period"] is None and masked == ["official_period"]


def test_only_maskable_fields_can_be_replaced():
    with pytest.raises(ValueError):
        make_snapshot(EXP, replaced={"genre": "기타"})
