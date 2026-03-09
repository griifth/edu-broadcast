---
name: feishu-demo
version: 1.0.0
description: "验证飞书多维表格写入是否正常"
trigger: "飞书demo|feishu demo|测试飞书写入"
tools: [shell, filesystem]
---

# feishu-demo — 飞书写入验证

用两条硬编码的测试数据（1 篇论文 + 1 条新闻），验证 `store_feishu.py` 能否正常写入飞书多维表格。

---

## Step 1：写入测试数据文件

用 filesystem 工具将以下内容写入 `data/demo_papers.json`：

```json
[
  {
    "title": "【测试论文】Large Language Models in K-12 Education: A Review",
    "venue": "Demo Journal",
    "date": "2026-03-08",
    "url": "https://example.com/demo-paper",
    "abstract_cn": "这是一篇测试论文，用于验证飞书多维表格写入功能是否正常。内容为占位文字，请忽略。",
    "tags": ["AI+教育"],
    "recommendation": "验证写入流程的测试条目，请核实飞书表格中是否出现本行。",
    "relevance_score": 1
  }
]
```

用 filesystem 工具将以下内容写入 `data/demo_news.json`：

```json
[
  {
    "title": "【测试新闻】Demo News Item for Feishu Upload Verification",
    "source": "Demo Source",
    "date": "2026-03-08",
    "url": "https://example.com/demo-news",
    "summary_cn": "这是一条测试新闻，用于验证飞书新闻表写入功能是否正常。内容为占位文字，请忽略。",
    "key_points": "验证写入流程；确认字段映射正确；确认表格 ID 配置无误。",
    "tags": ["教育技术"],
    "relevance_score": 1
  }
]
```

---

## Step 2：推送论文测试数据

```
exec: python scripts/store_feishu.py --papers data/demo_papers.json
```

观察输出：
- `论文归档: 成功 1, 失败 0` → 论文表写入正常
- 如有报错，检查环境变量 `FEISHU_APP_ID` / `FEISHU_BITABLE_TABLE_ID_PAPERS` 是否注入

---

## Step 3：推送新闻测试数据

```
exec: python scripts/store_feishu.py --news data/demo_news.json
```

观察输出：
- `新闻归档: 成功 1, 失败 0` → 新闻表写入正常
- 如有报错，检查环境变量 `FEISHU_BITABLE_TABLE_ID_NEWS` 是否注入

---

## Step 4：在飞书中确认

登录飞书多维表格，分别打开论文表和新闻表，确认：
- 标题列出现 `【测试论文】` / `【测试新闻】` 开头的测试行
- 各字段（摘要、标签、链接、推送日期）填充正确

确认无误后可手动删除这两条测试记录。

---

## 环境变量检查清单

若写入失败，逐项检查：

| 变量 | 用途 |
|------|------|
| `FEISHU_APP_ID` | 应用 ID |
| `FEISHU_APP_SECRET` | 应用密钥 |
| `FEISHU_BITABLE_APP_TOKEN` | 多维表格 App Token |
| `FEISHU_BITABLE_TABLE_ID_PAPERS` | 论文表 Table ID |
| `FEISHU_BITABLE_TABLE_ID_NEWS` | 新闻表 Table ID |
