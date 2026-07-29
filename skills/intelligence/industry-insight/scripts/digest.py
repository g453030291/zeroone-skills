#!/usr/bin/env python3
"""Decide what today's digest should cover, hand over full text, record the result.

The daily artifact is ``summaries/<profile>/YYYY-MM-DD.json`` — structured, not
prose, so the dashboard can render it without parsing anything and other tools
can consume it directly. Those files are also the only record of what has been
published: the 3-day cooldown reads the last few of them rather than a table, so
there is nothing to keep in sync.

Two rules keep output worth reading. A topic needs ≥2 articles, because the
value here is cross-source comparison — one article restated is a rewrite. And
a topic gets one write-up per 3 days, because repeating yesterday's conclusion
trains the reader to skim.

    python3 digest.py --plan    --profile auto-industry
    python3 digest.py --content 12
    python3 digest.py --record  --profile auto-industry --file <today.json>
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import store

COOLDOWN_DAYS = 3
JACCARD_SAME_TOPIC = 0.5

REQUIRED_TOPIC_FIELDS = ("topic_key", "title", "tldr")


def digest_path(profile_slug, date, workspace=None):
    return store.summary_dir(profile_slug, workspace) / f"{date}.json"


def recent_digests(profile_slug, workspace=None, days=COOLDOWN_DAYS, before=None):
    """已发布的近期产出。它们既是给人看的成品，也是冷却期的唯一依据。"""
    cutoff = datetime.strptime(before or store.today(), "%Y-%m-%d") - timedelta(days=days)
    out = []
    for path in sorted(store.summary_dir(profile_slug, workspace).glob("*.json"), reverse=True):
        try:
            date = datetime.strptime(path.stem, "%Y-%m-%d")
        except ValueError:
            continue
        if date <= cutoff:
            continue
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return out


def jaccard(a, b):
    # 兼容裸 id（旧文件）和复合键（新文件），否则同一批文章在新旧格式下
    # 会被判定成完全不重叠，冷却期直接失效。
    a = {store.normalize_article_key(x) for x in a}
    b = {store.normalize_article_key(x) for x in b}
    return len(a & b) / len(a | b) if a and b else 0.0


def published_topics(digests):
    for d in digests:
        for t in d.get("topics") or []:
            yield d.get("date"), t


def plan(conn, profile_slug, workspace, date):
    clusters = conn.execute(
        "SELECT * FROM clusters WHERE profile_slug=? ORDER BY article_count DESC",
        (profile_slug,),
    ).fetchall()
    prior = list(published_topics(recent_digests(profile_slug, workspace, before=date)))

    decisions = []
    for c in clusters:
        members = store.cluster_members(conn, c["id"])
        ids = [store.article_key(m["id"], m["source_type"]) for m in members]
        entry = {
            "cluster_id": c["id"],
            "topic_key": c["topic_key"],
            "title": c["title"],
            "article_count": len(members),
            "feeds": sorted({m["feed_name"] for m in members if m["feed_name"]}),
            "titles": [m["title"] for m in members],
        }

        match = None
        for past_date, t in prior:
            if t.get("topic_key") == c["topic_key"] or jaccard(ids, t.get("article_ids") or []) >= JACCARD_SAME_TOPIC:
                match = (past_date, t)
                break

        if match:
            past_date, t = match
            past_ids = {store.normalize_article_key(x) for x in (t.get("article_ids") or [])}
            new_ids = [i for i in ids if i not in past_ids]
            entry.update({
                "action": "update" if new_ids else "skip",
                "reason": (f"{past_date} 已写过《{t.get('title')}》，新增 {len(new_ids)} 篇"
                           if new_ids else f"{past_date} 已写过且无新增文章"),
                "new_article_ids": new_ids,
                "previous_title": t.get("title"),
            })
        elif len(members) >= 2:
            distinct_feeds = len({m["feed_name"] for m in members if m["feed_name"]})
            if distinct_feeds >= 2:
                entry.update({"action": "write",
                              "reason": f"{len(members)} 篇来自 {distinct_feeds} 个独立信源，可做交叉分析"})
            else:
                # 篇数够但都来自同一个公众号，不是真正的"跨源"——不该被自动
                # 当成高质量候选。默认和 single 一样先不写，但留给 AI 判断：
                # 有些同号连续报道确实有增量信息，值得收录并在 source_note 里说明。
                entry.update({"action": "same_feed",
                              "reason": f"{len(members)} 篇均来自同一公众号「{members[0]['feed_name']}」，"
                                        "不是跨源印证；默认不写，如内容确有增量可破例并在 source_note 注明"})
        else:
            entry.update({"action": "single", "reason": "仅 1 篇，默认不写；重大事件可破例"})
        decisions.append(entry)

    order = {"write": 0, "update": 1, "same_feed": 2, "single": 3, "skip": 4}
    decisions.sort(key=lambda d: (order[d["action"]], -d["article_count"]))

    existing = digest_path(profile_slug, date, workspace)
    return {
        "profile": profile_slug,
        "date": date,
        "output_path": str(existing),
        "already_written": existing.exists(),
        "cooldown_days": COOLDOWN_DAYS,
        "stats_preview": compute_stats(conn, profile_slug, [], date),
        "write": [d for d in decisions if d["action"] == "write"],
        "update": [d for d in decisions if d["action"] == "update"],
        "same_feed": [d for d in decisions if d["action"] == "same_feed"],
        "single": [d for d in decisions if d["action"] == "single"],
        "skipped": [{"topic_key": d["topic_key"], "title": d["title"], "reason": d["reason"]}
                    for d in decisions if d["action"] == "skip"],
    }


def compute_stats(conn, profile_slug, topics, date):
    """Dashboard 上的数字全部由脚本算，AI 不需要也不应该手填。"""
    def one(sql, args=()):
        row = conn.execute(sql, args).fetchone()
        return row[0] if row else 0

    feeds_used, articles_cited = set(), 0
    for t in topics:
        for s in t.get("sources") or []:
            if s.get("feed"):
                feeds_used.add(s["feed"])
            articles_cited += 1

    return {
        "articles_total": one("SELECT COUNT(*) FROM articles"),
        "feeds_total": one("SELECT COUNT(DISTINCT feed_name) FROM articles"),
        "chars_total": one("SELECT COALESCE(SUM(LENGTH(clean_content)),0) FROM articles"),
        "fetched_today": one("SELECT COALESCE(SUM(returned),0) FROM fetch_runs WHERE started_at LIKE ?", (f"{date}%",)),
        "new_today": one("SELECT COALESCE(SUM(inserted),0) FROM fetch_runs WHERE started_at LIKE ?", (f"{date}%",)),
        "duplicates_today": one("SELECT COALESCE(SUM(duplicates),0) FROM fetch_runs WHERE started_at LIKE ?", (f"{date}%",)),
        "fetch_runs_today": one("SELECT COUNT(*) FROM fetch_runs WHERE started_at LIKE ?", (f"{date}%",)),
        "scanned": one("SELECT COUNT(*) FROM relevance WHERE profile_slug=?", (profile_slug,)),
        "hits": one("SELECT COUNT(*) FROM relevance WHERE profile_slug=? AND verdict='hit'", (profile_slug,)),
        "clusters": one("SELECT COUNT(*) FROM clusters WHERE profile_slug=?", (profile_slug,)),
        "topics_published": len(topics),
        "feeds_used": len(feeds_used),
        "articles_cited": articles_cited,
    }


def validate(doc):
    """挡住无人值守时最容易出的几种残缺产出。"""
    problems = []
    if not isinstance(doc.get("topics"), list):
        problems.append("缺 topics 数组（今天没内容也要给空数组 []）")
        return problems
    for i, t in enumerate(doc["topics"]):
        where = f"topics[{i}]"
        for field in REQUIRED_TOPIC_FIELDS:
            if not t.get(field):
                problems.append(f"{where} 缺 {field}")
        if not t.get("cluster_id"):
            problems.append(f"{where} 缺 cluster_id，无法回填信源和冷却期依据")
    if doc["topics"] and not doc.get("highlights"):
        problems.append("有话题却没有 highlights（「今天值得看的几件事」是整篇入口）")
    return problems


def record(conn, profile_slug, workspace, date, path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    problems = validate(doc)
    if problems:
        raise SystemExit("产出不合格，未登记：\n  - " + "\n  - ".join(problems))

    # cluster_id 是全局自增的，不天然属于某个 profile。如果不在这里校验，
    # 给 profile A 登记时手滑填了 profile B 的 cluster_id，会把 B 的文章当
    # 信源写进 A 的情报里——跨行业串稿，且没有任何报错。
    wrong_profile = []
    for t in doc["topics"]:
        cluster = conn.execute(
            "SELECT profile_slug FROM clusters WHERE id=?", (t["cluster_id"],)
        ).fetchone()
        if cluster and cluster["profile_slug"] != profile_slug:
            wrong_profile.append(
                f"topics[{t.get('topic_key')}] 的 cluster_id={t['cluster_id']} "
                f"属于 profile '{cluster['profile_slug']}'，不是 '{profile_slug}'"
            )
    if wrong_profile:
        raise SystemExit("产出不合格，未登记（跨 profile 引用）：\n  - " + "\n  - ".join(wrong_profile))

    # 信源和 article_ids 由 cluster 回填——AI 手抄容易漏，而冷却期依赖它们
    unknown = []
    for t in doc["topics"]:
        members = store.cluster_members(conn, t["cluster_id"])
        if not members:
            unknown.append(t["cluster_id"])
        t["article_ids"] = [store.article_key(m["id"], m["source_type"]) for m in members]
        t["sources"] = [
            {"title": m["title"], "url": m["url"], "feed": m["feed_name"],
             "publish_time": m["publish_time"]}
            for m in members
        ]

    profile = store.load_profile(profile_slug, workspace)
    doc.update({
        "profile": profile_slug,
        "profile_name": profile.get("name") or profile_slug,
        "date": date,
        "generated_at": store.now(),
        "stats": compute_stats(conn, profile_slug, doc["topics"], date),
    })
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    out = {
        "recorded": str(path),
        "topics": [{"topic_key": t["topic_key"], "articles": len(t["article_ids"])}
                   for t in doc["topics"]],
        "stats": doc["stats"],
    }
    if unknown:
        out["warning"] = f"这些 cluster_id 没有成员，信源为空：{unknown}"
    return out


def main():
    ap = argparse.ArgumentParser(description="每日产出的规划、取文与登记")
    ap.add_argument("--profile")
    ap.add_argument("--workspace")
    ap.add_argument("--date", default=store.today())
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--content", type=int, metavar="CLUSTER_ID", help="取聚类成员全文")
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--file", help="写好的当日 JSON 路径，默认按日期推断")
    ap.add_argument("--list", action="store_true", help="列出历史产出")
    args = ap.parse_args()

    conn = store.connect(args.workspace)

    if args.content:
        print(json.dumps(
            [{"id": m["id"], "feed": m["feed_name"], "title": m["title"], "url": m["url"],
              "publish_time": m["publish_time"], "content": m["clean_content"]}
             for m in store.cluster_members(conn, args.content)],
            ensure_ascii=False, indent=2,
        ))
        return

    if not args.profile:
        raise SystemExit("需要 --profile")

    if args.list:
        rows = []
        for p in sorted(store.summary_dir(args.profile, args.workspace).glob("*.json"), reverse=True):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            rows.append({"date": d.get("date", p.stem), "path": str(p),
                         "topics": len(d.get("topics") or []),
                         "titles": [t.get("title") for t in d.get("topics") or []]})
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    if args.plan:
        print(json.dumps(plan(conn, args.profile, args.workspace, args.date),
                         ensure_ascii=False, indent=2))
        return

    if args.record:
        canonical = digest_path(args.profile, args.date, args.workspace)
        path = Path(args.file).expanduser() if args.file else canonical
        if args.file and path.resolve() != canonical.resolve():
            # --file 允许指向任意路径，但 dashboard.py 只扫描 summaries/<profile>/
            # 下按日期命名的文件——写到别处会成功登记却在 dashboard 上完全不可见，
            # 且不会有任何报错。默认路径已经是 canonical，只有显式传了不一致的
            # --file 才会触发这条提醒。
            print(
                f"警告：{path} 不在 {canonical.parent} 下，dashboard 不会展示这份产出。"
                f"没有特殊原因就不要用 --file，让它写到默认路径 {canonical}。",
                file=sys.stderr,
            )
        if not path.exists():
            raise SystemExit(f"文件不存在: {path}")
        print(json.dumps(record(conn, args.profile, args.workspace, args.date, path),
                         ensure_ascii=False, indent=2))
        return

    ap.print_help()


if __name__ == "__main__":
    main()
