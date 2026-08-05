#!/usr/bin/env python3
"""validate.py —— 校验 Agent 在③④⑤阶段写回的 JSON 是否符合契约。

report.py 的 filtered / clustered / summarized 三个命令，接收的是 Agent（也就是正在
执行 monitor-anything 这个 Skill 的你）读 prompts/*.md 后自己写出的 JSON。之前脚本对
这份输入完全信任——ID 越界就悄悄跳过、聚类重复就照单全收、score 超范围就原样展示，
出了问题也不会有任何提示，用户只会看到一份不完整或自相矛盾的报告。

这个模块只做一件事：在脚本处理 Agent 的输出之前，先检查它是否满足 prompts/*.md
里描述的契约。检查不通过就抛 ValidationError，调用方（report.py）负责打印人类可读的
错误信息、以非 0 退出——不带着有问题的数据继续往下游走。

零第三方依赖，不做网络请求或数据库访问，纯粹是数据结构上的一致性检查。
"""

from __future__ import annotations

import math
from typing import Any


class ValidationError(Exception):
    """契约校验失败。message 应该是可以直接展示给 Agent 看、指导它怎么改的人话。"""


def _require_list(value: Any, field: str) -> list[Any]:
    """顶层类型守卫。

    Agent 写出的 JSON 里这几个字段本该是数组，但写错成 `{}`、`null`、字符串的情况是
    真实存在的。以前这里直接开始遍历/取属性，Python 抛出的是 `AttributeError` /
    `TypeError` 这种堆栈——对调用方来说是崩溃，不是"校验没过"，也没法告诉 Agent 该怎么
    改。统一先过一道类型检查，把它变成和其他契约违反一样的 ValidationError。
    """
    if not isinstance(value, list):
        raise ValidationError(
            f"{field} 应该是一个数组，实际是 {type(value).__name__}：{value!r:.80}。"
            f"请检查写出的 JSON 结构。"
        )
    return value


def _require_dicts(items: list[Any], field: str) -> list[dict[str, Any]]:
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValidationError(
                f"{field}[{i}] 应该是一个对象，实际是 {type(item).__name__}：{item!r:.80}"
            )
    return items


def _require_nonempty_text(value: Any, field: str, index: Any) -> None:
    """headline / summary 是用户在报告里唯一会读到的东西，空字符串等于交了白卷。

    以前空值能一路通过校验，最终渲染成一张只有分数、没有标题也没有正文的卡片——
    看起来像页面坏了，但流程全程返回 0，没有任何地方提示出了问题。
    """
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(
            f"cluster_index {index} 的 {field} 是空的（{value!r}）。"
            f"这是用户在报告里直接会读到的字段，必须写出实际内容。"
        )


# --------------------------------------------------------------------------
# ③筛选结果：{"kept": [...], "low_confidence": [...], "examples": [...]}

def validate_kept_ids(kept_ids: list[str], candidate_ids: set[str]) -> list[str]:
    """kept 必须是候选 id 的子集。去重后按原顺序返回；越界的 id 视为契约违反。"""
    kept_ids = _require_list(kept_ids, "kept")
    seen: list[str] = []
    unknown: list[str] = []
    for i in kept_ids:
        if i not in candidate_ids:
            unknown.append(i)
        elif i not in seen:
            seen.append(i)
    if unknown:
        preview = unknown[:10]
        more = f"（还有 {len(unknown) - 10} 个未列出）" if len(unknown) > 10 else ""
        raise ValidationError(
            f"kept 里有 {len(unknown)} 个 id 不在候选列表中，可能是编造或拼写错误：{preview}{more}"
        )
    return seen


# --------------------------------------------------------------------------
# ④聚类结果：{"clusters": [{"ids": [...], "ai_reasoning": "..."}]}

def validate_clusters(clusters: list[dict[str, Any]], article_ids: set[str]) -> None:
    """三条契约：
    1. 每个 cluster.ids 必须是本轮文章 id 的子集
    2. 同一个 id 不能出现在多个 cluster 里
    3. 每篇文章都必须被恰好一个 cluster 覆盖（单篇独立成簇也算覆盖，见 cluster.md）
    """
    clusters = _require_dicts(_require_list(clusters, "clusters"), "clusters")
    seen: dict[str, int] = {}
    unknown: list[str] = []
    for idx, c in enumerate(clusters):
        ids = _require_list(c.get("ids", []), f"clusters[{idx}].ids")
        if not ids:
            raise ValidationError(
                f"聚类 {idx} 的 ids 是空列表——没有同伴的文章也应该单独成一个只含它自己的聚类，"
                "不能提交一个空聚类"
            )
        for i in ids:
            if i not in article_ids:
                unknown.append(i)
                continue
            if i in seen:
                raise ValidationError(
                    f"文章 id {i!r} 同时出现在聚类 {seen[i]} 和聚类 {idx} 中，"
                    "同一篇文章只能属于一个聚类"
                )
            seen[i] = idx
    if unknown:
        preview = unknown[:10]
        more = f"（还有 {len(unknown) - 10} 个未列出）" if len(unknown) > 10 else ""
        raise ValidationError(f"聚类结果里有 {len(unknown)} 个 id 不在本轮文章列表中：{preview}{more}")
    missing = sorted(article_ids - seen.keys())
    if missing:
        preview = missing[:10]
        more = f"（还有 {len(missing) - 10} 个未列出）" if len(missing) > 10 else ""
        raise ValidationError(
            f"{len(missing)} 篇文章没有被任何聚类覆盖：{preview}{more}"
        )


# --------------------------------------------------------------------------
# ⑤摘要结果：{"overview": "...", "clusters": [{"cluster_index": 0, "score": 8, ...}]}

def validate_summaries(
    summaries: list[dict[str, Any]], cluster_indexes: set[int]
) -> list[dict[str, Any]]:
    """cluster_index 必须存在、不重复、且全部被覆盖；headline / summary 不能为空；
    score 强制转成 0~10 的整数（超出范围的 clamp，而不是原样展示成「99/10」这种明显
    不合理的数字）。"""
    summaries = _require_dicts(_require_list(summaries, "clusters"), "clusters")
    seen_idx: set[int] = set()
    out: list[dict[str, Any]] = []
    for s in summaries:
        idx = s.get("cluster_index")
        if idx not in cluster_indexes:
            raise ValidationError(f"摘要结果引用了不存在的 cluster_index：{idx!r}")
        if idx in seen_idx:
            raise ValidationError(f"cluster_index {idx} 出现了多次摘要结果，应该一对一")
        seen_idx.add(idx)
        s = dict(s)
        _require_nonempty_text(s.get("headline"), "headline", idx)
        _require_nonempty_text(s.get("summary"), "summary", idx)
        # float("inf") / float("nan") 能通过 float()，但 int(round(inf)) 抛的是
        # OverflowError、int(round(nan)) 抛的是 ValueError——前者不在下面的 except 里，
        # 会变成没人接的崩溃。先显式判掉非有限值，再走正常的 clamp。
        raw_score = s.get("score", 0)
        try:
            score_f = float(raw_score)
        except (TypeError, ValueError):
            score_f = 0.0
        if not math.isfinite(score_f):
            raise ValidationError(
                f"cluster_index {idx} 的 score 不是一个有限数字（{raw_score!r}）。"
                f"score 应该是 0~10 之间的整数。"
            )
        s["score"] = max(0, min(10, int(round(score_f))))
        out.append(s)
    missing = cluster_indexes - seen_idx
    if missing:
        raise ValidationError(f"{len(missing)} 个聚类没有对应的摘要结果：{sorted(missing)}")
    return out
