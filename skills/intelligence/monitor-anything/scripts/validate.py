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

from typing import Any


class ValidationError(Exception):
    """契约校验失败。message 应该是可以直接展示给 Agent 看、指导它怎么改的人话。"""


# --------------------------------------------------------------------------
# ③筛选结果：{"kept": [...], "low_confidence": [...], "examples": [...]}

def validate_kept_ids(kept_ids: list[str], candidate_ids: set[str]) -> list[str]:
    """kept 必须是候选 id 的子集。去重后按原顺序返回；越界的 id 视为契约违反。"""
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
    seen: dict[str, int] = {}
    unknown: list[str] = []
    for idx, c in enumerate(clusters):
        ids = c.get("ids", [])
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
    """cluster_index 必须存在、不重复、且全部被覆盖；score 强制转成 0~10 的整数
    （超出范围的 clamp，而不是原样展示成「99/10」这种明显不合理的数字）。"""
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
        try:
            score = int(round(float(s.get("score", 0))))
        except (TypeError, ValueError):
            score = 0
        s["score"] = max(0, min(10, score))
        out.append(s)
    missing = cluster_indexes - seen_idx
    if missing:
        raise ValidationError(f"{len(missing)} 个聚类没有对应的摘要结果：{sorted(missing)}")
    return out
