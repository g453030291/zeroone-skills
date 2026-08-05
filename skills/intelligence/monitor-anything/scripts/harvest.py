#!/usr/bin/env python3
"""harvest.py —— 阶段①采集 + 阶段②清洗（零 LLM，零第三方依赖）。

用法：
    python harvest.py run              # 抓取最近 24 小时增量，清洗后写入 monitor.db
    python harvest.py status           # 查看最近若干次 runs 记录（供失败告警判断用）

v2：不再有 sample 数据这条路径——token 首次使用会自动申请（见 setup.py），不再需要
一份合成数据来垫等待时间，`run` 现在只有真实拉取这一种行为。

设计取舍见 ARCHITECTURE.md：为什么用 fetched_at 而非 publish_time 做过期依据、
为什么采集与报告解耦成两个脚本。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

import common


class FetchError(Exception):
    pass


def fetch_articles(base_url: str, token: str, timeout: int = 30, retries: int = 3) -> list[dict[str, Any]]:
    """请求数据源，失败按 2s/4s/8s 指数退避重试。接口不支持任何 query 参数，固定返回近 24 小时数据。"""
    req = urllib.request.Request(base_url, headers={"Authorization": f"Bearer {token}"})
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
            payload = json.loads(body)
            if payload.get("code") != 200:
                raise FetchError(f"接口返回异常：code={payload.get('code')} msg={payload.get('msg')}")
            return payload.get("data", [])
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise FetchError(
                    f"Token 校验失败（401），可能已过期。如需继续使用请邮件联系 "
                    f"{common.TOKEN_HELP_EMAIL} 申请延长有效期。"
                ) from e
            last_err = e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
        if attempt < retries - 1:
            time.sleep(2 ** (attempt + 1))
    raise FetchError(f"连续 {retries} 次请求失败：{last_err}")


def clean_and_store(conn, raw_articles: list[dict[str, Any]]) -> dict[str, int]:
    """执行 §6-② 四步清洗，逐条判断是否为新记录并入库。返回统计信息。"""
    stats = {
        "fetched": len(raw_articles),
        "dup_id": 0,
        "dup_url": 0,
        "noise": 0,
        "new": 0,
    }
    cur = conn.cursor()
    # 用 UTC 格式写入，跟 purge_expired() / report.py 里 `datetime('now', '-N hours')`
    # 窗口查询用的是同一个时区、同一种格式，字符串比较才是对的（见 common.now_utc_sql()）。
    fetched_at = common.now_utc_sql()

    # 已知 url_hash 集合，用于本批次内 + 历史的二次去重
    cur.execute("SELECT url_hash FROM articles WHERE url_hash != ''")
    known_hashes = {row[0] for row in cur.fetchall()}

    for raw in raw_articles:
        aid = str(raw.get("id", "")).strip()
        if not aid:
            continue

        # 1. 主键去重
        cur.execute("SELECT 1 FROM articles WHERE id = ?", (aid,))
        if cur.fetchone():
            stats["dup_id"] += 1
            continue

        # 2. URL 去重（剥离追踪参数后哈希）
        uhash = common.url_hash(raw.get("url"))
        if uhash and uhash in known_hashes:
            stats["dup_url"] += 1
            continue

        # 3. 规范化
        title = common.clean_text(raw.get("title", ""))
        description = common.clean_text(raw.get("description", ""))
        content = common.clean_text(raw.get("clean_content", ""))  # xhs 可能为空
        publish_time = common.normalize_publish_time(raw.get("publish_time", ""))

        # 4. 噪音过滤（不删除，只标记降权）
        low_quality = common.is_noise(title, description, content)
        if low_quality:
            stats["noise"] += 1

        cur.execute(
            """INSERT INTO articles
               (id, url_hash, source_type, feed_name, title, url, description,
                publish_time, content, content_len, low_quality, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                aid,
                uhash,
                raw.get("source_type", "unknown"),
                raw.get("feed_name", ""),
                title or "(无标题)",
                raw.get("url", ""),
                description,
                publish_time,
                content,
                len(content),
                1 if low_quality else 0,
                fetched_at,
            ),
        )
        if uhash:
            known_hashes.add(uhash)
        stats["new"] += 1

    conn.commit()
    return stats


