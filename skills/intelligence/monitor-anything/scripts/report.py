#!/usr/bin/env python3
"""report.py —— 编排阶段③筛选、④聚类、⑤摘要、⑥渲染前的数据组装。

**这个脚本自己不调用任何 LLM。** 语义理解（筛选/聚类/摘要）是 Agent（也就是正在
执行本 Skill 的你）读 prompts/*.md 后直接完成的推理，report.py 只负责：
  1. 从 SQLite 里按脚本能确定性完成的条件做预过滤（时间窗、关键词、静音源）
  2. 把需要语义判断的数据整理成干净的 JSON 喂给 Agent
  3. 接收 Agent 推理后写下的 JSON 结果，做校验、统计与组装，落盘为权威产物
     reports/<date>.json（§6.1 结构）

四个子命令依次调用，构成一条流水线，每一步都读上一步落盘的中间产物：

    candidates   -> 产出候选文章（标题+来源），供 Agent 执行 prompts/filter.md
    filtered     -> 接收筛选结果，产出聚类输入，供 Agent 执行 prompts/cluster.md
    clustered    -> 接收聚类结果，产出摘要输入，供 Agent 执行 prompts/summarize.md
    summarized   -> 接收摘要结果，组装 reports/<date>.json 的该 monitor 小节
    finalize     -> 所有 monitor 处理完后收尾：失败告警检查 + 打印终态进度表

详见 SKILL.md 的“主流程编排”一节与 ARCHITECTURE.md。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import common

CANDIDATE_CONTENT_SNIPPET = 400
CLUSTER_CONTENT_SNIPPET = 300


# --------------------------------------------------------------------------
# 通用辅助

def get_monitor(cfg: dict[str, Any], monitor_id: str) -> dict[str, Any]:
    for m in cfg.get("monitors", []):
        if m.get("id") == monitor_id:
            return m
    raise SystemExit(f"未在 config.json 中找到 monitor: {monitor_id}")


def window_articles(conn, hours: int = 24) -> list[dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM articles WHERE fetched_at >= datetime('now', ?) ORDER BY fetched_at DESC",
        (f"-{hours} hours",),
    )
    return [dict(r) for r in cur.fetchall()]


def window_run_totals(conn, hours: int = 24) -> dict[str, int]:
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(SUM(fetched),0), COALESCE(SUM(new),0) FROM runs WHERE run_at >= datetime('now', ?)",
        (f"-{hours} hours",),
    )
    fetched, new = cur.fetchone()
    return {"fetched": fetched, "new": new}


def source_breakdown(articles: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for a in articles:
        st = a.get("source_type") or "unknown"
        counts[st] = counts.get(st, 0) + 1
    return counts


def _record_handoff_elapsed(state: dict[str, Any], stage_key: str) -> None:
    """把「上一步交给 Agent 到这一步收到结果」之间的耗时记为该阶段的 stage_ms。

    因为③④⑤三个语义阶段的实际耗时主要花在 Agent 推理上，而 Agent 推理发生在两次
    report.py 调用之间（脚本进程已经退出），只能用「相邻两次调用的时间差」来近似。
    """
    now = time.time()
    prev = state.get("_handoff_ts")
    if prev:
        state.setdefault("stage_ms", {})[stage_key] = int((now - prev) * 1000)
    state["_handoff_ts"] = now


def recompute_aggregate_stats(state: dict[str, Any]) -> None:
    """把各 monitor 独立记录的漏斗计数汇总到顶层 stats（支持多 monitor 场景）。

    fetched / after_dedup / sources 是采集清洗阶段的全局值，与 monitor 无关，
    在 candidates 阶段写入后保持不变；after_prefilter 及之后的计数按 monitor 各自
    独立，因此需要对所有已处理的 monitor 求和才是顶层展示用的漏斗数字。
    """
    monitors = state.get("monitors", {})
    for key in ("after_prefilter", "after_llm_filter", "clusters", "selected"):
        state["stats"][key] = sum(m.get(key, 0) for m in monitors.values())


def check_recent_failures(conn) -> list[str]:
    """§13 失败告警：连续 2 次采集失败，或最近一次新增为 0，则给出人话提示。"""
    cur = conn.cursor()
    cur.execute("SELECT status, new FROM runs ORDER BY run_at DESC LIMIT 2")
    rows = cur.fetchall()
    alerts = []
    if len(rows) >= 2 and all(r[0] == "error" for r in rows):
        alerts.append("最近的数据采集出现了问题，今天的报告可能不完整。")
    elif rows and rows[0][0] == "ok" and rows[0][1] == 0:
        alerts.append("最近一次采集没有抓到新内容，可能是数据源暂时没有更新，也可能是连接异常。")
    return alerts


# --------------------------------------------------------------------------
# 阶段③ 前半：脚本预过滤 -> 产出候选给 Agent

def cmd_candidates(args: argparse.Namespace) -> int:
    cfg = common.load_config()
    monitor = get_monitor(cfg, args.monitor_id)
    conn = common.connect_db()
    date = args.date or common.today_str()

    all_articles = window_articles(conn)
    run_totals = window_run_totals(conn)
    sources = source_breakdown(all_articles)

    exclude_keywords = monitor.get("exclude_keywords", [])
    mute_feeds = set(monitor.get("mute_feeds", []))

    candidates = []
    for a in all_articles:
        title = a["title"] or ""
        if any(k and k in title for k in exclude_keywords):
            continue
        if a.get("feed_name") in mute_feeds:
            continue
        candidates.append(
            {
                "id": a["id"],
                "title": title,
                "source_type": a["source_type"],
                "feed_name": a.get("feed_name") or "",
                "low_quality": bool(a.get("low_quality")),
            }
        )

    state = common.load_run_state(date)
    state["stats"].update(
        {
            "fetched": run_totals["fetched"] or len(all_articles),
            "after_dedup": run_totals["new"] or len(all_articles),
            "sources": sources,
        }
    )
    mstate = state.setdefault("monitors", {}).setdefault(args.monitor_id, {})
    mstate["name"] = monitor.get("name", args.monitor_id)
    mstate["after_prefilter"] = len(candidates)
    recompute_aggregate_stats(state)
    state["_handoff_ts"] = time.time()  # 用于下一步反推 Agent 语义筛选耗时（filter stage_ms）
    common.save_run_state(date, state)

    out_path = common.work_dir() / f"candidates-{args.monitor_id}-{date}.json"
    payload = {
        "monitor": {
            "id": monitor["id"],
            "name": monitor.get("name", monitor["id"]),
            "description": monitor.get("description", ""),
        },
        "candidates": candidates,
        "instructions": "读取 prompts/filter.md，对以下候选执行标题级别的语义筛选。",
    }
    common.write_json(out_path, payload)

    common.print_stage_table(state["stats"], active_stage=3, detail="正在理解你关注的方向...")
    print(f"候选已写入：{out_path}")
    print(json.dumps({"candidate_count": len(candidates), "path": str(out_path)}, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------
# 接收筛选结果 -> 产出聚类输入

def cmd_filtered(args: argparse.Namespace) -> int:
    cfg = common.load_config()
    get_monitor(cfg, args.monitor_id)  # 校验存在
    date = args.date or common.today_str()
    result = common.read_json(Path(args.input))
    kept_ids: list[str] = result.get("kept", [])
    low_confidence_ids: set[str] = set(result.get("low_confidence", []))
    filter_examples = result.get("examples", [])

    conn = common.connect_db()
    cur = conn.cursor()
    articles = []
    for aid in kept_ids:
        cur.execute("SELECT * FROM articles WHERE id = ?", (aid,))
        row = cur.fetchone()
        if not row:
            continue
        a = dict(row)
        content = a["content"] or ""
        if not (content.strip()) and not (a["description"] or "").strip():
            # xhs 等正文可能为空的情况：仅标题判定，标记 low_confidence
            low_confidence_ids.add(aid)
        articles.append(
            {
                "id": a["id"],
                "title": a["title"],
                "source_type": a["source_type"],
                "feed_name": a.get("feed_name") or "",
                "description": a.get("description") or "",
                "content_snippet": content[:CLUSTER_CONTENT_SNIPPET],
                "publish_time": a.get("publish_time") or "",
                "url": a.get("url") or "",
                "low_confidence": aid in low_confidence_ids,
            }
        )

    state = common.load_run_state(date)
    _record_handoff_elapsed(state, "filter")
    mstate = state.setdefault("monitors", {}).setdefault(args.monitor_id, {})
    mstate["low_confidence_ids"] = list(low_confidence_ids)
    mstate["filter_examples"] = filter_examples
    mstate["after_llm_filter"] = len(kept_ids)
    recompute_aggregate_stats(state)
    common.save_run_state(date, state)

    out_path = common.work_dir() / f"cluster-input-{args.monitor_id}-{date}.json"
    common.write_json(
        out_path,
        {
            "articles": articles,
            "instructions": "读取 prompts/cluster.md，对以下条目做跨源同事件归并。",
        },
    )

    common.print_stage_table(state["stats"], active_stage=4)
    print(f"聚类输入已写入：{out_path}")
    print(json.dumps({"kept": len(kept_ids), "path": str(out_path)}, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------
# 接收聚类结果 -> 产出摘要输入

def cmd_clustered(args: argparse.Namespace) -> int:
    date = args.date or common.today_str()
    result = common.read_json(Path(args.input))
    clusters: list[dict[str, Any]] = result.get("clusters", [])

    cluster_input_path = common.work_dir() / f"cluster-input-{args.monitor_id}-{date}.json"
    articles_by_id = {a["id"]: a for a in common.read_json(cluster_input_path)["articles"]}

    enriched = []
    for idx, c in enumerate(clusters):
        members = [articles_by_id[i] for i in c.get("ids", []) if i in articles_by_id]
        if not members:
            continue
        sources = {m["source_type"] for m in members}
        enriched.append(
            {
                "cluster_index": idx,
                "articles": members,
                "ai_reasoning": c.get("ai_reasoning", ""),
                "cross_source": len(sources) > 1,
                "source_count": len(sources),
            }
        )

    state = common.load_run_state(date)
    _record_handoff_elapsed(state, "cluster")
    state.setdefault("monitors", {}).setdefault(args.monitor_id, {})["clusters"] = len(enriched)
    recompute_aggregate_stats(state)
    common.save_run_state(date, state)

    out_path = common.work_dir() / f"summarize-input-{args.monitor_id}-{date}.json"
    common.write_json(
        out_path,
        {
            "clusters": enriched,
            "instructions": "读取 prompts/summarize.md，为每个聚类生成 headline/summary/why_relevant/score。",
        },
    )

    common.print_stage_table(state["stats"], active_stage=5)
    print(f"摘要输入已写入：{out_path}")
    print(json.dumps({"clusters": len(enriched), "path": str(out_path)}, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------
# 接收摘要结果 -> 组装该 monitor 的最终报告小节

def cmd_summarized(args: argparse.Namespace) -> int:
    cfg = common.load_config()
    monitor = get_monitor(cfg, args.monitor_id)
    date = args.date or common.today_str()
    min_score = monitor.get("min_score", cfg.get("min_score", 6))

    result = common.read_json(Path(args.input))
    overview = result.get("overview", "")
    summaries: list[dict[str, Any]] = result.get("clusters", [])

    summarize_input_path = common.work_dir() / f"summarize-input-{args.monitor_id}-{date}.json"
    clusters_by_index = {c["cluster_index"]: c for c in common.read_json(summarize_input_path)["clusters"]}

    state = common.load_run_state(date)
    _record_handoff_elapsed(state, "summarize")
    monitor_state = state.get("monitors", {}).get(args.monitor_id, {})
    low_confidence_ids = set(monitor_state.get("low_confidence_ids", []))
    filter_examples = monitor_state.get("filter_examples", [])

    selected_clusters = []
    leads = []
    for s in summaries:
        idx = s.get("cluster_index")
        c = clusters_by_index.get(idx)
        if c is None:
            continue
        score = s.get("score", 0)
        articles = [
            {
                "title": a["title"],
                "url": a["url"],
                "source_type": a["source_type"],
                "feed_name": a["feed_name"],
                "publish_time": a["publish_time"],
            }
            for a in c["articles"]
        ]
        if score >= min_score:
            selected_clusters.append(
                {
                    "headline": s.get("headline", ""),
                    "score": score,
                    "summary": s.get("summary", ""),
                    "why_relevant": s.get("why_relevant", ""),
                    "ai_reasoning": c.get("ai_reasoning", ""),
                    "cross_source": c.get("cross_source", False),
                    "articles": articles,
                }
            )
        else:
            article_count = len(articles)
            source_count = c.get("source_count", len({a["source_type"] for a in articles}))
            cross_source = c.get("cross_source", False)
            feed_name = articles[0]["feed_name"] if articles else ""
            if article_count <= 1:
                reason = f"仅 1 篇（{feed_name or '单一来源'}），不足 2 个独立信源"
            elif cross_source:
                reason = f"{article_count} 篇 · 跨 {source_count} 个渠道，相关度 {score}/10 未达阈值"
            else:
                reason = f"{article_count} 篇 · 同源转载，相关度 {score}/10 未达阈值"
            leads.append(
                {
                    "title": s.get("headline") or (articles[0]["title"] if articles else ""),
                    "url": articles[0]["url"] if articles else "",
                    "source_type": articles[0]["source_type"] if articles else "",
                    "feed_name": feed_name,
                    "score": score,
                    "article_count": article_count,
                    "source_count": source_count,
                    "cross_source": cross_source,
                    "reason": reason,
                    "low_confidence": any(a["id"] in low_confidence_ids for a in c["articles"]),
                }
            )

    if not selected_clusters:
        overview = (
            overview
            or "今天你关注的方向没有明显动静，这很正常。数据每天更新，明天再来看看；"
            "也可以考虑放宽关注范围。"
        )

    monitor_report = {
        "id": monitor["id"],
        "name": monitor.get("name", monitor["id"]),
        "description": monitor.get("description", ""),
        "overview": overview,
        "clusters": selected_clusters,
        "leads": leads,
        "filter_examples": filter_examples,
    }

    _merge_into_report(date, monitor_report)

    monitor_state["selected"] = len(selected_clusters)
    recompute_aggregate_stats(state)
    common.save_run_state(date, state)
    common.print_stage_table(state["stats"], active_stage=6)
    print(json.dumps({"selected": len(selected_clusters), "leads": len(leads)}, ensure_ascii=False))
    return 0


def _merge_into_report(date: str, monitor_report: dict[str, Any]) -> None:
    path = common.reports_dir() / f"{date}.json"
    cfg = common.load_config()
    if path.exists():
        report = common.read_json(path)
    else:
        report = {
            "date": date,
            "generated_at": common.now_iso_local(),
            "language": cfg.get("language", "zh"),
            "stats": {},
            "monitors": [],
            "alerts": [],
        }
    report["monitors"] = [m for m in report["monitors"] if m["id"] != monitor_report["id"]]
    report["monitors"].append(monitor_report)
    report["generated_at"] = common.now_iso_local()
    common.write_json(path, report)


# --------------------------------------------------------------------------
# 收尾：写入 stats、告警，供 render.py 使用

def cmd_finalize(args: argparse.Namespace) -> int:
    date = args.date or common.today_str()
    conn = common.connect_db()
    state = common.load_run_state(date)
    alerts = check_recent_failures(conn)

    path = common.reports_dir() / f"{date}.json"
    if not path.exists():
        print("没有找到当日报告草稿，无法收尾。请先跑完 candidates/filtered/clustered/summarized。", file=sys.stderr)
        return 1
    report = common.read_json(path)
    report["stats"] = {
        "fetched": state["stats"].get("fetched", 0),
        "after_dedup": state["stats"].get("after_dedup", 0),
        "after_prefilter": state["stats"].get("after_prefilter", 0),
        "after_llm_filter": state["stats"].get("after_llm_filter", 0),
        "clusters": state["stats"].get("clusters", 0),
        "selected": state["stats"].get("selected", 0),
        "sources": state["stats"].get("sources", {}),
        "stage_ms": state.get("stage_ms", {}),
    }
    report["alerts"] = alerts
    common.write_json(path, report)

    state["stats"]["done"] = True
    common.print_stage_table(state["stats"], active_stage=6)
    print(f"报告 JSON 已生成：{path}")
    print(json.dumps({"path": str(path), "alerts": alerts}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="monitor-anything 报告编排（③~⑥的脚本部分）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("candidates", help="产出候选，供 Agent 执行 prompts/filter.md")
    p1.add_argument("--monitor-id", required=True)
    p1.add_argument("--date")
    p1.set_defaults(func=cmd_candidates)

    p2 = sub.add_parser("filtered", help="接收筛选结果，产出聚类输入")
    p2.add_argument("--monitor-id", required=True)
    p2.add_argument("--date")
    p2.add_argument("--input", required=True, help="Agent 写出的 {kept:[...], low_confidence:[...]} JSON 路径")
    p2.set_defaults(func=cmd_filtered)

    p3 = sub.add_parser("clustered", help="接收聚类结果，产出摘要输入")
    p3.add_argument("--monitor-id", required=True)
    p3.add_argument("--date")
    p3.add_argument("--input", required=True, help="Agent 写出的 {clusters:[{ids, ai_reasoning}]} JSON 路径")
    p3.set_defaults(func=cmd_clustered)

    p4 = sub.add_parser("summarized", help="接收摘要结果，组装该 monitor 的报告小节")
    p4.add_argument("--monitor-id", required=True)
    p4.add_argument("--date")
    p4.add_argument("--input", required=True, help="Agent 写出的 {overview, clusters:[{cluster_index,...}]} JSON 路径")
    p4.set_defaults(func=cmd_summarized)

    p5 = sub.add_parser("finalize", help="所有 monitor 处理完后收尾：写 stats/alerts")
    p5.add_argument("--date")
    p5.set_defaults(func=cmd_finalize)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
