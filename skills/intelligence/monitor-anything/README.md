# monitor-anything

用一句自然语言描述你关注的方向，剩下的交给它：每天自动完成**采集 → 清洗 → 语义筛选 →
跨源聚类 → 摘要成文**，产出一份深色科技风的可视化 HTML 日报。这是一个 [Claude Skill](https://docs.claude.com/)，
接入零一实验室（Lingyi Labs）的资讯数据池，在 Claude Code / Cowork 等支持 Skill 的产品里直接可用。

## 这是什么

- **数据采集**：多渠道（微信公众号、小红书、纽约时报、AI 热点……未来还会增加），有规模
- **数据整理**：去重、清洗噪音、语义理解筛选
- **跨源聚类**：识别「这 5 篇文章在讲同一件事，而且跨了 3 个渠道」
- **摘要与洞察**：不是简单罗列标题，而是告诉你「为什么这件事与你相关」

## 快速开始

在支持这个 Skill 的 Claude 产品里，直接说出你想监控的方向即可，例如：

> 帮我盯一下国内 AI 推理基础设施和芯片供应链的动态，我不关心融资八卦和课程广告。

第一次会引导你完成：
1. 检测/索取数据源 token（发邮件至 **gems9232@foxmail.com** 获取）
2. 用一段合成的示例数据（主题「AI 与智能汽车」）跑一遍完整流程，先看看报告长什么样
3. 确认你的关注方向、排除关键词、报告时间、数据保留天数
4. 抓取你自己的真实数据，生成第一份属于你的报告

之后每天会自动生成新的报告，固定文件名 `data/reports/dashboard.html`，单文件、双击即开、
可以直接发微信分享。

## 关于首次配置时的关注方向推断

Setup 阶段会尝试帮你推断关注方向（比如读 `CLAUDE.md`、项目 README、技术栈，或者调用
memory 能力），推断不出来就直接问你三个问题。这里有一条硬性边界：**只读取与工作/技术
背景相关的内容，推断结果必须原文展示给你确认，不会被静默使用。**

## 数据存放与隐私

- 所有抓取到的内容只保存在本地 `data/` 目录（SQLite + JSON），**默认保留 30 天**，过期自动清理
- `data/` 已加入 `.gitignore`，且 `harvest.py` 启动时会主动检查这个目录有没有被 git 跟踪
  ——抓取的是公众号、纽约时报的全文内容，误提交到公开仓库是真实的版权风险，不能只靠一行
  `.gitignore` 防
- token 需要邮件向 **gems9232@foxmail.com** 索取，一个 token 对应一个用户
- 报告仅供个人阅读筛选使用，不是数据资产项目、不做长期归档、不做搜索引擎（只处理每日增量，
  接口不支持历史回溯）

## sample 数据

`sample/articles.json` 里的约 250 条数据由 **AI 合成**，用于在拿到真实 token 之前先体验一遍
完整流程，不是真实抓取内容。详见 [sample/README.md](./sample/README.md)。

## 目录结构

```
monitor-anything/
├── SKILL.md              # Skill 入口：触发条件 + 主流程编排
├── ARCHITECTURE.md       # 面向专业用户的设计说明
├── scripts/               # 零依赖 Python 脚本（采集/清洗/编排/渲染/配置/定时任务）
├── prompts/                # 筛选/聚类/摘要三个阶段的 LLM 提示词
├── outputs/                # 扩展输出层（邮件、飞书/企微/钉钉 webhook）
├── assets/template.html    # HTML dashboard 模板
├── sample/                 # AI 合成的示例数据
└── data/                   # 本地数据（已 gitignore）：config.json / monitor.db / reports/
```

## 依赖

零第三方依赖，只用 Python 标准库（`urllib` / `sqlite3` / `json` / `hashlib` 等）。不需要
`pip install` 任何东西。
