from __future__ import annotations

from time import perf_counter
from typing import Any

import requests

from v2.config import V2Config, get_config
from v2.core.logger import V2RunLogger


def hpo_search(
    query: str,
    logger: V2RunLogger,
    caller: str,
    term: str = "",
    max_results: int = 5,
    top_k: int | None = None,
    config: V2Config | None = None,
) -> dict[str, Any]:
    cfg = config or get_config()
    start = perf_counter()
    q = term.strip() if term.strip() else query.strip()
    max_n = top_k if top_k is not None else max_results
    max_n = max(1, min(50, int(max_n)))
    tool_input = {"term": q, "caller": caller, "max_results": max_n}
    try:
        url = "https://clinicaltables.nlm.nih.gov/api/hpo/v3/search"
        params = {
            "terms": q,
            "count": max_n,
            "offset": 0,
            "ef": "synonym.term:synonyms",
        }
        resp = requests.get(url, params=params, timeout=cfg.request_timeout_s)
        resp.raise_for_status()
        data = resp.json()

        total = 0
        codes: list[str] = []
        names: list[str] = []
        synonyms_per_item: list[list[str]] = []
        if isinstance(data, list) and len(data) >= 4:
            total = int(data[0]) if isinstance(data[0], int | float) else 0
            codes = data[1] if isinstance(data[1], list) else []
            extras = data[2] if isinstance(data[2], dict) else {}
            rows = data[3] if isinstance(data[3], list) else []
            for row in rows:
                if isinstance(row, list) and row:
                    names.append(str(row[-1]).strip())
            syns = extras.get("synonyms", []) if isinstance(extras, dict) else []
            if isinstance(syns, list):
                for entry in syns:
                    if isinstance(entry, list):
                        synonyms_per_item.append([str(x).strip() for x in entry if str(x).strip()][:5])
                    elif isinstance(entry, str):
                        synonyms_per_item.append([entry.strip()] if entry.strip() else [])
                    else:
                        synonyms_per_item.append([])

        results: list[dict[str, str]] = []
        for idx, code in enumerate(codes[:max_n]):
            name = names[idx] if idx < len(names) else ""
            synonyms = synonyms_per_item[idx] if idx < len(synonyms_per_item) else []
            results.append(
                {
                    "hpo_id": str(code),
                    "name": name,
                    "synonym": "; ".join(synonyms[:3]) if synonyms else "",
                    "source": "nlm_clinicaltables_hpo",
                }
            )

        top_result_summaries = [f"{r.get('hpo_id', '')} | {r.get('name', '')}" for r in results[:3]]
        output = {
            "source": "nlm_clinicaltables_hpo",
            "term": q,
            "total": total,
            "result_count": len(results),
            "results": results[:max_n],
            "top_result_summaries": top_result_summaries,
            "api_url": "https://clinicaltables.nlm.nih.gov/api/hpo/v3/search",
            "data": {"items": results[:max_n]},
        }
        latency_ms = round((perf_counter() - start) * 1000, 2)
        logger.log_tool_call("hpo_search", tool_input, output, latency_ms, True, "")
        return output
    except Exception as exc:
        latency_ms = round((perf_counter() - start) * 1000, 2)
        logger.log_tool_call("hpo_search", tool_input, {}, latency_ms, False, str(exc))
        raise RuntimeError(f"HPO real call failed: {exc}") from exc
