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

1. **检测 token（v2：不再需要用户先发邮件）**
   ```
   python3 scripts/setup.py check-token
   ```
   - 返回 `has_token: false, auto_provisioned` 缺失或为空：说明自动申请也失败了（通常是
     网络问题），才需要引导用户邮件联系 **gems9232@foxmail.com**
   - 返回 `has_token: true, auto_provisioned: true`：脚本已经自动向零一实验室申请了一个
     **30 天有效期的试用 token** 并写入 config.json，用户不需要做任何事——直接把返回的
     `message`（里面带着到期日期）转述给用户即可，不用等他们回邮件
   - 返回 `valid: false`：直接把返回的 `message` 转述给用户（已经是人话，401 对应
     "token 好像过期或失效了"，超时对应"暂时连不上数据服务"），不要出现状态码或堆栈。
     token 过期后**不会**自动续期——那是明确的 SOP：引导用户邮件联系
     **gems9232@foxmail.com** 申请延长有效期
   - 返回里如果带 `expiry_note`（临期提醒，≤5 天）：顺带提醒用户一句，不用等真的过期
   - 如果用户自己邮件申请到了正式 token：`python3 scripts/setup.py set-token --token <token>`

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

   monitor JSON 里还可以顺带带上几个可选字段，dashboard 会用得上（缺省时都有兜底，
   不影响流程跑通）：`focus_tags`（2~4 个关注点标签，用于 dashboard 头部展示）、
   `setup_note`（一句更完整的"配置时你说……"确认语，缺省会自动用 `description` 拼一句）。
   还有一个 `output_kind` 字段（`digest`/`decision`），默认 `digest`——`decision`（决策型，
   dashboard 上会多一个"今日结论"区块）目前只是预留的 schema 开关，本版 report.py 还没有
   实现"该做什么/何时做"的语义判断逻辑，不要把 monitor 设成 `decision` 然后期待看到实际
   建议内容。

4. **展示定时任务命令**（只展示，不要替用户执行）：
   ```
   python3 scripts/schedule.py show
   ```
   把输出原样展示给用户，说明这是可选的：装上之后 `harvest.py` 会每 6 小时自动采集一次，
   不装的话你之后可以随时手动运行。

5. **抓真实数据**，明确告诉用户"现在为你抓取数据，大约 30 秒"（v2 起没有 sample 数据这
   一步了——token 是自动申请到的，不需要一份合成数据来垫等待时间，直接跑真实流程）：
   ```
   python3 scripts/harvest.py run
   ```
   走一遍下面「日常运行」的③~⑥步骤（`report.py` 的四个子命令 + `render.py`），生成用户
   自己的第一份报告。如果某个 monitor 筛选后一条都没命中，③筛选一节里的"补充检索"会先
   尝试兜底；如果补充之后依然没有，`report.py` 会自动给出诚实的兜底文案，不需要你额外
   解释，但可以提醒用户"这是每日增量，不是搜索引擎，明天再看看"。

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

**补充检索（v2 新增，只在需要时触发）**：如果这一步返回的 JSON 里 `needs_search_augment`
是 `true`（意味着 `kept` 是 0——当前数据池对这个 monitor 一条都没命中），先不要直接往下走
聚类，按下面的顺序补一次检索，尽量让用户当天也能看到实际内容：

1. 用返回里带的 `monitor_description`，拟一个适合搜索引擎的查询词（可以是中文，也可以
   翻译成更容易检索到国际信源的英文，你自己判断——这是语义工作，不是脚本能做的）。
2. ```
   python3 scripts/search.py ingest --query "<你拟的查询词>" --max-results 8
   ```
   这一步只接受最近 24 小时内的搜索结果，会自动写入 `monitor.db`（`source_type` 标记为
   `search`，复用①②阶段的去重/清洗逻辑）。
3. **重新跑一次候选**（这次候选列表会包含刚写入的 search 结果）：
   ```
   python3 scripts/report.py candidates --monitor-id <id> --date <date>
   ```
4. 再读一遍 `prompts/filter.md`，对新的候选做一次筛选，然后照常调用 `report.py filtered`
   继续往下走。

**这个补充检索最多做一次**：如果第二次筛选依然是 0 命中，说明确实没有可用信息，直接进入
下面「日常运行」④⑤⑥的正常流程——`report.py summarized` 会给出诚实的零命中兜底文案，
不要因为搜索也没找到就反复重试或强行编造内容凑数。

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

### ⑥ 渲染 + 生成分享链接

