#!/usr/bin/env python3
"""outputs/webhook.py —— 向飞书 / 企业微信 / 钉钉机器人 Webhook 地址推送日报摘要。

三家的 JSON 消息格式不完全一样，这里各自适配成对方要求的最简单文本消息格式，
只用标准库 urllib，不引入第三方 SDK。

配置项（config.json 里的 outputs_config.webhook）：

```json
{
  "outputs_config": {
    "webhook": {
      "provider": "feishu",   // feishu | wecom | dingtalk
      "url": "https://open.feishu.cn/open-apis/bot/v2/hook/xxxx"
    }
  }
}
```
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


def _build_text(report: dict[str, Any]) -> str:
    stats = report.get("stats", {})
    lines = [
        f"📡 monitor-anything · {report['date']} 日报",
        f"采集 {stats.get('fetched', 0)} 篇 · 保留 {stats.get('after_dedup', 0)} 篇 · "
        f"精选 {stats.get('selected', 0)} 条",
        "",
    ]
    for m in report.get("monitors", []):
        lines.append(f"【{m['name']}】{m.get('overview', '')}")
        for c in m.get("clusters", [])[:5]:
            lines.append(f"· {c['headline']}（{c.get('score', '-')}/10）")
    return "\n".join(lines)


def _payload(provider: str, text: str) -> dict[str, Any]:
    if provider == "feishu":
        return {"msg_type": "text", "content": {"text": text}}
    if provider == "wecom":
        return {"msgtype": "text", "text": {"content": text}}
    if provider == "dingtalk":
        return {"msgtype": "text", "text": {"content": text}}
    raise ValueError(f"不支持的 provider: {provider}（应为 feishu / wecom / dingtalk）")


def emit(report: dict[str, Any], config: dict[str, Any]) -> None:
    cfg = config.get("outputs_config", {}).get("webhook")
    if not cfg or not cfg.get("url"):
        raise RuntimeError("未找到 webhook 的配置（config.json 的 outputs_config.webhook.url）。")

    provider = cfg.get("provider", "feishu")
    text = _build_text(report)
    body = json.dumps(_payload(provider, text), ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        cfg["url"], data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except urllib.error.URLError as e:
        raise RuntimeError(f"推送到 {provider} 失败：{e}") from e
