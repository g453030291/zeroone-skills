# monitor-anything

[中文](README.md) | [English](README_EN.md) | [返回 ZeroOne Skills](../../../README.md)

用一句自然语言定义关注方向，让 AI Agent 每天把分散资讯整理成一份值得阅读的可视化报告。

`monitor-anything` 会完成一条完整的信息流水线：

**采集① → 清洗② → 语义筛选③ → 跨源聚类④ → 摘要成文⑤ → 可视化渲染⑥**

它适用于 Claude Code、Codex、Cowork 等支持 Agent Skills 的产品。Python 脚本只负责网络请求、数据库和格式转换；筛选、聚类和摘要由当前 Agent 依据 Skill 内的提示词完成，因此不需要额外配置 LLM API key。

## 你会得到什么

- **贴合关注方向**：用自然语言说明想看什么、不想看什么，而不是维护复杂规则。
- **多渠道信息池**：覆盖微信公众号、小红书、纽约时报、AI 热点等来源；实际可用范围以上游数据服务为准。
- **跨源事件聚类**：把多个独立账号或域名对同一事件的报道合并，减少重复阅读。
- **判断而非标题堆叠**：报告说明事件内容、与你的关系和各信源之间的差异。
- **中英文报告**：报告正文、页面文案和渠道名称统一使用配置的语言。
- **每日独立报告**：每天生成一个可单独打开和分享的 HTML 文件，同时维护一个本地历史目录。

这个 Skill 面向每日增量阅读，不是历史搜索引擎、全文归档系统或数据资产平台。

## 快速开始

### 方式一：从 SkillHub 安装

