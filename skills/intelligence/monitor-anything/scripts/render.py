#!/usr/bin/env python3
"""render.py —— 阶段⑥渲染：把 reports/<date>.json 渲成当天的独立 HTML 页面。

v3 起改了产出模型，一共三样东西：

- `reports/<date>.html` —— 每天一份自包含的独立报告页，**只含这一天的数据**。分享一份
  报告就是分享这一个文件，不会像旧版那样把过去 30 天的历史和其他 monitor 一起带出去。
- `reports/dates-manifest.js` —— 一份很小的清单文件（`<script src>` 加载的 JS 变量），
  列出磁盘上现存的每个日期、以及每个日期下各 monitor 的名字和精选条数（不含标题/摘要/
  链接）。每次跑这个脚本都会重新生成它，是首页能看到"最新有哪些日期"的唯一途径。
- `reports/dashboard.html` —— 纯静态的首页/索引页，**只在第一次运行时**从
  `assets/dashboard_static.html` 拷贝一次，之后这个脚本再也不会碰它。它在浏览器里靠
  `<script src="dates-manifest.js">` 读取最新清单来展示日期列表，点日期跳转到对应的
  `<date>.html`（普通的 `<a href>` 页面跳转，不是 JS 换数据，不需要服务器也能正常工作）。
  首页本身不含任何一天的报告内容，也没有分享按钮和统计埋点——那些都只在 `<date>.html` 里。

用法：
    python render.py --date 2026-08-05
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import common

# 渠道名字是报告内容的一部分（跟着文章一起展示），所以要跟 language 一起变——不像
# common.py 里那些错误提示/CLI 文案，这些字符串会被读者看到，必须有 zh/en 两份。
SOURCE_LABELS = {
    "zh": {
        "wx": ("微信公众号", "💬"),
        "xhs": ("小红书", "📕"),
        "nytimes": ("纽约时报", "📰"),
        "aihot": ("AI 热点", "🔥"),
        # search.py 补充检索写入的条目，跟数据池里固定渠道的性质不同（不是常驻订阅源，
        # 是当天临时按关键词查回来的），单独给个标签，不跟"未知 source_type 用原始
        # 字符串 + 默认图标兜底"逻辑混在一起。
        "search": ("AI 检索补充", "🔍"),
    },
    "en": {
        "wx": ("WeChat", "💬"),
        "xhs": ("Xiaohongshu", "📕"),
        "nytimes": ("NYTimes", "📰"),
        "aihot": ("AI Hot", "🔥"),
        "search": ("AI Search", "🔍"),
    },
}


def source_label(source_type: str, language: str = "zh") -> tuple[str, str]:
    table = SOURCE_LABELS.get(common.normalize_language(language), SOURCE_LABELS["zh"])
    return table.get(source_type, (source_type, "📡"))


# --------------------------------------------------------------------------
# 当天独立报告页

def render_day_html(date: str, report: dict[str, Any]) -> str:
    template_path = common.skill_root() / "assets" / "template.html"
    template = template_path.read_text(encoding="utf-8")

    language = common.normalize_language(report.get("language", "zh"))
    labels = SOURCE_LABELS.get(language, SOURCE_LABELS["zh"])
    payload = {
        "date": date,
        "report": report,
        "source_labels": {k: {"name": v[0], "icon": v[1]} for k, v in labels.items()},
    }
    data_json = json.dumps(payload, ensure_ascii=False)
    # JSON 里的内容来自抓取到的原文标题/正文，可能包含 "</script>" 甚至构造出新的
    # <script> 注入。不做字符串匹配（大小写变体会漏网），直接把尖括号转成 \uXXXX
    # 转义——尖括号在文本里不存在了，浏览器就没有机会把它解析成标签。同时转义
    # U+2028/U+2029（JS 字符串里的非法换行符，JSON.parse 能处理，作为额外防御一并处理）。
    data_json = (
        data_json.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )
    return template.replace("/*__REPORT_DATA_JSON__*/{}", data_json)


# --------------------------------------------------------------------------
# 首页用的日期清单（不含标题/摘要/链接，只有 monitor 名字和精选条数）

def collect_dates_manifest() -> list[dict[str, Any]]:
    """扫描磁盘上现存的 reports/*.json（谁被 harvest.py 的 purge_expired 删了、谁就不在
    清单里，不需要在这里重复判断保留期）。"""
    entries = []
    for f in sorted(common.reports_dir().glob("*.json"), reverse=True):
        try:
            data = common.read_json(f)
        except (json.JSONDecodeError, OSError):
            continue
        date = data.get("date") or f.stem
        monitors = [
            {
                "name": m.get("name") or m.get("id", ""),
                "selected": (m.get("stats") or {}).get("selected", 0),
            }
            for m in data.get("monitors", [])
        ]
        entries.append({"date": date, "monitors": monitors})
    entries.sort(key=lambda e: e["date"], reverse=True)
    return entries


def render_manifest_js(entries: list[dict[str, Any]], language: str) -> str:
    # language 放在这里而不是每个 entry 里——首页（dashboard_static.html）自己的界面文案
    # （"历史日期"之类）只有一份，用当前 config.json 的语言渲染；不是想给"某一天用中文
    # 生成、某一天用英文生成"这种历史混用场景做精确还原，没有必要。
    payload = {"language": common.normalize_language(language), "dates": entries}
    data_json = json.dumps(payload, ensure_ascii=False)
    return (
        "// 由 render.py 自动生成并覆盖——不要手改，改了也会在下次运行时被覆盖掉。\n"
        f"var MONITOR_ANYTHING_MANIFEST = {data_json};\n"
    )


# --------------------------------------------------------------------------
# 首页：只拷贝一次，之后不再触碰

def ensure_dashboard_shell() -> Path:
    dst = common.reports_dir() / "dashboard.html"
    if not dst.exists():
        src = common.skill_root() / "assets" / "dashboard_static.html"
        shutil.copyfile(src, dst)
    return dst


# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="渲染当天的独立报告页 + 更新首页日期清单")
    parser.add_argument("--date")
    args = parser.parse_args()

    date = common.validate_date(args.date or common.today_str())
    report_path = common.reports_dir() / f"{date}.json"
    if not report_path.exists():
        print(f"找不到 {report_path}，请先跑完 report.py 的 candidates/.../finalize。", file=sys.stderr)
        return 1
    report = common.read_json(report_path)

    day_html = render_day_html(date, report)
    day_path = common.reports_dir() / f"{date}.html"
    day_path.write_text(day_html, encoding="utf-8")

    cfg = common.load_config()
    manifest_js = render_manifest_js(collect_dates_manifest(), cfg.get("language", "zh"))
    (common.reports_dir() / "dates-manifest.js").write_text(manifest_js, encoding="utf-8")

    dashboard_path = ensure_dashboard_shell()

    print(json.dumps({"html": str(day_path), "dashboard": str(dashboard_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
