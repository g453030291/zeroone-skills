# 扩展输出层契约

`outputs/` 下的每个 `.py` 文件是一个独立的 emitter，负责把已经生成好的报告投递到某个
目标渠道（邮件、飞书群、企业微信群、钉钉群……）。emitter **不做任何 LLM 调用**，只消费
`reports/<date>.json`（阶段⑥产出的权威结构化产物），保证扩展层的行为始终确定、可测试。

## 接口

```python
# outputs/<name>.py
def emit(report: dict, config: dict) -> None:
    """消费 reports/<date>.json 反序列化后的 dict，投递到目标渠道。

    report: 见 SKILL.md / ARCHITECTURE.md 中描述的报告 JSON 结构（date/stats/monitors/alerts）
    config: data/config.json 反序列化后的 dict，emitter 自己的配置项建议放在
            config["outputs_config"][<name>] 下，避免和顶层字段冲突
    """
```

- 出错时应当抛出异常并附带人话说明（不要吞掉错误，也不要打印堆栈了事），由调用方决定如何
  展示给用户
- 不假设 stdout 会被谁读取，日志用 `print` 即可

## 如何启用

把文件放进 `outputs/` 目录即自动被发现（文件名即 emitter 名），然后在 `data/config.json`
的顶层 `outputs` 数组里加上这个名字，例如：

```json
{
  "outputs": ["html", "md", "email", "webhook"]
}
```

`html` 和 `md` 是内置产物（由 `scripts/render.py` 直接生成，不算 emitter），其余名字对应
`outputs/<name>.py`。

## 已内置的 emitter

- `email.py` —— 用标准库 `smtplib` 通过 SMTP 发送报告摘要邮件
- `webhook.py` —— 向飞书 / 企业微信 / 钉钉的机器人 Webhook 地址 POST 一条通用格式的 JSON 消息

两者都是第二阶段实现，不阻塞主流程；`config.json` 里没启用时完全不影响 setup 与日常
harvest / report / render 流程。
