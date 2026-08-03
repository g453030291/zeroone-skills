#!/usr/bin/env python3
"""render.py —— 阶段⑥渲染：把 reports/<date>.json 渲成 Markdown 与单文件 HTML dashboard。

所有扩展输出（HTML / MD / outputs/ 下的 email、webhook）都只消费 reports/<date>.json，
不重新调用 LLM —— JSON 是唯一的权威产物，渲染是纯确定性的格式转换。

用法：
    python render.py --date 2026-08-01                # 生成当天的 md + dashboard.html
    python render.py --date 2026-08-01 --sample        # 标记为示例报告，HTML 顶部会显著提示
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import common

TIMELINE_DAYS = 7

SOURCE_LABELS = {
    "wx": ("微信公众号", "💬"),
    "xhs": ("小红书", "📕"),
    "nytimes": ("纽约时报", "📰"),
    "aihot": ("AI 热点", "🔥"),
}


def source_label(source_type: str) -> tuple[str, str]:
    return SOURCE_LABELS.get(source_type, (source_type, "📡"))


# --------------------------------------------------------------------------
# Markdown

def render_markdown(report: dict[str, Any]) -> str:
    lines = [f"# {report['date']} 日报", ""]
    if report.get("alerts"):
        for a in report["alerts"]:
            lines.append(f"> ⚠️ {a}")
        lines.append("")

    stats = report.get("stats", {})
    lines.append(
        f"采集 {stats.get('fetched', 0)} 篇 · 保留 {stats.get('after_dedup', 0)} 篇 · "
        f"命中 {stats.get('after_llm_filter', 0)} 篇 · 精选 {stats.get('selected', 0)} 条"
    )
    lines.append("")

    for m in report.get("monitors", []):
        lines.append(f"## {m['name']}")
        lines.append("")
        lines.append(m.get("overview", ""))
        lines.append("")
        if m.get("clusters"):
            lines.append("### 精选")
            lines.append("")
            for c in m["clusters"]:
                tag = "跨源" if c.get("cross_source") else "单源"
                lines.append(f"#### {c['headline']}（相关度 {c.get('score', '-')}/10 · {tag}）")
                lines.append("")
                lines.append(c.get("summary", ""))
                lines.append("")
                lines.append(f"*为什么与你相关*：{c.get('why_relevant', '')}")
                lines.append("")
                for a in c.get("articles", []):
                    name, icon = source_label(a["source_type"])
                    lines.append(f"- {icon} [{a['title']}]({a['url']}) —— {a.get('feed_name', name)}")
                lines.append("")
        if m.get("leads"):
            lines.append("### 线索区（未达精选阈值）")
            lines.append("")
            for lead in m["leads"]:
                name, icon = source_label(lead.get("source_type", ""))
                mark = "（低置信度）" if lead.get("low_confidence") else ""
                reason = f" —— {lead['reason']}" if lead.get("reason") else ""
                if lead.get("url"):
                    lines.append(f"- {icon} [{lead['title']}]({lead['url']}){mark}{reason}")
                else:
                    lines.append(f"- {icon} {lead['title']}{mark}{reason}")
            lines.append("")

    lines.append("---")
    lines.append(f"生成时间：{report.get('generated_at', '')} · Powered by 零一实验室 · https://lingyilabs.com/")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 最近 N 天时间轴 + 可切换的历史报告

def load_recent_reports(current_date: str, days: int = TIMELINE_DAYS) -> list[dict[str, Any]]:
    reports = []
    for f in sorted(common.reports_dir().glob("*.json"), reverse=True):
        if f.stem in ("dashboard",):
            continue
        try:
            data = common.read_json(f)
        except (json.JSONDecodeError, OSError):
            continue
        reports.append(data)
        if len(reports) >= days:
            break
    reports.sort(key=lambda r: r.get("date", ""), reverse=True)
    return reports


# --------------------------------------------------------------------------
# 「数据资产」视角：跟当天漏斗数字是两套口径——这里统计的是当前 monitor.db 里
# 全量留存的语料（受 retention.articles_days 约束的滚动窗口），用来回答
# 「这个系统这些天一共为我攒了多少东西」，而不是「今天处理了多少」。

def build_asset_stats(conn) -> dict[str, Any]:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), COUNT(DISTINCT feed_name), COALESCE(SUM(content_len),0) FROM articles")
    total, channels, content_len = cur.fetchone()
    return {
        "total_stored": total or 0,
        "channels": channels or 0,
        "content_chars": content_len or 0,
        "content_wan": round((content_len or 0) / 10000, 1),
    }


def build_ingest_runs(conn, limit: int = 8) -> list[dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        "SELECT run_at, fetched, new, status, message FROM runs ORDER BY run_at DESC LIMIT ?", (limit,)
    )
    return [dict(zip(("run_at", "fetched", "new", "status", "message"), row)) for row in cur.fetchall()]


def build_timeline(recent_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timeline = [
        {
            "date": r.get("date", ""),
            "fetched": r.get("stats", {}).get("fetched", 0),
            "selected": r.get("stats", {}).get("selected", 0),
        }
        for r in recent_reports
    ]
    timeline.sort(key=lambda t: t["date"])
    return timeline


# --------------------------------------------------------------------------
# HTML

def render_html(current_date: str, recent_reports: list[dict[str, Any]], is_sample: bool) -> str:
    template_path = common.skill_root() / "assets" / "template.html"
    template = template_path.read_text(encoding="utf-8")

    conn = common.connect_db()
    asset_stats = build_asset_stats(conn)
    ingest_runs = build_ingest_runs(conn)
    conn.close()

    reports_by_date = {r["date"]: r for r in recent_reports}
    payload = {
        "current_date": current_date,
        "dates": [r["date"] for r in recent_reports],
        "reports": reports_by_date,
        "timeline": build_timeline(recent_reports),
        "assets": asset_stats,
        "ingest_runs": ingest_runs,
        "is_sample": is_sample,
        "source_labels": {k: {"name": v[0], "icon": v[1]} for k, v in SOURCE_LABELS.items()},
    }
    data_json = json.dumps(payload, ensure_ascii=False)
    # JSON 里可能出现 "</script>"（比如正文摘要中恰好包含这个子串），必须转义，
    # 否则会提前截断内嵌的 <script> 标签。
    data_json = data_json.replace("</script>", "<\\/script>")
    return template.replace("/*__REPORT_DATA_JSON__*/{}", data_json)


# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="渲染 Markdown 与 HTML dashboard")
    parser.add_argument("--date")
    parser.add_argument("--sample", action="store_true", help="标记为示例报告（HTML 顶部会提示）")
    args = parser.parse_args()

    date = args.date or common.today_str()
    report_path = common.reports_dir() / f"{date}.json"
    if not report_path.exists():
        print(f"找不到 {report_path}，请先跑完 report.py 的 candidates/.../finalize。", file=sys.stderr)
        return 1
    report = common.read_json(report_path)

    md = render_markdown(report)
    md_path = common.reports_dir() / f"{date}.md"
    md_path.write_text(md, encoding="utf-8")

    recent = load_recent_reports(date)
    html = render_html(date, recent, is_sample=args.sample)
    html_path = common.reports_dir() / "dashboard.html"
    html_path.write_text(html, encoding="utf-8")

    print(json.dumps({"md": str(md_path), "html": str(html_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