`monitor-anything` 的版本和详情发布在 [SkillHub](https://skillhub.cn/team-skills/monitor-anything)。已经安装 SkillHub CLI 时，运行：

```bash
skillhub install monitor-anything --dir <Agent 的 Skills 目录>
```

`--dir` 必须指向当前 Agent 实际加载 Skills 的目录。尚未安装 CLI 时，请先按照 [SkillHub 安装说明](https://skillhub.cn/install/skillhub.md)操作，也可以直接把该说明链接交给 Agent。

### 方式二：从仓库复制

```bash
git clone https://github.com/g453030291/zeroone-skills.git
cp -R zeroone-skills/skills/intelligence/monitor-anything /path/to/your/skills/
```

请将目标路径替换为当前 Agent 产品的 Skills 目录。安装完成后，直接对 Agent 说：

> 帮我盯一下国内 AI 推理基础设施和芯片供应链的动态，我不关心融资八卦和课程广告。

首次使用时，Agent 会：

1. 检查数据服务 token；没有 token 时，尝试自动申请一个有效期为 30 天的试用 token。
2. 推断或询问你的关注方向、排除项、报告语言、生成时间和数据保留天数。
3. 原样展示推断结果，得到确认后才写入配置。
4. 抓取真实数据并生成第一份报告。
5. 如果宿主产品支持定时唤醒，尝试创建独立的采集任务和日报任务。

试用 token 不会在到期后自动续期。届时需要通过项目发布渠道获取或延长有效 token。

## 它如何工作

| 阶段 | 执行者 | 作用 |
| --- | --- | --- |
| ① 采集 | Python 脚本 | 拉取最近 24 小时的增量资讯 |
| ② 清洗 | Python 脚本 | 规范内容、去重并写入本地 SQLite |
| ③ 语义筛选 | AI Agent | 根据关注方向判断相关性 |
| ④ 跨源聚类 | AI Agent | 合并独立信源对同一事件的报道 |
| ⑤ 摘要成文 | AI Agent | 生成标题、摘要、相关性说明和整体概览 |
| ⑥ 可视化渲染 | Python 脚本 | 生成 HTML 报告、历史目录和分享链接 |

如果某个关注方向筛选后没有命中，Agent 会最多进行一次补充检索，并且仍只接受最近 24 小时内的结果。补充后依然没有内容时，报告会诚实说明零命中，不会编造资讯凑数。

完整的 Agent 执行步骤见 [SKILL.md](SKILL.md)，设计取舍和文件职责见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 自动化

Skill 本身没有常驻后台进程，也不会修改系统的 `cron` 或 `launchd`。自动运行依赖宿主 Agent 产品的定时唤醒能力。

Setup 会在能力可用时尝试创建两类任务：

- **采集任务**：默认每天在 0、6、12、18 点拉取一次增量。成功时静默，失败时发送简短提醒。
- **报告任务**：默认每天 08:00 运行筛选、聚类、摘要、渲染和分享，并发送一次最终结果通知。

报告时间和采集时刻采用运行环境所在时区。宿主产品不支持自动化时，Agent 会说明限制；你仍然可以随时手动要求它生成最新报告。

## 报告与本地文件

默认数据位于 Skill 目录下的 `data/`：

```text
data/
├── config.json                 # monitors、语言、时间和 token 等配置
├── monitor.db                  # 原始内容、处理状态和运行记录
└── reports/
    ├── YYYY-MM-DD.json         # 当天结构化报告
    ├── YYYY-MM-DD.html         # 当天独立报告页
    ├── dashboard.html          # 本地历史目录首页
    └── dates-manifest.js       # 历史目录使用的日期摘要
```

`data/` 已被仓库的 `.gitignore` 排除，只有用于保留目录的 `.gitkeep` 会进入 Git。默认保留最近 30 天的数据，运行采集时会清理过期记录。

## 数据、分享与隐私

使用前请了解以下边界：

- 原始内容、配置和中间结果默认保存在本机的 `data/` 目录。
- 脚本会连接零一实验室数据服务，用于申请试用 token、获取资讯、必要时补充检索，以及上传报告。
- 每日独立报告 `YYYY-MM-DD.html` 默认会在生成后上传到零一实验室分享服务，得到一个任何拿到链接的人都能访问的公开地址。上传范围只包含当天这一份报告，不包含历史目录或其他日期。
- 当天报告页内置百度统计。浏览器打开报告时会向 `hm.baidu.com` 发送标准访问统计事件；报告正文不会作为统计事件内容发送。`dashboard.html` 不包含该统计脚本。
- token 只保存在本地配置中，不会写入或嵌入可分享的 HTML。
- 临时 token 的申请限额按客户端 IP 计算；token 创建后不绑定使用 IP。

如果你不接受默认上传公开报告或页面访问统计，不应在未调整实现和流程的情况下使用默认分享功能。

## 常用检查命令

以下命令需在 `monitor-anything` 目录中执行：

```bash
# 检查或自动申请试用 token
python3 scripts/setup.py check-token

# 手动采集最新增量
python3 scripts/harvest.py run

# 查看最近的采集状态
python3 scripts/harvest.py status
```

完整报告不是一个纯脚本命令：③④⑤需要 AI Agent 阅读 `prompts/` 并进行语义判断。日常使用时应直接唤起这个 Skill，而不是尝试绕过 Agent 拼接整个流程。

## 配置与依赖

- Python 3
- 仅使用 Python 标准库，无需 `pip install`
- 报告语言：`zh` 或 `en`
- 默认报告时间：`08:00`
- 默认采集时刻：`0 / 6 / 12 / 18`
- 默认数据保留：30 天

配置由首次 Setup 写入 `data/config.json`。涉及长期 token 时，应通过标准输入交给设置命令，避免凭据出现在命令历史或进程列表中；详细操作由 [SKILL.md](SKILL.md) 约束。

## 目录结构

```text
monitor-anything/
├── SKILL.md
├── ARCHITECTURE.md
├── README.md
├── README_EN.md
├── scripts/
├── prompts/
├── assets/
└── data/
```

## License

随 ZeroOne Skills 项目采用 [MIT License](../../../LICENSE)。
