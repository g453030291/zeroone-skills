#!/usr/bin/env python3
"""setup.py —— 首次配置涉及的确定性部分（§7 Setup 流程）。

**这个脚本不负责「推断用户关注方向」这一步** —— 那一步需要读 CLAUDE.md / 项目 README /
技术栈，或调用 memory，是语义理解工作，交给 Agent 完成，并且 Agent 必须把推断结果原文
展示给用户确认，不能静默使用（这条约束写在 SKILL.md 里，setup.py 只负责校验与落盘）。

用法：
    python setup.py check-token                          # 检测/校验 token
    python setup.py set-token --token xxx                 # 把 token 写入 config.json
    python setup.py init-config --monitors-json m.json \\
        [--language zh] [--report-time 08:00] [--retention-days 30]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import common
import harvest


def cmd_check_token(args: argparse.Namespace) -> int:
    cfg = common.load_config()
    token = common.get_token(cfg)
    if not token:
        print(
            json.dumps(
                {
                    "has_token": False,
                    "message": f"未配置 API token。请发邮件至 {common.TOKEN_HELP_EMAIL} 索取。"
                    "想先看看效果？可以用 --sample 跑一遍完整流程。",
                },
                ensure_ascii=False,
            )
        )
        return 0

    try:
        articles = harvest.fetch_articles(cfg["api"]["base_url"], token, timeout=args.timeout)
        print(
            json.dumps(
                {"has_token": True, "valid": True, "sample_count": len(articles)},
                ensure_ascii=False,
            )
        )
        return 0
    except harvest.FetchError as e:
        msg = str(e)
        if "401" in msg or "Token" in msg:
            friendly = f"Token 好像失效了，可以邮件联系 {common.TOKEN_HELP_EMAIL} 重新获取。"
        else:
            friendly = "暂时连不上数据服务，请检查网络后重试。"
        print(json.dumps({"has_token": True, "valid": False, "message": friendly, "detail": msg}, ensure_ascii=False))
        return 0


def cmd_set_token(args: argparse.Namespace) -> int:
    cfg = common.load_config()
    cfg.setdefault("api", {})["token"] = args.token
    if not cfg["api"].get("base_url"):
        cfg["api"]["base_url"] = common.DEFAULT_CONFIG["api"]["base_url"]
    common.save_config(cfg)
    print("token 已写入 config.json（也可以改用环境变量 MONITOR_API_TOKEN，脚本会优先读它）。")
    return 0


def cmd_init_config(args: argparse.Namespace) -> int:
    cfg = common.load_config()
    monitors = common.read_json(Path(args.monitors_json))
    if isinstance(monitors, dict):
        monitors = [monitors]
    for m in monitors:
        m.setdefault("exclude_keywords", [])
        m.setdefault("mute_feeds", [])
        if not m.get("id"):
            raise SystemExit("每个 monitor 必须有 id 字段")
        if not m.get("description"):
            raise SystemExit(f"monitor {m['id']} 缺少 description（唯一必填字段）")
        m.setdefault("name", m["id"])
    cfg["monitors"] = monitors
    cfg["language"] = args.language
    cfg["report_time"] = args.report_time
    cfg["retention"] = {"articles_days": args.retention_days, "reports_days": args.retention_days}
    if not cfg.get("outputs"):
        cfg["outputs"] = ["html", "md"]
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

    p2 = sub.add_parser("set-token", help="把 token 写入 config.json")
    p2.add_argument("--token", required=True)
    p2.set_defaults(func=cmd_set_token)

    p3 = sub.add_parser("init-config", help="写入 monitors / language / retention 等设置")
    p3.add_argument("--monitors-json", required=True, help="Agent 与用户确认后的 monitor 定义 JSON 路径")
    p3.add_argument("--language", default="zh", choices=["zh", "en", "raw"])
    p3.add_argument("--report-time", default="08:00")
    p3.add_argument("--retention-days", type=int, default=30)
    p3.set_defaults(func=cmd_init_config)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
