# ZeroOne Skills

[中文](README.md) | [English](README_EN.md)

ZeroOne Skills 是一个面向 AI Agent 的开源技能仓库。

我们关注怎样把真实工作中的方法、判断标准和操作流程，沉淀为 Agent 可以理解、执行和复用的 Skills。目前主要覆盖信息监控与行业情报，后续会持续扩展更多工程和生产力场景。

## 浏览 Skills

### Intelligence · 信息与情报

| Skill | 一句话介绍 |
| --- | --- |
| [monitor-anything](skills/intelligence/monitor-anything/README.md) | 用自然语言定义关注方向，自动生成经过筛选、跨源聚类和摘要的每日可视化报告。 |
| [industry-insight](skills/intelligence/industry-insight/SKILL.md) | 把资讯数据整理成一条可检查、可干预的行业情报分析流水线。 |

## 如何阅读这个仓库

1. **按分类浏览**：从上面的分类和一句话介绍中找到感兴趣的 Skill。
2. **先看 `README.md`**：了解它解决什么问题、适合什么场景，以及如何开始使用。
3. **再看 `SKILL.md`**：了解 Agent 的触发条件、工作步骤和执行边界。
4. **按需深入**：`ARCHITECTURE.md`、`references/`、`prompts/` 和 `scripts/` 分别包含架构、规范、提示词与确定性实现。

每个 Skill 都是相对独立的目录。只需阅读或安装你感兴趣的 Skill，不必理解整个仓库。

```text
skills/
├── intelligence/   # 信息监控、行业情报与研究
├── engineering/    # 工程工作流（持续建设中）
└── productivity/   # 通用生产力（持续建设中）
```

具体安装方式和使用说明以各 Skill 自己的 README 为准。

## 关于团队

ZeroOne Skills 团队专注于 AI Agent 与数据智能产品。我们希望将经过真实任务验证的工作流做成清晰、可组合、可持续迭代的开放能力，而不只是一次性的提示词。

## 参与贡献

欢迎通过 Issue 和 Pull Request 提交问题、改进文档或贡献新的 Skill。

## License

[MIT License](LICENSE)
