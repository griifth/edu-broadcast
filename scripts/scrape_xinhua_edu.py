#!/usr/bin/env python3
"""
新华教育频道采集脚本。

两种模式：
  index   抓取频道首页文章索引（标题、URL、日期、预览文字）
  full    对指定 URL 列表逐篇抓取正文

Usage:
    # 获取近 7 天文章索引
    python scrape_xinhua_edu.py index --output data/xinhua_index.json --days 7

    # 根据索引抓取正文（openclaw 筛选后调用）
    python scrape_xinhua_edu.py full --urls data/xinhua_filtered_urls.json \
                                     --output data/xinhua_full.json
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://education.news.cn/"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://education.news.cn/",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


# ===================== 工具函数 =====================

def fetch(url: str, timeout: int = 20) -> BeautifulSoup | None:
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"[WARN] 请求失败 {url}: {e}", file=sys.stderr)
        return None


def parse_cn_date(text: str) -> str:
    """将新华常见日期格式转为 YYYY-MM-DD。"""
    if not text:
        return ""
    text = text.strip()
    # 2026-03-07 / 2026/03/07
    m = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    # 03月07日
    m = re.search(r"(\d{1,2})月(\d{1,2})日", text)
    if m:
        year = datetime.now().year
        return f"{year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
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


# ===================== Index 模式 =====================

def scrape_index(days: int, timeout: int = 20) -> list[dict]:
    """抓取新华教育频道首页文章列表。"""
    soup = fetch(BASE_URL, timeout=timeout)
    if soup is None:
        return []

    articles = []
    seen_urls: set[str] = set()

    # 新华教育页面常见结构：a 标签包含标题，邻近元素有日期
    # URL 格式：education.news.cn/20250701/hexhash/c.html（8位日期无分隔符）
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href or href == "#":
            continue

        # 匹配两种格式：
        # 20250701/...（8位紧凑日期，相对或绝对路径）或 /2026-03/07/...（带分隔符日期）
        if not re.search(r"\d{8}/|/\d{4}[-/]\d{2}/", href):
            continue

        title = a.get_text(strip=True)
        if not title or len(title) < 8:
            continue

        url = urljoin(BASE_URL, href)
        if url in seen_urls:
            continue
        seen_urls.add(url)

        # 尝试从 href 提取日期
        date_str = ""
        # 格式一：/20250701/ 或 开头直接 20250701/（相对路径无前导斜杠）
        m = re.search(r"(?:^|/)(\d{4})(\d{2})(\d{2})/", href)
        if m:
            date_str = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        else:
            # 格式二：2026-03/07 或 2026/03/07
            m = re.search(r"(\d{4})[-/](\d{2})[-/](\d{2})", href)
            if m:
                date_str = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

        # 日期过滤
        if not within_days(date_str, days):
            continue

        # 获取父容器中的预览文字
        parent = a.parent
        preview = ""
        if parent:
            texts = [t.strip() for t in parent.stripped_strings if t.strip() != title]
            preview = " ".join(texts)[:200]

        articles.append({
            "type": "news",
            "title": title,
            "source": "新华教育",
            "date": date_str,
            "url": url,
            "preview": preview,
            "language": "zh",
        })

    return articles


# ===================== Full 模式 =====================

def scrape_full(urls: list[str], timeout: int = 20) -> list[dict]:
    """逐篇抓取文章正文。"""
    results = []

    for url in urls:
        print(f"[INFO] 抓取正文: {url}")
        soup = fetch(url, timeout=timeout)
        if soup is None:
            continue

        # 标题
        title_el = soup.find("h1") or soup.find("h2")
        title = title_el.get_text(strip=True) if title_el else ""

        # 日期
        date_str = ""
        date_el = soup.find(class_=re.compile(r"date|time|pub", re.I))
        if date_el:
            date_str = parse_cn_date(date_el.get_text())
        if not date_str:
            m = re.search(r"(\d{4})[-/](\d{2})[-/](\d{2})", url)
            if m:
                date_str = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

        # 正文：优先取 article / .content / #content 区域
        content_el = (
            soup.find("article")
            or soup.find(class_=re.compile(r"content|article|body", re.I))
            or soup.find(id=re.compile(r"content|article|body", re.I))
            or soup.find("main")
        )
        if content_el:
            paragraphs = [p.get_text(strip=True) for p in content_el.find_all("p")]
        else:
            paragraphs = [p.get_text(strip=True) for p in soup.find_all("p")]

        content = "\n".join(p for p in paragraphs if len(p) > 20)

        results.append({
            "type": "news",
            "title": title,
            "source": "新华教育",
            "date": date_str,
            "url": url,
            "content": content,
            "language": "zh",
            # 以下字段由 openclaw LLM 步骤填充
            "summary_cn": "",
            "key_points": "",
            "tags": [],
            "relevance_score": 0,
        })

    return results


# ===================== 主流程 =====================

def main() -> None:
    parser = argparse.ArgumentParser(description="新华教育频道采集")
    sub = parser.add_subparsers(dest="mode", required=True)

    # index 子命令
    p_index = sub.add_parser("index", help="抓取文章索引")
    p_index.add_argument("--output", required=True)
    p_index.add_argument("--days", type=int, default=7)
    p_index.add_argument("--timeout", type=int, default=20)

    # full 子命令
    p_full = sub.add_parser("full", help="抓取文章正文")
    p_full.add_argument("--urls", required=True, help="URL 列表 JSON 文件")
    p_full.add_argument("--output", required=True)
    p_full.add_argument("--timeout", type=int, default=20)

    args = parser.parse_args()

    if args.mode == "index":
        print(f"[INFO] 抓取新华教育索引（最近 {args.days} 天）")
        articles = scrape_index(days=args.days, timeout=args.timeout)
        print(f"[INFO] 获取 {len(articles)} 条")

    else:  # full
        with open(args.urls, "r", encoding="utf-8") as f:
            url_list = json.load(f)
        if isinstance(url_list[0], dict):
            url_list = [item.get("url", "") for item in url_list if item.get("url")]
        print(f"[INFO] 抓取正文，共 {len(url_list)} 篇")
        articles = scrape_full(url_list, timeout=args.timeout)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)

    print(f"[INFO] 已写入 {args.output}")


if __name__ == "__main__":
    import sys as _sys
    from step_logger import StepLogger
    with StepLogger("scrape_xinhua_edu " + " ".join(_sys.argv[1:])):
        main()
