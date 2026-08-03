#!/usr/bin/env python3
"""outputs/email.py —— 通过 SMTP（标准库 smtplib）把日报摘要发送到邮箱。

配置项（config.json 里的 outputs_config.email）：

```json
{
  "outputs_config": {
    "email": {
      "smtp_host": "smtp.example.com",
      "smtp_port": 465,
      "use_ssl": true,
      "username": "you@example.com",
      "password_env": "MONITOR_EMAIL_PASSWORD",
      "to": ["you@example.com"]
    }
  }
}
```

`password_env` 指向存放密码/授权码的环境变量名，不直接把密码写进 config.json。
"""

from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText
from email.utils import formatdate
from typing import Any


def _build_body(report: dict[str, Any]) -> str:
    lines = [f"{report['date']} 日报", ""]
    stats = report.get("stats", {})
    lines.append(
        f"采集 {stats.get('fetched', 0)} 篇 · 保留 {stats.get('after_dedup', 0)} 篇 · "
        f"精选 {stats.get('selected', 0)} 条"
    )
    lines.append("")
    for m in report.get("monitors", []):
        lines.append(f"【{m['name']}】{m.get('overview', '')}")
        for c in m.get("clusters", []):
            lines.append(f"  - {c['headline']}（{c.get('score', '-')}/10）：{c.get('summary', '')}")
        lines.append("")
    lines.append("完整报告见本地 dashboard.html。")
    return "\n".join(lines)


def emit(report: dict[str, Any], config: dict[str, Any]) -> None:
    cfg = config.get("outputs_config", {}).get("email")
    if not cfg:
        raise RuntimeError(
            "未找到 email 的配置（config.json 的 outputs_config.email）。"
            "需要 smtp_host / smtp_port / username / password_env / to。"
        )

    password = os.environ.get(cfg.get("password_env", ""), "")
    if not password:
        raise RuntimeError(f"环境变量 {cfg.get('password_env')} 未设置，无法发送邮件。")

    msg = MIMEText(_build_body(report), "plain", "utf-8")
    msg["Subject"] = f"[monitor-anything] {report['date']} 日报"
    msg["From"] = cfg["username"]
    msg["To"] = ", ".join(cfg["to"])
    msg["Date"] = formatdate(localtime=True)

    host, port = cfg["smtp_host"], int(cfg["smtp_port"])
    if cfg.get("use_ssl", True):
        server = smtplib.SMTP_SSL(host, port, timeout=20)
    else:
        server = smtplib.SMTP(host, port, timeout=20)
        server.starttls()
    try:
        server.login(cfg["username"], password)
        server.sendmail(cfg["username"], cfg["to"], msg.as_string())
    finally:
        server.quit()
