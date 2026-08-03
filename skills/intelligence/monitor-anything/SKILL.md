---
name: monitor-anything
description: |
  订阅并监控任何你关注的行业与话题。接入多渠道资讯数据池，自动完成采集、清洗、
  语义筛选、跨源聚类与摘要，产出可视化日报。当用户提到监控资讯、行业动态、
  舆情、订阅话题、每日简报、信息聚合、追踪某个领域，或想知道「今天我关注的
  领域发生了什么」时，都应使用本 Skill —— 即使用户没有明确说出「监控」二字。
---

# monitor-anything

把用户一句自然语言描述的关注方向，变成每天自动更新的可视化日报。六个阶段
**采集①→清洗②→筛选③→聚类④→摘要⑤→渲染⑥** 的名称与数字要在你和用户的交流中原样出现
——这是这个 Skill 想要展示的核心方法论，不要用别的措辞替代。

零依赖、零 LLM API key：③④⑤三个语义阶段由**你自己**读 `prompts/*.md` 后直接推理完成，
不是调用某个外部模型接口。`scripts/` 下的 Python 脚本只做确定性的部分（网络请求、
数据库、格式转换），所有脚本零第三方依赖，只用标准库，可以直接 `python3 xxx.py` 运行。

## 什么时候触发这个 Skill

用户想追踪某个行业、话题、公司动态，想要每天的简报，或者问"今天我关注的领域有什么新动态"
——即使没有说"监控"两个字。也适用于"帮我看看今天有没有关于 XX 的消息"这类临时性请求
（临时请求走真实数据 pipeline 但不需要安装定时任务）。

## 判断是否需要 Setup

先检查 `data/config.json` 是否存在且包含至少一个 monitor（可以直接尝试读取该文件，
不存在或 `monitors` 为空说明是第一次使用）。

- **已配置**：跳到「日常运行」一节
- **未配置**：先走一遍下面的 Setup 流程

## Setup 流程（目标：30 秒内建立信任）

1. **检测 token**
   ```
   python3 scripts/setup.py check-token
   ```
   - 返回 `has_token: false`：向用户说明这个 Skill 需要接入零一实验室的资讯数据池，
     引导发邮件至 **gems9232@foxmail.com** 索取 token；同时告诉用户"想先看看效果？
     可以用示例数据跑一遍完整流程"
   - 返回 `valid: false`：直接把返回的 `message` 转述给用户（已经是人话，401 对应
     "token 好像失效了"，超时对应"暂时连不上数据服务"），不要出现状态码或堆栈
   - 用户拿到 token 后：`python3 scripts/setup.py set-token --token <token>`

2. **推断用户关注方向**（这一步是你自己的语义判断，不是脚本能做的）

   按能力从高到低尝试：
   - 如果能读文件系统：看 `CLAUDE.md`、`~/.claude/`、当前项目的 `README` 与技术栈
   - 如果有 memory 能力：调用 memory 看看有没有相关背景
   - 都没有：直接问用户三个问题——所在行业 / 具体关注什么 / 明确不想看什么

   > **边界要求（必须遵守）**：只读取与用户工作/技术背景相关的内容，不要翻看无关的
   > 个人文件。推断出的结果必须**原文展示给用户确认**，不能静默拿去用——哪怕你觉得
   > 推断很有把握。这条约束同样写在 README 里。

3. **生成 monitor 定义，用自然语言向用户复述确认**，例如：
   "我理解你关注的是 X，会排除 Y。每天早 8 点为你生成报告，对吗？"

   得到确认后，把 monitor 写成 JSON（`id` 用简短英文 slug，`description` 是用户认可的
   那句自然语言描述，是唯一必填字段），保存到临时文件，然后：
   ```
   python3 scripts/setup.py init-config --monitors-json <path> \
       --language zh --report-time 08:00 --retention-days 30
   ```
   语言（zh/en/raw）和保留天数也在这一步顺带问一句，默认 zh / 30 天。

4. **展示定时任务命令**（只展示，不要替用户执行）：
   ```
   python3 scripts/schedule.py show
   ```
   把输出原样展示给用户，说明这是可选的：装上之后 `harvest.py` 会每 6 小时自动采集一次，
   不装的话你之后可以随时手动运行。

5. **先用 sample 数据跑一遍**，让用户在等待真实数据之前先看到报告长什么样：
   ```
   python3 scripts/harvest.py run --sample
   ```
   然后走一遍下面「日常运行」的③~⑥步骤（`report.py` 的四个子命令 + `render.py`，
   记得加 `--sample` 参数给 `render.py` 让 HTML 顶部标注"这是示例报告"）。跑完后
   告诉用户 `data/reports/dashboard.html` 在哪，建议打开看看。