def purge_expired(conn, articles_days: int, reports_days: int) -> None:
    """§5.3 过期清理：按 fetched_at（而非 publish_time）清理，避免公众号旧文重推被误删。

    v2 补了一个此前遗漏的清理点：`data/.work/` 里的中间产物（candidates/filter-result/
    cluster-result/summary-result/state-<date> 等 Agent 与脚本交换用的临时 JSON）此前
    从未被清理过，会无限堆积。这些文件只在当天报告生成过程里有用，跟 `articles_days` /
    `reports_days` 没有直接关系，所以给了一个固定的短保留期（`common.WORK_RETENTION_DAYS`，
    默认 3 天，够跨天重试/时区边界，不做成用户可配置项——没有必要为了几十 KB 的临时文件
    增加一个配置维度）。
    """
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM articles WHERE fetched_at < datetime('now', ?)",
        (f"-{articles_days} days",),
    )
    cur.execute(
        "DELETE FROM runs WHERE run_at < datetime('now', ?)",
        (f"-{articles_days} days",),
    )
    conn.commit()

    reports = common.reports_dir()
    cutoff = time.time() - reports_days * 86400
    for f in reports.glob("*.json"):
        if f.stem == "dashboard" or f.name == "dashboard.html":
            continue
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
    for f in reports.glob("*.html"):
        if f.name == "dashboard.html":
            continue
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)

    work_cutoff = time.time() - common.WORK_RETENTION_DAYS * 86400
    for f in common.work_dir().glob("*.json"):
        if f.stat().st_mtime < work_cutoff:
            f.unlink(missing_ok=True)


def record_run(conn, stats: dict[str, int], status: str, message: str) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO runs (run_at, fetched, new, status, message) VALUES (?,?,?,?,?)",
        (common.now_utc_sql(), stats.get("fetched", 0), stats.get("new", 0), status, message),
    )
    conn.commit()


def cmd_run(args: argparse.Namespace) -> int:
    common.check_data_not_tracked_by_git()
    cfg = common.load_config()
    conn = common.connect_db()

    t0 = time.time()
    try:
        token = common.get_token(cfg)
        if not token:
            print(
                "未配置 API token。请先跑一遍 `python3 scripts/setup.py check-token`"
                "（会自动申请一个 30 天试用 token，不需要发邮件）。",
                file=sys.stderr,
            )
            return 1
        raw = fetch_articles(cfg["api"]["base_url"], token)
    except FetchError as e:
        record_run(conn, {"fetched": 0, "new": 0}, "error", str(e))
        print(f"采集失败：{e}", file=sys.stderr)
        return 1
    fetch_ms = int((time.time() - t0) * 1000)
    common.record_stage_ms(common.today_str(), "fetch", fetch_ms)

    common.print_inplace(f"①  采集       抓到 {len(raw)} 篇，正在清洗…")
    t1 = time.time()
    stats = clean_and_store(conn, raw)
    common.record_stage_ms(common.today_str(), "clean", int((time.time() - t1) * 1000))
    n_sources = len({(a.get("source_type") or "unknown") for a in raw})
    common.print_inplace(
        f"①  采集  ████████████████  {stats['fetched']} 篇  ·  {n_sources} 个渠道"
    )
    print()
    dup_total = stats["dup_id"] + stats["dup_url"]
    common.print_inplace(
        f"②  清洗  ████████████████  {stats['fetched']} → {stats['fetched'] - dup_total}   "
        f"去重 {dup_total}  ·  噪音标记 {stats['noise']}"
    )
    print()

    purge_expired(conn, cfg["retention"]["articles_days"], cfg["retention"]["reports_days"])
    record_run(conn, stats, "ok", f"新增 {stats['new']} 条，去重 {dup_total} 条")
    conn.close()

    print(
        json.dumps(
            {
                "fetched": stats["fetched"],
                "new": stats["new"],
                "dup": dup_total,
                "noise": stats["noise"],
                "sources": n_sources,
            },
            ensure_ascii=False,
        )
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    conn = common.connect_db()
    cur = conn.cursor()
    cur.execute("SELECT run_at, fetched, new, status, message FROM runs ORDER BY run_at DESC LIMIT ?", (args.limit,))
    rows = [dict(r) for r in cur.fetchall()]
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    conn.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="monitor-anything 采集与清洗")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="抓取并清洗最近 24 小时增量")
    p_run.set_defaults(func=cmd_run)

    p_status = sub.add_parser("status", help="查看最近的采集记录")
    p_status.add_argument("--limit", type=int, default=8)
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
