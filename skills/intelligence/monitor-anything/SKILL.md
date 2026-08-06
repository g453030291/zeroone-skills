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

设计取舍（为什么这样分工、历史版本演进）见 `ARCHITECTURE.md`，这里只写你需要照做的流程。

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
   - 返回 `has_token: false, auto_provisioned` 缺失或为空：说明自动申请也失败了（通常是
     网络问题），才需要引导用户邮件联系 **gems9232@foxmail.com**
   - 返回 `has_token: true, auto_provisioned: true`：脚本已经自动向零一实验室申请了一个
     **30 天有效期的试用 token** 并写入 config.json，用户不需要做任何事——直接把返回的
     `message`（里面带着到期日期）转述给用户即可，不用等他们回邮件
   - 返回 `valid: false`：直接把返回的 `message` 转述给用户（已经是人话，401 对应
     "token 好像过期或失效了"，超时对应"暂时连不上数据服务"），不要出现状态码或堆栈。
     token 过期后**不会**自动续期——引导用户邮件联系 **gems9232@foxmail.com** 申请延长
   - 返回里如果带 `expiry_note`（临期提醒，≤5 天）：顺带提醒用户一句，不用等真的过期
   - 如果用户自己邮件申请到了正式 token：把 token 从 stdin 喂进去，
     `echo '<token>' | python3 scripts/setup.py set-token`。**不要**用
     `--token <token>` 的形式——命令行参数会留在进程列表和 shell 历史里，那是一个
     长期有效的凭据不该出现的地方（脚本仍接受 `--token`，但会警告一次）

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
   语言（`zh`/`en`，只支持这两种）和保留天数也在这一步顺带问一句，默认 `zh` / 30 天。
   这个语言设置会真正影响输出——③④⑤阶段你写的 headline/summary/why_relevant/overview/
   ai_reasoning/筛选实例都要用这个语言写（`report.py` 会把 `language` 字段带进每一步
   给你的输入 JSON，读 prompts/*.md 时按这个字段判断，不要一律写中文），报告页的界面
   文案（按钮、区块标题这些）和渠道名字（微信公众号/WeChat 之类）也会跟着切换。

   monitor JSON 里还可以顺带带上几个可选字段，报告页会用得上（缺省时都有兜底，
   不影响流程跑通）：`focus_tags`（2~4 个关注点标签，用于报告页头部展示）、
   `setup_note`（一句更完整的"配置时你说……"确认语，缺省会自动用 `description` 拼一句）。

4. **建立自动化**：这一步不是运行某个脚本，而是你（Agent）直接使用当前宿主平台提供的
   定时任务能力（比如一个调度类 Skill、一个创建定时任务的工具，或者类似机制——不同平台
   叫法和调用方式不同，按你实际可用的能力来）创建**两条**独立的定时任务：

   - **采集任务**：一天触发 `config.json` 的 `harvest_hours` 里配的那几个时刻（默认
     `[0, 6, 12, 18]`），每次触发只需要做一件事——运行 `python3 scripts/harvest.py run`。
     这一步不需要语义推理，是纯脚本操作，唤醒后几秒就能跑完。
   - **报告任务**：一天触发一次，时刻取 `config.json` 的 `report_time`（默认 `08:00`）。
     触发后完整走一遍下面「日常运行」里③~⑥的全部步骤——重新唤起这个 Skill，对
     `config.json` 里的每一个 monitor 依次跑完筛选/聚类/摘要，再渲染 + 按分享设置上传。

   如果当前宿主平台**不提供**定时唤醒 Agent 的能力，如实告诉用户这个环境下做不到真正的
   "每天自动生成"，只能在用户主动唤起你的时候按最新数据生成报告；`harvest.py` 仍然可以
   手动运行来积累数据。不要假装设置成功，也不要退回去生成系统级 crontab/launchd 命令
   （为什么不这样做见 `ARCHITECTURE.md`）。

5. **抓真实数据**，明确告诉用户"现在为你抓取数据，大约 30 秒"：
   ```
   python3 scripts/harvest.py run
   ```
   走一遍下面「日常运行」的③~⑥步骤（`report.py` 的四个子命令 + `render.py`），生成用户
   自己的第一份报告。如果某个 monitor 筛选后一条都没命中，③筛选一节里的"补充检索"会先
   尝试兜底；如果补充之后依然没有，`report.py` 会自动给出诚实的兜底文案，不需要你额外
   解释，但可以提醒用户"这是每日增量，不是搜索引擎，明天再看看"。

   首次运行结束时同样按⑥的**交付清单**把三项给齐——尤其是历史目录首页
   `data/reports/dashboard.html`：这是用户以后每天回来的入口，第一次一定要呈现出来并
   说明"以后打开这个页面就能看到全部历史报告，建议收藏"，不要等他问起。

## 日常运行（config.json 已存在时）

如果这次是被「报告任务」定时唤醒的（见 Setup 第 4 步），说明采集任务大概率已经在后台
按 `harvest_hours` 独立跑过了，数据池是新鲜的，直接从③开始。如果是用户手动唤起你、
或者你不确定采集任务有没有正常触发，先手动跑一次兜底，不吃亏：
```
python3 scripts/harvest.py run
```

接下来对 `config.json` 里的**每一个 monitor**依次执行。

> **③~⑥必须按顺序跑完，不能从中间接上**：`candidates` 会给这个 monitor 开启新的一轮
> （生成一个 run_id、清掉上一轮留在报告里的小节），后面每一步都会检查手里的中间产物
> 是不是同一轮的。所以"上午跑到一半、下午从④接着跑"是不行的——脚本会明确报错让你从
> `candidates` 重跑，而不是默默把两轮的数据拼在一起。同理，`finalize` 只认本轮生成的
> 小节：如果某个 monitor 没跑完就 finalize，它会以非 0 退出并告诉你缺了谁，不会把
> 昨天的旧报告当成今天的结果收尾。看到这类报错，照它说的从 `candidates` 重跑即可。

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

**补充检索（只在需要时触发）**：如果这一步返回的 JSON 里 `needs_search_augment` 是
`true`（意味着 `kept` 是 0——当前数据池对这个 monitor 一条都没命中），先不要直接往下走
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
下面④⑤⑥的正常流程——`report.py summarized` 会给出诚实的零命中兜底文案，不要因为搜索也
没找到就反复重试或强行编造内容凑数。

### ④ 聚类

上一步输出了聚类输入（`data/.work/cluster-input-<id>-<date>.json`）。

**读 `prompts/cluster.md`**，对这批文章做跨独立信源的同事件归并，把结果（`clusters` 数组，
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
这一步会裁掉不在当前配置里的 monitor、剔除不是本轮生成的陈旧小节、重算顶层统计、
检查最近的采集有没有失败，写进报告 JSON 的 `alerts` 字段。

**这一步的退出码是有意义的**：返回非 0 表示当前配置里还有 monitor 没跑完（或跑出来的
小节是上一轮的），报告是不完整的。这时不要继续往⑥走、也不要告诉用户"报告好了"——
按提示里列出的 monitor id，从 `candidates` 开始重跑它们，再 finalize 一次。

### ⑥ 渲染 + 生成分享链接

```
python3 scripts/render.py --date <date>
```
这一步产出三样东西：

- `data/reports/<date>.html`——**当天独立的自包含报告页**，只含这一天的数据。分享一份
  报告就是分享这一个文件，不会带出任何范围外的内容。
- `data/reports/dates-manifest.js`——一份很小的日期清单（每次都会重新生成），含 monitor
  名字、精选条数、跨源头条数，以及截断到 96 字的当天 overview；不含标题/正文/原文链接
- `data/reports/dashboard.html`——纯静态的首页/索引页，每次运行都会用 Skill 自带的最新
  模板覆盖一次（这个文件里没有任何用户数据，日期列表是打开时从上面那份清单读的，所以
  覆盖是安全的，也是老用户能拿到模板修复的唯一途径）。它靠清单文件把历史日期排成两列
  卡片，每张卡片带一句当天概览，点日期跳转到对应的 `<date>.html`。首页没有分享按钮和
  统计埋点。

**渲染完之后紧接着自动生成一次分享链接**，不用等用户开口要分享：
```
python3 scripts/share.py upload --date <date>
```
上传的是刚生成的 `<date>.html`，天生就只包含这一天的内容。这一步是每天流程里固定的一
部分，不是可选步骤。如果这一步失败（比如网络问题、token 过期），**不要中断当天的
流程**：本地的 `<date>.html` 已经生成好、可以正常查看，只是分享按钮那天会退化成复制
文案 + 提示重试。

### 交付清单（每次跑完⑥都要给齐这三项，不是三选一）

`render.py` 跑完会打印一段「本次交付」，照着它交付即可。三项都是固定动作，**不要等
用户开口问才给**：

1. **今日报告页** `data/reports/<date>.html`——当天的完整内容。
2. **分享链接**——`share.py` 返回的 `share_url` 有效就一并发给用户；失败就用 stderr 里
   的人话原因说明（比如"token 过期了，邮件联系 gems9232@foxmail.com"），不要跳过不提。
3. **历史目录首页** `data/reports/dashboard.html`——用户以后每天回来看历史报告的入口，
   每次运行都会自动更新日期列表。第一次交付时顺带说明它是常驻入口、建议收藏。

**呈现方式**：如果当前宿主平台有展示或打开文件的能力（文件卡片、预览面板、浏览器打开
之类——不同平台叫法不同，按你实际可用的能力来），直接用它把上面的 HTML 呈现给用户，
**不要只贴一条本地绝对路径**——用户拿到路径往往打不开。没有这类能力时才退回给路径，
并说明双击即可打开。

另外，如果 `finalize` 返回的 `alerts` 非空，也要主动提醒用户"最近的采集出现了问题"。

## 关于进度展示

`harvest.py` 和 `report.py` 的每条命令都会自己打印六阶段进度表（阶段名 + 数量 +
耗时），不需要你额外复述数字——直接把命令的终端输出展示给用户就够了。

## 关于零命中

`report.py filtered` 返回 0 命中时你应该先按③筛选一节的说明补一次检索，而不是直接跳到
这里。只有补充之后依然 0 命中，`report.py summarized` 才会自动把 `overview` 换成诚实的
兜底文案（"今天你关注的方向没有明显动静……"），线索区依然会展示当天处理过的全部聚类。
你不需要额外编造内容让报告看起来更热闹——诚实比好看更重要。

## 分享报告

正常情况下用户打开报告页时分享链接已经在那了（见上面⑥的说明，渲染完会自动上传一次）
——点「分享今日洞察」按钮就是复制这个真链接，不需要再专门找你要。如果用户仍然主动说
"分享一下今天的报告"/"重新生成一下分享链接"（比如当天自动上传失败了，或者报告内容后来
又更新过），照样是你来手动触发 `python3 scripts/share.py upload --date <date>`，处理方式
跟⑥里的一致。产出路径只有这一条：每天独立的 HTML 报告页。不要向用户建议邮件 / 群机器人
推送这类扩展能力。

## 深入了解

- 设计取舍（为什么小量级用 LLM 而非算法、为什么脚本与 Agent 分工、去重策略、
  为什么采集与报告解耦、历史版本演进）：`ARCHITECTURE.md`
- 三个 prompt 文件的完整输入输出结构：`prompts/filter.md` / `prompts/cluster.md` /
  `prompts/summarize.md`
- 报告 JSON 的完整字段说明：`ARCHITECTURE.md` 第 7、8 节 + `scripts/report.py` 顶部注释
