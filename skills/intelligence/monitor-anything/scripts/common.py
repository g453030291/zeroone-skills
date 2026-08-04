"""共享工具模块 —— 供 harvest.py / report.py / render.py / setup.py / schedule.py 复用。

设计原则（详见 ARCHITECTURE.md）：
- 零第三方依赖，只用标准库
- 本模块不做任何网络请求或 LLM 调用，只负责路径、配置、数据库、文本清洗与终端展示
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Optional

# --------------------------------------------------------------------------
# 路径

def skill_root() -> Path:
    """返回 monitor-anything/ 根目录（scripts/ 的上一级）。"""
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    d = skill_root() / "data"
    (d / "reports").mkdir(parents=True, exist_ok=True)
    (d / ".work").mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return data_dir() / "config.json"


def db_path() -> Path:
    return data_dir() / "monitor.db"


def reports_dir() -> Path:
    return data_dir() / "reports"


def work_dir() -> Path:
    """存放 Agent 与脚本之间交换中间结果的临时 JSON（筛选/聚类/摘要产物）。"""
    return data_dir() / ".work"


# .work/ 里的中间产物只在当天报告生成过程中有用（candidates / filter-result /
# cluster-result / summary-result / state-<date> 等），不需要跟着 articles_days /
# reports_days 留那么久。固定给 3 天余量（够跨天重试、时区边界），由 harvest.py 的
# purge_expired() 按这个天数清理，不做成用户可配置项。
WORK_RETENTION_DAYS = 3


# --------------------------------------------------------------------------
# 配置

# API 域名统一在这里配置一处（v2：从测试期的裸 IP 切到正式域名，见 ARCHITECTURE.md
# “为什么域名只在这里出现一次”）。真正请求用的地址都从它派生，不要在别处拼字符串。
API_HOST = "https://api.lingyilabs.com"
TEMP_TOKEN_URL = f"{API_HOST}/api/data/articles/temporary-token"
SHARE_HTML_URL = f"{API_HOST}/api/data/articles/share/html"
SEARCH_URL = f"{API_HOST}/api/data/articles/search"

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "_warning": "此文件包含访问凭证，请勿提交到公开仓库",
    "api": {
        "base_url": f"{API_HOST}/api/data/articles",
        "token": "",
        # v2 新增：临时 token 由 setup.py 自动申请，type 恒为 "temporary"；
        # 用户手动通过 set-token 写入的正式 token，type 会被置为 "manual"。
        "token_type": "",
        "expires_at": "",
    },
    "language": "zh",
    "monitors": [],
    # v2：不再有可插拔的 outputs 数组（email / webhook 扩展层已移除，见 ARCHITECTURE.md），
    # 产出路径固定为 html（render.py 无条件生成，不再生成 Markdown）+ 按需触发的 HTML 分享（share.py）。
    "retention": {"articles_days": 30, "reports_days": 30},
    "report_time": "08:00",
    "min_score": 6,
}


def load_config() -> dict[str, Any]:
    """读取 config.json；不存在时返回默认骨架（不落盘，由 setup.py 负责写入）。"""
    path = config_path()
    if not path.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # 兼容旧配置缺字段的情况（包括嵌套在 api 下的 token_type / expires_at，
    # 老版本 config.json 里没有这两个字段，顶层 setdefault 覆盖不到嵌套 dict）
    for key, value in DEFAULT_CONFIG.items():
        cfg.setdefault(key, value)
    for key, value in DEFAULT_CONFIG["api"].items():
        cfg["api"].setdefault(key, value)
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def get_token(cfg: dict[str, Any]) -> str:
    """token 优先从环境变量 MONITOR_API_TOKEN 读取，config.json 兜底。"""
    return os.environ.get("MONITOR_API_TOKEN") or cfg.get("api", {}).get("token", "") or ""


TOKEN_HELP_EMAIL = "gems9232@foxmail.com"


def token_expiry_note(cfg: dict[str, Any]) -> str:
    """v2 新增：临时 token 默认 30 天过期。距过期 ≤5 天或已过期时返回一句人话提示，
    正常情况下返回空字符串（调用方据此决定要不要在输出里附带这句话）。

    到期后的路径是明确的 SOP——邮件联系 TOKEN_HELP_EMAIL 申请延长，而不是脚本自己
    再调一次 temporary-token 接口——那个接口是给全新用户免排队试用的，不是续期入口。
    """
    expires_at = (cfg.get("api", {}) or {}).get("expires_at", "")
    if not expires_at:
        return ""
    try:
        from datetime import datetime

        expiry = datetime.fromisoformat(expires_at)
        now = datetime.now(expiry.tzinfo) if expiry.tzinfo else datetime.now()
        days_left = (expiry - now).total_seconds() / 86400
    except (ValueError, TypeError):
        return ""
    day_str = expires_at[:10]
    if days_left < 0:
        return f"Token 已于 {day_str} 过期，如需继续使用请邮件联系 {TOKEN_HELP_EMAIL} 申请延长有效期。"
    if days_left <= 5:
        return f"Token 将于 {day_str} 过期（还剩约 {days_left:.1f} 天），如需继续使用可以提前邮件联系 {TOKEN_HELP_EMAIL} 申请延长。"
    return ""


# --------------------------------------------------------------------------
# 数据库

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id           TEXT PRIMARY KEY,
    url_hash     TEXT,
    source_type  TEXT NOT NULL,
    feed_name    TEXT,
    title        TEXT NOT NULL,
    url          TEXT,
    description  TEXT,
    publish_time TEXT,
    content      TEXT,
    content_len  INTEGER,
    low_quality  INTEGER DEFAULT 0,
    fetched_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fetched  ON articles(fetched_at);
CREATE INDEX IF NOT EXISTS idx_url_hash ON articles(url_hash);

CREATE TABLE IF NOT EXISTS runs (
    run_at   TEXT PRIMARY KEY,
    fetched  INTEGER,
    new      INTEGER,
    status   TEXT,
    message  TEXT
);
"""
# 注：low_quality 是在标准 §5.2 schema 之上新增的一列，用于承载 §6-③ 的“噪音降权”
# 标记（harvest.py 判定，report.py 在筛选阶段读取）。详见 ARCHITECTURE.md “为什么加这一列”。


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.executescript(SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn


def check_data_not_tracked_by_git() -> None:
    """harvest.py 启动时的安全检查：确保 data/ 没有被 git 跟踪，防止全文内容被误提交。"""
    root = skill_root()
    git_dir = None
    cur = root
    for _ in range(6):
        if (cur / ".git").exists():
            git_dir = cur
            break
        if cur.parent == cur:
            break
        cur = cur.parent
    if git_dir is None:
        return  # 不在任何 git 仓库中，无需检查
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", str(git_dir), "ls-files", "--error-unmatch", str(data_dir())],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return  # 没有 git 可执行文件等异常情况，不阻断主流程
    if out.returncode == 0 and out.stdout.strip():
        print(
            "检测到 data/ 目录已被 git 跟踪。data/ 存放抓取到的公众号、纽约时报等全文内容，"
            "误提交到公开仓库存在版权风险。\n"
            "请先执行：git rm -r --cached data/ 并确认 .gitignore 中包含 data/，再重新运行。",
            file=sys.stderr,
        )
        sys.exit(1)


# --------------------------------------------------------------------------
# 文本清洗

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t ]{2,}")
_BLANKLINES_RE = re.compile(r"\n{3,}")
_ZERO_WIDTH_RE = re.compile(r"[​‌‍﻿]")

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "chksm", "scene", "from", "srcid",
}


