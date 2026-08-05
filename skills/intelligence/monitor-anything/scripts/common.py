"""共享工具模块 —— 供 harvest.py / report.py / render.py / setup.py 复用。

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# --------------------------------------------------------------------------
# 输入校验（防路径穿越）
#
# monitor_id 会被直接拼进 .work/ 下的中间产物文件名（candidates-<id>-<date>.json 等），
# date 同理。如果不做白名单校验，像 "../../../pwned" 这样的值会让拼接出的路径跳出
# .work/ 目录，把文件写到仓库里任意可写的地方。这里用白名单而非黑名单——只允许安全
# 字符通过，而不是尝试列举要拦截哪些危险字符。

_MONITOR_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_monitor_id(monitor_id: str) -> str:
    if not monitor_id or not _MONITOR_ID_RE.match(monitor_id):
        print(
            f"非法的 monitor id：{monitor_id!r}（只允许字母、数字、下划线、短横线，1~64 个字符）",
            file=sys.stderr,
        )
        sys.exit(1)
    return monitor_id


def validate_date(date: str) -> str:
    """既校验外形（YYYY-MM-DD，防路径穿越），也校验它是不是一个真实存在的日期。

    只做正则匹配是不够的——`2026-99-99` 外形完全合法，会一路带着往下走，最终产出
    一份日期本身就不存在的 reports/2026-99-99.json，而且一切都"成功"了没有任何提示。
    这里用 strptime 再过一道，把不存在的日期（含 2 月 30 日、非闰年的 2 月 29 日）
    在入口处就拦掉。
    """
    if not date or not _DATE_RE.match(date):
        print(f"非法的日期：{date!r}（格式应为 YYYY-MM-DD）", file=sys.stderr)
        sys.exit(1)
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        print(f"不存在的日期：{date!r}（外形合法，但这一天并不存在）", file=sys.stderr)
        sys.exit(1)
    return date


def new_run_id() -> str:
    """给"一个 monitor 的一轮③~⑥流水线"生成的唯一标识。

    存在的理由：同一天可以重复跑（改了配置、上午跑失败了下午重来、补充检索后重跑
    candidates）。没有这个标识时，`finalize` 只能看到"报告里有没有这个 monitor 的
    小节"，无法分辨那一节是本轮刚生成的，还是昨天/上午那一轮留下来的——于是
    "只跑 candidates 就直接 finalize" 这种明显没跑完的情况也会返回 0，把陈旧报告当
    成功收尾。加上 run_id 之后，finalize 要求每个 monitor 小节的 run_id 与本轮
    candidates 写下的那个完全一致，对不上就是没跑完。

    用时间戳 + 随机后缀而不是纯随机：出问题时肉眼就能看出这一轮是什么时候开始的。
    """
    return f"{time.strftime('%Y%m%dT%H%M%S')}-{os.urandom(4).hex()}"


# --------------------------------------------------------------------------
# 路径

def skill_root() -> Path:
    """返回 monitor-anything/ 根目录（scripts/ 的上一级）。"""
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """运行期数据（config.json、monitor.db、reports/、.work/）的根目录。

    默认仍然是 Skill 安装目录下的 `data/`，保持既有安装的零迁移。但允许用环境变量
    `MONITOR_DATA_DIR` 指向别处——Skill 安装目录在有些环境里是只读的，或者会在
    升级时被整目录替换，那样配置和历史报告会一起消失。想把数据放到 Skill 之外
    （比如 `~/.monitor-anything`），设这个环境变量即可，所有脚本都会跟着走。
    """
    override = os.environ.get("MONITOR_DATA_DIR")
    d = Path(override).expanduser().resolve() if override else skill_root() / "data"
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
    # v3：这两个字段驱动的不是脚本内部逻辑，而是 Setup 阶段 Agent 创建的两条宿主平台
    # 定时任务（见 SKILL.md「建立自动化」一节）——report_time 是「报告任务」（跑③~⑥）
    # 每天触发一次的时间点，harvest_hours 是「采集任务」（只跑 harvest.py run）一天内
    # 触发的时刻列表，默认 4 次覆盖 24 小时窗口，单次漏跑不丢数据。
    "report_time": "08:00",
    "harvest_hours": [0, 6, 12, 18],
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
    """config.json 含 API token，权限必须锁定为仅当前用户可读写（0600），不能依赖
    系统 umask 的默认结果——多数系统的默认 umask 会让新建文件变成 0644（同机器的
    其他账号也能读）。用 os.open 以目标权限直接创建文件，避免"先按默认权限创建、
    再补 chmod"这种做法之间出现的短暂窗口；如果文件已经存在（比如是旧版本产生的、
    权限更宽），os.open 的 mode 参数不会生效，所以后面再显式 chmod 一次兜底。
    """
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.chmod(path, 0o600)


# --------------------------------------------------------------------------
# 文案 i18n（v3 新增）
#
# ③④⑤三个语义阶段（headline/summary/why_relevant/overview/ai_reasoning/examples）的
# 语言由 Agent 在读 prompts/*.md 时根据 report.py 传入的 language 字段自己写成对应
# 语言，不归这里管。这里管的是 report.py/render.py 自己拼出来的固定文案——比如零命中
# 时的兜底 overview、线索区的排除原因、失败告警——这些是 Python 代码直接生成的字符串，
# 不经过 Agent，所以需要在这里维护一份 zh/en 对照表，而不能指望"Agent 会自己翻译"。
#
# 只支持 zh/en 两种（config.json 的 language 字段选择范围见 setup.py），不认识的语言
# 一律回退到 zh。

SUPPORTED_LANGUAGES = ("zh", "en")

_STRINGS: dict[str, dict[str, str]] = {
    "zh": {
        "output_label_digest": "每日日报",
        "overview_empty": (
            "今天你关注的方向没有明显动静，这很正常。数据每天更新，明天再来看看；"
            "也可以考虑放宽关注范围。"
        ),
        "setup_note_default": (
            "配置时你说：{description}。之后每天会按这个方向筛选、聚类、生成摘要，"
            "判断始终基于当天实际抓到的数据，不会为了好看而夸大。"
        ),
        "lead_reason_single": "仅 1 篇（{feed_name}），不足 2 个独立信源",
        "lead_reason_cross": "{count} 篇 · 跨 {sources} 个独立信源，相关度 {score}/10 未达阈值",
        "lead_reason_same_source": "{count} 篇 · 同源转载，相关度 {score}/10 未达阈值",
        "single_source_fallback": "单一来源",
        "alert_fetch_failed": "最近的数据采集出现了问题，今天的报告可能不完整。",
    },
    "en": {
        "output_label_digest": "Daily Digest",
        "overview_empty": (
            "Nothing notable happened today in what you're tracking — that's normal. "
            "Data refreshes daily, check back tomorrow, or consider broadening your focus."
        ),
        "setup_note_default": (
            "When you set this up you said: {description}. Every day it filters, clusters, "
            "and summarizes based on this direction, always grounded in what was actually "
            "fetched that day — never exaggerated for effect."
        ),
        "lead_reason_single": "Only 1 article ({feed_name}), fewer than 2 independent sources",
        "lead_reason_cross": (
            "{count} articles · across {sources} independent sources, "
            "relevance {score}/10 below threshold"
        ),
        "lead_reason_same_source": "{count} articles · same-source reposts, relevance {score}/10 below threshold",
        "single_source_fallback": "single source",
        "alert_fetch_failed": "Recent data collection ran into a problem — today's report may be incomplete.",
    },
}


def normalize_language(language: str) -> str:
    return language if language in SUPPORTED_LANGUAGES else "zh"


def t(language: str, key: str, **kwargs: Any) -> str:
    table = _STRINGS.get(normalize_language(language), _STRINGS["zh"])
    template = table.get(key, _STRINGS["zh"].get(key, key))
    return template.format(**kwargs) if kwargs else template


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
    path = db_path()
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.row_factory = sqlite3.Row
    # monitor.db 存的是抓取到的公众号、纽约时报等全文内容，同样不该让同机器的其他
    # 账号读到。sqlite3.connect() 建库时的权限也受 umask 影响，这里显式收紧一次；
    # 只读文件系统等异常情况不应该阻断主流程，静默跳过即可。
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
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
    if out.returncode != 0 or not out.stdout.strip():
        return
    # .gitkeep 是有意跟踪的占位文件（用于让 data/ 这个空目录结构本身进仓库），
    # 不算误提交，检查时要排除它，否则每次都会误报。真正需要拦截的是全文数据库、
    # config.json（含 token）之类被不小心加进 git 的文件。
    tracked = [line for line in out.stdout.splitlines() if Path(line).name != ".gitkeep"]
    if tracked:
        print(
            "检测到 data/ 目录下有以下文件被 git 跟踪，可能误提交了抓取到的全文内容或 "
            "API token：\n  " + "\n  ".join(tracked) + "\n"
            "存在版权风险和凭据泄露风险。请先执行：git rm -r --cached <上述文件> "
            "并确认 .gitignore 中包含 data/*（保留 data/.gitkeep 例外），再重新运行。",
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


# 这些域名是"多个互不相关的信源共用一个平台域名"的情况——mp.weixin.qq.com 下面有几十万
# 个彼此独立的公众号，光看域名会把它们全都算成同一个信源。对这类平台域名，账号名
# （feed_name）才是信源身份；其余情况域名本身就是最可靠的信源身份。
SHARED_PLATFORM_HOSTS = {
    "mp.weixin.qq.com",
    "weixin.qq.com",
    "xiaohongshu.com",
    "xhslink.com",
}


def source_key(source_type: str, feed_name: str, url: str) -> str:
    """返回一篇文章的**独立信源**标识，用于判断一个聚类是不是真的"跨源"。

    以前这里用的是 `source_type`（wx / xhs / nytimes / search / aihot），但那是**渠道
    类别**，不是信源。用它判断"跨源"会在两个方向上都出错：

    - 少算：量子位和机器之心是两个完全独立的公众号，各自独立报道同一件事本来是很强的
      交叉验证信号，但两者 source_type 都是 `wx`，会被算成"同一个源"，交叉验证信号
      就这么丢了；
    - 多算：纽约时报自有渠道的一篇报道，和补充检索从 nytimes.com 搜回来的同一篇，
      source_type 分别是 `nytimes` 和 `search`，会被算成"两个独立信源交叉验证"——
      实际上是同一家媒体的同一篇稿子。

    改成：能拿到域名且不是共享平台域名时用域名（这同时消掉了上面"多算"那种情况，
    因为两条记录的域名都是 nytimes.com）；共享平台域名下用 `渠道:账号名`（这修好了
    "少算"）；两者都拿不到时才退回 source_type。
    """
    host = ""
    if url:
        from urllib.parse import urlsplit

        host = (urlsplit(url).netloc or "").lower().split(":")[0]
        if host.startswith("www."):
            host = host[4:]
    if host and host not in SHARED_PLATFORM_HOSTS:
        return host
    feed_name = (feed_name or "").strip()
    if feed_name:
        return f"{source_type or 'unknown'}:{feed_name}"
    return host or (source_type or "unknown")


_SAFE_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def safe_article_url(url: Optional[str]) -> str:
    """服务端侧 URL 协议白名单：只放行 http(s)，非法协议（`javascript:` / `data:` 等）
    在数据层就地清空成空字符串。

    这是「防御纵深」的第一层——`assets/template.html` 的 `safeUrl()` 是第二层，在
    渲染时再兜一次底。只留客户端那一层的话，任何人以后改坏那份手写 JS（或者干脆
    绕过它直接读 REPORT_DATA）就会让恶意协议重新变得可点击；在这里让恶意协议
    根本不会出现在下发给浏览器的报告 JSON 里，两层缺一层都还有另一层兜底。
    """
    if not url:
        return ""
    return url if _SAFE_URL_RE.match(url) else ""


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
            # 去重数不可能是负数。①的 fetched 是"最近一次接口快照的原始条数"，②的
            # after_dedup 是"窗口内库里实际有多少条"，两者口径不同：补充检索写进来的
            # 条目、前几轮 harvest 留在窗口里的旧文章，都会让后者大于前者，直接相减就会
            # 打出"去重 -1"这种不可能的数字。report.py 那边已经把 fetched 抬到不小于
            # after_dedup，这里再 clamp 一次，任何调用方传进来的脏数据都不会显示成负数。
            dedup = max(0, stats.get("fetched", 0) - stats.get("after_dedup", 0))
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
    """给人看的本地时间戳（report.json 的 generated_at 等展示字段用）。

    用系统真实的本地 UTC 偏移，不是硬编码的 `+08:00`——旧实现不管运行环境实际时区
    是什么，一律贴 `+08:00` 标签，系统不在 UTC+8 时这个时间戳本身就是错的（只是
    "看起来像"本地时间，数值上并不对）。这个函数只用于展示，不要拿它写数据库时间戳，
    数据库统一用 now_utc_sql()（见下）。
    """
    return datetime.now().astimezone().isoformat(timespec="seconds")


def now_utc_sql() -> str:
    """返回 UTC 时间，格式与 SQLite `datetime('now')` 完全一致（`YYYY-MM-DD HH:MM:SS`，
    空格分隔、不带时区后缀），专门给写入数据库的时间戳字段用（articles.fetched_at /
    runs.run_at）。

    这两个字段全靠字符串比较去判断"是否在最近 N 小时内"（`WHERE fetched_at >=
    datetime('now', '-24 hours')`），只要写入和比较两边不是同一种格式、同一个时区，
    这个比较就是错的——旧的 now_iso_local() 把本地时间硬贴 +08:00 标签写进去，
    跟 SQLite 用 UTC 算出来的 `datetime('now', ...)` 比较时，时区本身就对不上
    （系统在非 UTC+8 环境下更是双重错误），实测出现过 33 小时前的数据仍被判定为
    "24 小时内"。这里让两边都用同一种 UTC、同一种格式，从根上消掉这个偏差。
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
