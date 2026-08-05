#!/usr/bin/env python3
"""share.py —— 把当天的独立报告页 data/reports/<date>.html 上传到零一实验室的分享服务，
换回一个可以直接在浏览器打开、随意转发的公开链接。这是报告页上「分享今日洞察」按钮想要
达成的效果。

**为什么上传逻辑不写进 assets/template.html 的按钮点击事件里**：<date>.html 本身就是
会被分享出去的文件——单文件、可离线、双击即开是这个项目刻意保留的能力。如果把上传请求
写进它的 `<script>`，就必须把能通过鉴权的 Bearer token 一起打包进这份到处转发的静态文件，
任何拿到这个文件的人都能读到源码里的 token 并冒用你的额度，这和「token 只留在本地
config.json，从不离开用户机器」的既有原则冲突。所以「上传」被设计成一次显式的、由 Agent
触发的脚本调用；浏览器里的按钮本身仍然只做本地文案复制/系统分享面板，不需要网络。

v3 起收窄了上传范围：每份 `<date>.html` 天生就只含那一天的数据（见 render.py），所以
「分享今日洞察」上传的就是这一个文件本身，不会像旧版那样把首页嵌入的历史窗口、其他
monitor 一起带出去——因为现在首页压根不内嵌任何报告内容了。也因此这个脚本不再接受
`--file`（任意本地文件都能传的口子，没有必要保留）。

跑完这个脚本、拿到公开链接后，脚本会把链接写回 reports/<date>.json 的 share_url 字段并
重新渲染那一天的 `<date>.html`——下次用户打开这份 HTML 再点「分享今日洞察」，按钮里用的
就是这个真正能发给别人的公开链接，而不是本地文件路径（file:// 对别人没有意义）。首页
dashboard.html 和 dates-manifest.js 不受影响，不需要重新生成。

用法：
    python share.py upload                              # 上传今天的 <date>.html
    python share.py upload --date 2026-08-04             # 指定日期
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import common
import render


class ShareError(Exception):
    pass


def upload_html(url: str, token: str, file_path: Path, timeout: int = 30) -> Any:
    """对应用户给的 curl：
        curl -X POST '<url>' -H 'Authorization: Bearer <token>' \\
             -H 'Content-Type: application/octet-stream' --data-binary '@<file>'
    """
    if not file_path.exists():
        raise ShareError(f"文件不存在：{file_path}，请先跑一遍 render.py 生成报告。")
    data = file_path.read_bytes()
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/octet-stream",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise ShareError(
                f"Token 校验失败（401），可能已过期。如需继续使用请邮件联系 "
                f"{common.TOKEN_HELP_EMAIL} 申请延长有效期。"
            ) from e
        try:
            detail = e.read().decode("utf-8", "ignore")
        except Exception:
            detail = str(e)
        raise ShareError(f"分享上传失败（HTTP {e.code}）：{detail[:200]}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise ShareError(f"暂时连不上分享服务，请检查网络后重试：{e}") from e

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        raise ShareError(f"分享服务返回了无法解析的内容：{body[:200]}") from e
    if payload.get("code") != 200:
        raise ShareError(f"分享接口返回异常：code={payload.get('code')} msg={payload.get('msg')}")
    return payload.get("data")


def extract_share_url(data: Any) -> str:
    """接口返回结构以实际线上为准，这里尽量兼容几种常见形状。"""
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("url", "share_url", "html_url", "link"):
            if data.get(key):
                return str(data[key])
    return ""


def cmd_upload(args: argparse.Namespace) -> int:
    cfg = common.load_config()
    token = common.get_token(cfg)
    if not token:
        print("未配置 API token，无法分享。请先完成 setup（python3 scripts/setup.py check-token）。", file=sys.stderr)
        return 1

    date = common.validate_date(args.date or common.today_str())
    file_path = common.reports_dir() / f"{date}.html"

    try:
        data = upload_html(common.SHARE_HTML_URL, token, file_path, timeout=args.timeout)
    except ShareError as e:
        print(f"分享失败：{e}", file=sys.stderr)
        return 1

    share_url = extract_share_url(data)
    if not share_url:
        # 接口返回了 200，但没能从返回内容里提取到链接——不能当成功处理，不然用户会
        # 拿着一句"分享成功"却没有任何真链接。把原始返回内容打出来，方便 Agent 直接
        # 读原始返回转述给用户，或者据此调整 extract_share_url() 兼容新的返回形状。
        print(
            f"分享接口返回了 200，但没能从返回内容里提取到链接，原始返回：{json.dumps(data, ensure_ascii=False)}",
            file=sys.stderr,
        )
        return 1

    report_path = common.reports_dir() / f"{date}.json"
    if report_path.exists():
        report = common.read_json(report_path)
        report["share_url"] = share_url
        common.write_json(report_path, report)
        # 把新链接带进这次重渲染，之后按钮里用的就是这个公开链接，而不是本地文件路径。
        # 只重渲染这一天的独立文件，首页 dashboard.html 和 dates-manifest.js 不受影响。
        html = render.render_day_html(date, report)
        file_path.write_text(html, encoding="utf-8")

    print(json.dumps({"share_url": share_url, "raw": data}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="上传当天的独立报告页，换取可分享的公开链接")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_upload = sub.add_parser("upload", help="上传 data/reports/<date>.html 到分享服务")
    p_upload.add_argument("--date", help="报告日期，默认今天；决定上传哪一天的独立文件")
    p_upload.add_argument("--timeout", type=int, default=30)
    p_upload.set_defaults(func=cmd_upload)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
