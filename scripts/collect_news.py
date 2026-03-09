#!/usr/bin/env python3
"""
新闻采集脚本 — 从 sources.yaml 中 scrape_targets 定义的网站抓取最新资讯。

输出格式与 store_feishu.py 的 news_to_fields 兼容：
  title, source, date, url, excerpt, language

LLM 处理（summary_cn / key_points / tags / relevance_score）由 openclaw 在推送流程中完成。

Usage:
    python collect_news.py --config config/sources.yaml --output data/news_raw.json
    python collect_news.py --config config/sources.yaml --output data/news_raw.json --days 7
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ===================== 工具函数 =====================

def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def fetch_page(url: str, timeout: int = 20) -> BeautifulSoup | None:
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"[WARN] 抓取失败 {url}: {e}", file=sys.stderr)
        return None


def parse_date(text: str) -> str:
    """尽力从各种日期字符串中提取 ISO 日期。"""
    if not text:
        return ""
    text = text.strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return text


def within_days(date_str: str, days: int) -> bool:
    if days <= 0 or not date_str:
        return True
    try:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= cutoff
    except Exception:
        return True


# ===================== CSS 选择器抓取 =====================

def scrape_target(target: dict, days: int, timeout: int = 20) -> list[dict]:
    """根据 sources.yaml 中配置的 CSS 选择器抓取文章列表。"""
    url = target.get("url", "")
    name = target.get("name", "unknown")
    language = target.get("language", "en")
    selectors = target.get("selectors", {})

    container_sel = selectors.get("container", "")
    title_sel = selectors.get("title", "")
    link_sel = selectors.get("link", "")
    date_sel = selectors.get("date", "")
    excerpt_sel = selectors.get("excerpt", "")

    soup = fetch_page(url, timeout=timeout)
    if soup is None:
        return []

    containers = soup.select(container_sel) if container_sel else [soup]
    articles = []

    for item in containers:
        # 标题
        title_el = item.select_one(title_sel) if title_sel else None
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue

        # 链接
        link_el = item.select_one(link_sel) if link_sel else title_el
        href = link_el.get("href", "") if link_el else ""
        article_url = urljoin(url, href) if href else url

        # 日期
        date_el = item.select_one(date_sel) if date_sel else None
        date_str = parse_date(date_el.get_text(strip=True) if date_el else "")

        # 摘要片段
        excerpt_el = item.select_one(excerpt_sel) if excerpt_sel else None
        excerpt = excerpt_el.get_text(strip=True) if excerpt_el else ""

        if not within_days(date_str, days):
            continue

        articles.append({
            "type": "news",
            "title": title,
            "source": name,
            "date": date_str,
            "url": article_url,
            "excerpt": excerpt,
            "language": language,
            # 以下字段由 openclaw LLM 步骤填充
            "summary_cn": "",
            "key_points": "",
            "tags": [],
            "relevance_score": 0,
        })

    return articles


# ===================== RSS 新闻采集 =====================

def parse_rss_date(entry) -> str:
    """从 feedparser entry 提取 ISO 日期字符串。"""
    for field in ("published_parsed", "updated_parsed"):
        t = entry.get(field)
        if t:
            try:
                return datetime(*t[:3]).strftime("%Y-%m-%d")
            except Exception:
                pass
    for field in ("published", "updated"):
        text = entry.get(field, "")
        if text:
            try:
                dt = parsedate_to_datetime(text)
                return dt.strftime("%Y-%m-%d")
            except Exception:
                pass
            return parse_date(text)
    return ""


def collect_rss_news(source: dict, days: int, timeout: int = 20) -> list[dict]:
    """采集一个 RSS 新闻源的文章列表。"""
    url = source.get("url", "")
    name = source.get("name", "unknown")
    language = source.get("language", "en")

    try:
        feed = feedparser.parse(url)
    except Exception as e:
        print(f"[WARN] RSS 采集失败 {url}: {e}", file=sys.stderr)
        return []

    articles = []
    for entry in feed.entries:
        title = entry.get("title", "").strip()
        if not title:
            continue

        link = entry.get("link", "")
        date_str = parse_rss_date(entry)
        excerpt = ""
        summary = entry.get("summary", "") or entry.get("description", "")
        if summary:
            excerpt = BeautifulSoup(summary, "html.parser").get_text(separator=" ", strip=True)[:300]

        if not within_days(date_str, days):
            continue

        articles.append({
            "type": "news",
            "title": title,
            "source": name,
            "date": date_str,
            "url": link,
            "excerpt": excerpt,
            "language": language,
            "summary_cn": "",
            "key_points": "",
            "tags": [],
            "relevance_score": 0,
        })

    return articles


# ===================== 主流程 =====================

def main() -> None:
    parser = argparse.ArgumentParser(description="从配置的新闻网站采集资讯")
    parser.add_argument("--config", required=True, help="sources.yaml 路径")
    parser.add_argument("--output", required=True, help="输出 JSON 路径")
    parser.add_argument("--days", type=int, default=-1,
                        help="只保留最近 N 天（0=不过滤，-1=读配置）")
    args = parser.parse_args()

    config = load_config(args.config)
    settings = config.get("news_settings", {})
    timeout = settings.get("timeout", 20)

    days = args.days if args.days >= 0 else settings.get("days_lookback", 7)

    targets = config.get("scrape_targets", [])
    rss_news_sources = config.get("rss_news", [])

    print(f"[INFO] 新闻采集开始，CSS 来源 {len(targets)} 个 + RSS 来源 {len(rss_news_sources)} 个，"
          f"日期过滤: {'最近 ' + str(days) + ' 天' if days > 0 else '不过滤'}")

    all_news = []
    for target in targets:
        name = target.get("name", "unknown")
        print(f"[INFO] 抓取（CSS）: {name}")
        articles = scrape_target(target, days=days, timeout=timeout)
        print(f"[INFO]   {name}: {len(articles)} 条")
        all_news.extend(articles)

    for source in rss_news_sources:
        name = source.get("name", "unknown")
        print(f"[INFO] 抓取（RSS）: {name}")
        articles = collect_rss_news(source, days=days, timeout=timeout)
        print(f"[INFO]   {name}: {len(articles)} 条")
        all_news.extend(articles)

    # 标题去重
    seen: set[str] = set()
    unique = []
    for item in all_news:
        key = item["title"].strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(item)

    from pathlib import Path
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(unique, f, ensure_ascii=False, indent=2)

    print(f"[INFO] 采集完成: {len(all_news)} 条 → 去重后 {len(unique)} 条 → {args.output}")


if __name__ == "__main__":
    main()
