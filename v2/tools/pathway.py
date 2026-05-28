from __future__ import annotations

import re
from time import perf_counter
from typing import Any

import requests

from v2.config import V2Config, get_config
from v2.core.logger import V2RunLogger


OPENTARGETS_GRAPHQL_ENDPOINT = "https://api.platform.opentargets.org/api/v4/graphql"


def _opentargets_post(query: str, variables: dict[str, Any], timeout_s: int) -> dict[str, Any]:
    resp = requests.post(
        OPENTARGETS_GRAPHQL_ENDPOINT,
        json={"query": query, "variables": variables},
        headers={"Content-Type": "application/json"},
        timeout=timeout_s,
    )
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Open Targets response is not a JSON object")
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        msg = "; ".join([str((e or {}).get("message", e)) for e in errors[:3]])
        raise RuntimeError(f"Open Targets GraphQL errors: {msg}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Open Targets response missing data field")
    return data


def opentargets_search(
    disease_query: str,
    logger: V2RunLogger,
    caller: str,
    top_k: int = 10,
    config: V2Config | None = None,
) -> dict[str, Any]:
    cfg = config or get_config()
    start = perf_counter()
    k = max(1, min(30, int(top_k)))
    tool_input = {"disease_query": disease_query, "top_k": k, "caller": caller}
    try:
        search_query = """
query DiseaseSearch($queryString: String!, $page: Pagination!) {
  search(queryString: $queryString, entityNames: ["disease"], page: $page) {
    total
    hits {
      id
      name
      entity
      score
    }
  }
}
"""
        search_data = _opentargets_post(
            query=search_query,
            variables={"queryString": disease_query, "page": {"index": 0, "size": k}},
            timeout_s=cfg.request_timeout_s,
        )
        search_obj = search_data.get("search") or {}
        raw_hits = search_obj.get("hits") if isinstance(search_obj, dict) else []
        if not isinstance(raw_hits, list):
            raw_hits = []
        disease_candidates: list[dict[str, Any]] = []
        for hit in raw_hits:
            if not isinstance(hit, dict):
                continue
            disease_candidates.append(
                {
                    "disease_id": str(hit.get("id", "")).strip(),
                    "disease_name": str(hit.get("name", "")).strip(),
                    "entity": str(hit.get("entity", "")).strip(),
                    "score": hit.get("score"),
                }
            )

        disease_evidence_query = """
query DiseaseEvidence($efoId: String!, $page: Pagination!) {
  disease(efoId: $efoId) {
    id
    name
    associatedTargets(page: $page) {
      count
      rows {
        score
        target {
          id
          approvedSymbol
          approvedName
        }
        datasourceScores {
          id
          score
        }
      }
    }
    drugAndClinicalCandidates {
      count
      rows {
        maxClinicalStage
        drug {
          id
          name
        }
        clinicalReports {
          source
          clinicalStage
          trialOverallStatus
        }
      }
    }
  }
}
"""
        chosen_disease_id = ""
        chosen_disease_name = ""
        target_associations: list[dict[str, Any]] = []
        known_drugs: list[dict[str, Any]] = []

        for candidate in disease_candidates:
            efo_id = str(candidate.get("disease_id", "")).strip()
            if not efo_id:
                continue
            disease_data = _opentargets_post(
                query=disease_evidence_query,
                variables={"efoId": efo_id, "page": {"index": 0, "size": k}},
                timeout_s=cfg.request_timeout_s,
            )
            disease_obj = disease_data.get("disease")
            if not isinstance(disease_obj, dict):
                continue
            chosen_disease_id = str(disease_obj.get("id", "")).strip() or efo_id
            chosen_disease_name = str(disease_obj.get("name", "")).strip() or str(candidate.get("disease_name", "")).strip()

            associated_targets = disease_obj.get("associatedTargets") or {}
            rows = associated_targets.get("rows") if isinstance(associated_targets, dict) else []
            if isinstance(rows, list):
                for row in rows[:k]:
                    if not isinstance(row, dict):
                        continue
                    target = row.get("target") or {}
                    datasource_scores = row.get("datasourceScores") if isinstance(row.get("datasourceScores"), list) else []
                    datasources: list[dict[str, Any]] = []
                    for ds in datasource_scores:
                        if not isinstance(ds, dict):
                            continue
                        ds_id = str(ds.get("id", "")).strip()
                        if not ds_id:
                            continue
                        datasources.append({"id": ds_id, "score": ds.get("score")})
                    target_associations.append(
                        {
                            "target_id": str(target.get("id", "")).strip(),
                            "approved_symbol": str(target.get("approvedSymbol", "")).strip(),
                            "target_name": str(target.get("approvedName", "")).strip(),
                            "association_score": row.get("score"),
                            "datasources": datasources,
                        }
                    )

            drug_candidates = disease_obj.get("drugAndClinicalCandidates") or {}
            drug_rows = drug_candidates.get("rows") if isinstance(drug_candidates, dict) else []
            if isinstance(drug_rows, list):
                for row in drug_rows[:k]:
                    if not isinstance(row, dict):
                        continue
                    drug_obj = row.get("drug") or {}
                    reports = row.get("clinicalReports") if isinstance(row.get("clinicalReports"), list) else []
                    source_list: list[str] = []
                    status = ""
                    for rep in reports:
                        if not isinstance(rep, dict):
                            continue
                        src = str(rep.get("source", "")).strip()
                        if src:
                            source_list.append(src)
                        if not status:
                            status = str(rep.get("trialOverallStatus", "")).strip()
                    known_drugs.append(
                        {
                            "drug_name": str(drug_obj.get("name", "")).strip(),
                            "mechanism_of_action": "",
                            "target": "",
                            "phase": str(row.get("maxClinicalStage", "")).strip(),
                            "status": status,
                            "datasources": sorted(list(set(source_list))),
                        }
                    )
            break

        top_result_summaries = [
            f"{x.get('approved_symbol', '')} | score={x.get('association_score', '')} | {x.get('target_name', '')[:80]}"
            for x in target_associations[:3]
        ]
        limitations: list[str] = []
        if not disease_candidates:
            limitations.append("No disease candidates returned by Open Targets search endpoint.")
        if not target_associations:
            limitations.append("No target associations found for selected disease candidate.")
        if not known_drugs:
            limitations.append("No known drugs returned in current disease clinical candidates response.")
        limitations.append("Drug rows are disease-level clinical candidates; mechanism_of_action/target may require target/drug endpoint join.")

        output = {
            "provider": "opentargets",
            "source": "opentargets",
            "query": disease_query,
            "result_count": len(target_associations),
            "selected_disease_id": chosen_disease_id,
            "selected_disease_name": chosen_disease_name,
            "disease_candidates": disease_candidates[:k],
            "target_associations": target_associations[:k],
            "known_drugs": known_drugs[:k],
            "top_result_summaries": top_result_summaries,
            "api_url": OPENTARGETS_GRAPHQL_ENDPOINT,
            "limitations": limitations,
            "data": {
                "provider": "opentargets",
                "query": disease_query,
                "disease_candidates": disease_candidates[:k],
                "target_associations": target_associations[:k],
                "known_drugs": known_drugs[:k],
                "limitations": limitations,
            },
        }
        latency_ms = round((perf_counter() - start) * 1000, 2)
        logger.log_tool_call("opentargets_search", tool_input, output, latency_ms, True, "")
        return output
    except Exception as exc:
        latency_ms = round((perf_counter() - start) * 1000, 2)
        logger.log_tool_call("opentargets_search", tool_input, {}, latency_ms, False, str(exc))
        raise RuntimeError(f"Open Targets real call failed: {exc}") from exc


