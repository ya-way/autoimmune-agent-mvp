from __future__ import annotations

from time import perf_counter
from typing import Any, Callable

from v2.core.llm import LLMClient
from v2.core.logger import V2RunLogger
from v2.schemas import BenchmarkItem
from v2.tools import TOOLS


def evidence_retrieval(
    item: BenchmarkItem,
    logger: V2RunLogger,
    caller: str,
    enable_web_tool: bool,
) -> dict[str, Any]:
    start = perf_counter()
    warnings: list[str] = []
    tool_status: dict[str, str] = {}

    def _call_with_retry(tool_name: str, max_attempts: int = 3, **kwargs: Any) -> dict[str, Any] | None:
        last_error = ""
        for attempt in range(1, max_attempts + 1):
            try:
                result = TOOLS[tool_name](**kwargs)
                tool_status[tool_name] = "success"
                return result
            except Exception as exc:
                last_error = str(exc)
                warnings.append(f"{tool_name} attempt {attempt}/{max_attempts} failed: {last_error}")
        tool_status[tool_name] = "failed"
        return None

    db_result = _call_with_retry(
        "local_empty_database_search",
        query=item.phenotype_text,
        logger=logger,
        caller=caller,
    ) or {"status": "unavailable", "warnings": ["local database tool failed"]}

    primary_term = ""
    if item.phenotype_names:
        primary_term = item.phenotype_names[0]
    elif item.phenotype_ids:
        primary_term = item.phenotype_ids[0]
    else:
        primary_term = item.phenotype_text[:120]

    hpo_result = _call_with_retry(
        "hpo_search",
        query=item.phenotype_text,
        term=primary_term,
        max_results=5,
        logger=logger,
        caller=caller,
    )

    pubmed_result = _call_with_retry(
        "pubmed_search",
        query=item.phenotype_text[:300],
        retmax=5,
        logger=logger,
        caller=caller,
    )

    web_result: dict[str, Any] | None = None
    if enable_web_tool:
        web_result = _call_with_retry(
            "web_search",
            query=item.phenotype_text,
            logger=logger,
            caller=caller,
        )

    output = {
        "database": db_result,
        "hpo": hpo_result,
        "pubmed": pubmed_result,
        "web": web_result,
        "tool_status": tool_status,
        "warnings": warnings,
        "evidence_summary": _build_evidence_summary(db_result, hpo_result, pubmed_result, web_result, warnings),
    }
    latency_ms = round((perf_counter() - start) * 1000, 2)
    logger.log_skill_call(
        "evidence_retrieval",
        {"case_id": item.case_id, "enable_web_tool": enable_web_tool},
        output,
        latency_ms,
        True,
        "",
    )
    return output


def final_answer(
    item: BenchmarkItem,
    llm: LLMClient,
    logger: V2RunLogger,
    caller: str,
    evidence_text: str = "",
) -> str:
    start = perf_counter()
    prompt = (
        "Given phenotype text, provide top 5 likely rare diagnoses.\n"
        "Output one diagnosis per line as '1. ...' to '5. ...'.\n\n"
        f"Case ID: {item.case_id}\n"
        f"Phenotype text: {item.phenotype_text}\n"
        f"Phenotype IDs: {', '.join(item.phenotype_ids)}\n"
        f"Optional evidence: {evidence_text}\n"
    )
    answer = llm.complete(prompt=prompt, caller=caller)
    latency_ms = round((perf_counter() - start) * 1000, 2)
    logger.log_skill_call(
        "final_answer",
        {"case_id": item.case_id, "caller": caller},
        {"answer_preview": answer[:500]},
        latency_ms,
        True,
        "",
    )
    return answer


def deeprare_answering(
    item: BenchmarkItem,
    llm: LLMClient,
    logger: V2RunLogger,
    caller: str,
    evidence_text: str = "",
) -> str:
    start = perf_counter()
    answer = final_answer(
        item=item,
        llm=llm,
        logger=logger,
        caller=f"{caller}.final_answer",
        evidence_text=evidence_text,
    )
    latency_ms = round((perf_counter() - start) * 1000, 2)
    logger.log_skill_call(
        "deeprare_answering",
        {"case_id": item.case_id, "caller": caller},
        {"answer_preview": answer[:500]},
        latency_ms,
        True,
        "",
    )
    return answer


def _build_evidence_summary(
    db_result: dict[str, Any],
    hpo_result: dict[str, Any] | None,
    pubmed_result: dict[str, Any] | None,
    web_result: dict[str, Any] | None,
    warnings: list[str],
) -> str:
    parts = [f"Database status: {db_result.get('status', 'unknown')}"]
    if hpo_result:
        parts.append(f"HPO source: {hpo_result.get('source', 'unknown')} count={hpo_result.get('result_count', 0)}")
        for line in (hpo_result.get("top_result_summaries") or [])[:2]:
            parts.append(f"- {line}")
    else:
        parts.append("HPO source: unavailable")
    if pubmed_result:
        parts.append(
            f"PubMed source: {pubmed_result.get('source', 'unknown')} count={pubmed_result.get('result_count', 0)}"
        )
        for line in (pubmed_result.get("top_result_summaries") or [])[:2]:
            parts.append(f"- {line}")
    else:
        parts.append("PubMed source: unavailable")
    if web_result:
        parts.append(f"Web source: {web_result.get('source', 'unknown')}")
        results = web_result.get("results", [])
        if results:
            parts.extend([f"- {line}" for line in results[:3]])
    else:
        parts.append("Web source: unavailable")
    if warnings:
        parts.append("Warnings:")
        parts.extend([f"- {w}" for w in warnings[:4]])
    return "\n".join(parts)


def build_skill_registry(*entries: tuple[str, Callable[..., Any]]) -> dict[str, Callable[..., Any]]:
    return {name: fn for name, fn in entries}
