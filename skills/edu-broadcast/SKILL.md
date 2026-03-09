---
name: edu-broadcast
version: 2.1.0
description: "每周教育领域论文 RSS 采集 + 新闻采集、LLM 筛选，推送到飞书"
trigger: "教育周报|推送教育论文|edu broadcast|设置教育兴趣|更新兴趣画像|更新定时配置|定时任务配置"
tools: [shell, filesystem, http, chat, browser]
author: openclaw
---

# edu-broadcast — 教育内容周报

每周从权威期刊 RSS 采集论文、每日从教育新闻网站采集资讯，由 LLM 结合用户画像筛选推荐，推送到飞书多维表格。

## 运行节奏

| 触发方式 | 时间 | 执行内容 |
|----------|------|----------|
| Cron 每周一 | 07:00 | RSS 论文采集 → ingest → 拆分为 7 份 batch |
| Cron 每日 | 07:00 | 新闻采集 + LLM 筛选 + 推送飞书（新闻） |
| Cron 每日 | 08:00 | 取当日 batch → LLM 筛选论文（最多4篇）→ 推送飞书 |
| 手动触发 | 随时 | 用户说"教育周报"或"推送教育论文" |

---

## 论文采集工作流（每周一 07:00）

### Step 1：RSS 采集

从 `config/sources.yaml` 中配置的期刊 RSS 订阅源采集最新论文：

```
exec: python scripts/collect_rss_papers.py \
        --config config/sources.yaml \
        --output data/papers_rss_raw.json
```

采集范围：
- 中文期刊（万方）：教育研究、高等教育研究、北京大学教育评论 等 10 本
- 英文期刊（SAGE）：Review of Educational Research、Educational Researcher 等 4 本
- 英文期刊（Wiley）：British Journal of Educational Technology、JCAL、RRQ
- 英文期刊（Springer）：Higher Education

输出：`data/papers_rss_raw.json`

### Step 2：ingest → 拆分 batch

将新论文入池（去重、摘要质量过滤、API验证），再均分为 7 份：

```
exec: python scripts/filter_papers.py --ingest \
        --raw data/papers_rss_raw.json \
        --pending data/pending_papers.json \
        --history data/dedup_history.json

exec: python scripts/filter_papers.py --split \
        --pending data/pending_papers.json \
        --batch-dir data/batches \
        --num-batches 7
```

- `data/pending_papers.json`：ingest 后填充，split 后清空
- `data/batches/day_1.json` … `day_7.json`：每日论文筛选用
- `data/dedup_history.json`：所有曾采集论文的完整数据（永久）

---

## 每日工作流（07:00 新闻 / 08:00 论文）

### 前置：读取定时配置

每次执行前，先用 filesystem 工具读取 `config/schedule.yaml`，提取以下变量供后续步骤使用：

| 变量 | 来源字段 |
|------|----------|
| `$DAYS_LOOKBACK` | `news.days_lookback` |
| `$NEWS_TOP_N` | `news.top_n` |
| `$PAPER_TOP_N` | `papers.top_n` |
| `$XINHUA_ENABLED` | `news.sources.xinhua` |
| `$EDSURGE_ENABLED` | `news.sources.edsurge` |

---

### Step 1：新闻采集

**渠道一：RSS + 定向网站**（仅当 `$EDSURGE_ENABLED` 为 true 时执行）

```
exec: python scripts/collect_news.py \
        --config config/sources.yaml \
        --output data/news_raw.json \
        --days $DAYS_LOOKBACK
```

输出：`data/news_raw.json`

**渠道二：新华教育频道**（仅当 `$XINHUA_ENABLED` 为 true 时执行）

```
exec: python scripts/scrape_xinhua_edu.py index \
        --output data/xinhua_index.json \
        --days $DAYS_LOOKBACK
```

输出：`data/xinhua_index.json`

若两个渠道均禁用，跳过 Step 1 并记录警告。

### Step 2：新闻 LLM 筛选

```
exec: python scripts/filter_news.py \
        --news data/news_raw.json \
        --xinhua data/xinhua_index.json \
        --profile config/profile.yaml \
        --output data/news_processed.json \
        --top-n $NEWS_TOP_N
```

若 `$EDSURGE_ENABLED` 为 false，则 `data/news_raw.json` 为空列表，`--news` 参数仍传入但内容为空，LLM 仅处理新华数据，反之亦然。

LLM 对每条入选新闻生成：
- `summary_cn`：约 100 字中文摘要（英文新闻自动翻译）
- `key_points`：核心要点（谁 + 做了什么 + 影响）
- `tags`：从 `profile.yaml` 的 `tag_taxonomy` 中选 1-3 个
- `relevance_score`：1-5 分

输出：`data/news_processed.json`

### Step 3：归档新闻到飞书