def reactome_search(
    query: str,
    logger: V2RunLogger,
    caller: str,
    top_k: int = 5,
    species: str = "Homo sapiens",
    result_types: str = "Pathway",
    config: V2Config | None = None,
) -> dict[str, Any]:
    cfg = config or get_config()
    start = perf_counter()
    tool_input = {
        "query": query,
        "caller": caller,
        "top_k": top_k,
        "species": species,
        "types": result_types,
    }
    try:
        url = "https://reactome.org/ContentService/search/query"
        params = {
            "query": query,
            "species": species,
            "types": result_types,
            "cluster": "true",
        }
        resp = requests.get(
            url,
            params=params,
            headers={"Accept": "application/json"},
            timeout=cfg.request_timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        raw_results = data.get("results") if isinstance(data, dict) else []
        if not isinstance(raw_results, list):
            raw_results = []

        flat_items: list[dict[str, Any]] = []
        for group in raw_results:
            if isinstance(group, dict) and isinstance(group.get("entries"), list):
                for item in group["entries"]:
                    if isinstance(item, dict):
                        flat_items.append(item)
            elif isinstance(group, dict):
                flat_items.append(group)

        results: list[dict[str, str]] = []
        for item in flat_items[:top_k]:
            if not isinstance(item, dict):
                continue
            species_value = item.get("species")
            if isinstance(species_value, list):
                species_text = ", ".join([str(x) for x in species_value if str(x).strip()])
            else:
                species_text = str(species_value or "").strip()
            results.append(
                {
                    "id": str(item.get("stId") or item.get("id") or item.get("dbId") or "").strip(),
                    "name": re.sub(r"<[^>]+>", "", str(item.get("name") or item.get("displayName") or "")).strip(),
                    "type": str(item.get("exactType") or item.get("type") or "").strip(),
                    "species": species_text,
                }
            )

        output = {
            "source": "reactome_content_service",
            "query": query,
            "result_count": len(results),
            "results": results[:top_k],
            "api_url": "https://reactome.org/ContentService/search/query",
        }
        latency_ms = round((perf_counter() - start) * 1000, 2)
        logger.log_tool_call("reactome_search", tool_input, output, latency_ms, True, "")
        return output
    except Exception as exc:
        latency_ms = round((perf_counter() - start) * 1000, 2)
        logger.log_tool_call("reactome_search", tool_input, {}, latency_ms, False, str(exc))
        raise RuntimeError(f"Reactome real call failed: {exc}") from exc
