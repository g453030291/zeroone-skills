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
import validate

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


def latest_run_fetched(conn, hours: int = 24) -> int:
    """①"采集"展示数字用的原始条数——只取窗口内**最近一次** run 的 fetched，不再对
    runs 表求和。接口固定返回"近 24 小时"快照（不支持增量/分页参数），默认一天跑
    4 次 harvest，每次几乎看到同一批数据；对 runs.fetched 求和会让这个数字虚高
    约 4 倍。最近一次 run 的 fetched 本身就是当前这份 24 小时快照的原始体量，不需要
    也不应该再跟前几次的快照叠加。
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT fetched FROM runs WHERE run_at >= datetime('now', ?) ORDER BY run_at DESC LIMIT 1",
        (f"-{hours} hours",),
    )
    row = cur.fetchone()
    return row[0] if row else 0


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


def check_recent_failures(conn, language: str = "zh") -> list[str]:
    """§13 失败告警：连续 2 次采集失败，或连续 3 次新增为 0，才给出人话提示。

    "最近一次 new=0 就报警" 是误报的主要来源——接口每次固定返回近 24 小时快照，
    默认一天跑 4 次 harvest，大多数时候后面几次自然就是"这批我都见过了"，new=0
    是完全正常的现象，不代表连接有问题。改成要求**连续 3 次**都是 new=0 才提示，
    单次或两次为 0 不再触发误报。
    """
    cur = conn.cursor()
    cur.execute("SELECT status, new FROM runs ORDER BY run_at DESC LIMIT 3")
    rows = cur.fetchall()
    alerts = []
    if len(rows) >= 2 and all(r[0] == "error" for r in rows[:2]):
        alerts.append(common.t(language, "alert_fetch_failed"))
    elif len(rows) >= 3 and all(r[0] == "ok" and r[1] == 0 for r in rows):
        alerts.append(common.t(language, "alert_no_new"))
    return alerts


# --------------------------------------------------------------------------
# 阶段③ 前半：脚本预过滤 -> 产出候选给 Agent

def cmd_candidates(args: argparse.Namespace) -> int:
    common.validate_monitor_id(args.monitor_id)
    cfg = common.load_config()
    monitor = get_monitor(cfg, args.monitor_id)
    conn = common.connect_db()
    date = common.validate_date(args.date or common.today_str())

    all_articles = window_articles(conn)
    latest_fetched = latest_run_fetched(conn)
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
            "fetched": latest_fetched or len(all_articles),
            # after_dedup 直接用 articles 表里这个时间窗内的行数（window_articles 已经
            # 按 fetched_at 查过一遍），不再用 runs.new 求和——articles 表本身就是"去重后
            # 落库了什么"的权威来源，不管当天跑了几次 harvest 都不会重复计数。
            "after_dedup": len(all_articles),
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
        # ③④⑤三个语义阶段生成的自由文本（examples[].reason / ai_reasoning / headline /
        # summary / why_relevant / overview）都要用这个语言写——读 prompts/*.md 时按这个
        # 字段判断，不要一律写中文。目前只支持 "zh" / "en"。
        "language": common.normalize_language(cfg.get("language", "zh")),
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
    common.validate_monitor_id(args.monitor_id)
    cfg = common.load_config()
    monitor = get_monitor(cfg, args.monitor_id)
    date = common.validate_date(args.date or common.today_str())
    result = common.read_json(Path(args.input))

    candidates_path = common.work_dir() / f"candidates-{args.monitor_id}-{date}.json"
    if not candidates_path.exists():
        print(f"找不到候选文件：{candidates_path}，请先跑一遍 candidates 子命令。", file=sys.stderr)
        return 1
    candidates_payload = common.read_json(candidates_path)
    candidate_ids = {c["id"] for c in candidates_payload["candidates"]}
    # 跟 cmd_clustered 一样，语言字段跟着上一步写下来的中间产物走，不重新读 config.json，
    # 保持同一次流水线运行里所有阶段语言设置一致。
    language = common.normalize_language(candidates_payload.get("language", "zh"))

    try:
        kept_ids = validate.validate_kept_ids(result.get("kept", []), candidate_ids)
    except validate.ValidationError as e:
        print(f"筛选结果校验失败：{e}\n请检查 {args.input} 里的 kept 字段后重新生成。", file=sys.stderr)
        return 1

    low_confidence_ids: set[str] = set(result.get("low_confidence", [])) & set(kept_ids)
    filter_examples = result.get("examples", [])

    conn = common.connect_db()
    cur = conn.cursor()
    articles = []
    missing_in_db: list[str] = []
    for aid in kept_ids:
        cur.execute("SELECT * FROM articles WHERE id = ?", (aid,))
        row = cur.fetchone()
        if not row:
            # kept_ids 已经校验过是候选 id 的子集，正常不会走到这里；只有
            # candidates 和 filtered 两次调用之间数据被清理（比如触发了过期清理）
            # 这种罕见时序问题才会命中，明确记下来而不是悄悄吞掉。
            missing_in_db.append(aid)
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
                "url": common.safe_article_url(a.get("url")),
                "low_confidence": aid in low_confidence_ids,
            }
        )
    if missing_in_db:
        print(
            f"警告：{len(missing_in_db)} 个通过校验的 id 在数据库里查不到（可能是与 candidates "
            f"之间发生了数据清理），已跳过：{missing_in_db}",
            file=sys.stderr,
        )

    state = common.load_run_state(date)
    _record_handoff_elapsed(state, "filter")
    mstate = state.setdefault("monitors", {}).setdefault(args.monitor_id, {})
    mstate["low_confidence_ids"] = list(low_confidence_ids)
    mstate["filter_examples"] = filter_examples
    # 用实际查到的文章数而不是 len(kept_ids) —— 后者只反映 Agent 声称保留了多少个 id，
    # 前者才是真正会进入下一步聚类的条数。两者理应相等，但只有以实际值为准，才不会在
    # 出现 missing_in_db 这种边缘情况时把漏斗数字变成谎话。
    mstate["after_llm_filter"] = len(articles)
    recompute_aggregate_stats(state)
    common.save_run_state(date, state)

    out_path = common.work_dir() / f"cluster-input-{args.monitor_id}-{date}.json"
    common.write_json(
        out_path,
        {
            "articles": articles,
            "language": language,
            # 后面 cmd_clustered 会继续往下透传给 summarize-input——摘要阶段需要用户的
            # 关注方向来写 why_relevant/overview，不应该让 Agent 在读 prompt 时自己再去
            # 猜"拿不到就读 config.json"，那样等于让脚本以外的地方多了一个隐性数据源。
            "monitor_description": monitor.get("description", ""),
            "instructions": "读取 prompts/cluster.md，对以下条目做跨源同事件归并。",
        },
    )

    common.print_stage_table(state["stats"], active_stage=4)
    print(f"聚类输入已写入：{out_path}")
    payload = {"kept": len(articles), "path": str(out_path), "needs_search_augment": len(articles) == 0}
    if not articles:
        # v2：当前数据池对这个 monitor 一条都没命中，把 description 带出来方便 Agent
        # 直接拟检索词——见 SKILL.md ③筛选一节"补充检索"的说明，只在这里触发一次，
        # 不是每次筛选都建议去调用 search（那样既没必要也浪费额度）。
        payload["monitor_description"] = monitor.get("description", "")
    print(json.dumps(payload, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------
# 接收聚类结果 -> 产出摘要输入

def cmd_clustered(args: argparse.Namespace) -> int:
    common.validate_monitor_id(args.monitor_id)
    date = common.validate_date(args.date or common.today_str())
    result = common.read_json(Path(args.input))
    clusters: list[dict[str, Any]] = result.get("clusters", [])

    cluster_input_path = common.work_dir() / f"cluster-input-{args.monitor_id}-{date}.json"
    if not cluster_input_path.exists():
        print(f"找不到聚类输入：{cluster_input_path}，请先跑一遍 filtered 子命令。", file=sys.stderr)
        return 1
    cluster_input = common.read_json(cluster_input_path)
    articles_by_id = {a["id"]: a for a in cluster_input["articles"]}
    # 语言字段跟着上一步（filtered）写下来的中间产物走，而不是重新读一遍 config.json——
    # 保持"同一次流水线运行里所有阶段用同一份语言设置"，不会因为运行期间用户改了配置
    # 就出现前后阶段语言不一致的情况。monitor_description 同理，原样透传给下一步。
    language = common.normalize_language(cluster_input.get("language", "zh"))
    monitor_description = cluster_input.get("monitor_description", "")

    try:
        validate.validate_clusters(clusters, set(articles_by_id.keys()))
    except validate.ValidationError as e:
        print(f"聚类结果校验失败：{e}\n请检查 {args.input} 后重新生成。", file=sys.stderr)
        return 1

    enriched = []
    for idx, c in enumerate(clusters):
        members = [articles_by_id[i] for i in c.get("ids", [])]  # 已通过校验，全部存在
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
            "language": language,
            "monitor_description": monitor_description,
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
    common.validate_monitor_id(args.monitor_id)
    cfg = common.load_config()
    monitor = get_monitor(cfg, args.monitor_id)
    date = common.validate_date(args.date or common.today_str())
    min_score = monitor.get("min_score", cfg.get("min_score", 6))

    result = common.read_json(Path(args.input))
    overview = result.get("overview", "")
    summaries: list[dict[str, Any]] = result.get("clusters", [])

    summarize_input_path = common.work_dir() / f"summarize-input-{args.monitor_id}-{date}.json"
    if not summarize_input_path.exists():
        print(f"找不到摘要输入：{summarize_input_path}，请先跑一遍 clustered 子命令。", file=sys.stderr)
        return 1
    summarize_input = common.read_json(summarize_input_path)
    clusters_by_index = {c["cluster_index"]: c for c in summarize_input["clusters"]}
    language = common.normalize_language(summarize_input.get("language", "zh"))

    try:
        summaries = validate.validate_summaries(summaries, set(clusters_by_index.keys()))
    except validate.ValidationError as e:
        print(f"摘要结果校验失败：{e}\n请检查 {args.input} 后重新生成。", file=sys.stderr)
        return 1

    state = common.load_run_state(date)
    _record_handoff_elapsed(state, "summarize")
    # 用 setdefault 而不是 get(..., {}) —— 后者拿到的是游离 dict，后面对它的写入
    # （比如 monitor_state["selected"] = ...）不会写回 state["monitors"]，导致
    # recompute_aggregate_stats 统计不到这个 monitor 刚产出的 selected 数字。
    monitor_state = state.setdefault("monitors", {}).setdefault(args.monitor_id, {})
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
                reason = common.t(
                    language,
                    "lead_reason_single",
                    feed_name=feed_name or common.t(language, "single_source_fallback"),
                )
            elif cross_source:
                reason = common.t(
                    language, "lead_reason_cross", count=article_count, sources=source_count, score=score
                )
            else:
                reason = common.t(language, "lead_reason_same_source", count=article_count, score=score)
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
        overview = overview or common.t(language, "overview_empty")

    setup_note = monitor.get("setup_note") or common.t(
        language, "setup_note_default", description=monitor.get("description", "")
    )

    monitor_report = {
        "id": monitor["id"],
        "name": monitor.get("name", monitor["id"]),
        "description": monitor.get("description", ""),
        "overview": overview,
        "clusters": selected_clusters,
        "leads": leads,
        "filter_examples": filter_examples,
        "focus_tags": monitor.get("focus_tags", []),
        "setup_note": setup_note,
        # 该 monitor 独立的漏斗计数（③~⑥），用于 dashboard 里每个 monitor 各自的
        # workflow 展示；①②两阶段是全局值，不存在这里，渲染时从 report.stats 取。
        "stats": {
            "after_prefilter": monitor_state.get("after_prefilter", 0),
            "after_llm_filter": monitor_state.get("after_llm_filter", 0),
            "clusters": monitor_state.get("clusters", 0),
            "selected": len(selected_clusters),
        },
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
            "language": common.normalize_language(cfg.get("language", "zh")),
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
    date = common.validate_date(args.date or common.today_str())
    cfg = common.load_config()
    conn = common.connect_db()
    state = common.load_run_state(date)

    path = common.reports_dir() / f"{date}.json"
    if not path.exists():
        print("没有找到当日报告草稿，无法收尾。请先跑完 candidates/filtered/clustered/summarized。", file=sys.stderr)
        return 1
    report = common.read_json(path)

    # 状态污染修复：只保留当前 config.monitors 里还存在的 monitor 小节。改配置
    # （删除/新增 monitor）之后再当天重跑，之前旧 monitor 留下的小节和 run_state
    # 累加值不会自动清理，finalize 是唯一一个能看到"完整当前配置"的时机，在这里
    # 兜底裁剪，而不是信任中途各阶段的累加状态。
    current_ids = {m["id"] for m in cfg.get("monitors", [])}
    kept_monitors = [m for m in report.get("monitors", []) if m["id"] in current_ids]
    stale_ids = [m["id"] for m in report.get("monitors", []) if m["id"] not in current_ids]
    if stale_ids:
        print(f"警告：已从报告中移除不在当前配置中的 monitor：{stale_ids}", file=sys.stderr)
    report["monitors"] = kept_monitors

    missing_ids = sorted(current_ids - {m["id"] for m in kept_monitors})
    exit_code = 0
    if missing_ids:
        print(f"警告：当前配置的以下 monitor 尚未处理，报告不完整：{missing_ids}", file=sys.stderr)
        exit_code = 1

    alerts = check_recent_failures(conn, common.normalize_language(report.get("language", "zh")))
    # after_prefilter/after_llm_filter/clusters/selected 改成直接对裁剪后的
    # kept_monitors 各自的 stats 求和，而不是信任 run_state 里的累加值——run_state
    # 是"这次进程调用序列里发生过什么"的流水账，不知道 monitor 是否已经被从配置里
    # 删除；report.json 里刚裁剪过的 monitors 列表才是"当前配置下真正该展示什么"
    # 的权威来源。fetched/after_dedup/sources 是①②两阶段的全局值，与 monitor 无关，
    # 仍然从 state["stats"] 取。
    report["stats"] = {
        "fetched": state["stats"].get("fetched", 0),
        "after_dedup": state["stats"].get("after_dedup", 0),
        "after_prefilter": sum(m.get("stats", {}).get("after_prefilter", 0) for m in kept_monitors),
        "after_llm_filter": sum(m.get("stats", {}).get("after_llm_filter", 0) for m in kept_monitors),
        "clusters": sum(m.get("stats", {}).get("clusters", 0) for m in kept_monitors),
        "selected": sum(m.get("stats", {}).get("selected", 0) for m in kept_monitors),
        "sources": state["stats"].get("sources", {}),
        "stage_ms": state.get("stage_ms", {}),
    }
    report["alerts"] = alerts
    common.write_json(path, report)

    state["stats"]["done"] = True
    common.print_stage_table(state["stats"], active_stage=6)
    print(f"报告 JSON 已生成：{path}")
    print(
        json.dumps(
            {"path": str(path), "alerts": alerts, "stale_monitors_removed": stale_ids, "missing_monitors": missing_ids},
            ensure_ascii=False,
        )
    )
    return exit_code


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