```
exec: python scripts/store_feishu.py --news data/news_processed.json
```

同时追加写入 `data/news_history.json`（永久存档）。

### Step 4：论文 LLM 筛选（每日 08:00）

取当日 batch 文件，结合用户画像筛选论文：

```
exec: python scripts/filter_papers.py --filter \
        --batch data/batches/day_{N}.json \
        --profile config/profile.yaml \
        --prompt config/filter_prompt.txt \
        --pushed data/pushed_papers.json \
        --feishu-out data/daily/{YYYY-MM-DD}.json \
        --top-n $PAPER_TOP_N
```

- `{N}` 为当天是周几（1=周一 … 7=周日）
- 脚本会自动合并 `data/news_processed.json` 写入 daily 文件

输出：
- `data/pushed_papers.json`：本地永久备份（追加模式）
- `data/daily/{YYYY-MM-DD}.json`：含当日论文 + 新闻，供归档使用

### Step 5：归档论文到飞书多维表格

```
exec: python scripts/store_feishu.py --papers data/daily/{YYYY-MM-DD}.json
```

所需环境变量（由 openclaw config vault 注入）：
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_BITABLE_APP_TOKEN`
- `FEISHU_BITABLE_TABLE_ID_PAPERS`
- `FEISHU_BITABLE_TABLE_ID_NEWS`

---

## 数据文件说明

| 文件 | 性质 | 说明 |
|------|------|------|
| `data/papers_rss_raw.json` | 临时 | 每周论文采集结果，每次覆盖 |
| `data/pending_papers.json` | 临时 | ingest 后填充，split 后清空 |
| `data/batches/day_{1-7}.json` | 临时 | 每日论文筛选 batch，每周一重新生成 |
| `data/dedup_history.json` | 永久 | 所有曾采集论文的完整数据（去重依据） |
| `data/pushed_papers.json` | 永久 | 历次推送精选结果的本地备份 |
| `data/daily/{date}.json` | 永久 | 每次推送的结构化数据，供飞书归档 |
| `data/news_raw.json` | 临时 | 定向网站新闻采集结果，每次覆盖 |
| `data/xinhua_index.json` | 临时 | 新华教育文章索引，每次覆盖 |
| `data/news_filtered_urls.json` | 临时 | LLM 筛选后待深读的 URL 列表 |
| `data/news_full.json` | 临时 | 正文深读结果（新华备用路径） |
| `data/news_processed.json` | 临时 | 新闻 LLM 处理结果，每次覆盖 |

---

## 冷启动 / 更新兴趣画像

当用户说"设置教育兴趣"或"更新兴趣画像"时，按以下步骤执行：

### Step 1：推送兴趣画像配置卡

通过飞书推送以下 JSON 配置卡，请用户修改后原文发回：

```
以下是教育日报的默认兴趣画像，你可以直接修改后发回给我，我会自动写入配置。

字段说明：
- domains            关注领域列表，每个领域包含：
  - name             领域名称
  - weight           权重（0.1-1.0，越高越优先推送）
  - keywords         该领域的关键词（中英文均可，可增删）
- exclude            不感兴趣的子领域或关键词，命中则过滤
- language           摘要语言偏好：bilingual（中英）/ zh（纯中文）/ en（纯英文）
- tag_taxonomy       标签体系，LLM 分类时从中选 1-3 个，可增删

{
  "edu_profile": {
    "domains": [
      {
        "name": "AI+教育",
        "weight": 0.9,
        "keywords": ["adaptive learning", "intelligent tutoring", "AI教育", "LLM教育应用", "大模型 教育", "AI tutor"]
      },
      {
        "name": "教育技术",
        "weight": 0.8,
        "keywords": ["educational technology", "EdTech", "教育信息化", "智慧教育", "learning analytics"]
      },
      {
        "name": "教育政策",
        "weight": 0.6,
        "keywords": ["教育改革", "课程标准", "education policy", "教育公平"]
      },
      {
        "name": "教学法研究",
        "weight": 0.5,
        "keywords": ["pedagogy", "教学设计", "课程设计", "instructional design"]
      }
    ],
    "exclude": ["体育教育", "military education", "physical education"],
    "language": "bilingual",
    "tag_taxonomy": [
      "#AI教育", "#教育政策", "#K12", "#高等教育", "#EdTech",
      "#教学法", "#教育公平", "#STEM", "#在线教育", "#教师发展",
      "#学习科学", "#教育评估"
    ]
  }
}
```

### Step 2：解析用户回复并写入画像

收到用户发回的 JSON 后：

**校验规则（不通过则告知用户并要求重新发）：**
- `domains` 不能为空，至少保留 1 个领域
- 每个 domain 的 `weight` 范围：0.1-1.0
- 每个 domain 的 `keywords` 不能为空列表
- `language` 只能是 `bilingual` / `zh` / `en`
- `tag_taxonomy` 不能为空列表

**校验通过后写入 `config/profile.yaml`：**

```yaml
domains:
  - name: "..."       # 从用户回复读取
    weight: 0.9
    keywords: [...]