```
python3 scripts/render.py --date <date>
```
生成固定文件名 `data/reports/dashboard.html`（每次覆盖，v2 起不再额外生成 Markdown——
没有任何地方展示或引用它，`reports/<date>.json` 才是唯一的每日数据产物，dashboard.html
是纯粹从它渲染出来的，见 ARCHITECTURE.md §7）。内嵌的历史数据跟 `retention.reports_days`
保持一致——默认 30 天，磁盘上留多久，dashboard 里日期切换器就能翻多久，不会出现"文件还在
但翻不到"的情况。

**v3：渲染完之后紧接着自动生成一次分享链接**，不用等用户开口要分享（原因和取舍见
ARCHITECTURE.md §20）：
```
python3 scripts/share.py upload --date <date>
```
这一步是每天流程里固定的一部分，不是可选步骤——`dashboard.html` 上「分享今日洞察」
按钮默认就应该有一个能用的公开链接。如果这一步失败（比如网络问题、token 过期），
**不要中断当天的流程**：本地的 `dashboard.html` 已经生成好、可以正常查看，只是分享按钮
那天会退化成复制文案 + 提示重试（模板里已经处理了这个兜底状态）。跑完告诉用户报告在哪，
如果 `share.py` 给出的 `share_url` 有效，一并发给用户；如果失败，用 stderr 里的人话
原因告诉用户（比如"token 过期了，邮件联系 gems9232@foxmail.com"），不用刻意隐瞒失败，
但也不必因为分享链接没生成就说整个报告失败了——两者是独立的产出。如果 `finalize` 返回的
`alerts` 非空，也要主动提醒用户"最近的采集出现了问题"。

## 关于进度展示

`harvest.py` 和 `report.py` 的每条命令都会自己打印六阶段进度表（阶段名 + 数量 +
耗时），不需要你额外复述数字——直接把命令的终端输出展示给用户就够了。这是"数据处理
能力"在运行时唯一的实时体感，不要用一句"处理中"替代掉这些输出。

## 关于零命中

v2 起，`report.py filtered` 返回 0 命中时你应该先按③筛选一节的说明补一次检索，而不是
直接跳到这里——补充检索是为了尽量让每个 monitor 当天都有实际内容。只有补充之后依然
0 命中，`report.py summarized` 才会自动把 `overview` 换成诚实的兜底文案（"今天你关注的
方向没有明显动静……"），线索区依然会展示当天处理过的全部聚类。你不需要额外编造内容让
报告看起来更热闹——诚实比好看更重要。

## 分享报告（v3：渲染后自动生成，不用等用户开口）

`dashboard.html` 上有一个「分享今日洞察」按钮。v3 起，每天走完⑥渲染之后你会紧接着自动
跑一次 `share.py upload`（见上面⑥的说明），所以正常情况下用户打开 dashboard 时分享链接
已经在那了——点按钮就是复制这个真链接，不需要再专门找你要。

按钮本身依然不会自己发网络请求——`dashboard.html` 是会被转发出去的静态文件，把能鉴权的
token 写进它的 JS 里等于把 token 一起发给收到文件的任何人，这条边界没有变（详见
ARCHITECTURE.md §12）。变的只是"谁来触发上传、什么时候触发"：以前是等用户开口，v3 起是
你每天渲染完就自动做一次。

如果用户仍然主动说"分享一下今天的报告"/"重新生成一下分享链接"（比如当天自动上传失败了，
或者报告内容后来又更新过），照样是你来手动触发一次：
```
python3 scripts/share.py upload --date <date>
```
跑完把命令输出里的 `share_url` 直接发给用户即可，不需要额外解释实现细节。如果失败，
`stderr` 里已经是人话（比如 token 过期会提示邮件联系 gems9232@foxmail.com）。

产出路径目前只有这一条：HTML dashboard（本地文件 + 按需上传换公开链接）。v2 起不再有
邮件 / 群机器人推送这类可插拔扩展层——不要向用户建议这些能力，也不用去找 `outputs/`
目录，那个目录已经不存在了。

## 深入了解

- 设计取舍（为什么小量级用 LLM 而非算法、为什么脚本与 Agent 分工、去重策略、
  为什么采集与报告解耦）：`ARCHITECTURE.md`
- 三个 prompt 文件的完整输入输出结构：`prompts/filter.md` / `prompts/cluster.md` /
  `prompts/summarize.md`
- 报告 JSON 的完整字段说明：`ARCHITECTURE.md` 第 7、8 节 + `scripts/report.py` 顶部注释
