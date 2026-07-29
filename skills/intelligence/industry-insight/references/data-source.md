# 数据源契约

一期只有一个信源：ZeroOne 自建的微信公众号采集接口。接新信源或让别的 skill 复用同一数据源时先读这份。

## 接口

```
GET http://8.130.106.19:8100/api/data/articles
Authorization: bearer <用户提供的 key>
```

key 存在 `~/.zeroone/config.json`，由用户在初始化时提供——它是数据方管控准入的唯一凭证，不内置在代码里。

**当前端点是明文 HTTP，key 会在网络上明文传输**，`ingest.py` 会在检测到 `http://` 时打印一次性警告。这是数据方那台服务器的限制，skill 这边改不了协议；如果数据方后续提供 HTTPS 端点，用 `python3 store.py --set-endpoint <https-url>` 切过去。

无参数，每次返回最新约 100 条。

```json
{
  "code": 200,
  "msg": "success",
  "data": [
    {
      "id": "3271670413-2247564859_1",
      "source_type": "wx",
      "feed_name": "汽车评中评",
      "title": "雷军明确表示小米澎程不做纯电车！可不止物理原因",
      "url": "https://mp.weixin.qq.com/s/cunsULWZqz2K7KoeZOWMcg",
      "description": "",
      "publish_time": "2026-07-28 16:04:19",
      "clean_content": "小米汽车推出全新子系列..."
    }
  ]
}
```

`code != 200` 按失败处理，`msg` 里有原因。401/403 通常是 key 无效或被回收。

## 实测特征

| 观察 | 数值 | 影响 |
|---|---|---|
| 单次返回 | 约 100 条 | 滚动窗口，老数据会滑出，历史只能靠轮询累积 |
| 时间跨度 | 约 **12.5 小时** | 采集间隔必须明显小于它，否则静默丢数据 |
| 正文长度 | min/avg/max ≈ 7 / 3430 / 21776 字 | 单次约 34 万字，全量进上下文不可行 |
| 信源数 | 约 48 个公众号 | |
| `source_type` | 恒为 `wx` | 保留字段，为将来接其他信源 |

## 字段语义

| 字段 | 说明 |
|---|---|
| `id` | 信源内唯一。`<公众号id>-<消息id>_<条内序号>`，同一次群发的多条共享前缀 |
| `source_type` | 信源类型。去重主键是 `(id, source_type)` 而非单独 `id`——不同信源的 id 空间可能撞车 |
| `feed_name` | 公众号名称，判断信源立场和可信度的主要依据 |
| `title` | 信息密度最高的字段，聚类时加权计入 |
| `url` | 原文链接，产出里必须引用 |
| `description` | 常为空或 `-`，不可靠，摘要一律从 `clean_content` 开头截 |
| `publish_time` | `YYYY-MM-DD HH:MM:SS` 本地时间 |
| `clean_content` | 清洗后正文纯文本 |

**`clean_content` 仍有尾部噪声**：`预览时标签不可点`、`微信扫一扫`、`点亮星标`、`一键三连`、`— 完 —`、视频播放器控件文本（`进度条，百分之0`、`倍速播放中`）。引用正文时别把这些当内容。

## 换数据源

只改 `scripts/ingest.py`：`DEFAULT_ENDPOINT`、`fetch()` 的响应解析、把新信源字段映射成上表那八个，并给一个不同的 `source_type`。下游（去重、打分、聚类、成稿、dashboard）只依赖这八个字段，不需要改。
