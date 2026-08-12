# ZeroOne Skills

[中文](README.md) | [English](README_EN.md)

面向 AI Agent 的开源技能集合，聚焦信息监控、行业情报与可复用的业务工作流。

每个 Skill 都把任务说明、提示词、确定性脚本和必要的前端资源放在一个独立目录中。将需要的目录复制到支持 Agent Skills 的产品里，就可以通过自然语言触发完整工作流。

## Skills

| Skill | 用途 | 主要产物 |
| --- | --- | --- |
| [monitor-anything](skills/intelligence/monitor-anything/README.md) | 用自然语言定义关注方向，自动完成采集、清洗、语义筛选、跨源聚类、摘要和渲染 | 每日独立 HTML 报告与历史目录首页，支持中文和英文 |
| [industry-insight](skills/intelligence/industry-insight/SKILL.md) | 将资讯数据按关注点过滤、聚类并进行多信源成稿判定 | 结构化行业情报与单文件 Dashboard |

两个 Skill 并列提供：`monitor-anything` 侧重日常订阅与自动报告，`industry-insight` 侧重可检查、可干预的行业情报流水线。可以根据工作方式任选其一。

## 安装

### 从仓库复制

```bash
git clone https://github.com/g453030291/zeroone-skills.git
cd zeroone-skills

# 复制你需要的 Skill；目标目录请替换成 Agent 产品的 Skills 目录
cp -R skills/intelligence/monitor-anything /path/to/your/skills/
cp -R skills/intelligence/industry-insight /path/to/your/skills/
```

不同 Agent 产品的 Skills 目录和加载方式可能不同，请以对应产品的说明为准。只需复制准备使用的 Skill，不必安装整个仓库。

### 通过 SkillHub 安装 monitor-anything

`monitor-anything` 同时发布在 [SkillHub](https://skillhub.cn/team-skills/monitor-anything)。已经安装 SkillHub CLI 时，可以运行：

```bash
skillhub install monitor-anything --dir <Agent 的 Skills 目录>
```

`--dir` 必须指向当前 Agent 实际加载 Skills 的目录。尚未安装 SkillHub CLI 时，请先查看 [SkillHub 安装说明](https://skillhub.cn/install/skillhub.md)，也可以直接把这个链接交给 Agent 处理。

## 使用

安装后直接用自然语言描述任务。Agent 会根据 Skill 的触发说明判断并运行相应流程。

例如：

> 帮我每天关注国内 AI 推理基础设施和芯片供应链的动态，排除融资八卦和课程广告。

> 帮我整理最近新能源汽车行业的重要变化，只有经过多个独立信源印证的事件才进入情报。

每个 Skill 的首次配置、自动化方式、数据边界和详细用法，请查看对应目录中的 `README.md` 或 `SKILL.md`。

## 目录结构

```text
zeroone-skills/
├── skills/
│   └── intelligence/
│       ├── monitor-anything/
│       └── industry-insight/
├── LICENSE
├── README.md
└── README_EN.md
```

一个 Skill 通常包含：

- `SKILL.md`：触发条件和 Agent 执行流程
- `scripts/`：网络、存储、转换等确定性操作
- `prompts/` 或 `references/`：语义任务说明和输出规范
- `assets/`：报告模板等静态资源

具体结构以各 Skill 目录为准。

## 参与贡献

欢迎通过 Issue 或 Pull Request 提交问题、文档改进和新的 Skill。请让每项改动保持聚焦，并在提交前确认示例命令、相对链接和数据边界与实际实现一致。

## License

本项目采用 [MIT License](LICENSE)。
