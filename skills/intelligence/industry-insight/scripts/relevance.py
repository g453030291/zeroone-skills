#!/usr/bin/env python3
"""Score stored articles against a profile, then record the AI's verdict.

Two stages on purpose. The script does the mechanical part: count profile
keywords and emit a compact table (id / feed / title / kw_score) that costs a
few thousand characters for a hundred articles. The AI reads that table and
decides — confirming obvious hits, rescuing articles that are clearly relevant
even though they never use a keyword, and asking for snippets only when the
title alone is ambiguous. Keyword counting alone would silently drop the
rescues; sending full text to the model would cost 340k characters per run.

    python3 relevance.py --profile auto-industry            # 待判表
    python3 relevance.py --profile auto-industry --snippets 3271670413-2247564859_1,...
    python3 relevance.py --profile auto-industry --apply verdicts.json
    cat verdicts.json | python3 relevance.py --profile auto-industry --apply -

verdicts.json 形如:
    [{"id": "...", "source_type": "wx", "score": 0.9, "verdict": "hit",
      "reason": "小米澎程增程车定名，属整车厂产品动态"}]
"""

import argparse
import json
import sys

import store

TITLE_WEIGHT = 3.0
BODY_WEIGHT = 1.0
BODY_HITS_CAP = 3  # 一个词在正文刷屏不该压过话题多样性

VALID_VERDICTS = {"hit", "miss"}


def keyword_score(article, include, exclude):
    """Cheap lexical relevance in [-1, 1]-ish space.

    Returns (score, matched_terms). The absolute value matters less than the
    ordering — the AI uses it to triage, not as a threshold.
    """
    title = (article["title"] or "")
    body = (article["clean_content"] or "")
    matched = []
    raw = 0.0
    for term in include:
        if not term:
            continue
        t_hits = title.count(term)
        b_hits = min(body.count(term), BODY_HITS_CAP)
        if t_hits or b_hits:
            matched.append(term)
            raw += TITLE_WEIGHT * t_hits + BODY_WEIGHT * b_hits
    penalty = 0.0
    blocked = []
    for term in exclude:
        if not term:
            continue
        if term in title:
            penalty += TITLE_WEIGHT
            blocked.append(term)
        elif term in body:
            penalty += BODY_WEIGHT
            blocked.append(term)

    # squash into a readable 0-1 range; 6 raw points ≈ solidly on-topic
    score = raw / (raw + 6.0) if raw > 0 else 0.0
    score -= min(penalty / 6.0, 0.5)
    return round(max(score, -0.5), 3), matched, blocked


