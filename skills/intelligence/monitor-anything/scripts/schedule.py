#!/usr/bin/env python3
"""schedule.py —— 生成定时任务条目（只调度 harvest.py，零 LLM 部分，§12）。

只**生成并展示**命令，不静默写入系统 crontab / launchd / 任务计划程序 —— 用户确认后
自己执行。报告生成（③~⑥）需要 Agent 在场，不适合 cron，由 Agent 每日触发或用户手动唤起。

用法：
    python schedule.py show                 # 自动探测操作系统，展示对应的定时任务条目
    python schedule.py show --os macos       # 指定操作系统
"""

from __future__ import annotations

import argparse
import platform
import sys

import common

DEFAULT_HOURS = [0, 6, 12, 18]


def detect_os() -> str:
    s = platform.system().lower()
    if s == "darwin":
        return "macos"
    if s == "windows":
        return "windows"
    return "linux"


def crontab_line(hours: list[int]) -> str:
    hour_expr = ",".join(str(h) for h in hours)
    script = common.skill_root() / "scripts" / "harvest.py"
    return f"0 {hour_expr} * * * /usr/bin/env python3 {script} run >> {common.data_dir()}/harvest.log 2>&1"


def launchd_plist(hours: list[int]) -> str:
    script = common.skill_root() / "scripts" / "harvest.py"
    intervals = "\n".join(
        f"""        <dict>
            <key>Hour</key><integer>{h}</integer>
            <key>Minute</key><integer>0</integer>
        </dict>"""
        for h in hours
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.monitor-anything.harvest</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/env</string>
        <string>python3</string>
        <string>{script}</string>
        <string>run</string>
    </array>
    <key>StartCalendarInterval</key>
    <array>
{intervals}
    </array>
    <key>StandardOutPath</key><string>{common.data_dir()}/harvest.log</string>
    <key>StandardErrorPath</key><string>{common.data_dir()}/harvest.log</string>
</dict>
</plist>"""


def schtasks_commands(hours: list[int]) -> list[str]:
    script = common.skill_root() / "scripts" / "harvest.py"
    return [
        f'schtasks /Create /SC DAILY /ST {h:02d}:00 /TN "monitor-anything-harvest-{h:02d}" '
        f'/TR "python \\"{script}\\" run"'
        for h in hours
    ]


def cmd_show(args: argparse.Namespace) -> int:
    target_os = args.os or detect_os()
    hours = args.hours or DEFAULT_HOURS

    print(f"检测到操作系统：{target_os}（每 6 小时抓一次，默认 {hours} 点；24 小时窗口相当于 4 倍冗余）\n")

    if target_os == "macos":
        plist_path = "~/Library/LaunchAgents/com.monitor-anything.harvest.plist"
        print(f"macOS 建议使用 launchd。把以下内容保存为 {plist_path}，然后执行：")
        print(f"  launchctl load {plist_path}\n")
        print(launchd_plist(hours))
    elif target_os == "windows":
        print("Windows 建议使用任务计划程序（schtasks），在命令提示符中依次执行：\n")
        for cmd in schtasks_commands(hours):
            print("  " + cmd)
    else:
        print("Linux 建议使用 crontab。执行 `crontab -e`，加入这一行：\n")
        print("  " + crontab_line(hours))

    print(
        "\n以上命令只是展示，不会自动帮你写入系统配置 —— 请确认无误后自己执行。"
        "报告生成（筛选/聚类/摘要）需要 Agent 在场，不放进这个定时任务，由 Agent 每天触发或你手动唤起。"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="展示定时任务条目（仅 harvest.py）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("show", help="展示当前系统对应的定时任务条目")
    p1.add_argument("--os", choices=["macos", "linux", "windows"], default=None)
    p1.add_argument("--hours", type=int, nargs="*", default=None)
    p1.set_defaults(func=cmd_show)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
