#!/usr/bin/env python3
"""Workspace, config, profiles and SQLite schema — shared by every other script.

The workspace lives outside the skill directory (``~/.zeroone/`` by default)
because this repo is public and collected data must never end up in a commit.
``~/.zeroone/`` is also the shared root for sibling ZeroOne skills: they use the
same access key and can read the same article pool.

    ~/.zeroone/
    ├── config.json          # 接口 key（首次初始化时由用户提供）
    └── industry-insight/
        ├── data.db
        ├── profiles.jsonl   # 一行一个关注点，可直接手改
        ├── summaries/<profile>/YYYY-MM-DD.json
        └── dashboard.html

Run directly to check state:

    python3 store.py --status
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ROOT = Path.home() / ".zeroone"

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id            TEXT NOT NULL,
    source_type   TEXT NOT NULL,
    feed_name     TEXT,
    title         TEXT,
    url           TEXT,
    description   TEXT,
    publish_time  TEXT,
    clean_content TEXT,
    fetched_at    TEXT NOT NULL,
    PRIMARY KEY (id, source_type)
);
CREATE INDEX IF NOT EXISTS idx_articles_publish ON articles (publish_time DESC);

CREATE TABLE IF NOT EXISTS fetch_runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    returned   INTEGER NOT NULL,
    inserted   INTEGER NOT NULL,
    duplicates INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS relevance (
    profile_slug TEXT NOT NULL,
    article_id   TEXT NOT NULL,
    source_type  TEXT NOT NULL,
    kw_score     REAL,
    ai_score     REAL,
    verdict      TEXT,            -- 'hit' | 'miss'
    reason       TEXT,
    judged_at    TEXT,
    PRIMARY KEY (profile_slug, article_id, source_type)
);
CREATE INDEX IF NOT EXISTS idx_relevance_verdict ON relevance (profile_slug, verdict);

CREATE TABLE IF NOT EXISTS clusters (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_slug  TEXT NOT NULL,
    topic_key     TEXT NOT NULL,
    title         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    article_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_clusters_profile ON clusters (profile_slug, topic_key);

CREATE TABLE IF NOT EXISTS cluster_members (
    cluster_id  INTEGER NOT NULL,
    article_id  TEXT NOT NULL,
    source_type TEXT NOT NULL,
    PRIMARY KEY (cluster_id, article_id, source_type)
);
"""

# 产出的历史就是 summaries/ 下那些按日期的 JSON 文件，没有对应的表。
# 冷却期直接读最近几个文件——少一张表、少一次同步、少一处可能对不上的地方。


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------

def root_path():
    """~/.zeroone —— 同系列 skill 共享的根目录（ZEROONE_HOME 可覆盖）。"""
    return Path(os.environ.get("ZEROONE_HOME", DEFAULT_ROOT)).expanduser()


def workspace_path(override=None):
    if override:
        return Path(override).expanduser()
    return root_path() / "industry-insight"


def summary_dir(profile_slug, workspace=None):
    d = workspace_path(workspace) / "summaries" / profile_slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def now():
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def today():
    return datetime.now().strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# config — the access key gates who may read the data, so it is never
# hardcoded and never committed; the user supplies it once at setup.
# --------------------------------------------------------------------------

CONFIG_HINT = (
    "还没有配置接口 key。向用户索取后写入 config.json：\n"
    "  python3 store.py --set-key <KEY>"
)


def config_path():
    return root_path() / "config.json"


def load_config():
    path = config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_config(**updates):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    cfg.update({k: v for k, v in updates.items() if v is not None})
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)  # 是凭据，别让同机其他用户读到
    return path


def require_token():
    token = os.environ.get("ZEROONE_TOKEN") or load_config().get("api_token")
    if not token:
        raise SystemExit(CONFIG_HINT)
    return token


# --------------------------------------------------------------------------
# profiles — one JSON object per line, hand-editable
# --------------------------------------------------------------------------

def profiles_path(workspace=None):
    return workspace_path(workspace) / "profiles.jsonl"


def list_profiles(workspace=None):
    path = profiles_path(workspace)
    if not path.exists():
        return []
    out = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            # 手改出错的一行不该让整条流水线停摆——但静默跳过会让症状
            # 变成"profile 明明写了却提示不存在"，排查起来毫无头绪。
            print(f"警告：{path} 第 {lineno} 行不是合法 JSON，已跳过：{e}", file=sys.stderr)
            continue
    return out


def load_profile(slug, workspace=None):
    for p in list_profiles(workspace):
        if p.get("slug") == slug:
            return p
    known = [p.get("slug") for p in list_profiles(workspace)]
    raise SystemExit(f"profiles.jsonl 里没有 '{slug}'。已有：{known or '（空）'}")


# --------------------------------------------------------------------------
# articles
# --------------------------------------------------------------------------

