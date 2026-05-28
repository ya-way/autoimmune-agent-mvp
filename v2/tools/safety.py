from __future__ import annotations

from time import perf_counter
from typing import Any

import requests

from v2.config import V2Config, get_config
from v2.core.logger import V2RunLogger

OPENFDA_DRUG_EVENT_ENDPOINT = "https://api.fda.gov/drug/event.json"


def _build_drug_clause(drug: str) -> str:
    term = drug.strip()
    return f'patient.drug.medicinalproduct:"{term}"'


def _build_openfda_search(drug: str, reaction: str) -> str:
    clauses = [_build_drug_clause(drug)]
    if reaction.strip():
        clauses.append(f'patient.reaction.reactionmeddrapt.exact:"{reaction.strip()}"')
    return "+AND+".join(clauses)


def _extract_event_example(item: dict[str, Any]) -> dict[str, Any]:
    patient = item.get("patient") if isinstance(item.get("patient"), dict) else {}
    reaction_rows = patient.get("reaction") if isinstance(patient.get("reaction"), list) else []
    drug_rows = patient.get("drug") if isinstance(patient.get("drug"), list) else []
    reactions: list[str] = []
    for row in reaction_rows:
        if not isinstance(row, dict):
            continue
        val = str(row.get("reactionmeddrapt", "")).strip()
        if val:
            reactions.append(val)
    drugs: list[str] = []
    for row in drug_rows:
        if not isinstance(row, dict):
            continue
        val = str(row.get("medicinalproduct", "")).strip()
        if val:
            drugs.append(val)
    outcomes: list[str] = []
    for key in ["seriousnessdeath", "seriousnesslifethreatening", "seriousnesshospitalization", "seriousnessdisabling"]:
        val = str(item.get(key, "")).strip()
        if val:
            outcomes.append(f"{key}:{val}")
    return {
        "safetyreportid": str(item.get("safetyreportid", "")).strip(),
        "serious": str(item.get("serious", "")).strip(),
        "reactions": sorted(list(set(reactions)))[:10],
        "drugs": sorted(list(set(drugs)))[:10],
        "outcomes": sorted(list(set(outcomes)))[:10],
        "patient_age": str(patient.get("patientonsetage", "")).strip() if isinstance(patient, dict) else "",
        "patient_sex": str(patient.get("patientsex", "")).strip() if isinstance(patient, dict) else "",
        "reporter_country": str((item.get("primarysource") or {}).get("reportercountry", "")).strip()
        if isinstance(item.get("primarysource"), dict)
        else "",
    }


def openfda_drug_event_search(
    drug: str,
    reaction: str,
    logger: V2RunLogger,
    caller: str,
    limit: int = 10,
    config: V2Config | None = None,
) -> dict[str, Any]:
    cfg = config or get_config()
    start = perf_counter()
    n = max(1, min(50, int(limit)))
    search_expr = _build_openfda_search(drug=drug.strip(), reaction=reaction.strip())
    tool_input = {
        "drug": drug,
        "reaction": reaction,
        "limit": n,
        "caller": caller,
    }
    try:
        event_params: dict[str, Any] = {
            "search": search_expr,
            "limit": n,
        }
        if cfg.openfda_api_key:
            event_params["api_key"] = cfg.openfda_api_key
        used_search_expr = search_expr
        event_resp = requests.get(OPENFDA_DRUG_EVENT_ENDPOINT, params=event_params, timeout=cfg.request_timeout_s)
        if event_resp.status_code == 404 and reaction.strip():
            used_search_expr = _build_openfda_search(drug=drug.strip(), reaction="")
            event_params["search"] = used_search_expr
            event_resp = requests.get(OPENFDA_DRUG_EVENT_ENDPOINT, params=event_params, timeout=cfg.request_timeout_s)
        event_resp.raise_for_status()
        event_payload = event_resp.json()
        event_results = event_payload.get("results") if isinstance(event_payload, dict) else []
        if not isinstance(event_results, list):
            event_results = []
        event_examples = [_extract_event_example(item) for item in event_results[:n] if isinstance(item, dict)]

        count_params: dict[str, Any] = {
            "search": _build_drug_clause(drug.strip()),
            "count": "patient.reaction.reactionmeddrapt.exact",
            "limit": n,
        }
        if cfg.openfda_api_key:
            count_params["api_key"] = cfg.openfda_api_key
        count_resp = requests.get(
            OPENFDA_DRUG_EVENT_ENDPOINT,
            params=count_params,
            timeout=cfg.request_timeout_s,
        )
        count_resp.raise_for_status()
        count_payload = count_resp.json()
        raw_counts = count_payload.get("results") if isinstance(count_payload, dict) else []
        if not isinstance(raw_counts, list):
            raw_counts = []
        top_reactions: list[dict[str, Any]] = []
        for row in raw_counts[:n]:
            if not isinstance(row, dict):
                continue
            top_reactions.append(
                {
                    "reaction": str(row.get("term", "")).strip(),
                    "count": int(row.get("count", 0)) if str(row.get("count", "")).strip() else 0,
                }
            )

        meta_total = ((event_payload.get("meta") or {}).get("results") or {}).get("total") if isinstance(event_payload, dict) else None
        result_count = len(event_examples)
        top_result_summaries = [f"{x.get('reaction', '')}: {x.get('count', 0)}" for x in top_reactions[:5]]
        limitations = [
            "FAERS is spontaneous reporting data and cannot establish causality or incidence.",
            "A single report may include multiple drugs and multiple reactions; drug-reaction linkage is not one-to-one.",
            "Reporting counts are signal indicators and are influenced by reporting bias and under-reporting.",
        ]

        output = {
            "provider": "openfda_drug_event",
            "source": "openfda_drug_event",
            "drug": drug,
            "reaction": reaction,
            "query": used_search_expr,
            "result_count": result_count,
            "reported_total": meta_total,
            "top_reactions": top_reactions,
            "event_examples": event_examples,
            "top_result_summaries": top_result_summaries,
            "api_url": OPENFDA_DRUG_EVENT_ENDPOINT,
            "limitations": limitations,
            "data": {
                "provider": "openfda_drug_event",
                "drug": drug,
                "reaction": reaction,
                "query": used_search_expr,
                "result_count": result_count,
                "top_reactions": top_reactions,
                "event_examples": event_examples,
                "limitations": limitations,
            },
        }
        latency_ms = round((perf_counter() - start) * 1000, 2)
        logger.log_tool_call("openfda_drug_event_search", tool_input, output, latency_ms, True, "")
        return output
    except Exception as exc:
        latency_ms = round((perf_counter() - start) * 1000, 2)
        logger.log_tool_call("openfda_drug_event_search", tool_input, {}, latency_ms, False, str(exc))
        raise RuntimeError(f"openFDA drug event real call failed: {exc}") from exc
