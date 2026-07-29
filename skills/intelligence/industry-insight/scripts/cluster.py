#!/usr/bin/env python3
"""Pre-cluster relevant articles by lexical similarity, then record AI topics.

Chinese has no whitespace tokens, so this uses character bigrams — cheap,
dependency-free, and good enough to put "同一件事的多篇报道" next to each
other. It is deliberately a *pre*-cluster: bigram overlap cannot tell that
"雷军谈增程" and "小米澎程定名" are the same story, so the script hands the AI
candidate groups plus loose articles and lets it merge, split, and name them.

    python3 cluster.py --profile auto-industry                  # 候选组（只看新文章）
    python3 cluster.py --profile auto-industry --threshold 0.15 # 放松
    python3 cluster.py --profile auto-industry --apply topics.json
    python3 cluster.py --profile auto-industry --full            # 连旧文章也重新分组

topics.json 形如:
    [{"topic_key": "xiaomi-pengcheng-erev", "title": "小米澎程定位增程",
      "members": ["3271670413-2247564859_1", "..."]}]
"""

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict

import store

BODY_CHARS = 500       # 正文前 N 字已足够定位话题，且避免长文压垮相似度
TITLE_REPEAT = 3       # 标题信息密度高，重复计入以加权
SNIPPET_CHARS = 200
CJK = re.compile(r"[一-鿿]")
ALNUM = re.compile(r"[a-zA-Z0-9]+")


def features(row):
    """Character bigrams from the title (weighted) plus the body head."""
    title = row["title"] or ""
    body = (row["clean_content"] or "")[:BODY_CHARS]
    text = (title + "。") * TITLE_REPEAT + body

    cjk_chars = "".join(ch for ch in text if CJK.match(ch))
    grams = [cjk_chars[i : i + 2] for i in range(len(cjk_chars) - 1)]
    # latin/number runs (车型号、公司英文名) carry a lot of signal on their own
    grams += [w.lower() for w in ALNUM.findall(text) if len(w) > 1]
    return Counter(grams)


def tfidf_vectors(docs):
    n = len(docs)
    df = Counter()
    for counts in docs:
        df.update(counts.keys())
    vectors = []
    for counts in docs:
        vec = {}
        for gram, tf in counts.items():
            if df[gram] == n and n > 2:
                continue  # 出现在每一篇里的词没有区分度
            idf = math.log((n + 1) / (df[gram] + 1)) + 1.0
            vec[gram] = (1 + math.log(tf)) * idf
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vectors.append({g: v / norm for g, v in vec.items()})
    return vectors


def cosine(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(g, 0.0) for g, v in a.items())


def connected_components(n, edges):
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j in edges:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    return list(groups.values())


