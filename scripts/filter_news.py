#!/usr/bin/env python3
"""
新闻 LLM 筛选脚本 — 合并多路新闻源，调用 LLM 筛选并生成摘要。

Usage:
    python filter_news.py
    python filter_news.py --news data/news_raw.json \
                          --xinhua data/xinhua_index.json \
                          --output data/news_processed.json \
                          --top-n 8
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import openai
import yaml


# ===================== 工具函数 =====================

def load_json(path: Path, default):
    if path.exists() and path.stat().st_size > 2:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def load_profile(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_domains_str(profile: dict) -> str:
    lines = []
    for d in profile.get("domains", []):
        kws = "、".join(d.get("keywords", []))
        lines.append(f"- {d['name']}（权重 {d['weight']}）：{kws}")
    return "\n".join(lines)


def format_news_for_prompt(news_list: list[dict]) -> str:
    lines = []
    for i, n in enumerate(news_list):
        excerpt = n.get("excerpt") or n.get("preview") or ""
        if excerpt:
            lines.append(f"[{i:03d}] [{n['source']}] {n['title']}\n  摘要：{excerpt[:150]}")
        else:
            lines.append(f"[{i:03d}] [{n['source']}] {n['title']}")
    return "\n\n".join(lines)


# ===================== 主流程 =====================

def main() -> None:
    parser = argparse.ArgumentParser(description="新闻 LLM 筛选")
    parser.add_argument("--news",    default="data/news_raw.json")
    parser.add_argument("--xinhua", default="data/xinhua_index.json")
    parser.add_argument("--profile", default="config/profile.yaml")
    parser.add_argument("--output",  default="data/news_processed.json")
    parser.add_argument("--top-n",   type=int, default=8)
    args = parser.parse_args()

    # 加载新闻
    news_list: list[dict] = load_json(Path(args.news), [])
    xinhua_list: list[dict] = load_json(Path(args.xinhua), [])
    all_news = news_list + xinhua_list

    if not all_news:
        print("[INFO] 无新闻数据，退出")
        return

    print(f"[INFO] 共 {len(all_news)} 条新闻（EdSurge等 {len(news_list)} + 新华 {len(xinhua_list)}），开始 LLM 筛选...")

    # 加载配置
    profile = load_profile(args.profile)
    domains_str = build_domains_str(profile)
    exclude_list = "\n".join(f"- {e}" for e in profile.get("exclude", [])) or "（无）"
    tags_str = " ".join(profile.get("tag_taxonomy", []))
    news_text = format_news_for_prompt(all_news)

    prompt = f"""你是一个教育资讯筛选助手，帮助用户从每日采集的新闻中找出最值得关注的内容。

## 用户兴趣画像

### 关注领域
{domains_str}

### 排除主题
{exclude_list}

## 可用标签（每条选 1-3 个）
{tags_str}

## 筛选要求

1. 从下方新闻列表中选出最相关的 {args.top_n} 条
2. 优先选择有实质内容、政策动向、技术进展的新闻，排除转载重复、无实质内容的条目
3. 对每条入选新闻生成：
   - summary_cn：约 100 字中文摘要（英文新闻需翻译）
   - key_points：核心要点，格式"谁 + 做了什么 + 影响/意义"，50字以内
   - tags：从可用标签中选 1-3 个
   - relevance_score：1-5 分（5=高度相关）

## 输出格式

严格输出 JSON 数组，不要有任何其他文字：

[
  {{
    "index": 0,
    "summary_cn": "约100字中文摘要",
    "key_points": "核心要点",
    "tags": ["#标签1"],
    "relevance_score": 4
  }}
]

## 待筛选新闻列表

{news_text}
"""

    # 调用 LLM
    secrets_path = Path(__file__).parent.parent / "config" / "secrets.yaml"
    with open(secrets_path, "r", encoding="utf-8") as f:
        secrets = yaml.safe_load(f) or {}
    client = openai.OpenAI(
        api_key=secrets.get("llm_api_key", ""),
        base_url=secrets.get("llm_base_url", "https://api.openai.com/v1"),
    )
    response = client.chat.completions.create(
        model=secrets.get("llm_model", "gpt-4o"),
        max_tokens=4096,
        temperature=0.3,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_output = response.choices[0].message.content.strip()

    # 解析
    json_match = re.search(r"\[.*\]", raw_output, re.DOTALL)
    if not json_match:
        print(f"[ERROR] LLM 输出无法解析为 JSON:\n{raw_output}", file=sys.stderr)
        sys.exit(1)

    selected_meta: list[dict] = json.loads(json_match.group())

    # 回查原始数据，补充 LLM 生成字段
    selected_news = []
    for item in selected_meta:
        idx = item.get("index", -1)
        if 0 <= idx < len(all_news):
            news = dict(all_news[idx])
            news["summary_cn"] = item.get("summary_cn", "")
            news["key_points"] = item.get("key_points", "")
            news["tags"] = item.get("tags", [])
            news["relevance_score"] = item.get("relevance_score", 0)
            selected_news.append(news)

    print(f"[INFO] LLM 筛选完成，入选 {len(selected_news)} 条")

    # 写出
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(selected_news, f, ensure_ascii=False, indent=2)
    print(f"[INFO] 已写入: {args.output}")


if __name__ == "__main__":
    main()