def connect(workspace=None):
    ws = workspace_path(workspace)
    ws.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ws / "data.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_articles(conn, articles, fetched_at=None):
    """去重就是 (id, source_type) 这把主键。返回 (新增, 重复)。"""
    fetched_at = fetched_at or now()
    inserted = 0
    for a in articles:
        cur = conn.execute(
            """INSERT OR IGNORE INTO articles
               (id, source_type, feed_name, title, url, description,
                publish_time, clean_content, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (a.get("id"), a.get("source_type"), a.get("feed_name"), a.get("title"),
             a.get("url"), a.get("description"), a.get("publish_time"),
             a.get("clean_content"), fetched_at),
        )
        inserted += cur.rowcount
    conn.commit()
    return inserted, len(articles) - inserted


def record_fetch_run(conn, returned, inserted, duplicates, started_at=None):
    conn.execute(
        "INSERT INTO fetch_runs (started_at, returned, inserted, duplicates) VALUES (?,?,?,?)",
        (started_at or now(), returned, inserted, duplicates),
    )
    conn.commit()


def unjudged_articles(conn, profile_slug):
    return conn.execute(
        """SELECT a.* FROM articles a
           LEFT JOIN relevance r
             ON r.article_id = a.id AND r.source_type = a.source_type
            AND r.profile_slug = ?
           WHERE r.article_id IS NULL
           ORDER BY a.publish_time DESC""",
        (profile_slug,),
    ).fetchall()


def hit_articles(conn, profile_slug):
    return conn.execute(
        """SELECT a.* FROM articles a
           JOIN relevance r
             ON r.article_id = a.id AND r.source_type = a.source_type
           WHERE r.profile_slug = ? AND r.verdict = 'hit'
           ORDER BY a.publish_time DESC""",
        (profile_slug,),
    ).fetchall()


def cluster_members(conn, cluster_id):
    return conn.execute(
        """SELECT a.* FROM cluster_members m
           JOIN articles a ON a.id = m.article_id AND a.source_type = m.source_type
           WHERE m.cluster_id = ? ORDER BY a.publish_time""",
        (cluster_id,),
    ).fetchall()


def article_exists(conn, article_id, source_type):
    return bool(conn.execute(
        "SELECT 1 FROM articles WHERE id=? AND source_type=?", (article_id, source_type)
    ).fetchone())


def article_key(article_id, source_type):
    """去重主键是 (id, source_type)，但下游代码很容易图省事只传 id——
    id 目前恰好全局唯一（source_type 恒为 wx）所以不会立刻出错，但接了
    第二个信源后同名 id 就会撞车。所有跨脚本传递文章引用的地方统一用这个
    复合键，而不是裸 id。"""
    return f"{article_id}@{source_type or 'wx'}"


def parse_article_key(key):
    article_id, sep, source_type = key.partition("@")
    return article_id, (source_type if sep else "wx")


def normalize_article_key(value):
    """把裸 id（旧数据、或懒得写 source_type 的调用方）和复合键统一成同一形式，
    这样新旧两种写法在集合比较（冷却期的 Jaccard）时能正确匹配。"""
    return value if "@" in value else article_key(value, "wx")


def clustered_article_keys(conn, profile_slug):
    """该 profile 下已经归入某个聚类的文章——聚类脚本用它跳过已处理过的旧文章。"""
    rows = conn.execute(
        """SELECT m.article_id, m.source_type FROM cluster_members m
           JOIN clusters c ON c.id = m.cluster_id
           WHERE c.profile_slug = ?""",
        (profile_slug,),
    ).fetchall()
    return {(r["article_id"], r["source_type"]) for r in rows}


def hours_since_fetch(conn):
    """距上次采集多久。上游窗口只有约 12.5 小时，超了就有静默丢数据的风险。"""
    row = conn.execute("SELECT MAX(started_at) FROM fetch_runs").fetchone()
    if not row or not row[0]:
        return None, None
    try:
        last = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return row[0], None
    return row[0], round((datetime.now() - last).total_seconds() / 3600, 1)


def stats(conn):
    def one(sql, args=()):
        row = conn.execute(sql, args).fetchone()
        return row[0] if row else 0

    last_fetch, since = hours_since_fetch(conn)
    return {
        "last_fetch": last_fetch,
        "hours_since_fetch": since,
        "articles": one("SELECT COUNT(*) FROM articles"),
        "feeds": one("SELECT COUNT(DISTINCT feed_name) FROM articles"),
        "fetch_runs": one("SELECT COUNT(*) FROM fetch_runs"),
        "returned_total": one("SELECT COALESCE(SUM(returned),0) FROM fetch_runs"),
        "duplicates_total": one("SELECT COALESCE(SUM(duplicates),0) FROM fetch_runs"),
        "chars_total": one("SELECT COALESCE(SUM(LENGTH(clean_content)),0) FROM articles"),
        "clusters": one("SELECT COUNT(*) FROM clusters"),
        "earliest": one("SELECT MIN(publish_time) FROM articles"),
        "latest": one("SELECT MAX(publish_time) FROM articles"),
    }


def main():
    ap = argparse.ArgumentParser(description="工作区与配置")
    ap.add_argument("--workspace")
    ap.add_argument("--set-key", metavar="KEY", help="保存接口 key 到 config.json")
    ap.add_argument("--set-endpoint", metavar="URL")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.set_key or args.set_endpoint:
        path = save_config(api_token=args.set_key, api_endpoint=args.set_endpoint)
        print(json.dumps({"saved": str(path), "has_key": True}, ensure_ascii=False))
        return

    conn = connect(args.workspace)
    cfg = load_config()
    recent = [dict(r) for r in conn.execute(
        "SELECT started_at, returned, inserted, duplicates FROM fetch_runs ORDER BY id DESC LIMIT 5"
    ).fetchall()]
    print(json.dumps(
        {
            "workspace": str(workspace_path(args.workspace)),
            "config": str(config_path()),
            "has_key": bool(cfg.get("api_token") or os.environ.get("ZEROONE_TOKEN")),
            "profiles": [p.get("slug") for p in list_profiles(args.workspace)],
            **stats(conn),
            "recent_fetches": recent,
        },
        ensure_ascii=False, indent=2,
    ))


if __name__ == "__main__":
    main()
