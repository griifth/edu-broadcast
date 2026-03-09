---
name: feishu-init
version: 1.0.0
description: "检查飞书多维表格是否存在，缺失则自动创建"
trigger: "初始化飞书表格|feishu init"
tools: [shell, filesystem]
author: openclaw
---

# feishu-init — 飞书多维表格初始化

检查 `config/secrets.yaml` 中配置的飞书多维表格是否已建好。
若论文表或新闻表不存在，自动创建并回写 `table_id` 到 `secrets.yaml`。

通常由 `edu-broadcast` 冷启动流程在完成配置后自动调用，也可手动触发。

---

## Step 1：读取凭证

用 filesystem 工具读取 `config/secrets.yaml`，提取：
- `feishu_app_id`
- `feishu_app_secret`
- `feishu_bitable_app_token`
- `feishu_bitable_table_id_papers`（可为空）
- `feishu_bitable_table_id_news`（可为空）

若 `feishu_bitable_app_token` 为空，终止并提示用户：
> "请先在飞书创建一个多维表格文档，将文档链接中的 app_token 填入 config/secrets.yaml，再重新触发初始化。"

---

## Step 2：检查论文表

若 `feishu_bitable_table_id_papers` 已填写，调用飞书 API 验证该 table 是否存在：

```
GET https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}
```

- 返回 `code: 0` → 论文表已存在，跳过创建
- 返回错误 / table_id 为空 → 执行 Step 3 创建论文表

---

## Step 3：创建论文表（按需）

```
POST https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables
Body: { "table": { "name": "论文" } }
```

创建成功后，依次添加以下字段（默认已有"多行文本"类型的第一个字段，重命名为"标题"即可）：

| 字段名 | 类型 |
|--------|------|
| 标题 | 文本（默认字段，重命名） |
| 来源 | 文本 |
| 日期 | 文本 |
| 摘要 | 文本 |
| 标签 | 文本 |
| 链接 | 超链接 |
| 推荐理由 | 文本 |
| 相关性评分 | 数字 |
| 推送日期 | 文本 |
| 用户评价 | 文本 |
| 备注 | 文本 |

将返回的 `table_id` 写入 `config/secrets.yaml` 的 `feishu_bitable_table_id_papers` 字段。

---

## Step 4：检查新闻表

同 Step 2，验证 `feishu_bitable_table_id_news` 是否存在。

- 存在 → 跳过
- 不存在 / 为空 → 执行 Step 5 创建新闻表

---

## Step 5：创建新闻表（按需）

```
POST https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables
Body: { "table": { "name": "新闻" } }
```

添加以下字段：

| 字段名 | 类型 |
|--------|------|
| 标题 | 文本（默认字段，重命名） |
| 来源 | 文本 |
| 日期 | 文本 |
| 摘要 | 文本 |
| 核心要点 | 文本 |
| 标签 | 文本 |
| 链接 | 超链接 |
| 相关性评分 | 数字 |
| 推送日期 | 文本 |
| 用户评价 | 文本 |
| 备注 | 文本 |

将返回的 `table_id` 写入 `config/secrets.yaml` 的 `feishu_bitable_table_id_news` 字段。

---

## Step 6：回报结果

执行完成后输出一条汇报：

- 若两表均已存在：`"飞书表格检查完毕，论文表和新闻表均已就绪。"`
- 若有新建：`"已创建：[论文表 / 新闻表]，table_id 已回写到 config/secrets.yaml。"`

---

## Error Handling

- 获取 access_token 失败：终止，提示检查 `feishu_app_id` / `feishu_app_secret`
- 创建表失败：终止，输出飞书返回的错误信息
- 添加字段部分失败：记录失败字段名，继续添加其余字段，最终汇报缺失字段，提示手动补建
