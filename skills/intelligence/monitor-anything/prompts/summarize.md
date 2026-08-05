# 阶段⑤ 摘要 prompt

**输入**：`scripts/report.py clustered` 产出的 JSON，结构为：

```json
{
  "clusters": [
    {
      "cluster_index": 0,
      "articles": [ { "id": "...", "title": "...", "source_type": "wx", "description": "...", "content_snippet": "...", "url": "..." } ],
      "ai_reasoning": "...",
      "cross_source": true,
      "source_count": 3
    }
  ],
  "language": "zh",
  "monitor_description": "用户对自己关注方向的自然语言描述"
}
```

## 你的任务

摘要与打分都要围绕 `monitor_description`（用户对自己关注方向的自然语言描述）来写，不是
写一份通用新闻摘要——这个字段由 `report.py` 从阶段③一路透传过来，直接用即可，不需要你
再去读 `data/config.json`。

**`language`（"zh" 或 "en"）决定 `headline`/`summary`/`why_relevant`/`overview` 这几个
字段用什么语言写**——这是用户实际会读到的报告正文，语言设置在这里最要紧。原文文章是
中文还是英文都不影响这个判断：如果 `language` 是 `"en"`，即使原文全是中文，也要把
headline/summary 等写成英文；反之亦然。不要因为原文语言而顺手写成另一种语言。

对每个聚类生成：

- `headline`：提炼的事件标题，8~20 字，不是照抄某一篇原文标题，而是概括这个事件本身
- `summary`：2~4 句摘要，说清楚「发生了什么」，如果是跨源聚类可以简单点出不同渠道的角度差异
- `why_relevant`：一句话说明这件事为什么与用户描述的关注方向相关。这是「为什么与你相关」，
  要具体到用户描述里的措辞，不要写「因为与该话题相关」这种同义反复
- `score`：0~10 的相关度分数，衡量的是「对用户描述的关注方向有多重要/多值得看」，不是新闻本身
  的大小。参考标准：
  - 8~10：直接命中关注方向的核心，值得优先看
  - 6~7：相关，值得收录进「精选」
  - 3~5：沾边但不是重点，进入「线索区」，只列标题
  - 0~2：基本不相关（如果聚类阶段已经很准，这个分数区间应该很少出现）

同时写一句 `overview`（不是针对单个聚类，是针对整个 monitor 当天的情况）：一句话总览今天
这个关注方向上发生了什么，如果聚类结果里相关度普遍不高，如实说明（不要夸大凑数）。

## 输出

写成 JSON 文件，结构：

```json
{
  "overview": "今天你关注的方向上，最大的动态是……",
  "clusters": [
    { "cluster_index": 0, "headline": "...", "summary": "...", "why_relevant": "...", "score": 8 }
  ]
}
```

`cluster_index` 必须与输入中的 `cluster_index` 一一对应，脚本会据此把摘要和原始文章列表拼回去。

## 零命中/低命中兜底

如果打分下来大部分聚类都低于 6 分（min_score 默认值，具体以 `data/config.json` 里的
`min_score` 为准），如实体现在 `overview` 里，例如（中文示例，`language` 是 `"en"` 时
照样要写成英文）：「今天你关注的方向没有特别突出的动态，以下是当天资讯的整体情况」。
不要为了让报告看起来热闹而人为拔高分数 —— 诚实比好看更重要，
这一点在 SKILL.md 和 README 里反复强调过。`score` 低于阈值的聚类会自动进入「线索区」，
用户依然能看到系统确实处理了当天全部数据。