def main():
    ap = argparse.ArgumentParser(description="预聚类 / 回写 AI 话题划分")
    ap.add_argument("--profile", required=True)
    ap.add_argument("--workspace")
    # 0.15 是实测的甜点：能抓到"极氪9X上市"这类跨号同题，又不至于把
    # "轮胎实力榜"和"补胎气囊事故"这种只共享行业词的文章配成一对。
    # 宁可稍松——AI 复判能拆错配，却看不见没被提名的组合。
    ap.add_argument("--threshold", type=float, default=0.15, help="余弦相似度阈值，越小越松")
    ap.add_argument("--apply", help="AI 话题划分 JSON，'-' 表示读 stdin")
    ap.add_argument("--list", action="store_true", help="列出已登记的聚类")
    ap.add_argument("--full", action="store_true",
                     help="连已归入某聚类的旧文章也一起重新预聚类（默认只看新文章，见下）")
    args = ap.parse_args()

    conn = store.connect(args.workspace)

    if args.list:
        rows = conn.execute(
            """SELECT c.*, GROUP_CONCAT(m.article_id) ids
               FROM clusters c LEFT JOIN cluster_members m ON m.cluster_id = c.id
               WHERE c.profile_slug = ? GROUP BY c.id ORDER BY c.updated_at DESC""",
            (args.profile,),
        ).fetchall()
        print(
            json.dumps(
                [
                    {
                        "id": r["id"],
                        "topic_key": r["topic_key"],
                        "title": r["title"],
                        "article_count": r["article_count"],
                        "updated_at": r["updated_at"],
                    }
                    for r in rows
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.apply:
        raw = sys.stdin.read() if args.apply == "-" else open(args.apply, encoding="utf-8").read()
        topics = json.loads(raw)
        result, unknown = [], []
        for t in topics:
            members = t.get("members") or []
            if not members:
                continue
            existing = conn.execute(
                "SELECT * FROM clusters WHERE profile_slug=? AND topic_key=?",
                (args.profile, t["topic_key"]),
            ).fetchone()
            if existing:
                cid = existing["id"]
                conn.execute(
                    "UPDATE clusters SET title=?, updated_at=? WHERE id=?",
                    (t.get("title") or existing["title"], store.now(), cid),
                )
            else:
                cur = conn.execute(
                    """INSERT INTO clusters
                       (profile_slug, topic_key, title, created_at, updated_at, article_count)
                       VALUES (?,?,?,?,?,0)""",
                    (args.profile, t["topic_key"], t.get("title"), store.now(), store.now()),
                )
                cid = cur.lastrowid
            # 每次 --apply 传入的 members 是这个话题当下应有的完整名单，不是
            # 增量。之前这里只 INSERT OR IGNORE，从来不删——想把话题从 4 篇
            # 拆成 1 篇、或纠正一次错误配对，旧成员会永远赖在库里，"拆开重
            # 合并"实际不生效。改成先按新名单全量替换。
            resolved = []
            for aid in members:
                article_id, _, source_type = aid.partition("@")
                source_type = source_type or "wx"
                if not store.article_exists(conn, article_id, source_type):
                    unknown.append(aid)  # 打错的 id 会让聚类少一篇却看不出来
                    continue
                resolved.append((article_id, source_type))

            conn.execute("DELETE FROM cluster_members WHERE cluster_id=?", (cid,))
            for article_id, source_type in resolved:
                conn.execute(
                    "INSERT OR IGNORE INTO cluster_members (cluster_id, article_id, source_type) VALUES (?,?,?)",
                    (cid, article_id, source_type),
                )
            count = conn.execute(
                "SELECT COUNT(*) FROM cluster_members WHERE cluster_id=?", (cid,)
            ).fetchone()[0]
            conn.execute("UPDATE clusters SET article_count=? WHERE id=?", (count, cid))
            result.append({"cluster_id": cid, "topic_key": t["topic_key"], "article_count": count})
        conn.commit()
        out = {"clusters": result}
        if unknown:
            out["unknown_ids"] = unknown
            out["warning"] = "这些 id 不在库里，未计入聚类"
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    rows = store.hit_articles(conn, args.profile)
    if not rows:
        print(json.dumps({"groups": [], "singletons": [], "note": "该 profile 还没有 hit 文章，先跑 relevance.py"}, ensure_ascii=False))
        return

    # hit_articles 拿的是这个 profile 有史以来所有命中，不加过滤的话，
    # 跑得越久候选表越臃肿——每天都要 AI 重新过一遍几天前已经处理过的
    # 旧文章。默认只看"还没归入任何聚类"的文章；--full 才连旧的一起重来
    # （比如改了聚类阈值、想整体重新分组时用）。
    skipped_old = 0
    if not args.full:
        already_clustered = store.clustered_article_keys(conn, args.profile)
        skipped_old = sum(1 for r in rows if (r["id"], r["source_type"]) in already_clustered)
        rows = [r for r in rows if (r["id"], r["source_type"]) not in already_clustered]
        if not rows:
            print(json.dumps(
                {"groups": [], "singletons": [],
                 "note": f"{skipped_old} 篇已命中的文章都已归入过聚类，没有新文章待聚类。"
                         "想整体重新分组用 --full。"},
                ensure_ascii=False,
            ))
            return

    vectors = tfidf_vectors([features(r) for r in rows])
    edges = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            if cosine(vectors[i], vectors[j]) >= args.threshold:
                edges.append((i, j))

    def brief(row):
        return {
            "id": row["id"],
            "source_type": row["source_type"],
            "feed": row["feed_name"],
            "title": row["title"],
            "publish_time": row["publish_time"],
            "snippet": (row["clean_content"] or "")[:SNIPPET_CHARS],
        }

    groups, singletons = [], []
    for comp in connected_components(len(rows), edges):
        members = [brief(rows[i]) for i in comp]
        if len(members) == 1:
            singletons.append(members[0])
        else:
            groups.append({"seed": members[0]["id"], "size": len(members), "articles": members})
    groups.sort(key=lambda g: g["size"], reverse=True)

    print(
        json.dumps(
            {
                "profile": args.profile,
                "threshold": args.threshold,
                "hit_articles": len(rows),
                "skipped_already_clustered": skipped_old,
                "groups": groups,
                "singletons": singletons,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
