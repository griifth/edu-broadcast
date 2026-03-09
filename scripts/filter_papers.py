#!/usr/bin/env python3
"""
论文 LLM 筛选脚本 — 管理 pending 池并调用 Claude 进行论文筛选。

两种运行模式：
  --ingest   将 papers_rss_raw.json 中的新论文追加到 pending 池（每日采集后运行）
  --filter   从 pending 池筛选论文，写入本地备份和飞书输入文件（每周运行）

Usage:
    # 每天采集后：更新 pending 池
    python filter_papers.py --ingest

    # 每周一：LLM 筛选 + 输出
    python filter_papers.py --filter

    # 指定参数
    python filter_papers.py --ingest --raw data/papers_rss_raw.json
    python filter_papers.py --filter --top-n 10 --feishu-out data/daily/2026-03-10.json
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import openai
import requests
import yaml


# ===================== Profile 处理 =====================

def load_profile(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_prompt_vars(profile: dict, top_n: int) -> dict:
    """
    从 profile.yaml 提取 prompt 所需变量。
    只注入 domains / exclude / tag_taxonomy，跳过 journals / authors 等。
    """
    # domains → 自然语言列表
    domains_lines = []
    for d in profile.get("domains", []):
        kws = "、".join(d.get("keywords", []))
        domains_lines.append(f"- {d['name']}（权重 {d['weight']}）：{kws}")

    # exclude → 自然语言列表
    exclude_lines = [f"- {e}" for e in profile.get("exclude", [])]

    # tag_taxonomy → 空格分隔
    tags = " ".join(profile.get("tag_taxonomy", []))

    return {
        "domains": "\n".join(domains_lines),
        "exclude": "\n".join(exclude_lines) or "（无）",
        "tags": tags,
        "top_n": str(top_n),
    }


# ===================== 论文格式化 =====================

def _clean_abstract(ab: str) -> str:
    """去除 SAGE/Wiley RSS 的期刊引用前缀，保留真实摘要内容。"""
    ab = re.sub(r"^[^.]+(?:Ahead of Print|EarlyView|Page \d[^.]*)\.\s*", "", ab)
    return ab.strip()


def format_papers_for_prompt(papers: list[dict]) -> str:
    lines = []
    for i, p in enumerate(papers):
        lines.append(f"[{i:03d}] {p['title']}")
    return "\n".join(lines)


# ===================== AI4Scholar API 查询 =====================

def _is_english(text: str) -> bool:
    """判断文本是否为英文（无中文字符）。"""
    return not re.search(r'[\u4e00-\u9fff]', text)


def _title_word_overlap(a: str, b: str) -> float:
    """计算两个标题的词级重合率（取较短一方为分母）。"""
    wa = set(re.sub(r'[^\w\s]', '', a.lower()).split())
    wb = set(re.sub(r'[^\w\s]', '', b.lower()).split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def lookup_paper_api(title: str, api_key: str) -> dict | None:
    """
    用标题在 AI4Scholar API 搜索论文。
    返回第一条相似度 ≥ 0.7 的结果（含 abstract），或 None。
    """
    try:
        resp = requests.get(
            "https://ai4scholar.net/graph/v1/paper/search",
            headers={"Authorization": f"Bearer {api_key}"},
            params={"query": title, "fields": "title,abstract", "limit": 3},
            timeout=12,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[WARN] API 查询异常: {e}", file=sys.stderr)
        return None

    for item in data.get("data", []):
        if _title_word_overlap(title, item.get("title", "")) >= 0.7:
            return item
    return None


# ===================== Pending 池 / History 工具 =====================

def load_json(path: Path, default):
    if path.exists() and path.stat().st_size > 2:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_known_ids(history: dict) -> set:
    """从 dedup_history.json 中提取所有已知的 paper_id。
    兼容旧格式（titles 列表）和新格式（rss_papers 字典）。"""
    ids = set()
    # 新格式：rss_papers = {paper_id: full_data}
    for pid in history.get("rss_papers", {}).keys():
        ids.add(pid)
    return ids


# ===================== Ingest 模式 =====================

def run_ingest(raw_path: str, pending_path: str, history_path: str) -> None:
    """将 raw 文件中的新论文追加到 pending 池，并写入 dedup_history。"""
    raw_file = Path(raw_path)
    if not raw_file.exists():
        print(f"[ERROR] 原始文件不存在: {raw_path}", file=sys.stderr)
        sys.exit(1)

    with open(raw_file, "r", encoding="utf-8") as f:
        raw_papers: list[dict] = json.load(f)

    # 加载 API key
    secrets_path = Path(__file__).parent.parent / "config" / "secrets.yaml"
    with open(secrets_path, "r", encoding="utf-8") as f:
        secrets = yaml.safe_load(f) or {}
    api_key = secrets.get("mcp_token", "")

    history_file = Path(history_path)
    history: dict = load_json(history_file, {})
    if "rss_papers" not in history:
        history["rss_papers"] = {}

    pending_file = Path(pending_path)
    pending: list[dict] = load_json(pending_file, [])

    known_ids = get_known_ids(history)
    pending_ids = {p.get("paper_id", "") for p in pending}

    new_count = 0
    skipped_no_abstract = 0
    skipped_not_indexed = 0

    for paper in raw_papers:
        pid = paper.get("paper_id") or paper.get("url", "")
        if not pid or pid in known_ids or pid in pending_ids:
            continue

        title = paper.get("title", "")
        raw_abstract = (paper.get("abstract") or "").strip()

        # 清除期刊前缀占位符（EarlyView / Ahead of Print）
        cleaned_abstract = _clean_abstract(raw_abstract)

        if _is_english(title):
            # 英文论文：摘要不完整时查 API，以 API 收录作为"已发表"判断标准
            if len(cleaned_abstract) < 80:
                if not api_key:
                    skipped_no_abstract += 1
                    continue
                api_result = lookup_paper_api(title, api_key)
                time.sleep(0.3)  # 避免频繁请求
                if api_result is None:
                    # API 未收录 → 视为未发表，跳过
                    skipped_not_indexed += 1
                    continue
                # API 有更好的摘要则采用
                api_abstract = (api_result.get("abstract") or "").strip()
                if len(api_abstract) >= 80:
                    cleaned_abstract = api_abstract
                # 若 API 无摘要但已收录，保留（说明已发表，摘要只是受版权限制）
        else:
            # 中文论文：仅做摘要长度过滤
            if len(cleaned_abstract) < 80:
                skipped_no_abstract += 1
                continue

        paper = dict(paper)
        paper["paper_id"] = pid
        paper["abstract"] = cleaned_abstract  # 存入清洗后的摘要
        paper["collected_at"] = datetime.now(tz=timezone.utc).isoformat()

        pending.append(paper)
        history["rss_papers"][pid] = paper
        pending_ids.add(pid)
        new_count += 1

    history["last_updated"] = datetime.now(tz=timezone.utc).isoformat()

    save_json(pending_file, pending)
    save_json(history_file, history)

    print(
        f"[INFO] Ingest 完成: {new_count} 篇新论文加入 pending 池"
        f"（当前共 {len(pending)} 篇）"
        f"，跳过无摘要 {skipped_no_abstract} 篇"
        f"，跳过 API 未收录 {skipped_not_indexed} 篇"
    )


# ===================== Filter 模式 =====================

def run_filter(
    pending_path: str,
    profile_path: str,
    prompt_path: str,
    pushed_path: str,
    feishu_out_path: str,
    top_n: int,
) -> None:
    """从 batch 文件筛选论文，写入本地备份和飞书输入文件。"""
    batch_file = Path(pending_path)
    pending: list[dict] = load_json(batch_file, [])

    if not pending:
        print("[INFO] batch 为空，无需筛选")
        return

    print(f"[INFO] 本批共 {len(pending)} 篇，开始 LLM 筛选...")

    # 构建 prompt
    profile = load_profile(profile_path)
    prompt_vars = build_prompt_vars(profile, top_n)
    prompt_vars["papers"] = format_papers_for_prompt(pending)

    with open(prompt_path, "r", encoding="utf-8") as f:
        template = f.read()

    # 用 <<var>> 占位符替换，避免与 JSON 花括号冲突
    prompt = template
    for key, val in prompt_vars.items():
        prompt = prompt.replace(f"<<{key}>>", val)

    # 调用 LLM API（兼容 OpenAI 格式）
    secrets_path = Path(__file__).parent.parent / "config" / "secrets.yaml"
    with open(secrets_path, "r", encoding="utf-8") as f:
        secrets = yaml.safe_load(f) or {}
    client = openai.OpenAI(
        api_key=secrets.get("llm_api_key", ""),
        base_url=secrets.get("llm_base_url", "https://api.openai.com/v1"),
    )
    response = client.chat.completions.create(
        model=secrets.get("llm_model", "gpt-4o"),
        max_tokens=2048,
        temperature=0.3,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_output = response.choices[0].message.content.strip()

    # 解析 JSON 输出
    json_match = re.search(r"\[.*\]", raw_output, re.DOTALL)
    if not json_match:
        print(f"[ERROR] LLM 输出无法解析为 JSON:\n{raw_output}", file=sys.stderr)
        sys.exit(1)

    selected_meta: list[dict] = json.loads(json_match.group())

    # 用 index 取 pending 中的完整数据，补充 LLM 给的字段
    selected_papers = []
    for item in selected_meta:
        idx = item.get("index", -1)
        if 0 <= idx < len(pending):
            paper = dict(pending[idx])
            paper["tags"] = item.get("tags", [])
            paper["reason"] = item.get("reason", "")
            # store_feishu.py 期望的字段名
            paper["relevance_score"] = item.get("relevance_score", 0)
            paper["abstract_cn"] = paper.get("abstract", "")
            paper["recommendation"] = paper.get("reason", "")
            selected_papers.append(paper)

    print(f"[INFO] LLM 筛选完成，入选 {len(selected_papers)} 篇")

    push_date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    # 写入 pushed_papers.json（追加模式，本地永久备份）
    pushed_file = Path(pushed_path)
    pushed_history: list = load_json(pushed_file, [])
    pushed_history.append({
        "pushed_at": push_date,
        "total_candidates": len(pending),
        "papers": selected_papers,
    })
    save_json(pushed_file, pushed_history)
    print(f"[INFO] 本地备份已写入: {pushed_path}")

    # 写入飞书输入文件（供 store_feishu.py 使用）
    # 顺带合并当日新闻处理结果
    news_processed_path = Path(feishu_out_path).parent.parent / "news_processed.json"
    if news_processed_path.exists():
        news_data = load_json(news_processed_path, [])
        print(f"[INFO] 合并新闻 {len(news_data)} 条到 daily 文件")
    else:
        news_data = []

    feishu_out = Path(feishu_out_path)
    save_json(feishu_out, {
        "date": push_date,
        "archived": False,
        "papers": selected_papers,
        "news": news_data,
    })
    print(f"[INFO] 飞书输入文件已写入: {feishu_out_path}")


# ===================== Split 模式 =====================

def run_split(pending_path: str, batch_dir: str, num_batches: int = 7) -> None:
    """将 pending 池均分为 num_batches 份，保存到 batch_dir，然后清空 pending 池。"""
    pending_file = Path(pending_path)
    pending: list[dict] = load_json(pending_file, [])

    if not pending:
        print("[INFO] pending 池为空，无需拆分")
        return

    batch_dir_path = Path(batch_dir)
    batch_dir_path.mkdir(parents=True, exist_ok=True)

    total = len(pending)
    base_size = total // num_batches
    remainder = total % num_batches

    print(f"[INFO] pending 池共 {total} 篇，拆分为 {num_batches} 批")

    idx = 0
    for i in range(num_batches):
        size = base_size + (1 if i < remainder else 0)
        batch = pending[idx: idx + size]
        idx += size
        out_path = batch_dir_path / f"day_{i + 1}.json"
        save_json(out_path, batch)
        print(f"  day_{i + 1}.json: {len(batch)} 篇")

    # 清空 pending 池
    save_json(pending_file, [])
    print("[INFO] pending 池已清空，batch 文件已生成")


# ===================== 主流程 =====================

def main() -> None:
    parser = argparse.ArgumentParser(description="论文 LLM 筛选与 pending 池管理")

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--ingest", action="store_true", help="将 raw 新论文追加到 pending 池")
    mode.add_argument("--split",  action="store_true", help="将 pending 池均分为 7 份 batch 文件")
    mode.add_argument("--filter", action="store_true", help="从指定 batch 文件筛选论文")

    parser.add_argument("--raw",       default="data/papers_rss_raw.json")
    parser.add_argument("--pending",   default="data/pending_papers.json")
    parser.add_argument("--history",   default="data/dedup_history.json")
    parser.add_argument("--batch-dir", default="data/batches")
    parser.add_argument("--batch",     default=None, help="--filter 模式下指定 batch 文件路径")
    parser.add_argument("--profile",   default="config/profile.yaml")
    parser.add_argument("--prompt",    default="config/filter_prompt.txt")
    parser.add_argument("--pushed",    default="data/pushed_papers.json")
    parser.add_argument("--feishu-out", default=None)
    parser.add_argument("--top-n",     type=int, default=4)
    parser.add_argument("--num-batches", type=int, default=7)

    args = parser.parse_args()

    if args.ingest:
        run_ingest(args.raw, args.pending, args.history)
    elif args.split:
        run_split(args.pending, args.batch_dir, args.num_batches)
    else:
        batch_path = args.batch or f"data/batches/day_1.json"
        feishu_out = args.feishu_out or (
            f"data/daily/{datetime.now().strftime('%Y-%m-%d')}.json"
        )
        run_filter(
            batch_path,
            args.profile,
            args.prompt,
            args.pushed,
            feishu_out,
            args.top_n,
        )


if __name__ == "__main__":
    main()