def clean_text(text: Optional[str]) -> str:
    """去 HTML 标签、折叠连续空白、去零宽字符。"""
    if not text:
        return ""
    text = _TAG_RE.sub("", text)
    text = _ZERO_WIDTH_RE.sub("", text)
    text = unicodedata.normalize("NFKC", text)
    text = _WS_RE.sub(" ", text)
    text = _BLANKLINES_RE.sub("\n\n", text)
    return text.strip()


def normalize_url(url: Optional[str]) -> str:
    """剥离追踪参数（utm_*、chksm、scene、from、srcid、sharer_* 等），用于二次去重哈希。"""
    if not url:
        return ""
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

    parts = urlsplit(url)
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k not in TRACKING_PARAMS and not k.startswith("sharer_")
    ]
    kept.sort()
    new_query = urlencode(kept)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, ""))


def url_hash(url: Optional[str]) -> str:
    normalized = normalize_url(url)
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def normalize_publish_time(raw: Optional[str]) -> str:
    """把上游 'YYYY-MM-DD HH:MM:SS'（默认北京时间）规范化为 ISO 8601 +08:00。

    解析失败时原样返回，避免因个别脏数据中断整批入库。
    """
    if not raw:
        return ""
    raw = raw.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            t = time.strptime(raw, fmt)
            return time.strftime("%Y-%m-%dT%H:%M:%S+08:00", t)
        except ValueError:
            continue
    return raw


DEFAULT_NOISE_TITLE_WORDS = [
    "招聘", "报名", "限时优惠", "优惠券", "扫码进群", "加群", "内推",
    "投递简历", "点击进群", "拼团", "秒杀",
]


