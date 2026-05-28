from __future__ import annotations

import re
from typing import Any

from v2.schemas import IntentResult


def _extract_phenotypes(text: str) -> list[str]:
    phenotype_terms = [
        "malar rash",
        "oral ulcers",
        "proteinuria",
        "fever",
        "photosensitive rash",
        "hematuria",
        "arthritis",
    ]
    lower = text.lower()
    out: list[str] = []
    for p in phenotype_terms:
        if p in lower:
            out.append(p)
    return out


def _extract_diagnosis(text: str) -> str | None:
    lower = text.lower()
    if "systemic lupus erythematosus" in lower or "suspected sle" in lower or re.search(r"\bsle\b", lower):
        return "systemic lupus erythematosus"
    if "rheumatoid arthritis" in lower:
        return "rheumatoid arthritis"
    return None


def _extract_safety_focus(text: str) -> list[str]:
    lower = text.lower()
    candidates = ["infection", "sepsis", "hyperglycemia", "osteoporosis", "pneumonitis", "toxicity"]
    out: list[str] = []
    for c in candidates:
        if c in lower:
            out.append(c)
    return out


def parse_intent(raw_input: str, metadata: dict | None = None) -> IntentResult:
    text = raw_input.strip()
    lower = text.lower()
    metadata = metadata or {}

    diagnosis = _extract_diagnosis(text)
    phenotypes = _extract_phenotypes(text)
    safety_focus = _extract_safety_focus(text)

    benchmark_keywords = ["benchmark", "deeprare", "rarebench", "official eval"]
    clinical_keywords = ["evidence", "literature", "pubmed", "clinical evidence", "differential diagnosis"]
    mechanism_keywords = ["mechanism", "target", "pathway", "drug target", "open targets", "reactome"]
    safety_keywords = ["adverse event", "side effect", "safety", "faers", "infection risk", "toxicity"]
    review_keywords = [
        "case review",
        "autoimmune",
        "immunosuppression",
        "suspected sle",
        "safety risk",
    ]

    extracted_fields: dict[str, Any] = {
        "suspected_diagnosis": diagnosis,
        "candidate_drug": None,
        "phenotypes": phenotypes,
        "safety_focus": safety_focus,
    }

    if any(k in lower for k in benchmark_keywords):
        return IntentResult(
            intent="deeprare_benchmark",
            confidence=0.98,
            routed_to="benchmark_cli_guidance",
            extracted_fields=extracted_fields,
            missing_fields=[],
            reason_summary="包含 benchmark/DeepRare/RareBench 关键词，路由为 benchmark 指引。",
        )

    if (
        any(k in lower for k in review_keywords)
        or (diagnosis == "systemic lupus erythematosus" and len(phenotypes) >= 2 and bool(safety_focus))
    ):
        missing = []
        if diagnosis is None:
            missing.append("suspected_diagnosis")
        missing.append("candidate_drug")
        if not phenotypes:
            missing.append("phenotypes")
        return IntentResult(
            intent="autoimmune_case_review",
            confidence=0.9,
            routed_to="autoimmune_case_review",
            extracted_fields=extracted_fields,
            missing_fields=missing,
            reason_summary="命中 autoimmune/SLE/安全风险等组合特征，路由到病例证据收敛 workflow。",
        )

    if any(k in lower for k in mechanism_keywords):
        missing = [] if diagnosis else ["suspected_diagnosis"]
        return IntentResult(
            intent="mechanism_evidence",
            confidence=0.85,
            routed_to="mechanism_evidence_skill",
            extracted_fields=extracted_fields,
            missing_fields=missing,
            reason_summary="命中 mechanism/target/pathway/Open Targets/Reactome 关键词。",
        )

    if any(k in lower for k in safety_keywords):
        missing = []
        missing.append("candidate_drug")
        return IntentResult(
            intent="drug_safety",
            confidence=0.85,
            routed_to="drug_safety_skill",
            extracted_fields=extracted_fields,
            missing_fields=missing,
            reason_summary="命中 adverse event/side effect/safety/FAERS 等安全关键词。",
        )

    if any(k in lower for k in clinical_keywords):
        missing = [] if diagnosis else ["suspected_diagnosis"]
        return IntentResult(
            intent="clinical_evidence",
            confidence=0.82,
            routed_to="clinical_evidence_skill",
            extracted_fields=extracted_fields,
            missing_fields=missing,
            reason_summary="命中 evidence/literature/PubMed/clinical evidence 关键词。",
        )

    return IntentResult(
        intent="unknown",
        confidence=0.2,
        routed_to="none",
        extracted_fields=extracted_fields,
        missing_fields=["intent_clarification"],
        reason_summary="未命中明确意图关键词，需用户补充需求。",
    )
