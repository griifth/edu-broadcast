# edu-broadcast — 教育内容周报推送系统

基于 OpenClaw 的教育领域内容周报推送系统。每日自动采集教育相关学术论文和热门新闻，生成中文摘要与分类标签，推送并归档到飞书多维表格。

## 项目结构

```
edu-broadcast/
├── skills/
│   └── edu-broadcast/           # 主 skill：采集→处理→推送→归档
│       ├── SKILL.md             # 工作流指令 + 冷启动流程
│       └── skill.yaml           # OpenClaw manifest
├── scripts/
│   ├── collect_rss_papers.py    # 论文 RSS 采集
│   ├── collect_news.py          # 新闻采集（RSS + 定向网站）
│   ├── scrape_xinhua_edu.py     # 新华教育频道采集
│   ├── filter_papers.py         # 论文 ingest / split / LLM 筛选
│   ├── filter_news.py           # 新闻 LLM 筛选
│   └── store_feishu.py          # 推送 + 归档到飞书多维表格
├── config/
│   ├── sources.yaml             # 采集源配置
│   ├── filter_prompt.txt        # LLM 筛选 prompt 模板
│   ├── profile.yaml             # 用户兴趣画像（冷启动生成，不提交）
│   ├── profile.yaml.example     # 兴趣画像示例
│   ├── schedule.yaml            # 定时任务配置（冷启动生成，不提交）
│   ├── schedule.yaml.example    # 定时配置示例
│   ├── secrets.yaml             # 本地密钥（不提交）
│   └── secrets.yaml.example     # 密钥配置示例
├── data/
│   └── daily/                   # 每日推送记录（运行时生成）
├── docs/
│   └── cron-setup.md            # OpenClaw 配置操作手册
├── requirements.txt             # Python 依赖
└── README.md
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 安装 Skill 到 OpenClaw

```bash
ln -s $(pwd)/skills/edu-broadcast ~/.openclaw/skills/edu-broadcast
```

### 3. 配置密钥

```bash
cp config/secrets.yaml.example config/secrets.yaml
# 编辑 config/secrets.yaml，填入真实密钥
```

需要填写的内容：
- **MCP Token**：AI4Scholar 论文采集接口密钥
- **LLM API Key**：新闻/论文 LLM 筛选（默认 Kimi）
- **飞书凭证**：App ID / Secret / 多维表格 Token 和 Table ID

### 4. 冷启动

对 OpenClaw 说 **"设置教育兴趣"**，OpenClaw 会依次：

1. 推送**兴趣画像** JSON 卡到飞书 → 修改后发回 → 自动写入 `config/profile.yaml`
2. 推送**定时配置** JSON 卡到飞书 → 修改后发回 → 自动写入 `config/schedule.yaml` 并注册定时任务

> 参照 `config/profile.yaml.example` 和 `config/schedule.yaml.example` 了解各字段含义。

### 5. 手动测试

对 OpenClaw 说 **"教育周报"** 触发一次完整推送流程。

## 内容源

- **论文**：中文期刊（万方）10 本 + 英文期刊（SAGE / Wiley / Springer）8 本，每周一 RSS 采集
- **新闻**：EdSurge RSS + 新华教育频道，每日采集

## 运行节奏

| 任务 | 时间 | 执行内容 |
|------|------|----------|
| 论文 RSS 采集 | 每周一（可配置） | RSS采集 → ingest → split 7份batch |
| 新闻采集推送 | 每天（可配置） | 采集 → LLM筛选 → 推送 + 归档飞书 |
| 论文筛选推送 | 每天（可配置） | 取当日batch → LLM筛选 → 推送 + 归档飞书 |

## 后续规划

- [ ] 反馈学习 + 个性化推荐（基于飞书多维表格中的用户评价数据）
- [ ] 动态追踪某领域/新闻进展（独立 skill）