def main():
    ap = argparse.ArgumentParser(description="按 profile 做相关性粗筛 / 回写 AI 判定")
    ap.add_argument("--profile", required=True)
    ap.add_argument("--workspace")
    ap.add_argument("--apply", help="AI 判定结果 JSON 文件路径，'-' 表示读 stdin")
    ap.add_argument("--snippets", help="逗号分隔的 article id，返回其 200 字摘要供复判")
    ap.add_argument("--snippet-chars", type=int, default=200)
    ap.add_argument("--rejudge", action="store_true", help="重判所有文章（改了关键词后用）")
    ap.add_argument("--status", action="store_true", help="只看当前 profile 的判定统计")
    ap.add_argument("--pretty", action="store_true", help="缩进输出（默认紧凑，省 token）")
    args = ap.parse_args()

    conn = store.connect(args.workspace)
    profile = store.load_profile(args.profile, args.workspace)

    if args.status:
        rows = conn.execute(
            """SELECT verdict, COUNT(*) n FROM relevance
               WHERE profile_slug = ? GROUP BY verdict""",
            (args.profile,),
        ).fetchall()
        counts = {r["verdict"]: r["n"] for r in rows}
        total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        print(
            json.dumps(
                {
                    "profile": args.profile,
                    "articles_total": total,
                    "hit": counts.get("hit", 0),
                    "miss": counts.get("miss", 0),
                    "unjudged": total - sum(counts.values()),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.snippets:
        # 去重主键是 (id, source_type)。接 id@source_type 可以精确指定；
        # 只给裸 id 时按 id 查询可能不止一行——不要默认挑第一条了事，
        # 把所有匹配都返回并标出 source_type，让调用方自己消歧义。
        wanted = [s.strip() for s in args.snippets.split(",") if s.strip()]
        out = []
        for ref in wanted:
            article_id, sep, source_type = ref.partition("@")
            if sep:
                rows = conn.execute(
                    "SELECT * FROM articles WHERE id=? AND source_type=?",
                    (article_id, source_type),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchall()
            for row in rows:
                out.append(
                    {
                        "id": row["id"],
                        "source_type": row["source_type"],
                        "key": store.article_key(row["id"], row["source_type"]),
                        "ambiguous": not sep and len(rows) > 1,
                        "feed": row["feed_name"],
                        "title": row["title"],
                        "snippet": (row["clean_content"] or "")[: args.snippet_chars],
                    }
                )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    if args.apply:
        raw = sys.stdin.read() if args.apply == "-" else open(args.apply, encoding="utf-8").read()
        verdicts = json.loads(raw)
        applied = 0
        unknown, invalid = [], []
        for v in verdicts:
            # 一个拼错的 verdict（如 "hitt"）如果被无条件接受，会插入一行
            # 既不是 hit 也不是 miss 的记录——但 unjudged_articles 只看"有没有
            # 记录"，不看 verdict 的值，所以这篇文章从此既不会被当作命中，
            # 也不会再出现在待判表里，永久消失且没有任何报错。严格白名单。
            verdict = v.get("verdict")
            if verdict not in VALID_VERDICTS:
                invalid.append({"id": v.get("id"), "verdict": verdict})
                continue
            if not store.article_exists(conn, v["id"], v.get("source_type", "wx")):
                unknown.append(v["id"])
                continue
            conn.execute(
                """INSERT INTO relevance
                   (profile_slug, article_id, source_type, kw_score, ai_score,
                    verdict, reason, judged_at)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(profile_slug, article_id, source_type) DO UPDATE SET
                     ai_score = excluded.ai_score,
                     verdict  = excluded.verdict,
                     reason   = excluded.reason,
                     judged_at = excluded.judged_at""",
                (
                    args.profile,
                    v["id"],
                    v.get("source_type", "wx"),
                    v.get("kw_score"),
                    v.get("score"),
                    verdict,
                    v.get("reason", ""),
                    store.now(),
                ),
            )
            applied += 1
        conn.commit()
        hits = conn.execute(
            "SELECT COUNT(*) FROM relevance WHERE profile_slug=? AND verdict='hit'",
            (args.profile,),
        ).fetchone()[0]
        out = {"applied": applied, "total_hits": hits}
        if unknown:
            out["unknown_ids"] = unknown
            out["warning_unknown"] = "这些 id 不在库里，判定未生效——对应文章仍未判"
        if invalid:
            out["invalid_verdicts"] = invalid
            out["warning_invalid"] = "verdict 必须是 hit 或 miss，这些未写入——对应文章仍未判，重新提交"
        print(json.dumps(out, ensure_ascii=False))
        return

    # default: emit the triage table
    if args.rejudge:
        rows = conn.execute("SELECT * FROM articles ORDER BY publish_time DESC").fetchall()
    else:
        rows = store.unjudged_articles(conn, args.profile)

    include = profile.get("include") or []
    exclude = profile.get("exclude") or []
    if not include:
        raise SystemExit(
            f"profile '{args.profile}' 的 include 关键词为空——粗筛完全依赖它做第一层召回。"
            " 在 profiles.jsonl 里补上。"
        )
    items = []
    for row in rows:
        score, matched, blocked = keyword_score(row, include, exclude)
        items.append(
            {
                "id": row["id"],
                "source_type": row["source_type"],
                "feed": row["feed_name"],
                "title": row["title"],
                "kw_score": score,
                "matched": matched[:4],
                "blocked": blocked[:2],
            }
        )
    items.sort(key=lambda x: x["kw_score"], reverse=True)
    print(
        json.dumps(
            {
                "profile": {
                    "slug": profile["slug"],
                    "name": profile.get("name"),
                    "description": profile.get("description"),
                },
                "pending": len(items),
                "articles": items,
            },
            ensure_ascii=False,
            indent=2 if args.pretty else None,
        )
    )


if __name__ == "__main__":
    main()
