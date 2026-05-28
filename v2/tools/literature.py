from __future__ import annotations

import re
from time import perf_counter
from typing import Any
from urllib.parse import quote_plus

import requests

from v2.config import V2Config, get_config
from v2.core.logger import V2RunLogger


def pubmed_search(
    query: str,
    logger: V2RunLogger,
    caller: str,
    retmax: int = 5,
    top_k: int | None = None,
    config: V2Config | None = None,
) -> dict[str, Any]:
    cfg = config or get_config()
    start = perf_counter()
    max_results = top_k if top_k is not None else retmax
    max_results = max(1, min(20, int(max_results)))
    tool_input = {"query": query, "caller": caller, "retmax": max_results}
    try:
        esearch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        esearch_params = {
            "db": "pubmed",
            "term": query,
            "retmode": "json",
            "retmax": max_results,
            "sort": "relevance",
        }
        esearch_resp = requests.get(esearch_url, params=esearch_params, timeout=cfg.request_timeout_s)
        esearch_resp.raise_for_status()
        esearch_data = esearch_resp.json()
        id_list = ((esearch_data.get("esearchresult") or {}).get("idlist")) or []
        if not isinstance(id_list, list):
            id_list = []

        results: list[dict[str, str]] = []
        if id_list:
            esummary_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
            esummary_params = {
                "db": "pubmed",
                "id": ",".join([str(x) for x in id_list[:max_results]]),
                "retmode": "json",
            }
            esummary_resp = requests.get(esummary_url, params=esummary_params, timeout=cfg.request_timeout_s)
            esummary_resp.raise_for_status()
            esummary_data = esummary_resp.json()
            result_obj = esummary_data.get("result") or {}
            for pmid in id_list[:max_results]:
                item = result_obj.get(str(pmid)) or {}
                title = str(item.get("title", "")).strip()
                pubdate = str(item.get("pubdate", "")).strip()
                source = str(item.get("source", "")).strip()
                year_match = re.search(r"\b(19|20)\d{2}\b", pubdate)
                year = year_match.group(0) if year_match else ""
                if not title:
                    continue
                results.append(
                    {
                        "pmid": str(pmid),
                        "title": title,
                        "year": year,
                        "journal": source,
                        "source": "pubmed",
                        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    }
                )

        top_result_summaries = [
            f"{r.get('pmid', '')} | {r.get('title', '')[:120]} | {r.get('year', '')}" for r in results[:3]
        ]
        output = {
            "source": "ncbi_eutils_pubmed",
            "query": query,
            "retmax": max_results,
            "result_count": len(results),
            "results": results[:max_results],
            "top_result_summaries": top_result_summaries,
            "search_url": f"{esearch_url}?db=pubmed&term={quote_plus(query)}",
            "data": {"items": results[:max_results]},
        }
        latency_ms = round((perf_counter() - start) * 1000, 2)
        logger.log_tool_call("pubmed_search", tool_input, output, latency_ms, True, "")
        return output
    except Exception as exc:
        latency_ms = round((perf_counter() - start) * 1000, 2)
        logger.log_tool_call("pubmed_search", tool_input, {}, latency_ms, False, str(exc))
        raise RuntimeError(f"PubMed real call failed: {exc}") from exc
