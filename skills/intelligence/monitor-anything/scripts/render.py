#!/usr/bin/env python3
"""render.py —— 阶段⑥渲染：把 reports/<date>.json 渲成当天的独立 HTML 页面。

v3 起改了产出模型，一共三样东西：

- `reports/<date>.html` —— 每天一份自包含的独立报告页，**只含这一天的数据**。分享一份
  报告就是分享这一个文件，不会像旧版那样把过去 30 天的历史和其他 monitor 一起带出去。
- `reports/dates-manifest.js` —— 一份很小的清单文件（`<script src>` 加载的 JS 变量），
  列出磁盘上现存的每个日期、以及每个日期下各 monitor 的名字和精选条数（不含标题/摘要/
  链接）。每次跑这个脚本都会重新生成它，是首页能看到"最新有哪些日期"的唯一途径。
- `reports/dashboard.html` —— 纯静态的首页/索引页，每次运行都从
  `assets/dashboard_static.html` 覆盖一次（这个文件里没有任何用户数据，覆盖是安全的，
  也是老用户能拿到模板修复的唯一途径）。它在浏览器里靠
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

# 首页卡片上那段摘要的长度上限。截断放在这里而不是只靠 CSS 的 line-clamp：清单文件是
# 每次打开首页都要下载解析的，把 30 天 × N 个 monitor 的完整 overview 全塞进去，文件会
# 涨到没必要的大小，而卡片上本来也只显示这么多。CSS 那边仍然有 line-clamp 兜底排版。
MANIFEST_OVERVIEW_LIMIT = 96


def _clip(text: str, limit: int = MANIFEST_OVERVIEW_LIMIT) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def collect_dates_manifest() -> list[dict[str, Any]]:
    """扫描磁盘上现存的 reports/*.json（谁被 harvest.py 的 purge_expired 删了、谁就不在
    清单里，不需要在这里重复判断保留期）。

    每个 monitor 除了名字和精选条数，还带一段截断过的 `overview`（当天那句"今天 XX
    领域最大的动态是……"）和跨源头条数 `cross`——首页卡片要靠它们让用户在点进去之前就
    知道那天大概发生了什么。这**打破了 v3"首页不内嵌任何报告正文"的原则**，是有意为之，
    理由和代价见 ARCHITECTURE.md §22。标题、正文、原文链接依然不在清单里。
    """
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
                "overview": _clip(m.get("overview") or ""),
                "cross": len([c for c in (m.get("clusters") or []) if c.get("cross_source")]),
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


def write_manifest(language: str | None = None) -> Path:
    """重新扫描磁盘、覆盖写 dates-manifest.js。

    抽成独立函数是因为除了这里，`harvest.py` 的过期清理删掉旧报告之后也必须调用它：
    首页展示哪些日期完全由这份清单决定，删了 JSON/HTML 却不更新清单，首页就会继续
    列出那些日期，点进去是 404 死链。
    """
    if language is None:
        language = common.load_config().get("language", "zh")
    path = common.reports_dir() / "dates-manifest.js"
    path.write_text(render_manifest_js(collect_dates_manifest(), language), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# 首页：每次渲染都用 assets/ 里的最新版覆盖

def ensure_dashboard_shell() -> Path:
    """dashboard.html 是纯静态外壳，**不含任何用户数据**——它的全部内容来自
    `assets/dashboard_static.html`，日期列表是运行时从 dates-manifest.js 读的。

    以前这里是"不存在才拷贝"，结果是老用户的首页永远停留在安装当天那一版：Skill 升级
    修好的模板 bug（哪怕是首页本身的显示错误）对他们完全不生效，而且没有任何办法察觉。
    既然这个文件里没有任何需要保留的东西，每次直接覆盖是最简单也最正确的做法。
    """
    dst = common.reports_dir() / "dashboard.html"
    src = common.skill_root() / "assets" / "dashboard_static.html"
    shutil.copyfile(src, dst)
    return dst


# --------------------------------------------------------------------------
# 交付块：把"这次跑完用户应该拿到什么"从 SKILL.md 的散文里挪进脚本的确定性输出

def print_delivery_block(date: str, day_path: Path, dashboard_path: Path) -> None:
    """在 JSON 之后再打印一段人话交付清单。

    只打 JSON 的时候，历史目录 `dashboard.html` 是 JSON 里一个不起眼的字段，Agent 大概率
    只把当天报告和分享链接交给用户，首页要等用户主动问才出现——而首页恰恰是用户第二天
    回来看历史的唯一入口。SKILL.md 里当然也写了这件事，但散文约束是概率性的，脚本输出
    不是：Agent 会照原样把终端输出展示给用户，所以把交付契约放在这里最稳。
    """
    language = common.load_config().get("language", "zh")
    if language == "en":
        lines = [
            "",
            "── Deliverables ──",
            f"  Today's report : {day_path}",
            f"  Report index   : {dashboard_path}  <- bookmark this, updates itself daily",
            "  (open/present both files to the user, don't just paste the paths)",
        ]
    else:
        lines = [
            "",
            "── 本次交付 ──",
            f"  今日报告：{day_path}",
            f"  历史目录：{dashboard_path}  ← 建议收藏，每天自动更新",
            "  （把这两个文件打开/呈现给用户，不要只贴路径）",
        ]
    print("\n".join(lines))


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

    write_manifest()
    dashboard_path = ensure_dashboard_shell()

    print(json.dumps({"html": str(day_path), "dashboard": str(dashboard_path)}, ensure_ascii=False))
    print_delivery_block(date, day_path, dashboard_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
