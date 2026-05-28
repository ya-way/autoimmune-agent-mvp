from __future__ import annotations

from time import perf_counter, sleep
from typing import Any, Callable

from v2.config import V2Config
from v2.core.logger import V2RunLogger
from v2.skills.evidence import clinical_evidence_skill
from v2.skills.mechanism import mechanism_evidence_skill
from v2.skills.safety import drug_safety_skill


def _call_with_retry(
    *,
    component_name: str,
    fn: Callable[[], dict[str, Any]],
    logger: V2RunLogger,
    max_retries: int = 2,
    retry_delay_seconds: int = 10,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    attempts: list[dict[str, Any]] = []
    total_attempts = max(1, int(max_retries))
    last_error = ""
    for idx in range(1, total_attempts + 1):
        start = perf_counter()
        try:
            result = fn()
            latency_ms = round((perf_counter() - start) * 1000, 2)
            attempt = {
                "component": component_name,
                "attempt_index": idx,
                "success": True,
                "latency_ms": latency_ms,
                "error_message": "",
            }
            attempts.append(attempt)
            logger.log_workflow_event("retry_attempt", attempt)
            return result, attempts, ""
        except Exception as exc:
            latency_ms = round((perf_counter() - start) * 1000, 2)
            last_error = str(exc)
            attempt = {
                "component": component_name,
                "attempt_index": idx,
                "success": False,
                "latency_ms": latency_ms,
                "error_message": last_error,
            }
            attempts.append(attempt)
            logger.log_workflow_event("retry_attempt", attempt)
            if idx < total_attempts:
                sleep(max(0, int(retry_delay_seconds)))
    return {}, attempts, last_error


def autoimmune_case_review(
    case_text: str,
    suspected_diagnosis: str | None,
    candidate_drug: str | None,
    safety_focus: list[str],
    phenotypes: list[str],
    logger: V2RunLogger,
    caller: str = "workflow.autoimmune_case_review",
    config: V2Config | None = None,
    max_retries: int = 2,
    retry_delay_seconds: int = 10,
) -> dict[str, Any]:
    start = perf_counter()
    missing_fields: list[str] = []
    if not str(case_text).strip():
        missing_fields.append("case_text")
    if not str(suspected_diagnosis or "").strip():
        missing_fields.append("suspected_diagnosis")
    if not str(candidate_drug or "").strip():
        missing_fields.append("candidate_drug")
    if not phenotypes:
        missing_fields.append("phenotypes")
    if not safety_focus:
        missing_fields.append("safety_focus")
    logger.log_workflow_event(
        "workflow_input",
        {
            "workflow": "autoimmune_case_review",
            "case_text_preview": case_text[:400],
            "suspected_diagnosis": suspected_diagnosis,
            "candidate_drug": candidate_drug,
            "phenotypes": phenotypes,
            "safety_focus": safety_focus,
            "max_retries": max_retries,
            "retry_delay_seconds": retry_delay_seconds,
        },
    )
    if missing_fields:
        latency_ms = round((perf_counter() - start) * 1000, 2)
        output = {
            "status": "needs_clarification",
            "missing_fields": missing_fields,
            "case_summary": case_text[:500],
            "clinical_evidence": {},
            "mechanism_evidence": {},
            "drug_safety_evidence": {},
            "failed_components": [],
            "retry_attempts": [],
            "evidence_convergence_summary": "Input incomplete: clarification required before workflow execution.",
            "safety_gate": [],
            "limitations": [
                "Workflow execution skipped due to incomplete structured input.",
            ],
        }
        logger.log_workflow_event(
            "no_tool_call_reason",
            {
                "workflow": "autoimmune_case_review",
                "reason": "incomplete_input",
                "missing_fields": missing_fields,
            },
        )
        logger.log_skill_call(
            "autoimmune_case_review",
            {
                "caller": caller,
                "status": "needs_clarification",
                "missing_fields": missing_fields,
            },
            output,
            latency_ms,
            True,
            "",
        )
        return output
    failed_components: list[dict[str, str]] = []
    retry_attempts: list[dict[str, Any]] = []

    clinical_output, clinical_attempts, clinical_error = _call_with_retry(
        component_name="clinical_evidence_skill",
        fn=lambda: clinical_evidence_skill(
            clinical_question=case_text,
            phenotypes=phenotypes,
            suspected_diagnosis=str(suspected_diagnosis or ""),
            logger=logger,
            caller=f"{caller}.clinical",
            top_k=5,
            config=config,
        ),
        logger=logger,
        max_retries=max_retries,
        retry_delay_seconds=retry_delay_seconds,
    )
    retry_attempts.extend(clinical_attempts)
    if clinical_error:
        failed_components.append({"component": "clinical_evidence_skill", "error": clinical_error})

    mechanism_output, mechanism_attempts, mechanism_error = _call_with_retry(
        component_name="mechanism_evidence_skill",
        fn=lambda: mechanism_evidence_skill(
            disease=str(suspected_diagnosis or ""),
            mechanism_focus="immune pathway, target, drug evidence",
            logger=logger,
            caller=f"{caller}.mechanism",
            top_k=5,
            config=config,
        ),
        logger=logger,
        max_retries=max_retries,
        retry_delay_seconds=retry_delay_seconds,
    )
    retry_attempts.extend(mechanism_attempts)
    if mechanism_error:
        failed_components.append({"component": "mechanism_evidence_skill", "error": mechanism_error})

    safety_output, safety_attempts, safety_error = _call_with_retry(
        component_name="drug_safety_skill",
        fn=lambda: drug_safety_skill(
            drug=str(candidate_drug or ""),
            condition_context=f"{str(suspected_diagnosis or '')} with infection risk",
            adverse_event_focus=safety_focus,
            logger=logger,
            caller=f"{caller}.safety",
            top_k=10,
            config=config,
        ),
        logger=logger,
        max_retries=max_retries,
        retry_delay_seconds=retry_delay_seconds,
    )
    retry_attempts.extend(safety_attempts)
    if safety_error:
        failed_components.append({"component": "drug_safety_skill", "error": safety_error})

    clinical_summary = (
        clinical_output.get("evidence_summary", "clinical evidence unavailable")
        if isinstance(clinical_output, dict)
        else "clinical evidence unavailable"
    )
    mechanism_summary = (
        mechanism_output.get("mechanism_summary", "mechanism evidence unavailable")
        if isinstance(mechanism_output, dict)
        else "mechanism evidence unavailable"
    )
    safety_summary = (
        safety_output.get("safety_summary", "drug safety evidence unavailable")
        if isinstance(safety_output, dict)
        else "drug safety evidence unavailable"
    )

    evidence_convergence_summary = (
        f"Clinical: {clinical_summary} || Mechanism: {mechanism_summary} || Safety: {safety_summary}"
    )
    if failed_components:
        evidence_convergence_summary += " || Evidence is partial due to failed components."

    safety_gate = [
        "Exclude infection before high-dose immunosuppression.",
        "Interpret FAERS as reporting signal, not causality.",
        "Review comorbidity and baseline risk factors before escalating immunosuppression.",
        "If critical evidence components fail, mark review as incomplete and re-run.",
    ]
    limitations = [
        "This workflow is an evidence convergence prototype, not a diagnosis system.",
        "This workflow does not provide treatment recommendations.",
        "External API instability can lead to partial evidence.",
        "FAERS-based safety signals do not establish causality or incidence.",
    ]

    output = {
        "status": "success" if not failed_components else "partial",
        "missing_fields": [],
        "case_summary": case_text[:500],
        "clinical_evidence": clinical_output if isinstance(clinical_output, dict) else {},
        "mechanism_evidence": mechanism_output if isinstance(mechanism_output, dict) else {},
        "drug_safety_evidence": safety_output if isinstance(safety_output, dict) else {},
        "failed_components": failed_components,
        "retry_attempts": retry_attempts,
        "evidence_convergence_summary": evidence_convergence_summary,
        "safety_gate": safety_gate,
        "limitations": limitations,
    }
    latency_ms = round((perf_counter() - start) * 1000, 2)
    logger.log_workflow_event(
        "workflow_output",
        {
            "workflow": "autoimmune_case_review",
            "success": len(failed_components) == 0,
            "latency_ms": latency_ms,
            "failed_components": failed_components,
            "summary_preview": evidence_convergence_summary[:500],
        },
    )
    logger.log_skill_call(
        "autoimmune_case_review",
        {
            "caller": caller,
            "suspected_diagnosis": suspected_diagnosis,
            "candidate_drug": candidate_drug,
            "safety_focus": safety_focus,
        },
        output,
        latency_ms,
        True,
        "",
    )
    return output
