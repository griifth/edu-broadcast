#!/usr/bin/env python3
"""
RSS 论文采集脚本 — 从权威期刊 RSS 订阅获取最新论文。

支持的期刊分类（按 URL 域名自动识别，也可在 sources.yaml 中显式指定 category）：
  wanfang       : apps.wanfangdata.com.cn   （中文，统一处理）
  sage          : journals.sagepub.com
  apa           : psycnet.apa.org
  sciencedirect : www.sciencedirect.com
  wiley         : onlinelibrary.wiley.com

输出格式与 papers_raw.json 一致，可直接进入后续去重 → LLM 初筛 → 飞书归档流程。

Usage:
    python collect_rss_papers.py \\
        --config config/sources.yaml \\
        --output data/papers_rss_raw.json
    # 只保留最近 N 天（0 = 不过滤，依赖去重，适合 cron 每日运行）
    python collect_rss_papers.py \\
        --config config/sources.yaml \\
        --output data/papers_rss_raw.json \\
        --days 7
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import requests
import yaml


# ===================== HTML 工具 =====================

class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)


def strip_html(text: str) -> str:
    """去除 HTML 标签并合并空白。"""
    if not text:
        return ""
    s = _HTMLStripper()
    try:
        s.feed(text)
    except Exception:
        return text
    return re.sub(r'\s+', ' ', ''.join(s._parts)).strip()


# ===================== URL 域名 → 分类 =====================

_DOMAIN_CATEGORY: dict[str, str] = {
    "apps.wanfangdata.com.cn": "wanfang",
    "journals.sagepub.com": "sage",
    "onlinelibrary.wiley.com": "wiley",
    "link.springer.com": "springer",
}


def detect_category(url: str) -> str:
    host = urlparse(url).netloc
    return _DOMAIN_CATEGORY.get(host, "rss")


# ===================== RSS 抓取 =====================

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def fetch_feed(url: str, timeout: int = 20) -> feedparser.FeedParserDict | None:
    """用 requests 抓取 RSS XML，再交给 feedparser 解析（避免 feedparser 内置 fetch 被反爬）。"""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
        return feedparser.parse(resp.text)
    except Exception as e:
        print(f"[WARN] 抓取失败 {url}: {e}", file=sys.stderr)
        return None


# ===================== 字段提取（按分类定制）=====================

def _generic_authors(entry) -> list[str]:
    if getattr(entry, 'authors', None):
        return [a.get('name', '') for a in entry.authors if a.get('name')]
    if getattr(entry, 'author', None):
        return [entry.author]
    return []


def extract_authors(entry, category: str) -> list[str]:
    """按分类提取作者列表。"""
    if category == 'sciencedirect':
        # Elsevier 用 Dublin Core dc:creator，可能是逗号分隔字符串
        dc = getattr(entry, 'dc_creator', None)
        if dc:
            return [a.strip() for a in dc.split(',') if a.strip()]
    if category == 'wiley':
        # Wiley 的 entry.author 是多作者合并的单字符串，以 ", \n" 分隔
        author_str = getattr(entry, 'author', '') or ''
        if author_str and ('\n' in author_str or ';' in author_str):
            parts = re.split(r'[;\n]+', author_str)
            return [p.strip().strip(',').strip() for p in parts if p.strip().strip(',').strip()]
    # SAGE / APA / Wanfang：优先 authors 列表
    return _generic_authors(entry)


def extract_date(entry) -> str:
    """从 feedparser entry 提取日期，优先使用 published_parsed（UTC struct_time）。"""
    for attr in ('published_parsed', 'updated_parsed', 'created_parsed'):
        val = getattr(entry, attr, None)
        if val:
            try:
                dt = datetime(*val[:6], tzinfo=timezone.utc)
                return dt.isoformat()
            except Exception:
                pass
    # 退而求其次：使用原始字符串
    for attr in ('published', 'updated'):
        val = getattr(entry, attr, None)
        if val:
            return val
    return ""


def extract_abstract(entry, category: str) -> str:
    """
    提取论文摘要。
    - wanfang: description 字段含摘要（有 HTML）
    - 其余: summary > description > content[0].value
    """
    raw = ""
    if category == 'wanfang':
        raw = (
            getattr(entry, 'description', None)
            or getattr(entry, 'summary', None)
            or ""
        )
    else:
        raw = (
            getattr(entry, 'summary', None)
            or getattr(entry, 'description', None)
            or ""
        )
        # content 字段：若 summary 是期刊占位符（无真实摘要），优先用 content
        content_list = getattr(entry, 'content', None)
        if content_list:
            content_val = content_list[0].get('value', '')
            if not raw or len(strip_html(content_val)) > len(strip_html(raw)):
                raw = content_val
    return strip_html(raw)


def extract_doi(url: str) -> str:
    """从 URL 中提取 DOI（如有）。"""
    if url and "doi.org/" in url:
        return url.split("doi.org/", 1)[-1]
    return ""


# ===================== 归一化 =====================

def normalize_entry(entry, journal_name: str, category: str) -> dict:
    """将 feedparser entry 归一化为与 papers_raw.json 一致的结构。"""
    title = strip_html(getattr(entry, 'title', '') or '')
    url = getattr(entry, 'link', '') or ''
    date = extract_date(entry)
    abstract = extract_abstract(entry, category)
    authors = extract_authors(entry, category)
    paper_id = getattr(entry, 'id', '') or url

    return {
        "type": "paper",
        "title": title,
        "authors": authors,
        "venue": journal_name,
        "date": date,
        "abstract": abstract,
        "doi": extract_doi(url),
        "url": url,
        "source_db": category,
        "citation_count": 0,
        "paper_id": paper_id,
    }


# ===================== 日期过滤 =====================

def within_days(date_str: str, days: int) -> bool:
    """判断 date_str 是否在最近 days 天内。days=0 表示不过滤。"""
    if days <= 0 or not date_str:
        return True
    try:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= cutoff
    except Exception:
        return True  # 日期解析失败时保留条目


# ===================== 主流程 =====================

def load_config(path: str) -> dict:
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def main() -> None:
    parser = argparse.ArgumentParser(description="从期刊 RSS 采集论文")
    parser.add_argument("--config", required=True, help="sources.yaml 路径")
    parser.add_argument("--output", required=True, help="输出 JSON 路径")
    parser.add_argument(
        "--days", type=int, default=-1,
        help="只保留最近 N 天的条目（0=不过滤，-1=读 sources.yaml 配置，默认）"
    )
    args = parser.parse_args()

    config = load_config(args.config)
    settings = config.get("rss_settings", {})
    timeout = settings.get("timeout", 20)

    # days 优先级：CLI > sources.yaml > 0（不过滤）
    if args.days >= 0:
        days = args.days
    else:
        days = settings.get("days_lookback", 0)

    feeds: list[dict] = config.get("rss_journals", [])
    if not feeds:
        print("[ERROR] sources.yaml 中未找到 rss_journals 配置", file=sys.stderr)
        sys.exit(1)

    print(f"[INFO] RSS 论文采集开始，共 {len(feeds)} 个期刊，日期过滤: {'最近 ' + str(days) + ' 天' if days > 0 else '不过滤'}")

    all_papers: list[dict] = []

    for feed_cfg in feeds:
        url = feed_cfg.get("url", "")
        name = feed_cfg.get("name", "unknown")
        # 优先使用 yaml 中显式声明的 category，否则按 URL 域名推断
        category = feed_cfg.get("category") or detect_category(url)

        print(f"[INFO] 获取: [{category}] {name}")

        parsed = fetch_feed(url, timeout=timeout)
        if parsed is None:
            continue

        # bozo=True 说明 feedparser 收到了非合法 XML（如 HTML 重定向页）
        if parsed.bozo and not parsed.get("entries"):
            print(f"[WARN]   {name}: RSS 解析失败（返回非 XML，可能被反爬或 URL 失效）", file=sys.stderr)
            continue

        entries = parsed.get("entries", [])
        if not entries:
            print(f"[WARN]   {name}: feed 无条目（可能为空刊期）", file=sys.stderr)
            continue

        count = 0
        for entry in entries:
            paper = normalize_entry(entry, name, category)
            if not paper["title"]:
                continue
            if not within_days(paper["date"], days):
                continue
            all_papers.append(paper)
            count += 1

        print(f"[INFO]   {name}: {count} 篇")

    # 标题去重（同一 RSS 内或跨 feed 的重复条目）
    seen: set[str] = set()
    unique: list[dict] = []
    for p in all_papers:
        key = p["title"].strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(p)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(unique, f, ensure_ascii=False, indent=2)

    print(
        f"[INFO] 采集完成: 总计 {len(all_papers)} 条"
        f" → 去重后 {len(unique)} 条"
        f" → {args.output}"
    )


if __name__ == "__main__":
    import sys as _sys
    from step_logger import StepLogger
    with StepLogger("collect_rss_papers " + " ".join(_sys.argv[1:])):
        main()
