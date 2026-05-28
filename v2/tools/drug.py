from __future__ import annotations

from time import perf_counter
from typing import Any

import requests

from v2.config import V2Config, get_config
from v2.core.logger import V2RunLogger

RXNAV_BASE = "https://rxnav.nlm.nih.gov/REST"


def _is_class_like(text: str) -> bool:
    lower = text.lower()
    class_tokens = [
        "glucocorticoid",
        "corticosteroid",
        "steroid",
        "immunosuppressant",
    ]
    return any(token in lower for token in class_tokens)


def _get(url: str, params: dict[str, Any], timeout_s: int) -> dict[str, Any]:
    resp = requests.get(url, params=params, timeout=timeout_s)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise RuntimeError("RxNav response is not a JSON object")
    return data


def _extract_rxcui_list(payload: dict[str, Any]) -> list[str]:
    id_group = payload.get("idGroup")
    if not isinstance(id_group, dict):
        return []
    values = id_group.get("rxnormId")
    if not isinstance(values, list):
        return []
    return [str(x).strip() for x in values if str(x).strip()]


def _extract_approx_candidates(payload: dict[str, Any]) -> list[dict[str, str]]:
    group = payload.get("approximateGroup")
    if not isinstance(group, dict):
        return []
    raw = group.get("candidate")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        rxcui = str(item.get("rxcui", "")).strip()
        score = str(item.get("score", "")).strip()
        rank = str(item.get("rank", "")).strip()
        if not rxcui:
            continue
        out.append({"rxcui": rxcui, "score": score, "rank": rank})
    return out


def _fetch_name_by_rxcui(rxcui: str, timeout_s: int) -> str:
    payload = _get(f"{RXNAV_BASE}/rxcui/{rxcui}/properties.json", {}, timeout_s)
    prop = payload.get("properties")
    if not isinstance(prop, dict):
        return ""
    return str(prop.get("name", "")).strip()


def rxnorm_normalize_drug(
    drug_text: str,
    logger: V2RunLogger,
    caller: str,
    config: V2Config | None = None,
) -> dict[str, Any]:
    cfg = config or get_config()
    start = perf_counter()
    clean = drug_text.strip()
    tool_input = {"drug_text": clean, "caller": caller}
    if not clean:
        latency_ms = round((perf_counter() - start) * 1000, 2)
        output = {
            "provider": "rxnorm_rxnav",
            "input_text": clean,
            "normalized_name": "",
            "rxcui": "",
            "match_type": "unresolved",
            "candidates": [],
            "requires_confirmation": True,
            "limitations": ["Empty medication text."],
        }
        logger.log_tool_call("rxnorm_normalize_drug", tool_input, output, latency_ms, True, "")
        return output
    if _is_class_like(clean):
        latency_ms = round((perf_counter() - start) * 1000, 2)
        output = {
            "provider": "rxnorm_rxnav",
            "input_text": clean,
            "normalized_name": clean,
            "rxcui": "",
            "match_type": "class_entity",
            "candidates": [],
            "requires_confirmation": True,
            "limitations": [
                "Input appears to be a drug class, not a specific ingredient/product.",
                "Specify a concrete medication name before downstream drug-level safety retrieval.",
            ],
        }
        logger.log_tool_call("rxnorm_normalize_drug", tool_input, output, latency_ms, True, "")
        return output
    try:
        exact_payload = _get(
            f"{RXNAV_BASE}/rxcui.json",
            {"name": clean, "search": 2},
            cfg.request_timeout_s,
        )
        exact_ids = _extract_rxcui_list(exact_payload)
        if exact_ids:
            rxcui = exact_ids[0]
            name = _fetch_name_by_rxcui(rxcui, cfg.request_timeout_s) or clean
            output = {
                "provider": "rxnorm_rxnav",
                "input_text": clean,
                "normalized_name": name,
                "rxcui": rxcui,
                "match_type": "exact",
                "candidates": [{"normalized_name": name, "rxcui": rxcui}],
                "requires_confirmation": False,
                "limitations": [],
            }
            latency_ms = round((perf_counter() - start) * 1000, 2)
            logger.log_tool_call("rxnorm_normalize_drug", tool_input, output, latency_ms, True, "")
            return output

        normalized_payload = _get(
            f"{RXNAV_BASE}/rxcui.json",
            {"name": clean, "search": 1},
            cfg.request_timeout_s,
        )
        normalized_ids = _extract_rxcui_list(normalized_payload)
        if normalized_ids:
            rxcui = normalized_ids[0]
            name = _fetch_name_by_rxcui(rxcui, cfg.request_timeout_s) or clean
            output = {
                "provider": "rxnorm_rxnav",
                "input_text": clean,
                "normalized_name": name,
                "rxcui": rxcui,
                "match_type": "normalized",
                "candidates": [{"normalized_name": name, "rxcui": rxcui}],
                "requires_confirmation": False,
                "limitations": [],
            }
            latency_ms = round((perf_counter() - start) * 1000, 2)
            logger.log_tool_call("rxnorm_normalize_drug", tool_input, output, latency_ms, True, "")
            return output

        approx_payload = _get(
            f"{RXNAV_BASE}/approximateTerm.json",
            {"term": clean, "maxEntries": 5},
            cfg.request_timeout_s,
        )
        candidates = _extract_approx_candidates(approx_payload)
        if candidates:
            first = candidates[0]
            rxcui = first["rxcui"]
            name = _fetch_name_by_rxcui(rxcui, cfg.request_timeout_s) or clean
            output = {
                "provider": "rxnorm_rxnav",
                "input_text": clean,
                "normalized_name": name,
                "rxcui": rxcui,
                "match_type": "approximate",
                "candidates": [
                    {"rxcui": c["rxcui"], "score": c["score"], "rank": c["rank"]}
                    for c in candidates
                ],
                "requires_confirmation": True,
                "limitations": ["Approximate RxNorm match; user confirmation recommended."],
            }
            latency_ms = round((perf_counter() - start) * 1000, 2)
            logger.log_tool_call("rxnorm_normalize_drug", tool_input, output, latency_ms, True, "")
            return output

        output = {
            "provider": "rxnorm_rxnav",
            "input_text": clean,
            "normalized_name": "",
            "rxcui": "",
            "match_type": "unresolved",
            "candidates": [],
            "requires_confirmation": True,
            "limitations": ["No RxNorm match found for input text."],
        }
        latency_ms = round((perf_counter() - start) * 1000, 2)
        logger.log_tool_call("rxnorm_normalize_drug", tool_input, output, latency_ms, True, "")
        return output
    except Exception as exc:
        latency_ms = round((perf_counter() - start) * 1000, 2)
        logger.log_tool_call("rxnorm_normalize_drug", tool_input, {}, latency_ms, False, str(exc))
        raise RuntimeError(f"RxNorm RxNav normalization failed: {exc}") from exc

