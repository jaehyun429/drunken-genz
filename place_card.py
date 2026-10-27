"""
새 경험 카드(사용자 입력)를 밑지도 위에 배치 — 앱 DB 쪽 로직의 원형.

입력 CSV 열: user, activity_name, activity_type, role, situation, action, feeling
  1) Claude(Haiku) 로 태그·한 줄 요약 (tag_cards.tag_one 재사용, 어휘는 map/tag_vocab.json 교집합)
  2) embedding_text = 활동명 + 유형 + 역할 + ② + ③ (extract_experience.to_row 와 같은 규칙) → Gemini 임베딩
  3) map/regions.json + map/base_vectors.npz 로 place() → (x, y, 섬)
  4) 같은 user 카드끼리 태그 공유 상위 3개 연결
출력: map/user_cards.json. --render 를 주면 map/index.html 의 가상 사용자를 이 카드들로 바꿔 다시 만듦.

실행:  python place_card.py sample_cards.csv [--render] [--placement knn|centroid]
"""
import argparse
import csv
import json
import random
import sys
from pathlib import Path

import numpy as np

from build_map import (EMBED_DIM, EMBED_FIELDS, EMBED_MODEL, MAP_DIR, TEMPLATE, embed_texts, gemini_client, l2norm,
                       place, place_by_centroid)
from extract_experience import build_client
from tag_cards import tag_one

USER_COLORS = 3


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--placement", choices=["knn", "centroid"], default="knn")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    regions = json.loads((MAP_DIR / "regions.json").read_text(encoding="utf-8"))
    vocab = regions["vocab"]
    rows = list(csv.DictReader(open(args.csv, encoding="utf-8")))
    if not rows:
        print("카드가 없습니다.", file=sys.stderr)
        return 1
    for i, r in enumerate(rows):
        r.setdefault("user", "user1")
        r["id"] = f"new{i + 1}"

    # 1. 태그·요약
    client = build_client()
    print(f"태그 추출 {len(rows)}장…")
    for r in rows:
        _, info = tag_one(client, r)
        r["summary"] = info["summary"]
        r["tags_all"] = info["tags"]
        r["tags"] = [t for t in info["tags"] if t in vocab]
        print(f"  {r['activity_name'][:24]} → {', '.join(r['tags'])}" + (f"  (어휘 밖: {', '.join(t for t in info['tags'] if t not in vocab)})" if len(r["tags"]) < len(info["tags"]) else ""))

    # 2. 임베딩
    mode = regions["meta"].get("embed_fields", "all")
    for r in rows:
        r["embedding_text"] = " ".join(s for s in [r["activity_name"], r["activity_type"], r["role"], r["action"], r["feeling"]] if s)
    texts = [EMBED_FIELDS[mode](r) for r in rows]
    print(f"임베딩 입력 규칙: {mode}")
    vecs = l2norm(embed_texts(gemini_client(), texts))

    # 3. 배치
    if args.placement == "knn":
        z = np.load(MAP_DIR / "base_vectors.npz")
        base_vecs, base_xy, base_region = l2norm(z["vecs"]), z["xy"].astype(float), z["region"]
    centroids = np.stack([np.array(r["centroid"]) for r in regions["regions"]])
    centers = np.array([[r["cx"], r["cy"]] for r in regions["regions"]])
    radii = np.array([r["radius"] for r in regions["regions"]])
    for r, v in zip(rows, vecs):
        if args.placement == "knn":
            x, y, reg, s = place(v, base_vecs, base_xy, base_region, rng=rng)
        else:
            x, y, reg, s = place_by_centroid(v, centroids, centers, radii, rng)
        r["x"], r["y"], r["region"], r["placed_sim"] = round(x, 1), round(y, 1), int(reg), round(s, 3)
        name = regions["regions"][reg]["name_poetic"]
        print(f"  {r['activity_name'][:24]} → 섬 {reg} {name} ({x:.0f}, {y:.0f}) 유사도 {s:.2f}")

    # 4. 같은 사용자 카드끼리 연결 (공유 태그 수 → 코사인 유사도)
    users = {}
    for r, v in zip(rows, vecs):
        users.setdefault(r["user"], {"id": r["user"], "name": r["user"], "cards": [], "links": [], "_vecs": {}})
        users[r["user"]]["cards"].append(r)
        users[r["user"]]["_vecs"][r["id"]] = v
    for u in users.values():
        cs = u["cards"]
        seen = set()
        for a in cs:
            cand = []
            for b in cs:
                if a is b:
                    continue
                sh = sorted(set(a["tags"]) & set(b["tags"]))
                if sh:
                    cand.append((len(sh), float(u["_vecs"][a["id"]] @ u["_vecs"][b["id"]]), b, sh))
            cand.sort(key=lambda t: (-t[0], -t[1]))
            for w, s, b, sh in cand[:3]:
                key = tuple(sorted((a["id"], b["id"])))
                if key not in seen:
                    seen.add(key)
                    u["links"].append({"s": key[0], "t": key[1], "w": w, "shared": sh})
        del u["_vecs"]
        u["cards"] = [{k: c[k] for k in ("id", "activity_name", "activity_type", "role", "situation", "action", "feeling",
                                          "summary", "tags", "x", "y", "region", "placed_sim")} | {"title": ""} for c in cs]
    out = {"users": list(users.values()), "meta": {"embedding_model": EMBED_MODEL, "embedding_dim": EMBED_DIM, "placement": args.placement}}
    (MAP_DIR / "user_cards.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장 → {MAP_DIR / 'user_cards.json'}")

    if args.render:
        m = json.loads((MAP_DIR / "map.json").read_text(encoding="utf-8"))
        m["users"] = out["users"]
        html = TEMPLATE.read_text(encoding="utf-8")
        payload = json.dumps(m, ensure_ascii=False).replace("</", "<\\/")
        (MAP_DIR / "index_users.html").write_text(html.replace("/*__MAP_DATA__*/null", payload), encoding="utf-8")
        print(f"뷰어 → {MAP_DIR / 'index_users.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
