#!/usr/bin/env python3
"""Collect the daily JSON outputs into one self-contained HTML page.

This script does no formatting decisions of its own — it gathers the daily
digests plus a few database counters, and drops them into the template as
inline JSON. The template owns the rendering. Because the digests are already
structured, there is nothing to parse: no markdown, no external libraries, no
network requests. The page opens by double click and works offline.

    python3 dashboard.py
    python3 dashboard.py --output ./demo.html --open
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import store

TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "dashboard_template.html"


def span_label(times):
    times = sorted(t for t in times if t)
    if not times:
        return "—"
    return times[0][:10] if times[0][:10] == times[-1][:10] else f"{times[0][5:10]} → {times[-1][5:10]}"


def load_digests(profile_slug, workspace):
    out = []
    for path in sorted(store.summary_dir(profile_slug, workspace).glob("*.json"), reverse=True):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue  # 半截文件不该让整个 dashboard 打不开
    return out


def build_profile(conn, profile, workspace):
    slug = profile["slug"]
    digests = load_digests(slug, workspace)

    clusters = []
    for c in conn.execute(
        "SELECT * FROM clusters WHERE profile_slug=? ORDER BY article_count DESC, updated_at DESC",
        (slug,),
    ).fetchall():
        members = store.cluster_members(conn, c["id"])
        clusters.append({
            "topic_key": c["topic_key"],
            "title": c["title"],
            "article_count": len(members),
            "feeds": sorted({m["feed_name"] for m in members if m["feed_name"]}),
            "span": span_label([m["publish_time"] for m in members]),
            "articles": [{"title": m["title"], "feed": m["feed_name"], "url": m["url"]}
                         for m in members],
        })

    def one(sql, args=()):
        row = conn.execute(sql, args).fetchone()
        return row[0] if row else 0

    latest = digests[0]["stats"] if digests and digests[0].get("stats") else {}
    return {
        "slug": slug,
        "name": profile.get("name") or slug,
        "description": profile.get("description") or "",
        "funnel": {
            "returned": one("SELECT COALESCE(SUM(returned),0) FROM fetch_runs"),
            "stored": one("SELECT COUNT(*) FROM articles"),
            "hit": one("SELECT COUNT(*) FROM relevance WHERE profile_slug=? AND verdict='hit'", (slug,)),
            "clustered": sum(c["article_count"] for c in clusters),
            "clusters": len(clusters),
            "publishable": sum(1 for c in clusters if c["article_count"] >= 2),
            "topics": sum(len(d.get("topics") or []) for d in digests),
        },
        "latest_stats": latest,
        "clusters": clusters,
        "digests": digests,
    }


def build_data(conn, workspace):
    st = store.stats(conn)
    profiles = [build_profile(conn, p, workspace) for p in store.list_profiles(workspace)]
    return {
        "generated_at": store.now(),
        "workspace": str(store.workspace_path(workspace)),
        "global": {
            "articles": st["articles"],
            "feeds": st["feeds"],
            "fetch_runs": st["fetch_runs"],
            "duplicates_total": st["duplicates_total"],
            "chars_total": st["chars_total"],
            "window": [st["earliest"], st["latest"]],
            "digests": sum(len(p["digests"]) for p in profiles),
            "topics": sum(p["funnel"]["topics"] for p in profiles),
        },
        "feeds": [{"name": r["feed_name"], "count": r["n"]} for r in conn.execute(
            "SELECT feed_name, COUNT(*) n FROM articles GROUP BY feed_name ORDER BY n DESC"
        ).fetchall()],
        "runs": [dict(r) for r in conn.execute(
            "SELECT started_at, returned, inserted, duplicates FROM fetch_runs ORDER BY id DESC"
        ).fetchall()],
        "profiles": profiles,
    }


def main():
    ap = argparse.ArgumentParser(description="生成单文件 dashboard")
    ap.add_argument("--workspace")
    ap.add_argument("--output")
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()

    conn = store.connect(args.workspace)
    data = build_data(conn, args.workspace)

    # </ 会提前闭合 script 标签
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", payload)

    out = Path(args.output).expanduser() if args.output else store.workspace_path(args.workspace) / "dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")

    print(json.dumps({
        "dashboard": str(out),
        "size_kb": round(out.stat().st_size / 1024, 1),
        "profiles": [p["slug"] for p in data["profiles"]],
        "digests": data["global"]["digests"],
        "topics": data["global"]["topics"],
    }, ensure_ascii=False, indent=2))

    if args.open:
        subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", str(out)], check=False)


if __name__ == "__main__":
    main()
