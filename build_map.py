"""
경험 지도 1차 생성 (대륙 방식).

experiences.csv(카드) + map/tags_raw.json(태그·요약) →
  1) 가상 사용자 3명×4장을 떼어 두고, 나머지 카드를 Gemini 임베딩 → t-SNE 2D → KMeans 로 섬(영역) 나눔
  2) 섬마다 Claude 로 이름(감성형·설명형)과 설명 생성
  3) 카드 간 태그 공유(등장 3장 이상 태그만) 연결 계산
  4) 가상 사용자 카드를 place() 규칙으로 밑지도 위에 배치, 같은 사용자 카드끼리 태그 연결
출력:
  map/map.json          뷰어용 전체 데이터(유튜브 카드 텍스트 포함, 테스트용)
  map/regions.json      유튜브 삭제 후에도 남길 것: 섬 정보 + 익명 좌표 + 배치 파라미터 + 태그 어휘
  map/base_vectors.npz  밑지도 임베딩(익명). kNN 배치에 필요. 보관 여부는 팀 결정(centroid 배치로 대체 가능)
  map/tag_vocab.json    연결에 쓰는 태그와 등장 수
  map/index.html        map_template.html 에 map.json 을 인라인 주입한 뷰어

실행:  python build_map.py [--k 10] [--k-range 8 12] [--users 3 --cards-per-user 4] [--seed 0] [--skip-naming]
환경변수: GEMINI_API_KEY(임베딩), ANTHROPIC_API_KEY(섬 이름), (선택) MAP_NAMING_MODEL 기본 claude-sonnet-5-5
"""
import argparse
import collections
import csv
import hashlib
import json
import os
import random
import sys
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

from extract_experience import OUT_CSV, build_client  # noqa: E402

MAP_DIR = HERE / "map"
TAGS_RAW = MAP_DIR / "tags_raw.json"
EMBED_FIELDS = {
    "all": lambda c: c["embedding_text"],                       # 7차 정의: 활동명+유형+역할+②+③ → 활동 종류별로 묶임
    "review": lambda c: f"{c['action']} {c['feeling']}",        # ②+③: 무엇을 했고 어땠나 → 경험의 성격별로 묶임
    "feeling": lambda c: c["feeling"],                           # ③만: 느낀 것 위주
}
TEMPLATE = HERE / "map_template.html"
NAMES_CACHE = MAP_DIR / "region_names.json"   # 멤버 구성이 같은 섬은 이름을 다시 짓지 않음 (이름이 매번 바뀌는 것 방지)

EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 768
MIN_TAG_COUNT = 3          # 이 횟수 미만 등장한 태그는 연결에 쓰지 않음
PLACE_K = 10               # 새 카드 배치 시 참조하는 이웃 수
NAMING_MODEL = os.environ.get("MAP_NAMING_MODEL", "claude-sonnet-5-5")
TYPES = ["동아리·학회", "팀플·공모전", "아르바이트", "대외활동·서포터즈",
         "학생회·행사 운영", "봉사·멘토링", "학과 전시", "개인 프로젝트", "인턴"]


# ---------- 임베딩 ----------
def gemini_client():
    from google import genai
    return genai.Client()


def embed_texts(client, texts: list[str], batch: int = 50) -> np.ndarray:
    from google.genai import types
    out = []
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        res = client.models.embed_content(
            model=EMBED_MODEL, contents=chunk,
            config=types.EmbedContentConfig(task_type="CLUSTERING", output_dimensionality=EMBED_DIM),
        )
        out.extend(e.values for e in res.embeddings)
        print(f"  임베딩 {min(i + batch, len(texts))}/{len(texts)}")
    return np.asarray(out, dtype=np.float32)


