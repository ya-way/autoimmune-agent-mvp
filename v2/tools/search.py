from __future__ import annotations

import re
from time import perf_counter
from typing import Any

import requests

from v2.config import V2Config, get_config
from v2.core.logger import V2RunLogger


def web_search(query: str, logger: V2RunLogger, caller: str, config: V2Config | None = None) -> dict[str, Any]:
    cfg = config or get_config()
    start = perf_counter()
    tool_input = {"query": query, "caller": caller}
    if not cfg.anysearch_api_key:
        output = {
            "source": "web_search_unavailable",
            "results": [],
            "warning": "ANYSEARCH_API_KEY is missing; continue without web snippets.",
        }
        latency_ms = round((perf_counter() - start) * 1000, 2)
        logger.log_tool_call("web_search", tool_input, output, latency_ms, False, output["warning"])
        return output

    base_url = cfg.anysearch_base_url.rstrip("/")
    is_brave = "search.brave.com" in base_url
    try:
        if is_brave:
            url = base_url
            headers = {
                "Accept": "application/json",
                "X-Subscription-Token": cfg.anysearch_api_key,
            }
            effective_query = query
            params = {"q": effective_query, "count": 5}
            resp = requests.get(url, headers=headers, params=params, timeout=cfg.request_timeout_s)
            if resp.status_code == 422 and len(effective_query) > 280:
                trimmed_terms = re.split(r"[,\n;]+", effective_query)
                trimmed_query = ", ".join([t.strip() for t in trimmed_terms if t.strip()][:15])[:280]
                if trimmed_query:
                    params = {"q": trimmed_query, "count": 5}
                    resp = requests.get(url, headers=headers, params=params, timeout=cfg.request_timeout_s)
                    tool_input["effective_query"] = trimmed_query
            else:
                tool_input["effective_query"] = effective_query
        else:
            url = f"{base_url}/search"
            headers = {
                "Authorization": f"Bearer {cfg.anysearch_api_key}",
                "Content-Type": "application/json",
            }
            payload = {"query": query, "top_k": 5}
            resp = requests.post(url, headers=headers, json=payload, timeout=cfg.request_timeout_s)
        resp.raise_for_status()
        data = resp.json()
        if is_brave:
            raw_results = ((data.get("web") or {}).get("results")) or []
        else:
            raw_results = data.get("results") or data.get("data") or []
        normalized: list[str] = []
        if isinstance(raw_results, list):
            for item in raw_results[:5]:
                if isinstance(item, str):
                    normalized.append(item)
                elif isinstance(item, dict):
                    title = str(item.get("title", "")).strip()
                    snippet = str(item.get("snippet", "") or item.get("description", "") or item.get("content", "")).strip()
                    url_item = str(item.get("url", "")).strip()
                    normalized.append(f"{title} | {snippet} | {url_item}".strip(" |"))
        output = {"source": "brave_search" if is_brave else "anysearch", "results": normalized[:5]}
        latency_ms = round((perf_counter() - start) * 1000, 2)
        logger.log_tool_call("web_search", tool_input, output, latency_ms, True, "")
        return output
    except Exception as exc:
        latency_ms = round((perf_counter() - start) * 1000, 2)
        provider = "BraveSearch" if is_brave else "AnySearch"
        output = {
            "source": "web_search_failed",
            "results": [],
            "warning": f"{provider} call failed: {exc}",
        }
        logger.log_tool_call("web_search", tool_input, output, latency_ms, False, output["warning"])
        return output
