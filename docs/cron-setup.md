# OpenClaw Cron 配置说明

edu-broadcast 系统需要配置两个定时任务。

## 方式一：CLI 命令

```bash
# 每天 08:00 执行教育日报推送
openclaw cron add "执行每日教育内容推送" --schedule "0 8 * * *" --deliver feishu

# 每天 08:30 执行归档到飞书多维表格
openclaw cron add "归档教育日报到飞书多维表格" --schedule "30 8 * * *"
```

## 方式二：编辑 openclaw.json

在 `~/.openclaw/openclaw.json` 中添加：

```json
{
  "cron": [
    {
      "schedule": "0 8 * * *",
      "action": "执行每日教育内容推送",
      "channel": "feishu"
    },
    {
      "schedule": "30 8 * * *",
      "action": "归档教育日报到飞书多维表格"
    }
  ]
}
```

## Skill 安装

将 skills 目录下的两个 skill 安装到 OpenClaw：

```bash
# 方式一：symlink（开发阶段推荐）
ln -s /Users/hujingkai/Desktop/pypypy/paperBroadcast/skills/edu-broadcast ~/.openclaw/skills/edu-broadcast
ln -s /Users/hujingkai/Desktop/pypypy/paperBroadcast/skills/edu-broadcast-archiver ~/.openclaw/skills/edu-broadcast-archiver

# 方式二：直接安装
openclaw skill install ./skills/edu-broadcast
openclaw skill install ./skills/edu-broadcast-archiver
```

## Archiver Skill 凭证配置

```bash
openclaw skill config edu-broadcast-archiver --set feishu_app_id=cli_xxx
openclaw skill config edu-broadcast-archiver --set feishu_app_secret=xxx
openclaw skill config edu-broadcast-archiver --set feishu_bitable_app_token=xxx
openclaw skill config edu-broadcast-archiver --set feishu_bitable_table_id_papers=xxx
openclaw skill config edu-broadcast-archiver --set feishu_bitable_table_id_news=xxx
```

## 验证

```bash
# 查看已安装 skill
openclaw skill list

# 查看 cron 任务
openclaw cron list

# 手动触发测试
openclaw skill test ./skills/edu-broadcast/SKILL.md "教育日报"
```