def l2norm(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.clip(n, 1e-9, None)


def load_embeddings(ids: list[str], texts: list[str], mode: str = "all") -> np.ndarray:
    """video_id 키 캐시(모드별 파일). 없는 것만 Gemini 호출."""
    EMB_CACHE = MAP_DIR / ("embeddings.npz" if mode == "all" else f"embeddings_{mode}.npz")
    cache: dict[str, np.ndarray] = {}
    if EMB_CACHE.exists():
        z = np.load(EMB_CACHE, allow_pickle=False)
        cache = dict(zip(z["ids"].tolist(), z["vecs"]))
    missing = [i for i, vid in enumerate(ids) if vid not in cache]
    if missing:
        print(f"임베딩 호출 {len(missing)}건 ({EMBED_MODEL}, {EMBED_DIM}차원)")
        vecs = embed_texts(gemini_client(), [texts[i] for i in missing])
        for i, v in zip(missing, vecs):
            cache[ids[i]] = v
        np.savez(EMB_CACHE, ids=np.array(list(cache.keys())), vecs=np.stack(list(cache.values())))
    return l2norm(np.stack([cache[v] for v in ids]))


# ---------- 배치 규칙 ----------
def place(vec: np.ndarray, base_vecs: np.ndarray, base_xy: np.ndarray, base_region: np.ndarray,
          k: int = PLACE_K, jitter: float = 6.0, rng: random.Random | None = None) -> tuple[float, float, int, float]:
    """새 카드 벡터(정규화) → (x, y, 섬, 최근접 유사도). 유사도 상위 k 이웃의 가중 평균 좌표, 섬은 유사도 합 최대."""
    sims = base_vecs @ vec
    idx = np.argsort(-sims)[:k]
    w = np.clip(sims[idx], 1e-6, None)
    w = w ** 4                       # 가까운 이웃에 더 큰 비중
    xy = (base_xy[idx] * w[:, None]).sum(0) / w.sum()
    region_score = collections.defaultdict(float)
    for i, s in zip(idx, sims[idx]):
        region_score[int(base_region[i])] += float(s)
    region = max(region_score, key=region_score.get)
    if rng is not None and jitter:
        xy = xy + np.array([rng.uniform(-jitter, jitter), rng.uniform(-jitter, jitter)])
    return float(xy[0]), float(xy[1]), region, float(sims[idx[0]])


def place_by_centroid(vec: np.ndarray, centroids: np.ndarray, centers: np.ndarray, radii: np.ndarray,
                      rng: random.Random) -> tuple[float, float, int, float]:
    """밑지도 벡터를 보관하지 않을 때의 대안: 가장 가까운 섬 중심 근처에 찍음."""
    sims = l2norm(centroids) @ vec
    r = int(np.argmax(sims))
    ang = rng.uniform(0, 2 * np.pi)
    dist = rng.uniform(0, radii[r] * 0.6)
    return float(centers[r][0] + dist * np.cos(ang)), float(centers[r][1] + dist * np.sin(ang)), r, float(sims[r])


# ---------- 섬 이름 ----------
def _members_key(r: dict) -> str:
    return hashlib.sha1("|".join(sorted(r["member_ids"])).encode()).hexdigest()[:16]


def name_regions(regions: list[dict]) -> list[dict]:
    cache = json.loads(NAMES_CACHE.read_text(encoding="utf-8")) if NAMES_CACHE.exists() else {}
    todo = [r for r in regions if _members_key(r) not in cache]
    for r in regions:
        if _members_key(r) in cache:
            r.update(cache[_members_key(r)])
    if not todo:
        print("섬 이름: 캐시 재사용")
        return regions
    if len(todo) < len(regions):
        print(f"섬 이름: {len(todo)}개만 새로 생성 (나머지 캐시)")
    regions_named = _name_regions_llm(todo)
    for r in regions_named:
        cache[_members_key(r)] = {k: r[k] for k in ("name_poetic", "name_plain", "description")}
    NAMES_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return regions


def _name_regions_llm(regions: list[dict]) -> list[dict]:
    import anthropic
    from pydantic import BaseModel, Field

    class RegionName(BaseModel):
        region: int = Field(description="섬 번호 (입력의 번호 그대로)")
        name_poetic: str = Field(description="지도에 쓸 이름. '사랑과 인간의 숲' 꼴(두 추상 명사 + 지형), 활동 명칭 금지")
        name_plain: str = Field(description="설명형 한 줄. 이 섬의 경험들이 공통으로 남긴 것, 20자 안팎")
        description: str = Field(description="후기를 근거로 사람들이 무엇을 겪고 얻었는지 한두 문장")

    class Naming(BaseModel):
        regions: list[RegionName]

    lines = []
    for r in regions:
        lines.append(
            f"## 섬 {r['id']} (카드 {r['size']}장)\n"
            f"- 자주 나온 감정 단어: {', '.join(f'{w}({n})' for w, n in r['emotion_words'])}\n"
            f"- 활동 유형(참고용, 이름에 쓰지 말 것): {', '.join(f'{t} {n}' for t, n in r['type_mix'][:3])}\n"
            f"- 후기(③ 어땠나) 샘플:\n" + "\n".join(f"  · {x}" for x in r["sample_feelings"]) + "\n"
            f"- 한 일(②) 샘플:\n" + "\n".join(f"  · {x}" for x in r["sample_actions"])
        )
    system = """대학생 경험 지도의 영역(섬) 이름을 짓습니다. 각 섬은 비슷한 경험들이 모인 땅이고, 사용자는 자기 경험이 어느 섬에 찍히는지 보며 "지나고 나서야 보이는 경험의 연결"을 발견합니다.

이름은 활동의 종류가 아니라, 그 경험들을 지나온 사람들이 공통으로 느끼고 얻은 것에서 나와야 합니다. 후기(③)에 반복해서 나타나는 감정·관계·깨달음·태도를 읽고 그것을 땅 이름으로 만드세요.
- 형식: "사랑과 인간의 숲"처럼 「〇〇과 〇〇의 지형」. 지형은 숲·들판·언덕·바다·강·항구·골짜기·광장·섬·고원 등 섬마다 다르게.
- 앞의 두 낱말은 경험의 본질을 담은 짧은 추상 명사(예: 사랑, 인간, 인내, 성취, 용기, 환대, 몰입, 연대, 서툶, 발견, 책임, 자유). 활동 명칭(알바·인턴·취재·봉사·동아리·공모전·개발 등)과 장소·직무 단어는 쓰지 않습니다.
- 섬끼리 낱말이 겹치지 않게 합니다.
- name_plain 은 설명형 한 줄: 이 섬의 경험들이 공통으로 남긴 것(예: "사람을 상대하며 마음이 단단해지는 경험").
- description 은 후기들을 근거로 한 두 문장. 활동 종류를 나열하지 말고 사람들이 무엇을 겪고 무엇을 얻었는지 씁니다.
모든 섬에 대해 답합니다."""
    client = build_client()
    res = client.messages.parse(
        model=NAMING_MODEL, max_tokens=6000, system=system,
        output_config={"effort": "high"},
        messages=[{"role": "user", "content": "\n\n".join(lines)}],
        output_format=Naming,
    )
    out: Naming = res.parsed_output
    by_id = {n.region: n for n in out.regions}
    for r in regions:
        n = by_id.get(r["id"])
        if n:
            r["name_poetic"], r["name_plain"], r["description"] = n.name_poetic.strip(), n.name_plain.strip(), n.description.strip()
        else:
            r["name_poetic"], r["name_plain"], r["description"] = f"섬 {r['id']}", f"섬 {r['id']}", ""
    print(f"섬 이름 생성: 입력 {res.usage.input_tokens:,} / 출력 {res.usage.output_tokens:,} 토큰 ({NAMING_MODEL})")
    return regions


# ---------- 메인 ----------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=None, help="섬 개수 강제")
    ap.add_argument("--k-range", type=int, nargs=2, default=(8, 12), help="실루엣으로 고를 섬 개수 범위")
    ap.add_argument("--users", type=int, default=3)
    ap.add_argument("--cards-per-user", type=int, default=4)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--skip-naming", action="store_true", help="Claude 호출 없이 '섬 N' 이름 사용")
    ap.add_argument("--placement", choices=["knn", "centroid"], default="knn")
    ap.add_argument("--embed-fields", choices=list(EMBED_FIELDS), default="review",
                    help="임베딩 입력. all=활동명+유형+역할+②+③(활동별로 묶임), review=②+③(경험의 성격별), feeling=③")
    ap.add_argument("--render-only", action="store_true", help="계산 없이 기존 map.json 을 템플릿에 다시 주입")
    args = ap.parse_args()
    rng = random.Random(args.seed)
    if args.render_only:
        return render_html(json.loads((MAP_DIR / "map.json").read_text(encoding="utf-8")))

    from sklearn.cluster import KMeans
    from sklearn.manifold import TSNE
    from sklearn.metrics import silhouette_score

    # 1. 카드 + 태그 로드, 어휘 결정
    cards = list(csv.DictReader(OUT_CSV.open(encoding="utf-8")))
    raw = json.loads(TAGS_RAW.read_text(encoding="utf-8"))
    cards = [c for c in cards if c["video_id"] in raw]
    tag_count = collections.Counter(t for c in cards for t in raw[c["video_id"]]["tags"])
    vocab = {t: n for t, n in tag_count.items() if n >= MIN_TAG_COUNT}
    for c in cards:
        c["summary"] = raw[c["video_id"]]["summary"]
        c["tags"] = [t for t in raw[c["video_id"]]["tags"] if t in vocab]
    (MAP_DIR / "tag_vocab.json").write_text(
        json.dumps(dict(sorted(vocab.items(), key=lambda kv: -kv[1])), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"카드 {len(cards)}장, 태그 어휘 {len(vocab)}개 (등장 {MIN_TAG_COUNT}장 이상; 버린 태그 {len(tag_count) - len(vocab)}개)")

    # 2. 가상 사용자 분리 (유형이 섞이도록: 유형별로 돌면서 뽑음)
    n_user_cards = args.users * args.cards_per_user
    by_type = collections.defaultdict(list)
    for c in cards:
        by_type[c["activity_type"]].append(c)
    for lst in by_type.values():
        rng.shuffle(lst)
    picked = []
    types_cycle = [t for t in TYPES if by_type.get(t)]
    while len(picked) < n_user_cards:
        for t in types_cycle:
            if by_type[t] and len(picked) < n_user_cards:
                picked.append(by_type[t].pop())
    rng.shuffle(picked)
    picked_ids = {c["video_id"] for c in picked}
    base = [c for c in cards if c["video_id"] not in picked_ids]
    users = [{"id": f"u{i + 1}", "name": f"가상 사용자 {chr(65 + i)}",
              "cards": picked[i * args.cards_per_user:(i + 1) * args.cards_per_user]} for i in range(args.users)]

    # 3. 임베딩 (전체 카드 한 번에; 캐시)
    all_ids = [c["video_id"] for c in cards]
    vecs_all = load_embeddings(all_ids, [EMBED_FIELDS[args.embed_fields](c) for c in cards], args.embed_fields)
    vec_of = dict(zip(all_ids, vecs_all))
    base_vecs = np.stack([vec_of[c["video_id"]] for c in base])

    # 4. 2D 좌표 (밑지도만)
    print("t-SNE 계산 중…")
    xy = TSNE(n_components=2, metric="cosine", init="pca", perplexity=30, random_state=args.seed).fit_transform(base_vecs)
    lo, hi = xy.min(0), xy.max(0)
    xy = (xy - lo) / (hi - lo) * 900 + 50          # 50~950
    base_xy = xy.astype(float)

    # 5. 섬 나누기
    if args.k:
        ks = [args.k]
    else:
        ks = list(range(args.k_range[0], args.k_range[1] + 1))
    # 섬은 지도 위에서 한 덩어리로 보여야 하므로 2D 좌표(t-SNE)에서 군집. 비슷한 경험이 이미 가까이 놓여 있어 의미도 유지됨.
    best = None
    for k in ks:
        km = KMeans(n_clusters=k, n_init=10, random_state=args.seed).fit(base_xy)
        sil = silhouette_score(base_xy, km.labels_)
        sizes = np.bincount(km.labels_)
        print(f"  k={k}: 실루엣 {sil:.3f}, 섬 크기 {sorted(sizes.tolist(), reverse=True)}")
        if best is None or sil > best[1]:
            best = (k, sil, km)
    k, sil, km = best
    labels = km.labels_
    print(f"선택 k={k} (2D 실루엣 {sil:.3f})")

    # 섬 요약
    N = len(base)
    regions = []
    for r in range(k):
        idx = np.where(labels == r)[0]
        members = [base[i] for i in idx]
        center = base_xy[idx].mean(0)
        radius = float(np.percentile(np.linalg.norm(base_xy[idx] - center, axis=1), 80)) if len(idx) > 1 else 30.0
        tc = collections.Counter(t for c in members for t in c["tags"])
        lift = {t: (n / len(members)) / (vocab[t] / N) for t, n in tc.items() if n >= 3}
        top_tags = [t for t, _ in sorted(lift.items(), key=lambda kv: -kv[1])[:5]]
        type_mix = collections.Counter(c["activity_type"] for c in members).most_common()
        cen = l2norm(base_vecs[idx].mean(0))          # 섬의 임베딩 중심 (centroid 배치용)
        order = idx[np.argsort(-(base_vecs[idx] @ cen))]
        regions.append({
            "id": r, "size": len(members), "cx": float(center[0]), "cy": float(center[1]), "radius": radius,
            "top_tags": top_tags, "type_mix": type_mix, "centroid": cen.astype(float).round(6).tolist(),
            "sample_summaries": [base[i]["summary"] for i in order[:12]],
            "sample_feelings": [base[i]["feeling"] for i in order[:15]],
            "sample_actions": [base[i]["action"] for i in order[:6]],
            "emotion_words": collections.Counter(
                w.strip(" ,.") for c in members for w in c["feeling"].split(".")[0].replace("과", ",").replace("와", ",").replace("·", ",").split(",")
                if 1 < len(w.strip(" ,.")) <= 5).most_common(8),
            "member_ids": [base[i]["video_id"] for i in idx],
        })
    regions = [r for r in sorted(regions, key=lambda r: -r["size"])]
    # id 를 크기순으로 다시 매김
    remap = {r["id"]: i for i, r in enumerate(regions)}
    labels = np.array([remap[int(l)] for l in labels])
    for i, r in enumerate(regions):
        r["id"] = i
    if args.skip_naming:
        for r in regions:
            r["name_poetic"], r["name_plain"], r["description"] = f"섬 {r['id'] + 1}", f"섬 {r['id'] + 1}", ""
    else:
        regions = name_regions(regions)
    for r in regions:
        print(f"  섬 {r['id']}: {r['name_poetic']} / {r['name_plain']} ({r['size']}장) 태그 {', '.join(r['top_tags'])}")

    # 6. 태그 연결 (밑지도)
    id2i = {c["video_id"]: i for i, c in enumerate(base)}
    tag_index = collections.defaultdict(list)
    for i, c in enumerate(base):
        for t in c["tags"]:
            tag_index[t].append(i)
    shared: dict[tuple[int, int], list[str]] = collections.defaultdict(list)
    for t, members in tag_index.items():
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                shared[(members[a], members[b])].append(t)
    sim = base_vecs @ base_vecs.T
    nbrs = collections.defaultdict(list)
    for (a, b), tags in shared.items():
        s = float(sim[a, b])
        nbrs[a].append((b, len(tags), s, tags))
        nbrs[b].append((a, len(tags), s, tags))
    keep = set()
    for a, lst in nbrs.items():
        lst.sort(key=lambda x: (-x[1], -x[2]))
        for b, w, s, tags in lst[:6]:
            keep.add((min(a, b), max(a, b)))
    edges = []
    for (a, b), tags in shared.items():
        if len(tags) >= 2 or (a, b) in keep:
            edges.append({"s": base[a]["video_id"], "t": base[b]["video_id"], "w": len(tags),
                          "sim": round(float(sim[a, b]), 3), "shared": tags})
    print(f"태그 공유 쌍 {len(shared):,}개 중 뷰어 간선 {len(edges):,}개")

    def card_json(c, x, y, region, extra=None):
        d = {"id": c["video_id"], "title": c["title"], "activity_name": c["activity_name"],
             "activity_type": c["activity_type"], "role": c["role"], "situation": c["situation"],
             "action": c["action"], "feeling": c["feeling"], "summary": c["summary"], "tags": c["tags"],
             "x": round(x, 1), "y": round(y, 1), "region": int(region)}
        if extra:
            d.update(extra)
        return d

    base_json = []
    for i, c in enumerate(base):
        nb = [{"id": base[b]["video_id"], "w": w, "sim": round(s, 3), "shared": tags} for b, w, s, tags in nbrs[i][:10]]
        base_json.append(card_json(c, base_xy[i][0], base_xy[i][1], labels[i], {"neighbors": nb}))

    # 7. 가상 사용자 배치
    centroids = np.stack([np.array(r["centroid"]) for r in regions])
    centers = np.array([[r["cx"], r["cy"]] for r in regions])
    radii = np.array([r["radius"] for r in regions])
    users_json = []
    for u in users:
        placed = []
        for c in u["cards"]:
            v = vec_of[c["video_id"]]
            if args.placement == "knn":
                x, y, reg, s = place(v, base_vecs, base_xy, labels, rng=rng)
            else:
                x, y, reg, s = place_by_centroid(v, centroids, centers, radii, rng)
            placed.append(card_json(c, x, y, reg, {"placed_sim": round(s, 3)}))
        links = []
        for a in range(len(placed)):
            cand = []
            for b in range(len(placed)):
                if a == b:
                    continue
                sh = sorted(set(placed[a]["tags"]) & set(placed[b]["tags"]))
                if sh:
                    cand.append((len(sh), float(vec_of[placed[a]["id"]] @ vec_of[placed[b]["id"]]), b, sh))
            cand.sort(key=lambda x: (-x[0], -x[1]))
            for w, s, b, sh in cand[:3]:
                key = tuple(sorted((a, b)))
                if not any((l["s"], l["t"]) == (placed[key[0]]["id"], placed[key[1]]["id"]) for l in links):
                    links.append({"s": placed[key[0]]["id"], "t": placed[key[1]]["id"], "w": w, "shared": sh})
        users_json.append({"id": u["id"], "name": u["name"], "cards": placed, "links": links})
        print(f"  {u['name']}: " + " / ".join(f"{p['activity_name'][:14]}→섬{p['region']}" for p in placed) + f", 연결 {len(links)}개")

    # 8. 저장
    meta = {"embedding_model": EMBED_MODEL, "embedding_dim": EMBED_DIM, "k": k, "silhouette": round(float(sil), 4),
            "base_cards": len(base), "min_tag_count": MIN_TAG_COUNT, "place_k": PLACE_K, "seed": args.seed,
            "placement": args.placement, "coord_range": [0, 1000], "embed_fields": args.embed_fields}
    region_pub = [{k2: r[k2] for k2 in ("id", "name_poetic", "name_plain", "description", "size", "cx", "cy", "radius", "top_tags", "type_mix", "centroid")}
                  for r in regions]
    map_json = {"meta": meta, "vocab": vocab,
                "regions": [{**rp, "member_ids": r["member_ids"]} for rp, r in zip(region_pub, regions)],
                "cards": base_json, "edges": edges, "users": users_json}
    (MAP_DIR / "map.json").write_text(json.dumps(map_json, ensure_ascii=False), encoding="utf-8")
    regions_json = {"meta": meta, "vocab": vocab, "regions": region_pub,
                    "base_points": [[round(float(x), 1), round(float(y), 1), int(l)] for (x, y), l in zip(base_xy, labels)]}
    (MAP_DIR / "regions.json").write_text(json.dumps(regions_json, ensure_ascii=False, indent=1), encoding="utf-8")
    np.savez(MAP_DIR / "base_vectors.npz", vecs=base_vecs.astype(np.float32), xy=base_xy.astype(np.float32), region=labels.astype(np.int16))

    print(f"저장: map.json, regions.json, base_vectors.npz, tag_vocab.json → {MAP_DIR}/")
    return render_html(map_json)


def render_html(map_json: dict, out_name: str = "index.html") -> int:
    if not TEMPLATE.exists():
        print(f"{TEMPLATE} 가 없어 뷰어를 만들지 않았습니다.", file=sys.stderr)
        return 0
    html = TEMPLATE.read_text(encoding="utf-8")
    payload = json.dumps(map_json, ensure_ascii=False).replace("</", "<\\/")
    (MAP_DIR / out_name).write_text(html.replace("/*__MAP_DATA__*/null", payload), encoding="utf-8")
    print(f"뷰어 → {MAP_DIR / out_name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
