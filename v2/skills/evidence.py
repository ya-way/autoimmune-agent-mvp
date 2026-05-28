from __future__ import annotations

from time import perf_counter
from typing import Any

from v2.config import V2Config
from v2.core.logger import V2RunLogger
from v2.tools import TOOLS


def literature_evidence_skill(
    query: str,
    logger: V2RunLogger,
    caller: str = "skill.literature_evidence",
    top_k: int = 5,
    config: V2Config | None = None,
) -> dict[str, Any]:
    start = perf_counter()
    tool_output = TOOLS["pubmed_search"](query=query, logger=logger, caller=caller, top_k=top_k, config=config)
    snippets: list[str] = []
    for item in (tool_output.get("results") or [])[:top_k]:
        if not isinstance(item, dict):
            continue
        pmid = str(item.get("pmid", "")).strip()
        title = str(item.get("title", "")).strip()
        year = str(item.get("year", "") or item.get("year_or_date", "")).strip()
        snippets.append(f"[PMID:{pmid}] {title} ({year})")
    output = {
        "source": tool_output.get("source", "ncbi_eutils_pubmed"),
        "query": query,
        "result_count": len(tool_output.get("results") or []),
        "snippets": snippets,
        "results": tool_output.get("results", []),
    }
    latency_ms = round((perf_counter() - start) * 1000, 2)
    logger.log_skill_call(
        "literature_evidence_skill",
        {"query": query, "top_k": top_k, "caller": caller},
        output,
        latency_ms,
        True,
        "",
    )
    return output


def clinical_evidence_skill(
    clinical_question: str,
    phenotypes: list[str],
    suspected_diagnosis: str,
    logger: V2RunLogger,
    caller: str = "skill.clinical_evidence",
    top_k: int = 5,
    config: V2Config | None = None,
) -> dict[str, Any]:
    start = perf_counter()
    normalized_phenotypes: list[dict[str, Any]] = []
    for p in phenotypes:
        term = str(p).strip()
        if not term:
            continue
        hpo_out = TOOLS["hpo_search"](
            query=term,
            term=term,
            logger=logger,
            caller=f"{caller}.hpo",
            max_results=3,
            config=config,
        )
        hpo_items = hpo_out.get("results", []) if isinstance(hpo_out, dict) else []
        top = hpo_items[0] if isinstance(hpo_items, list) and hpo_items else {}
        normalized_phenotypes.append(
            {
                "input_term": term,
                "hpo_id": str(top.get("hpo_id", "")).strip() if isinstance(top, dict) else "",
                "name": str(top.get("name", "")).strip() if isinstance(top, dict) else "",
                "synonym": str(top.get("synonym", "")).strip() if isinstance(top, dict) else "",
            }
        )

    phenotype_terms = [x["input_term"] for x in normalized_phenotypes if x.get("input_term")]
    key_terms = ", ".join(phenotype_terms[:3]) if phenotype_terms else ""
    pubmed_query = f"{suspected_diagnosis} {key_terms} diagnosis differential".strip()
    pubmed_out = TOOLS["pubmed_search"](
        query=pubmed_query,
        retmax=top_k,
        logger=logger,
        caller=f"{caller}.pubmed",
        config=config,
    )
    evidence_items = pubmed_out.get("results", []) if isinstance(pubmed_out, dict) else []
    evidence_lines: list[str] = []
    for item in evidence_items[:3]:
        if not isinstance(item, dict):
            continue
        pmid = str(item.get("pmid", "")).strip()
        title = str(item.get("title", "")).strip()
        year = str(item.get("year", "")).strip()
        evidence_lines.append(f"[PMID:{pmid}] {title} ({year})")
    evidence_summary = " | ".join(evidence_lines) if evidence_lines else "No PubMed evidence returned."
    output = {
        "clinical_question": clinical_question,
        "suspected_diagnosis": suspected_diagnosis,
        "normalized_phenotypes": normalized_phenotypes,
        "pubmed_query": pubmed_query,
        "evidence_items": evidence_items[:top_k],
        "evidence_summary": evidence_summary,
        "limitations": [
            "This skill collects evidence only and does not produce a diagnosis decision.",
            "PubMed retrieval quality depends on query wording and indexing latency.",
            "HPO normalization uses lexical search and may miss context-dependent terms.",
        ],
    }
    latency_ms = round((perf_counter() - start) * 1000, 2)
    logger.log_skill_call(
        "clinical_evidence_skill",
        {
            "caller": caller,
            "clinical_question": clinical_question[:240],
            "phenotype_count": len(phenotypes),
            "suspected_diagnosis": suspected_diagnosis,
        },
        output,
        latency_ms,
        True,
        "",
    )
    return output
