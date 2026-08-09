#!/usr/bin/env python3
"""search.py —— 封装 `/api/data/articles/search` 接口，并把结果接入数据池（v2 新增）。

这是数据池之外的另一条能力：不是拉取"最近 24 小时增量"，而是按关键词做即时检索
（很可能是接了一个通用搜索/网页问答服务，不只是零一实验室自己的资讯池，从返回结构里的
`answer` / `follow_up_questions` / `images` 字段能看出来）。**接口自己保证只返回最近
24 小时内的结果**，客户端不用也不应该再重复判断一遍新鲜度，拿到什么就是什么。

用途已经接进主流程（见 SKILL.md ③筛选一节、ARCHITECTURE.md §14/§18）：当某个 monitor
在常规数据池里筛选后一条都没命中（`report.py filtered` 返回 `needs_search_augment: true`），
Agent 会拿 monitor 的 `description` 拟一个查询词，跑 `search.py ingest` 把结果**写进
monitor.db**，再重新走一遍候选/筛选，尽量让用户的 monitor 当天也能有实际内容，而不是
直接展示"今天没有动静"。

接口目前**只接受两个参数**，多传字段可能不被接受，不要自作主张加别的参数：
    query          搜索查询词（必填）
    max_results    返回结果条数上限（可选，不传就不带这个字段，让接口自己决定默认值）

用法：
    python search.py query --query "electric vehicle market" --max-results 5
    python search.py query --query "electric vehicle market" --pretty      # 美化输出，人读
    python search.py ingest --query "electric vehicle market" --max-results 8

也可以当模块用：
    from search import search
    data = search("electric vehicle market", max_results=5)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional
from urllib.parse import urlsplit

import common
import harvest

BEIJING_TZ = timezone(timedelta(hours=8))


class SearchError(Exception):
    pass


def search(
    query: str,
    max_results: Optional[int] = None,
    token: Optional[str] = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """POST common.SEARCH_URL，返回响应体里的 `data` 字段（query/answer/results/... 原样透传）。

    只组装接口明确支持的两个字段，`max_results` 为 None 时完全不带这个 key，而不是传 null——
    避免接口把显式的 null 当成"传了但是空"来处理，具体行为未知，保守起见不传。
    """
    cfg = common.load_config()
    token = token or common.get_token(cfg)
    if not token:
        raise SearchError("未配置 API token，无法调用搜索接口。")

    body: dict[str, Any] = {"query": query}
    if max_results is not None:
        body["max_results"] = max_results

    req = urllib.request.Request(
        common.SEARCH_URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            e.close()
            raise SearchError(common.token_invalid_message()) from e
        try:
            detail = e.read().decode("utf-8", "ignore")
        except Exception:
            detail = str(e)
        finally:
            e.close()
        raise SearchError(f"搜索接口请求失败（HTTP {e.code}）：{detail[:200]}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise SearchError(f"暂时连不上搜索服务，请检查网络后重试：{e}") from e

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SearchError(f"搜索接口返回了无法解析的内容：{raw[:200]}") from e
    if payload.get("code") != 200:
        raise SearchError(f"搜索接口返回异常：code={payload.get('code')} msg={payload.get('msg')}")
    return payload.get("data", {})


def cmd_query(args: argparse.Namespace) -> int:
    try:
        data = search(args.query, args.max_results, timeout=args.timeout)
    except SearchError as e:
        print(f"搜索失败：{e}", file=sys.stderr)
        return 1
    print(json.dumps(data, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


# --------------------------------------------------------------------------
# 把 search 结果接入数据池：转成 harvest.clean_and_store() 能吃的原始文章形状，
# 复用①②阶段既有的去重/清洗/噪音判定逻辑，不重新写一遍。

def _parse_published(raw_date: Optional[str]) -> Optional[datetime]:
    """解析 published_date（示例形如 "Mon, 03 Aug 2026 15:37:47 GMT"）。只用于填充
    publish_time 这个展示字段，解析失败/缺失不影响是否收录——新鲜度由接口自己保证，
    见 to_pool_shape() 的说明。"""
    if not raw_date:
        return None
    try:
        dt = parsedate_to_datetime(raw_date)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_pool_shape(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 search 接口 `data.results` 里的条目转成跟 /articles 接口一样的原始文章形状
    （id/source_type/feed_name/title/url/description/publish_time/clean_content），
    这样就能直接喂给 harvest.clean_and_store()，不用重新实现一遍去重/清洗。

    接口本身已经保证只返回最近 24 小时内的结果，这里不再重复判断新鲜度、也不会因为
    `published_date` 缺失或解析失败就丢弃结果——`published_date` 只用来填充展示用的
    `publish_time`，解析不出来就退化成用当前时间兜底（反正内容本身就是新的）。
    """
    accepted: list[dict[str, Any]] = []
    for r in results:
        url = (r.get("url") or "").strip()
        if not url:
            continue
        published = _parse_published(r.get("published_date"))
        published_beijing = (published or datetime.now(timezone.utc)).astimezone(BEIJING_TZ)
        article_id = "search-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
        feed_name = urlsplit(url).netloc or "AI 检索"
        accepted.append(
            {
                "id": article_id,
                "source_type": "search",
                "feed_name": feed_name,
                "title": r.get("title") or "",
                "url": url,
                "description": "",
                # harvest.normalize_publish_time() 把朴素时间戳当作"已经是北京时间"处理，
                # 所以这里要先把 UTC 转成北京时间再格式化成朴素字符串，否则会被错记 8 小时。
                "publish_time": published_beijing.strftime("%Y-%m-%d %H:%M:%S"),
                "clean_content": r.get("content") or "",
            }
        )
    return accepted


def cmd_ingest(args: argparse.Namespace) -> int:
    try:
        data = search(args.query, args.max_results, timeout=args.timeout)
    except SearchError as e:
        print(f"检索失败：{e}", file=sys.stderr)
        return 1

    results = data.get("results", []) if isinstance(data, dict) else []
    raw = to_pool_shape(results)

    conn = common.connect_db()
    stats = harvest.clean_and_store(conn, raw)
    conn.close()

    print(
        json.dumps(
            {
                "query": args.query,
                "search_results": len(results),
                "new_articles": stats["new"],
                "dup": stats["dup_id"] + stats["dup_url"],
            },
            ensure_ascii=False,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="调用资讯搜索接口（query + max_results），可选写入数据池")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_query = sub.add_parser("query", help="按关键词搜索，只打印结果，不写入数据池")
    p_query.add_argument("--query", required=True, help="搜索查询词")
    p_query.add_argument("--max-results", type=int, default=None, help="返回结果条数上限（可选）")
    p_query.add_argument("--timeout", type=int, default=30)
    p_query.add_argument("--pretty", action="store_true", help="美化输出的 JSON，方便人读")
    p_query.set_defaults(func=cmd_query)

    p_ingest = sub.add_parser("ingest", help="按关键词搜索，把结果写入 monitor.db（接口本身只返回最近 24 小时内的结果）")
    p_ingest.add_argument("--query", required=True, help="搜索查询词")
    p_ingest.add_argument("--max-results", type=int, default=8, help="返回结果条数上限")
    p_ingest.add_argument("--timeout", type=int, default=30)
    p_ingest.set_defaults(func=cmd_ingest)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
