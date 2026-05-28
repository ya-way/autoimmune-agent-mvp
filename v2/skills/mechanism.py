from __future__ import annotations

from time import perf_counter
from typing import Any

from v2.config import V2Config
from v2.core.logger import V2RunLogger
from v2.tools import TOOLS


def mechanism_evidence_skill(
    disease: str,
    mechanism_focus: str,
    logger: V2RunLogger,
    caller: str = "skill.mechanism_evidence",
    top_k: int = 5,
    config: V2Config | None = None,
) -> dict[str, Any]:
    start = perf_counter()
    ot_output = TOOLS["opentargets_search"](
        disease_query=disease,
        logger=logger,
        caller=f"{caller}.opentargets",
        top_k=top_k,
        config=config,
    )
    reactome_query = disease if not mechanism_focus.strip() else f"{disease} {mechanism_focus}"
    pathway_output = TOOLS["reactome_search"](
        query=reactome_query,
        logger=logger,
        caller=f"{caller}.reactome",
        top_k=top_k,
        config=config,
    )

    target_evidence: list[dict[str, Any]] = []
    for item in (ot_output.get("target_associations") or [])[:top_k]:
        if not isinstance(item, dict):
            continue
        target_evidence.append(
            {
                "target_id": str(item.get("target_id", "")).strip(),
                "approved_symbol": str(item.get("approved_symbol", "")).strip(),
                "target_name": str(item.get("target_name", "")).strip(),
                "association_score": item.get("association_score"),
                "datasources": item.get("datasources", []),
            }
        )

    pathway_evidence: list[dict[str, Any]] = []
    for item in (pathway_output.get("results") or [])[:top_k]:
        if not isinstance(item, dict):
            continue
        pathway_evidence.append(
            {
                "id": str(item.get("id", "")).strip(),
                "name": str(item.get("name", "")).strip(),
                "type": str(item.get("type", "")).strip(),
                "species": str(item.get("species", "")).strip(),
            }
        )

    drug_hints: list[dict[str, Any]] = []
    for item in (ot_output.get("known_drugs") or [])[:top_k]:
        if not isinstance(item, dict):
            continue
        drug_hints.append(
            {
                "drug_name": str(item.get("drug_name", "")).strip(),
                "mechanism_of_action": str(item.get("mechanism_of_action", "")).strip(),
                "target": str(item.get("target", "")).strip(),
                "phase": str(item.get("phase", "")).strip(),
                "status": str(item.get("status", "")).strip(),
                "datasources": item.get("datasources", []),
            }
        )

    top_targets = ", ".join([x["approved_symbol"] for x in target_evidence[:3] if x.get("approved_symbol")]) or "none"
    top_pathways = ", ".join([x["name"] for x in pathway_evidence[:3] if x.get("name")]) or "none"
    top_drugs = ", ".join([x["drug_name"] for x in drug_hints[:3] if x.get("drug_name")]) or "none"
    mechanism_summary = (
        f"Disease={disease}; top_targets={top_targets}; "
        f"top_pathways={top_pathways}; drug_hints={top_drugs}."
    )

    limitations = list(ot_output.get("limitations", [])) if isinstance(ot_output, dict) else []
    limitations.append("Mechanism evidence skill is evidence-only and does not output diagnosis decisions.")
    output = {
        "disease": disease,
        "mechanism_focus": mechanism_focus,
        "target_evidence": target_evidence,
        "pathway_evidence": pathway_evidence,
        "drug_hints": drug_hints,
        "mechanism_summary": mechanism_summary,
        "limitations": limitations,
    }
    latency_ms = round((perf_counter() - start) * 1000, 2)
    logger.log_skill_call(
        "mechanism_evidence_skill",
        {"disease": disease, "mechanism_focus": mechanism_focus, "top_k": top_k, "caller": caller},
        output,
        latency_ms,
        True,
        "",
    )
    return output
