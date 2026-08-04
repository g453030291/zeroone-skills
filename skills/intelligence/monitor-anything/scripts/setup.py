#!/usr/bin/env python3
"""setup.py —— 首次配置涉及的确定性部分（§7 Setup 流程）。

**这个脚本不负责「推断用户关注方向」这一步** —— 那一步需要读 CLAUDE.md / 项目 README /
技术栈，或调用 memory，是语义理解工作，交给 Agent 完成，并且 Agent 必须把推断结果原文
展示给用户确认，不能静默使用（这条约束写在 SKILL.md 里，setup.py 只负责校验与落盘）。

v2 变更：`check-token` 不再要求用户先发邮件才能拿到 token —— 这个卡点在第一版的实际
体验里太致命。现在没有 token 时会自动向 temporary-token 接口申请一个 30 天有效期的
试用 token 并直接写入 config.json，用户全程不需要做任何事。只有这个试用 token 过期后
还想继续用，才需要走邮件联系 TOKEN_HELP_EMAIL 申请延长有效期的 SOP（见 ARCHITECTURE.md）。

用法：
    python setup.py check-token                          # 检测 token；没有则自动申请试用 token
    python setup.py set-token --token xxx                 # 手动写入一个正式 token（覆盖试用 token）
    python setup.py init-config --monitors-json m.json \\
        [--language zh] [--report-time 08:00] [--retention-days 30]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import common
import harvest


def _provision_temp_token(timeout: int) -> dict:
    """POST temporary-token 接口，拿一个默认 30 天有效期的试用 token。

    接口不需要携带任何鉴权头——这是专门给全新用户免排队自助试用设计的入口，
    和「已有 token 但过期了」是两条不同的路径（后者走邮件 SOP，见 SKILL.md §Setup）。
    """
    req = urllib.request.Request(common.TEMP_TOKEN_URL, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
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
            friendly = f"Token 好像过期或失效了，可以邮件联系 {common.TOKEN_HELP_EMAIL} 申请延长有效期。"
        else:
            friendly = "暂时连不上数据服务，请检查网络后重试。"
        print(json.dumps({"has_token": True, "valid": False, "message": friendly, "detail": msg}, ensure_ascii=False))
        return 0


def cmd_set_token(args: argparse.Namespace) -> int:
    """手动写入一个正式 token（比如用户邮件申请延长后拿到的），覆盖自动申请的试用 token。"""
    cfg = common.load_config()
    cfg["api"]["token"] = args.token
    cfg["api"]["token_type"] = "manual"
    cfg["api"]["expires_at"] = ""
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
        # output_kind 目前只有 digest 会被实际渲染（decision 是给未来「决策型」
        # 输出预留的 schema 开关，本版不生成 advice 内容，见 report.py 顶部注释）。
        m.setdefault("output_kind", "digest")
        m.setdefault("focus_tags", [])
        m.setdefault("setup_note", "")
    cfg["monitors"] = monitors
    cfg["language"] = args.language
    cfg["report_time"] = args.report_time
    cfg["retention"] = {"articles_days": args.retention_days, "reports_days": args.retention_days}
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
