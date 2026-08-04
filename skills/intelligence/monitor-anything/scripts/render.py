#!/usr/bin/env python3
"""render.py —— 阶段⑥渲染：把 reports/<date>.json 渲成单文件 HTML dashboard。

v2 起产出路径只有 HTML 这一条（不再生成 Markdown——没有任何展示或引用它的地方，纯粹是
从没被用起来的产物，见 ARCHITECTURE.md）。dashboard.html 完全由 reports/<date>.json
（当天）+ 最近若干天的 reports/*.json（历史）+ assets/template.html（静态模板）三样东西
渲染而成，不重新调用 LLM、不连接 monitor.db——JSON 才是唯一的权威产物，渲染是纯确定性的
格式转换，可以随时重新跑一遍而不丢失任何信息。

用法：
    python render.py --date 2026-08-01                # 生成当天的 dashboard.html
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import common

# dashboard 里日期切换器可回看的天数，v2 起不再单独写死一个数字，而是直接跟磁盘上报告
# 文件的保留期（config.json 的 retention.reports_days，默认 30 天）统一——磁盘上留多久，
# dashboard 里就能翻多久，不再出现"文件还在但翻不到"的情况。这里的 30 只是没有 config.json
# 时的兜底默认值，实际取值见 main() 里对 cfg["retention"]["reports_days"] 的读取。
DEFAULT_HISTORY_DAYS = 30

SOURCE_LABELS = {
    "wx": ("微信公众号", "💬"),
    "xhs": ("小红书", "📕"),
    "nytimes": ("纽约时报", "📰"),
    "aihot": ("AI 热点", "🔥"),
    # v2 新增：search.py 补充检索写入的条目，跟数据池里固定渠道的性质不同（不是常驻
    # 订阅源，是当天临时按关键词查回来的），单独给个标签，不跟 render.py 的
    # "未知 source_type 用原始字符串 + 默认图标兜底" 逻辑混在一起。
    "search": ("AI 检索补充", "🔍"),
}


def source_label(source_type: str) -> tuple[str, str]:
    return SOURCE_LABELS.get(source_type, (source_type, "📡"))


# --------------------------------------------------------------------------
# 最近 N 天的历史报告（dashboard 日期切换器可回看的范围）

def load_recent_reports(current_date: str, days: int = DEFAULT_HISTORY_DAYS) -> list[dict[str, Any]]:
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
# HTML
#
# 新版 dashboard（§对齐 Claude Design 交付稿）改成逐 monitor 切换 tab 展示，
# 不再有「数据资产总览」「7天采集趋势图」这两个全局区块，所以这里不需要连接
# monitor.db——reports/<date>.json 已经是渲染所需的唯一权威产物（见
# ARCHITECTURE.md §7）。

def render_html(current_date: str, recent_reports: list[dict[str, Any]]) -> str:
    template_path = common.skill_root() / "assets" / "template.html"
    template = template_path.read_text(encoding="utf-8")

    reports_by_date = {r["date"]: r for r in recent_reports}
    payload = {
        "current_date": current_date,
        "dates": [r["date"] for r in recent_reports],
        "reports": reports_by_date,
        "source_labels": {k: {"name": v[0], "icon": v[1]} for k, v in SOURCE_LABELS.items()},
    }
    data_json = json.dumps(payload, ensure_ascii=False)
    # JSON 里可能出现 "</script>"（比如正文摘要中恰好包含这个子串），必须转义，
    # 否则会提前截断内嵌的 <script> 标签。
    data_json = data_json.replace("</script>", "<\\/script>")
    return template.replace("/*__REPORT_DATA_JSON__*/{}", data_json)


# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="渲染单文件 HTML dashboard")
    parser.add_argument("--date")
    args = parser.parse_args()

    date = args.date or common.today_str()
    report_path = common.reports_dir() / f"{date}.json"
    if not report_path.exists():
        print(f"找不到 {report_path}，请先跑完 report.py 的 candidates/.../finalize。", file=sys.stderr)
        return 1

    cfg = common.load_config()
    history_days = cfg.get("retention", {}).get("reports_days", DEFAULT_HISTORY_DAYS)
    recent = load_recent_reports(date, days=history_days)
    html = render_html(date, recent)
    html_path = common.reports_dir() / "dashboard.html"
    html_path.write_text(html, encoding="utf-8")

    print(json.dumps({"html": str(html_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