6. **再抓真实数据**，明确告诉用户"现在为你抓取真实数据，大约 30 秒"：
   ```
   python3 scripts/harvest.py run
   ```
   走一遍「日常运行」③~⑥（这次不加 `--sample`），生成用户自己的第一份报告。
   如果这次命中很少甚至 0 条，`report.py` 会自动给出诚实的兜底文案，不需要你额外解释，
   但可以提醯用户"这是每日增量，不是搜索引擎，明天再看看"。

## 日常运行（config.json 已存在时）

如果 `harvest.py` 有配好定时任务，采集①②已经在后台跑了；如果没有，先手动跑一次：
```
python3 scripts/harvest.py run
```

接下来对 `config.json` 里的**每一个 monitor**依次执行：

### ③ 筛选

```
python3 scripts/report.py candidates --monitor-id <id> --date <date>
```
输出候选文章列表（写到 `data/.work/candidates-<id>-<date>.json`，同时打印到 stdout）。

**读 `prompts/filter.md`**，按其中的说明对候选做标题级别语义筛选，把结果写成 JSON
（`kept` / `low_confidence` / `examples` 三个字段，具体结构见 prompt 文件），保存到
比如 `data/.work/filter-result-<id>-<date>.json`。

```
python3 scripts/report.py filtered --monitor-id <id> --date <date> \
    --input data/.work/filter-result-<id>-<date>.json
```

### ④ 聚类

上一步输出了聚类输入（`data/.work/cluster-input-<id>-<date>.json`）。

**读 `prompts/cluster.md`**，对这批文章做跨源同事件归并，把结果（`clusters` 数组，
每个元素含 `ids` 和 `ai_reasoning`）写到比如 `data/.work/cluster-result-<id>-<date>.json`。

```
python3 scripts/report.py clustered --monitor-id <id> --date <date> \
    --input data/.work/cluster-result-<id>-<date>.json
```

### ⑤ 摘要

上一步输出了摘要输入（`data/.work/summarize-input-<id>-<date>.json`）。

**读 `prompts/summarize.md`**，为每个聚类生成 `headline` / `summary` / `why_relevant` /
`score`，加上整体 `overview`，写到比如 `data/.work/summary-result-<id>-<date>.json`。

```
python3 scripts/report.py summarized --monitor-id <id> --date <date> \
    --input data/.work/summary-result-<id>-<date>.json
```

**所有 monitor 都跑完③~⑤之后**，收尾一次（只需要一次，不是每个 monitor 都跑）：
```
python3 scripts/report.py finalize --date <date>
```
这一步会检查最近的采集有没有失败（§13 失败告警），写进报告 JSON 的 `alerts` 字段。

### ⑥ 渲染

```
python3 scripts/render.py --date <date>
```
生成 `data/reports/<date>.md` 和固定文件名 `data/reports/dashboard.html`（每次覆盖，
内嵌最近 7 天数据，用户打开后可以切换日期）。跑完告诉用户报告在哪，如果 `finalize`
返回的 `alerts` 非空，要主动提醒用户"最近的采集出现了问题"。

## 关于进度展示

`harvest.py` 和 `report.py` 的每条命令都会自己打印六阶段进度表（阶段名 + 数量 +
耗时），不需要你额外复述数字——直接把命令的终端输出展示给用户就够了。这是"数据处理
能力"在运行时唯一的实时体感，不要用一句"处理中"替代掉这些输出。

## 关于零命中

`report.py summarized` 在某个 monitor 一个精选都没命中时，会自动把 `overview` 换成
诚实的兜底文案（"今天你关注的方向没有明显动静……"），线索区依然会展示当天处理过的
全部聚类。你不需要额外编造内容让报告看起来更热闹——诚实比好看更重要。

## 扩展输出（可选，第二阶段能力）

如果用户想要邮件或群机器人推送，参考 `outputs/_contract.md`，在 `config.json` 的
`outputs` 数组里加上 `email` / `webhook`，并按 `outputs/email.py` / `outputs/webhook.py`
文件头的说明填好 `outputs_config`。这两个 emitter 只消费 `reports/<date>.json`，
不需要重新跑一遍③~⑤。

## 深入了解

- 设计取舍（为什么小量级用 LLM 而非算法、为什么脚本与 Agent 分工、去重策略、
  为什么采集与报告解耦）：`ARCHITECTURE.md`
- 三个 prompt 文件的完整输入输出结构：`prompts/filter.md` / `prompts/cluster.md` /
  `prompts/summarize.md`
- 报告 JSON 的完整字段说明：`ARCHITECTURE.md` 第 7、8 节 + `scripts/report.py` 顶部注释