exclude: [...]
language: "bilingual"
tag_taxonomy: [...]
```

### Step 3：定时任务配置

兴趣画像写入完成后，立即通过飞书推送以下 JSON 配置卡，请用户确认或修改后原文发回：

推送内容（说明文字 + JSON）：

```
以下是教育日报的默认定时配置，你可以直接修改后发回给我，我会自动应用。

字段说明：
- news.collect_and_push_time     每日新闻采集 + 推送飞书的时间
- news.days_lookback             新闻回溯天数（建议 3-7）
- news.top_n                     每日推送新闻条数
- news.sources.xinhua            是否启用新华教育频道（true/false）
- news.sources.edsurge           是否启用 EdSurge（true/false）
- papers.rss_collect_day         每周 RSS 论文采集日（monday-sunday）
- papers.rss_collect_time        RSS 采集时间
- papers.daily_filter_time       每日论文筛选 + 推送飞书的时间
- papers.top_n                   每日推送论文篇数（建议 2-6）

{
  "edu_broadcast_config": {
    "news": {
      "collect_and_push_time": "07:30",
      "days_lookback": 7,
      "top_n": 8,
      "sources": {
        "xinhua": true,
        "edsurge": true
      }
    },
    "papers": {
      "rss_collect_day": "monday",
      "rss_collect_time": "07:00",
      "daily_filter_time": "08:00",
      "top_n": 4
    }
  }
}
```

### Step 4：解析用户回复并写入配置

收到用户发回的 JSON 后：

**校验规则（不通过则告知用户并要求重新发）：**
- `news.top_n` 范围：1-20
- `papers.top_n` 范围：1-10
- `news.days_lookback` 范围：1-30
- 时间格式必须为 `HH:MM`

**校验通过后写入 `config/schedule.yaml`：**

```yaml
news:
  collect_and_push_time: "07:30"   # 从用户回复读取
  days_lookback: 7
  top_n: 8
  sources:
    xinhua: true
    edsurge: true

papers:
  rss_collect_day: "monday"
  rss_collect_time: "07:00"
  daily_filter_time: "08:00"
  top_n: 4
```

### Step 5：注册定时任务

读取 `config/schedule.yaml`，向 OpenClaw 注册以下定时任务：

| 任务说明 | 触发时间 | 执行内容 |
|----------|----------|----------|
| 每日新闻采集推送 | 每天 `news.collect_and_push_time` | 执行新闻采集→筛选→推送飞书→归档多维表格 |
| 每周 RSS 论文采集 | 每周 `papers.rss_collect_day` `papers.rss_collect_time` | RSS采集→ingest→split 7份batch |
| 每日论文筛选推送 | 每天 `papers.daily_filter_time` | 取当日batch→LLM筛选→推送飞书→归档多维表格 |

注意：新闻和论文的归档步骤均合并在各自推送任务末尾执行，无需单独的 archive cron。

注册完成后，静默调用 `feishu-init` skill，检查并按需创建飞书多维表格，完成后通过飞书回复一条汇总确认消息，内容包括：
- 已生效的定时时间表
- 飞书表格初始化结果（已存在 / 新建）

---

## 配置文件说明

| 文件 | 说明 |
|------|------|
| `config/sources.yaml` | RSS 订阅源列表、采集参数 |
| `config/profile.yaml` | 用户兴趣画像（domains / exclude / tag_taxonomy） |
| `config/filter_prompt.txt` | LLM 筛选 prompt 模板（`<<var>>` 占位符） |
| `config/schedule.yaml` | 定时任务配置（冷启动时生成，可重新触发"更新定时配置"修改） |

---

## Error Handling

- Step 1 失败（RSS 不可达）：记录警告，跳过该期刊，继续其他来源
- Step 2 失败（写文件失败）：报错退出，不清空 pending 池
- Step 3 pending 池为空：打印提示，正常退出，不调用 LLM
- Step 3 LLM 输出无法解析：打印原始输出，报错退出，不清空 pending 池（保留数据，下次可重试）
- Step 4 飞书写入失败：本地备份已写入，可手动重跑 `store_feishu.py`
- Step 3 新闻抓取失败（网站结构变化）：跳过该来源，继续其他来源，推送时标注"部分新闻来源不可用"
- Step 3 新华采集失败：跳过新华，仅使用定向网站新闻
- Step 4 浏览器工具不可用：新华来源 fallback 到 `scrape_xinhua_edu.py full`；其他来源保留 excerpt 作为摘要替代，标注"未完成正文深读"
