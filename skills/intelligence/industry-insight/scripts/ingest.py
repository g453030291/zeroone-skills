#!/usr/bin/env python3
"""Fetch the latest articles and store the ones not seen before.

The endpoint returns the most recent ~100 articles on every call, so polling
repeatedly is how history accumulates. Dedupe is the (id, source_type) primary
key — running this twice in a row is safe and mostly reports duplicates.

The access key gates who can read the data, so it is never hardcoded: it comes
from ``~/.zeroone/config.json`` (or the ZEROONE_TOKEN env var).

    python3 ingest.py --run
    python3 ingest.py --run --from-file saved.json    # 离线回放
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import store

DEFAULT_ENDPOINT = "http://8.130.106.19:8100/api/data/articles"


def fetch(endpoint, token, timeout=60):
    if urlsplit(endpoint).scheme == "http":
        # key 是准入凭证，明文 HTTP 意味着它和拿到的数据都可能在网络路径上
        # 被嗅探。这是数据方那台服务器的限制，这个脚本改不了协议——只能
        # 提醒。真有 https 端点时用 --set-endpoint / --endpoint 切过去。
        print(
            f"警告：{endpoint} 是明文 HTTP，接口 key 会以明文形式经过网络传输。"
            "如果数据方提供了 HTTPS 端点，建议改用 python3 store.py --set-endpoint。",
            file=sys.stderr,
        )
    req = urllib.request.Request(
        endpoint, headers={"Authorization": f"bearer {token}", "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("code") != 200:
        raise SystemExit(
            f"接口返回 code={payload.get('code')} msg={payload.get('msg')}。"
            "如果是鉴权失败，检查 config.json 里的 api_token 是否正确。"
        )
    return payload.get("data") or []


def main():
    ap = argparse.ArgumentParser(description="采集并去重入库")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--workspace")
    ap.add_argument("--endpoint")
    ap.add_argument("--limit", type=int, help="只入库前 N 条（演示用）")
    ap.add_argument("--from-file", help="用本地 JSON 代替接口")
    args = ap.parse_args()

    if not args.run:
        ap.print_help()
        return

    started_at = store.now()
    if args.from_file:
        payload = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
        articles = payload.get("data") if isinstance(payload, dict) else payload
    else:
        cfg = store.load_config()
        endpoint = args.endpoint or cfg.get("api_endpoint") or DEFAULT_ENDPOINT
        try:
            articles = fetch(endpoint, store.require_token())
        except urllib.error.HTTPError as e:
            raise SystemExit(
                f"接口返回 HTTP {e.code}。401/403 通常是 key 无效或已被回收，找管理员确认。"
            )
        except urllib.error.URLError as e:
            raise SystemExit(f"接口请求失败: {e}. 检查网络，或用 --from-file 回放本地样本。")

    if args.limit:
        articles = articles[: args.limit]

    conn = store.connect(args.workspace)
    inserted, duplicates = store.upsert_articles(conn, articles, fetched_at=started_at)
    store.record_fetch_run(conn, len(articles), inserted, duplicates, started_at)

    st = store.stats(conn)
    print(json.dumps(
        {
            "started_at": started_at,
            "returned": len(articles),
            "inserted": inserted,
            "duplicates": duplicates,
            "total_articles": st["articles"],
            "distinct_feeds": st["feeds"],
            "window": [st["earliest"], st["latest"]],
        },
        ensure_ascii=False, indent=2,
    ))


if __name__ == "__main__":
    sys.exit(main())