def is_noise(title: str, description: str, content: str) -> bool:
    """§6-② 噪音判定：标题命中硬排除词，或正文过短且无摘要兜底。"""
    title = title or ""
    for w in DEFAULT_NOISE_TITLE_WORDS:
        if w in title:
            return True
    content_len = len(content or "")
    if content_len < 20 and not (description or "").strip():
        return True
    return False


# --------------------------------------------------------------------------
# 终端进度展示（§8）

STAGE_NAMES = ["采集", "清洗", "筛选", "聚类", "摘要", "生成报告"]


def supports_ansi() -> bool:
    if os.environ.get("MONITOR_NO_ANSI"):
        return False
    return sys.stdout.isatty()


def render_stage_table(
    stats: dict[str, Any],
    active_stage: int,
    detail: str = "",
) -> str:
    """渲染六阶段进度表（纯文本，调用方决定是否配合 \\r / ANSI 使用）。

    active_stage: 1~6，当前所处阶段；小于该阶段视为已完成（满格），大于则为待处理（空格）。
    stats 中可能包含：fetched, sources(dict), after_dedup, after_prefilter,
    after_llm_filter, clusters, selected
    """
    lines = []
    bar_width = 16
    for i, name in enumerate(STAGE_NAMES, start=1):
        if i < active_stage:
            filled = bar_width
        elif i == active_stage:
            filled = bar_width if detail == "" else int(bar_width * 0.75)
        else:
            filled = 0
        bar = "█" * filled + "░" * (bar_width - filled)
        label = f"  {chr(0x2460 + i - 1)}  {name:<6} {bar}"
        suffix = ""
        if i == 1 and "fetched" in stats:
            n_sources = len(stats.get("sources", {}))
            suffix = f"  {stats['fetched']} 篇  ·  {n_sources} 个渠道"
        elif i == 2 and "after_dedup" in stats:
            dedup = stats.get("fetched", 0) - stats.get("after_dedup", 0)
            suffix = f"  {stats.get('fetched', 0)} → {stats['after_dedup']}   去重 {dedup}"
        elif i == 3 and "after_llm_filter" in stats:
            suffix = f"  {stats.get('after_prefilter', '?')} → {stats['after_llm_filter']}"
        elif i == 3 and i == active_stage and detail:
            suffix = f"  {detail}"
        elif i == 4 and "clusters" in stats:
            suffix = f"  {stats['clusters']} 组聚类"
        elif i == 5 and "selected" in stats:
            suffix = f"  精选 {stats['selected']} 条"
        elif i == 6 and stats.get("done"):
            suffix = "  完成"
        lines.append(label + suffix)
    return "\n".join(lines)


def print_stage_table(stats: dict[str, Any], active_stage: int, detail: str = "") -> None:
    table = render_stage_table(stats, active_stage, detail)
    if supports_ansi():
        # 每次整表重绘。report.py 的③~⑥各阶段分属不同进程调用（因为中间要等
        # Agent 完成语义推理），无法在同一进程内做光标复位的连续动画，
        # 因此退化为“每次调用重打一张最新的表”，在终端里表现为表格随进度
        # 不断刷新覆盖（见 ARCHITECTURE.md）。harvest.py 由于全程单进程，
        # 才能做到真正的原地刷新。
        sys.stdout.write("\n" + table + "\n")
        sys.stdout.flush()
    else:
        for line in table.split("\n"):
            print(line.strip())


def print_inplace(line: str) -> None:
    """单行原地刷新，供 harvest.py 在同一进程内展示①②阶段实时数字。"""
    if supports_ansi():
        sys.stdout.write("\r" + line + " " * 8)
        sys.stdout.flush()
    else:
        print(line)


# --------------------------------------------------------------------------
# 运行态（stage_ms 计时 + 累计统计），供 report.py 跨进程调用间传递

def load_run_state(date: str) -> dict[str, Any]:
    path = work_dir() / f"state-{date}.json"
    if not path.exists():
        return {"date": date, "stats": {}, "stage_ms": {}, "monitors": {}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_run_state(date: str, state: dict[str, Any]) -> None:
    path = work_dir() / f"state-{date}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def record_stage_ms(date: str, stage: str, ms: int) -> None:
    """记录某阶段耗时（毫秒），累加进当天的 run_state，最终会被 report.py finalize
    写入 reports/<date>.json 的 stats.stage_ms。harvest.py 用它记录 fetch/clean 两个
    纯脚本阶段；report.py 的 filter/cluster/summarize 三个阶段耗时记录方式见其自身注释。
    """
    state = load_run_state(date)
    state.setdefault("stage_ms", {})[stage] = ms
    save_run_state(date, state)


def today_str() -> str:
    return time.strftime("%Y-%m-%d")


def now_iso_local() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+08:00")


def read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
