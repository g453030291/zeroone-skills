#!/usr/bin/env python3
"""setup.py —— 首次配置涉及的确定性部分（§7 Setup 流程）。

**这个脚本不负责「推断用户关注方向」这一步** —— 那一步需要读 CLAUDE.md / 项目 README /
技术栈，或调用 memory，是语义理解工作，交给 Agent 完成，并且 Agent 必须把推断结果原文
展示给用户确认，不能静默使用（这条约束写在 SKILL.md 里，setup.py 只负责校验与落盘）。

v2 变更：`check-token` 不再要求用户先发邮件才能拿到 token —— 这个卡点在第一版的实际
体验里太致命。现在没有 token 时会自动向 temporary-token 接口申请一个 30 天有效期的
试用 token 并直接写入 config.json，用户全程不需要做任何事。只有这个试用 token 过期后
还想继续用，才需要走邮件联系 TOKEN_HELP_EMAIL 申请延长有效期的 SOP（见 ARCHITECTURE.md）。
同一客户端 IP 在滚动 30 天内最多申请 10 个临时 token；达到上限时会返回明确的人话提示，
不会把配额问题误报成网络故障，也不会无意义重试。

用法：
    python setup.py check-token                          # 检测 token；没有则自动申请试用 token
    echo '<token>' | python setup.py set-token            # 手动写入一个正式 token（覆盖试用 token）
    python setup.py init-config --monitors-json m.json \\
        [--language zh] [--report-time 08:00] [--retention-days 30]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import common
import harvest


class TempTokenRateLimitError(Exception):
    pass


def _provision_temp_token(timeout: int) -> dict:
    """POST temporary-token 接口，拿一个默认 30 天有效期的试用 token。

    接口不需要携带任何鉴权头——这是专门给全新用户免排队自助试用设计的入口，
    和「已有 token 但过期了」是两条不同的路径（后者走邮件 SOP，见 SKILL.md §Setup）。
    """
    req = urllib.request.Request(common.TEMP_TOKEN_URL, data=b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 429:
            e.close()
            raise TempTokenRateLimitError(
                "当前客户端 IP 在最近 30 天内申请的临时 Token 已达到 10 个上限。"
                "请使用已有 Token；如需帮助，"
                f"请邮件联系 {common.TOKEN_HELP_EMAIL}。"
            ) from e
        e.close()
        raise
    payload = json.loads(body)
    if payload.get("code") != 200:
        raise RuntimeError(f"code={payload.get('code')} msg={payload.get('msg')}")
    data = payload.get("data") or {}
    if not data.get("token"):
        raise RuntimeError("接口返回中没有 token 字段")
    return data


def cmd_check_token(args: argparse.Namespace) -> int:
    cfg = common.load_config()
    token = common.get_token(cfg)

    if not token:
        # v2：不再引导用户先发邮件——直接自动申请一个 30 天试用 token。
        try:
            data = _provision_temp_token(args.timeout)
        except TempTokenRateLimitError as e:
            print(
                json.dumps(
                    {
                        "has_token": False,
                        "reason": "rate_limited",
                        "message": str(e),
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        except Exception as e:
            print(
                json.dumps(
                    {
                        "has_token": False,
                        "message": "自动申请试用 token 失败，请检查网络后重试；如果一直失败，"
                        f"可以邮件联系 {common.TOKEN_HELP_EMAIL}。",
                        "detail": str(e),
                    },
                    ensure_ascii=False,
                )
            )
            return 0

        cfg["api"]["token"] = data["token"]
        cfg["api"]["token_type"] = data.get("token_type", "temporary")
        cfg["api"]["expires_at"] = data.get("expires_at", "")
        if not cfg["api"].get("base_url"):
            cfg["api"]["base_url"] = common.DEFAULT_CONFIG["api"]["base_url"]
        common.save_config(cfg)
        expires_at = cfg["api"]["expires_at"]
        print(
            json.dumps(
                {
                    "has_token": True,
                    "valid": True,
                    "auto_provisioned": True,
                    "expires_at": expires_at,
                    "message": f"已自动获取一个 30 天试用 token，{expires_at[:10] or '约一个月后'} "
                    f"前有效，不需要发邮件。到期后如果还想继续用，邮件联系 "
                    f"{common.TOKEN_HELP_EMAIL} 申请延长即可。",
                },
                ensure_ascii=False,
            )
        )
        return 0

    try:
        articles = harvest.fetch_articles(cfg["api"]["base_url"], token, timeout=args.timeout)
        result = {"has_token": True, "valid": True, "sample_count": len(articles)}
        note = common.token_expiry_note(cfg)
        if note:
            result["expiry_note"] = note
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except harvest.FetchError as e:
        msg = str(e)
        if "401" in msg or "Token" in msg:
            friendly = common.token_invalid_message()
        else:
            friendly = "暂时连不上数据服务，请检查网络后重试。"
        print(json.dumps({"has_token": True, "valid": False, "message": friendly, "detail": msg}, ensure_ascii=False))
        return 0


def cmd_set_token(args: argparse.Namespace) -> int:
    """手动写入一个正式 token（比如用户邮件申请延长后拿到的），覆盖自动申请的试用 token。

    默认从 stdin 读，而不是 `--token xxx`：命令行参数会出现在进程列表（同机器上任何
    账号 `ps` 一下就能看到）和 shell 历史文件里，等于把一个长期有效的凭据留在两个
    根本不该有它的地方。`--token` 仍然保留（脚本化场景需要），但会明确警告一次。
    """
    cfg = common.load_config()
    if args.token:
        print(
            "警告：通过 --token 传入的凭据会进入进程参数列表和 shell 历史。"
            "更安全的做法是 `echo '<token>' | python3 scripts/setup.py set-token`，"
            "或者干脆只用环境变量 MONITOR_API_TOKEN（脚本会优先读它）。",
            file=sys.stderr,
        )
        token = args.token.strip()
    else:
        if sys.stdin.isatty():
            print("请粘贴 token 后回车（输入不会被回显到 shell 历史）：", file=sys.stderr)
        token = sys.stdin.read().strip()
    if not token:
        print("没有读到任何 token，没有改动 config.json。", file=sys.stderr)
        return 1
    cfg["api"]["token"] = token
    cfg["api"]["token_type"] = "manual"
    cfg["api"]["expires_at"] = ""
    if not cfg["api"].get("base_url"):
        cfg["api"]["base_url"] = common.DEFAULT_CONFIG["api"]["base_url"]
    common.save_config(cfg)
    print("token 已写入 config.json（也可以改用环境变量 MONITOR_API_TOKEN，脚本会优先读它）。")
    return 0


_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _validate_report_time(value: str) -> str:
    """report_time 会被 Agent 直接拿去创建宿主平台的定时任务。写坏了（"25:00"、"8am"、
    "0800"）不会在这里报错，而是等到建定时任务那一步才失败，或者更糟——建成了一个
    永远不触发的任务，用户以为配好了，几天后才发现从来没收到过报告。在写进配置之前
    就把它挡住。"""
    if not _TIME_RE.match(value or ""):
        raise SystemExit(f"非法的 report_time：{value!r}（应为 24 小时制 HH:MM，例如 08:00）")
    return value


def _validate_retention_days(value: int) -> int:
    """负数保留天数是有实际破坏力的：清理逻辑算的是 `now - days`，天数为负会让这个
    cutoff 落到**未来**，于是"比 cutoff 更旧的都删掉"匹配到磁盘上的每一份历史报告，
    一次运行就把用户攒下的全部历史清空。0 天同理（当天生成的报告立刻被判为过期）。
    下限锁在 1 天。"""
    if value < 1:
        raise SystemExit(
            f"非法的 retention-days：{value}（必须 ≥ 1 天）。"
            f"负数或 0 会让过期清理的时间点落在未来，把全部历史报告一次性删光。"
        )
    return value


def cmd_init_config(args: argparse.Namespace) -> int:
    cfg = common.load_config()
    monitors = common.read_json(Path(args.monitors_json))
    if isinstance(monitors, dict):
        monitors = [monitors]
    if not isinstance(monitors, list):
        raise SystemExit(f"monitors JSON 应该是数组或单个对象，实际是 {type(monitors).__name__}")
    if not monitors:
        # 空数组能写进去，但之后每天的报告都会是一份没有任何关注点的空壳，而且
        # SKILL.md 判断"是否需要 Setup"看的就是 monitors 非空——写了空数组等于让
        # Setup 既没配成、又不会被重新触发。
        raise SystemExit("monitors 不能为空——至少要有一个关注方向，否则报告没有任何内容可生成")
    seen_ids: set[str] = set()
    for m in monitors:
        if not isinstance(m, dict):
            raise SystemExit(f"每个 monitor 必须是一个对象，实际是 {type(m).__name__}：{m!r:.80}")
        m.setdefault("exclude_keywords", [])
        m.setdefault("mute_feeds", [])
        if not m.get("id"):
            raise SystemExit("每个 monitor 必须有 id 字段")
        common.validate_monitor_id(m["id"])
        if m["id"] in seen_ids:
            # 重复 id 不会报错，但后面所有按 id 索引的地方（中间产物文件名、run_state
            # 的 monitors 字典、报告小节去重）都会让后一个覆盖前一个，用户配的两个
            # 关注点最终只剩一个，而且没有任何提示。
            raise SystemExit(f"monitor id 重复：{m['id']!r}——每个关注方向的 id 必须唯一")
        seen_ids.add(m["id"])
        if not m.get("description"):
            raise SystemExit(f"monitor {m['id']} 缺少 description（唯一必填字段）")
        m.setdefault("name", m["id"])
        m.setdefault("focus_tags", [])
        m.setdefault("setup_note", "")
    retention_days = _validate_retention_days(args.retention_days)
    cfg["monitors"] = monitors
    cfg["language"] = args.language
    cfg["report_time"] = _validate_report_time(args.report_time)
    cfg["retention"] = {"articles_days": retention_days, "reports_days": retention_days}
    common.save_config(cfg)
    print(f"config.json 已写入：{common.config_path()}")
    print(json.dumps({"monitors": [m["id"] for m in monitors]}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="monitor-anything 首次配置")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("check-token", help="检测并真实校验 token")
    p1.add_argument("--timeout", type=int, default=15)
    p1.set_defaults(func=cmd_check_token)

    p2 = sub.add_parser(
        "set-token",
        help="把 token 写入 config.json（默认从 stdin 读，避免凭据进入 shell 历史）",
    )
    p2.add_argument(
        "--token",
        help="不推荐：会进入进程参数与 shell 历史。默认改为从 stdin 读取。",
    )
    p2.set_defaults(func=cmd_set_token)

    p3 = sub.add_parser("init-config", help="写入 monitors / language / retention 等设置")
    p3.add_argument("--monitors-json", required=True, help="Agent 与用户确认后的 monitor 定义 JSON 路径")
    p3.add_argument(
        "--language",
        default="zh",
        choices=list(common.SUPPORTED_LANGUAGES),
        help="报告用哪种语言输出——目前只支持中文/英文，决定③④⑤阶段 Agent 写作用的语言，"
        "以及 report.py/render.py 自己生成的固定文案（零命中兜底、线索原因、失败告警等）",
    )
    p3.add_argument("--report-time", default="08:00")
    p3.add_argument("--retention-days", type=int, default=30)
    p3.set_defaults(func=cmd_init_config)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
