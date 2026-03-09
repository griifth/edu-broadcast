#!/usr/bin/env python3
"""
飞书多维表格归档脚本 - 将每日推送记录写入飞书 Bitable。

凭证通过环境变量传入（由 OpenClaw config vault 注入）：
  FEISHU_APP_ID, FEISHU_APP_SECRET,
  FEISHU_BITABLE_APP_TOKEN, FEISHU_BITABLE_TABLE_ID_PAPERS, FEISHU_BITABLE_TABLE_ID_NEWS

Usage:
    python store_feishu.py --input data/daily/2026-03-04.json
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
import yaml


def _load_secrets() -> dict:
    """读取 config/secrets.yaml，找不到则返回空字典。"""
    p = Path(__file__).parent.parent / "config" / "secrets.yaml"
    if p.exists():
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {}


class FeishuBitableClient:
    """飞书多维表格 API 客户端。"""

    BASE_URL = "https://open.feishu.cn/open-apis"

    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._tenant_token = None
        self._token_expires = 0

    def _get_tenant_token(self) -> str:
        if self._tenant_token and time.time() < self._token_expires:
            return self._tenant_token

        url = f"{self.BASE_URL}/auth/v3/tenant_access_token/internal"
        resp = requests.post(url, json={
            "app_id": self.app_id,
            "app_secret": self.app_secret,
        })
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取 tenant_access_token 失败: {data.get('msg')}")

        self._tenant_token = data["tenant_access_token"]
        self._token_expires = time.time() + data.get("expire", 7200) - 60
        return self._tenant_token

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._get_tenant_token()}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def add_record(self, app_token: str, table_id: str, fields: dict) -> bool:
        """向多维表格中添加一条记录。"""
        url = f"{self.BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
        resp = requests.post(url, headers=self._headers(), json={"fields": fields})

        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == 0:
                return True
            print(f"[WARN] 写入失败: {data.get('msg')}", file=sys.stderr)
            return False

        print(f"[WARN] HTTP {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        return False

    def batch_add_records(self, app_token: str, table_id: str, records: list[dict]) -> tuple[int, int]:
        """批量写入记录，返回 (成功数, 失败数)。"""
        success = 0
        failed = 0
        for fields in records:
            if self.add_record(app_token, table_id, fields):
                success += 1
            else:
                failed += 1
            time.sleep(0.2)
        return success, failed


def _url_field(url: str) -> dict:
    """飞书超链接字段格式。"""
    return {"link": url, "text": url} if url else {"link": "", "text": ""}


def paper_to_fields(paper: dict, push_date: str) -> dict:
    tags = paper.get("tags", [])
    return {
        "标题": paper.get("title", ""),
        "来源": paper.get("venue", ""),
        "日期": paper.get("date", ""),
        "摘要": paper.get("abstract_cn", ""),
        "标签": ", ".join(tags) if isinstance(tags, list) else str(tags),
        "链接": _url_field(paper.get("url", "")),
        "推荐理由": paper.get("recommendation", ""),
        "相关性评分": paper.get("relevance_score", 0),
        "推送日期": push_date,
        "用户评价": "",
        "备注": "",
    }


def news_to_fields(news: dict, push_date: str) -> dict:
    tags = news.get("tags", [])
    return {
        "标题": news.get("title", ""),
        "来源": news.get("source", ""),
        "日期": news.get("date", ""),
        "摘要": news.get("summary_cn", ""),
        "核心要点": news.get("key_points", ""),
        "标签": ", ".join(tags) if isinstance(tags, list) else str(tags),
        "链接": _url_field(news.get("url", "")),
        "相关性评分": news.get("relevance_score", 0),
        "推送日期": push_date,
        "用户评价": "",
        "备注": "",
    }


def main():
    parser = argparse.ArgumentParser(description="飞书多维表格归档")
    # 新接口：分别传论文 / 新闻文件
    parser.add_argument("--papers", help="论文 JSON 文件路径（filter_papers.py 输出）")
    parser.add_argument("--news",   help="新闻 JSON 文件路径（news_processed.json）")
    # 旧接口兼容：合并格式 {date, papers, news}
    parser.add_argument("--input",  help="合并推送记录 JSON 路径（兼容旧格式）")
    args = parser.parse_args()

    if not args.papers and not args.news and not args.input:
        parser.error("需要提供 --papers / --news 至少一个，或使用旧格式 --input")

    # 凭证：优先环境变量，fallback secrets.yaml
    secrets = _load_secrets()
    app_id     = os.environ.get("FEISHU_APP_ID")     or secrets.get("feishu_app_id", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET")  or secrets.get("feishu_app_secret", "")
    app_token  = os.environ.get("FEISHU_BITABLE_APP_TOKEN") or secrets.get("feishu_bitable_app_token", "")
    table_papers = os.environ.get("FEISHU_BITABLE_TABLE_ID_PAPERS") or secrets.get("feishu_bitable_table_id_papers", "")
    table_news   = os.environ.get("FEISHU_BITABLE_TABLE_ID_NEWS")   or secrets.get("feishu_bitable_table_id_news", "")

    if not all([app_id, app_secret, app_token, table_papers, table_news]):
        print("[ERROR] 缺少飞书凭证，��检查环境变量或 config/secrets.yaml", file=sys.stderr)
        sys.exit(1)

    from datetime import date as _date
    today = _date.today().isoformat()

    papers_list: list[dict] = []
    news_list: list[dict] = []
    push_date = today

    if args.input:
        # 旧格式：{date, papers, news}
        p = Path(args.input)
        if not p.exists():
            print(f"[ERROR] 文件不存在: {args.input}", file=sys.stderr)
            sys.exit(1)
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("archived"):
            print(f"[INFO] {args.input} 已归档，跳过")
            return
        push_date = data.get("date", today)
        papers_list = data.get("papers", [])
        news_list = data.get("news", [])

    if args.papers:
        p = Path(args.papers)
        if not p.exists():
            print(f"[ERROR] 文件不存在: {args.papers}", file=sys.stderr)
            sys.exit(1)
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 支持两种格式：直接数组 或 {date, papers}
        if isinstance(data, list):
            papers_list = data
        else:
            push_date = data.get("date", push_date)
            papers_list = data.get("papers", [])

    if args.news:
        p = Path(args.news)
        if not p.exists():
            print(f"[ERROR] 文件不存在: {args.news}", file=sys.stderr)
            sys.exit(1)
        with open(p, "r", encoding="utf-8") as f:
            news_list = json.load(f)
        if not isinstance(news_list, list):
            news_list = news_list.get("news", [])

    print(f"[INFO] 开始归档: {push_date}, 论文 {len(papers_list)} 篇, 新闻 {len(news_list)} 条")

    client = FeishuBitableClient(app_id, app_secret)

    if papers_list:
        paper_records = [paper_to_fields(p, push_date) for p in papers_list]
        p_ok, p_fail = client.batch_add_records(app_token, table_papers, paper_records)
        print(f"[INFO] 论文归档: 成功 {p_ok}, 失败 {p_fail}")

    if news_list:
        news_records = [news_to_fields(n, push_date) for n in news_list]
        n_ok, n_fail = client.batch_add_records(app_token, table_news, news_records)
        print(f"[INFO] 新闻归档: 成功 {n_ok}, 失败 {n_fail}")

        # 新闻历史存档（追加模式）
        history_path = Path(__file__).parent.parent / "data" / "news_history.json"
        if history_path.exists() and history_path.stat().st_size > 2:
            with open(history_path, "r", encoding="utf-8") as f:
                news_history: list = json.load(f)
        else:
            news_history = []
        news_history.append({"pushed_at": push_date, "news": news_list})
        history_path.parent.mkdir(parents=True, exist_ok=True)
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump(news_history, f, ensure_ascii=False, indent=2)
        print(f"[INFO] 新闻历史存档已更新: {history_path}")


if __name__ == "__main__":
    main()
