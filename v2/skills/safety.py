from __future__ import annotations

from time import perf_counter
from typing import Any

from v2.config import V2Config
from v2.core.logger import V2RunLogger
from v2.tools import TOOLS


def drug_safety_skill(
    drug: str,
    condition_context: str,
    adverse_event_focus: list[str],
    logger: V2RunLogger,
    caller: str = "skill.drug_safety",
    top_k: int = 10,
    config: V2Config | None = None,
) -> dict[str, Any]:
    start = perf_counter()
    clean_drug = str(drug).strip()
    if not clean_drug:
        latency_ms = round((perf_counter() - start) * 1000, 2)
        output = {
            "status": "needs_clarification",
            "missing_fields": ["medication"],
            "drug": "",
            "condition_context": condition_context,
            "signals": [],
            "event_examples": [],
            "safety_summary": "Medication is required before drug-level safety signal retrieval.",
            "limitations": [
                "No openFDA call was made because medication input is missing.",
            ],
        }
        logger.log_workflow_event(
            "no_tool_call_reason",
            {
                "skill": "drug_safety_skill",
                "reason": "missing_medication",
                "missing_fields": ["medication"],
            },
        )
        logger.log_skill_call(
            "drug_safety_skill",
            {
                "drug": "",
                "condition_context": condition_context[:200],
                "focus_count": len(adverse_event_focus),
                "caller": caller,
                "status": "needs_clarification",
            },
            output,
            latency_ms,
            True,
            "",
        )
        return output
    signals: list[dict[str, Any]] = []
    merged_examples: list[dict[str, Any]] = []
    for focus in adverse_event_focus:
        focus_term = str(focus).strip()
        if not focus_term:
            continue
        try:
            tool_out = TOOLS["openfda_drug_event_search"](
                drug=clean_drug,
                reaction=focus_term,
                limit=top_k,
                logger=logger,
                caller=f"{caller}.openfda.{focus_term}",
                config=config,
            )
            top_reactions = (tool_out.get("top_reactions") or [])[:5]
            examples = (tool_out.get("event_examples") or [])[:3]
            serious_count = sum(1 for x in examples if str((x or {}).get("serious", "")).strip() in {"1", "2"})
            signals.append(
                {
                    "focus": focus_term,
                    "query": tool_out.get("query", ""),
                    "result_count": tool_out.get("result_count", 0),
                    "top_reactions": top_reactions,
                    "serious_examples_count": serious_count,
                }
            )
            merged_examples.extend(examples)
        except Exception as exc:
            signals.append(
                {
                    "focus": focus_term,
                    "query": "",
                    "result_count": 0,
                    "top_reactions": [],
                    "serious_examples_count": 0,
                    "error": str(exc),
                }
            )

    dedup_examples: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in merged_examples:
        if not isinstance(item, dict):
            continue
        rid = str(item.get("safetyreportid", "")).strip()
        if rid and rid in seen_ids:
            continue
        if rid:
            seen_ids.add(rid)
        dedup_examples.append(item)

    focus_bits = [f"{s.get('focus')}:count={s.get('result_count', 0)}" for s in signals]
    safety_summary = (
        f"Drug={clean_drug}; context={condition_context}; "
        f"focus_signals={' | '.join(focus_bits) if focus_bits else 'none'}."
    )
    limitations = [
        "FAERS cannot prove causality.",
        "Counts are reporting counts, not incidence rates.",
        "Spontaneous reports are affected by under-reporting, stimulated reporting, and confounding.",
        "This skill collects safety signals only and does not provide treatment recommendations.",
    ]
    if any(str((s or {}).get("error", "")).strip() for s in signals):
        limitations.append("One or more focus queries failed due to API/network conditions; partial signal set returned.")
    output = {
        "status": "success" if not any(str((s or {}).get("error", "")).strip() for s in signals) else "partial",
        "missing_fields": [],
        "drug": clean_drug,
        "condition_context": condition_context,
        "signals": signals,
        "event_examples": dedup_examples[:10],
        "safety_summary": safety_summary,
        "limitations": limitations,
    }
    latency_ms = round((perf_counter() - start) * 1000, 2)
    logger.log_skill_call(
        "drug_safety_skill",
        {
            "drug": drug,
            "condition_context": condition_context[:200],
            "focus_count": len(adverse_event_focus),
            "caller": caller,
        },
        output,
        latency_ms,
        True,
        "",
    )
    return output
