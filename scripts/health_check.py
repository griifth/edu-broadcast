#!/usr/bin/env python3
"""
信息流健康检查中台

定期检查 edu-broadcast 系统各环节的健康状态：
- RSS 源可用性
- 新闻源可用性
- LLM API 可用性
- 飞书 API 可用性
- 数据采集量
- 推送成功率

Usage:
    python scripts/health_check.py
    python scripts/health_check.py --alert  # 异常时推送飞书告警
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import requests
import yaml


def load_config() -> dict:
    """读取配置文件"""
    config_dir = Path(__file__).parent.parent / "config"

    # 读取 sources.yaml
    sources_path = config_dir / "sources.yaml"
    if sources_path.exists():
        with open(sources_path, encoding="utf-8") as f:
            sources = yaml.safe_load(f)
    else:
        sources = {}

    # 读取 secrets.yaml
    secrets_path = config_dir / "secrets.yaml"
    if secrets_path.exists():
        with open(secrets_path, encoding="utf-8") as f:
            secrets = yaml.safe_load(f)
    else:
        secrets = {}

    return {"sources": sources, "secrets": secrets}


def check_rss_sources(sources_config: dict) -> Dict:
    """检查 RSS 源可用性"""
    result = {
        "status": "healthy",
        "total": 0,
        "available": 0,
        "unavailable": [],
        "checked_at": datetime.now(tz=timezone.utc).isoformat()
    }

    rss_journals = sources_config.get("rss_journals", [])
    result["total"] = len(rss_journals)

    for journal in rss_journals[:5]:  # 只检查前 5 个，避免耗时过长
        url = journal.get("url", "")
        name = journal.get("name", "Unknown")

        try:
            resp = requests.head(url, timeout=10, allow_redirects=True)
            if resp.status_code < 400:
                result["available"] += 1
            else:
                result["unavailable"].append({"name": name, "status_code": resp.status_code})
        except Exception as e:
            result["unavailable"].append({"name": name, "error": str(e)[:50]})

    if result["unavailable"]:
        result["status"] = "degraded" if result["available"] > 0 else "unhealthy"

    return result


def check_news_sources(sources_config: dict) -> Dict:
    """检查新闻源可用性"""
    result = {
        "status": "healthy",
        "sources": {},
        "checked_at": datetime.now(tz=timezone.utc).isoformat()
    }

    # 检查 EdSurge RSS
    rss_news = sources_config.get("rss_news", [])
    for news in rss_news:
        url = news.get("url", "")
        name = news.get("name", "Unknown")
        try:
            resp = requests.head(url, timeout=10)
            result["sources"][name] = "available" if resp.status_code < 400 else f"error_{resp.status_code}"
        except Exception as e:
            result["sources"][name] = f"error: {str(e)[:30]}"

    # 检查新华教育
    try:
        resp = requests.head("https://education.news.cn/", timeout=10)
        result["sources"]["新华教育"] = "available" if resp.status_code < 400 else f"error_{resp.status_code}"
    except Exception as e:
        result["sources"]["新华教育"] = f"error: {str(e)[:30]}"

    # 判断整体状态
    unavailable_count = sum(1 for v in result["sources"].values() if "error" in v)
    if unavailable_count == len(result["sources"]):
        result["status"] = "unhealthy"
    elif unavailable_count > 0:
        result["status"] = "degraded"

    return result


def check_llm_api(secrets: dict) -> Dict:
    """检查 LLM API 可用性"""
    result = {
        "status": "unknown",
        "checked_at": datetime.now(tz=timezone.utc).isoformat()
    }

    api_key = secrets.get("llm_api_key", "")
    base_url = secrets.get("llm_base_url", "")

    if not api_key or not base_url:
        result["status"] = "not_configured"
        return result

    try:
        # 简单的 API 健康检查（不实际调用，只检查认证）
        headers = {"Authorization": f"Bearer {api_key}"}
        resp = requests.get(f"{base_url.rstrip('/v1')}/v1/models", headers=headers, timeout=10)

        if resp.status_code == 200:
            result["status"] = "healthy"
        elif resp.status_code == 401:
            result["status"] = "auth_failed"
        else:
            result["status"] = f"error_{resp.status_code}"
    except Exception as e:
        result["status"] = f"error: {str(e)[:30]}"

    return result


def check_feishu_api(secrets: dict) -> Dict:
    """检查飞书 API 可用性"""
