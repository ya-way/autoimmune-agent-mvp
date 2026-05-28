from __future__ import annotations

from typing import Any

from v2.config import V2Config
from v2.core.action_schema import ActionSpec
from v2.core.logger import V2RunLogger
from v2.skills import SKILLS
from v2.tools import TOOLS


def _tool_hpo(*, args: dict[str, Any], state: dict[str, Any], logger: V2RunLogger, caller: str, config: V2Config) -> dict[str, Any]:
    term = str(args.get("term", "")).strip() or str(args.get("query", "")).strip()
    return TOOLS["hpo_search"](
        query=term,
        term=term,
        logger=logger,
        caller=caller,
        top_k=int(args.get("top_k", 5) or 5),
        config=config,
    )


def _tool_pubmed(*, args: dict[str, Any], state: dict[str, Any], logger: V2RunLogger, caller: str, config: V2Config) -> dict[str, Any]:
    return TOOLS["pubmed_search"](
        query=str(args.get("query", "")).strip(),
        retmax=int(args.get("top_k", 5) or 5),
        logger=logger,
        caller=caller,
        config=config,
    )


def _tool_rxnorm(*, args: dict[str, Any], state: dict[str, Any], logger: V2RunLogger, caller: str, config: V2Config) -> dict[str, Any]:
    return TOOLS["rxnorm_normalize_drug"](
        drug_text=str(args.get("drug_text", "")).strip(),
        logger=logger,
        caller=caller,
        config=config,
    )


def _tool_openfda(*, args: dict[str, Any], state: dict[str, Any], logger: V2RunLogger, caller: str, config: V2Config) -> dict[str, Any]:
    return TOOLS["openfda_drug_event_search"](
        drug=str(args.get("drug", "")).strip(),
        reaction=str(args.get("reaction", "")).strip(),
        limit=int(args.get("limit", 10) or 10),
        logger=logger,
        caller=caller,
        config=config,
    )


def _tool_opentargets(*, args: dict[str, Any], state: dict[str, Any], logger: V2RunLogger, caller: str, config: V2Config) -> dict[str, Any]:
    return TOOLS["opentargets_search"](
        disease_query=str(args.get("disease_query", "")).strip(),
        top_k=int(args.get("top_k", 5) or 5),
        logger=logger,
        caller=caller,
        config=config,
    )


def _tool_reactome(*, args: dict[str, Any], state: dict[str, Any], logger: V2RunLogger, caller: str, config: V2Config) -> dict[str, Any]:
    return TOOLS["reactome_search"](
        query=str(args.get("query", "")).strip(),
        top_k=int(args.get("top_k", 5) or 5),
        logger=logger,
        caller=caller,
        config=config,
    )


def _tool_web(*, args: dict[str, Any], state: dict[str, Any], logger: V2RunLogger, caller: str, config: V2Config) -> dict[str, Any]:
    return TOOLS["web_search"](
        query=str(args.get("query", "")).strip(),
        logger=logger,
        caller=caller,
        config=config,
    )


def _skill_clinical(*, args: dict[str, Any], state: dict[str, Any], logger: V2RunLogger, caller: str, config: V2Config) -> dict[str, Any]:
    return SKILLS["clinical_evidence_skill"](
        clinical_question=str(args.get("clinical_question", "")).strip(),
        phenotypes=list(args.get("phenotypes", []) or []),
        suspected_diagnosis=str(args.get("suspected_diagnosis", "")).strip(),
        logger=logger,
        caller=caller,
        top_k=int(args.get("top_k", 5) or 5),
        config=config,
    )


def _skill_mechanism(*, args: dict[str, Any], state: dict[str, Any], logger: V2RunLogger, caller: str, config: V2Config) -> dict[str, Any]:
    return SKILLS["mechanism_evidence_skill"](
        disease=str(args.get("disease", "")).strip(),
        mechanism_focus=str(args.get("mechanism_focus", "immune pathway, target, drug evidence")).strip(),
        logger=logger,
        caller=caller,
        top_k=int(args.get("top_k", 5) or 5),
        config=config,
    )


def _skill_safety(*, args: dict[str, Any], state: dict[str, Any], logger: V2RunLogger, caller: str, config: V2Config) -> dict[str, Any]:
    return SKILLS["drug_safety_skill"](
        drug=str(args.get("drug", "")).strip(),
        condition_context=str(args.get("condition_context", "")).strip(),
        adverse_event_focus=list(args.get("adverse_event_focus", []) or []),
        logger=logger,
        caller=caller,
        top_k=int(args.get("top_k", 10) or 10),
        config=config,
    )


def _skill_norm(*, args: dict[str, Any], state: dict[str, Any], logger: V2RunLogger, caller: str, config: V2Config) -> dict[str, Any]:
    return SKILLS["medication_normalization_skill"](
        medications=list(args.get("medications", []) or []),
        logger=logger,
        caller=caller,
        config=config,
    )


def _workflow_case_review(*, args: dict[str, Any], state: dict[str, Any], logger: V2RunLogger, caller: str, config: V2Config) -> dict[str, Any]:
    return SKILLS["autoimmune_case_review"](
        case_text=str(args.get("case_text", "")).strip(),
        suspected_diagnosis=str(args.get("suspected_diagnosis", "")).strip(),
        candidate_drug=str(args.get("candidate_drug", "")).strip(),
        safety_focus=list(args.get("safety_focus", []) or []),
        phenotypes=list(args.get("phenotypes", []) or []),
        logger=logger,
        caller=caller,
        config=config,
        max_retries=int(args.get("max_retries", 2) or 2),
        retry_delay_seconds=int(args.get("retry_delay_seconds", 10) or 10),
    )


def _final_action(*, args: dict[str, Any], state: dict[str, Any], logger: V2RunLogger, caller: str, config: V2Config) -> dict[str, Any]:
    return {"final_answer_payload": args}


def build_action_registry() -> dict[str, ActionSpec]:
    return {
        "hpo_search": ActionSpec(
            name="hpo_search",
            description="Normalize phenotype terms to HPO entries.",
            input_schema={"required": ["term"], "properties": {"term": {"type": "string"}, "top_k": {"type": "integer"}}},
            output_schema={"type": "object"},
            callable=_tool_hpo,
            category="tool",
            safe_for_benchmark=True,
            safe_for_ask=True,
        ),
        "pubmed_search": ActionSpec(
            name="pubmed_search",
            description="Search PubMed evidence for a query.",
            input_schema={"required": ["query"], "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}}},
            output_schema={"type": "object"},
            callable=_tool_pubmed,
            category="tool",
            safe_for_benchmark=True,
            safe_for_ask=True,
        ),
        "rxnorm_normalize_drug": ActionSpec(
            name="rxnorm_normalize_drug",
            description="Normalize medication names with RxNorm/RxNav.",
            input_schema={"required": ["drug_text"], "properties": {"drug_text": {"type": "string"}}},
            output_schema={"type": "object"},
            callable=_tool_rxnorm,
            category="tool",
            safe_for_benchmark=False,
            safe_for_ask=True,
        ),
        "openfda_drug_event_search": ActionSpec(
            name="openfda_drug_event_search",
            description="Query FAERS/openFDA signals for drug-reaction.",
            input_schema={
                "required": ["drug", "reaction"],
                "properties": {"drug": {"type": "string"}, "reaction": {"type": "string"}, "limit": {"type": "integer"}},
            },
            output_schema={"type": "object"},
            callable=_tool_openfda,
            category="tool",
            safe_for_benchmark=False,
            safe_for_ask=True,
        ),
        "opentargets_search": ActionSpec(
            name="opentargets_search",
            description="Retrieve disease-target-drug evidence from Open Targets.",
            input_schema={"required": ["disease_query"], "properties": {"disease_query": {"type": "string"}, "top_k": {"type": "integer"}}},
            output_schema={"type": "object"},
            callable=_tool_opentargets,
            category="tool",
            safe_for_benchmark=False,
            safe_for_ask=True,
        ),
        "reactome_search": ActionSpec(
            name="reactome_search",
            description="Retrieve pathway evidence from Reactome.",
            input_schema={"required": ["query"], "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}}},
            output_schema={"type": "object"},
            callable=_tool_reactome,
            category="tool",
            safe_for_benchmark=False,
            safe_for_ask=True,
        ),
        "web_search": ActionSpec(
            name="web_search",
            description="Retrieve web snippets for extra evidence.",
            input_schema={"required": ["query"], "properties": {"query": {"type": "string"}}},
            output_schema={"type": "object"},
            callable=_tool_web,
            category="tool",
            safe_for_benchmark=False,
            safe_for_ask=True,
        ),
        "clinical_evidence_skill": ActionSpec(
            name="clinical_evidence_skill",
            description="Collect phenotype normalization and literature evidence.",
            input_schema={
                "required": ["clinical_question", "phenotypes", "suspected_diagnosis"],
                "properties": {
                    "clinical_question": {"type": "string"},
                    "phenotypes": {"type": "array"},
                    "suspected_diagnosis": {"type": "string"},
                },
            },
            output_schema={"type": "object"},
            callable=_skill_clinical,
            category="skill",
            safe_for_benchmark=False,
            safe_for_ask=True,
        ),
        "mechanism_evidence_skill": ActionSpec(
            name="mechanism_evidence_skill",
            description="Collect disease mechanism evidence via target/pathway resources.",
            input_schema={
                "required": ["disease", "mechanism_focus"],
                "properties": {"disease": {"type": "string"}, "mechanism_focus": {"type": "string"}},
            },
            output_schema={"type": "object"},
            callable=_skill_mechanism,
            category="skill",
            safe_for_benchmark=False,
            safe_for_ask=True,
        ),
        "drug_safety_skill": ActionSpec(
            name="drug_safety_skill",
            description="Collect drug safety signals for adverse event focuses.",
            input_schema={
                "required": ["drug", "condition_context", "adverse_event_focus"],
                "properties": {"drug": {"type": "string"}, "condition_context": {"type": "string"}, "adverse_event_focus": {"type": "array"}},
            },
            output_schema={"type": "object"},
            callable=_skill_safety,
            category="skill",
            safe_for_benchmark=False,
            safe_for_ask=True,
        ),
        "medication_normalization_skill": ActionSpec(
            name="medication_normalization_skill",
            description="Normalize a medication list using RxNorm tool.",
            input_schema={"required": ["medications"], "properties": {"medications": {"type": "array"}}},
            output_schema={"type": "object"},
            callable=_skill_norm,
            category="skill",
            safe_for_benchmark=False,
            safe_for_ask=True,
        ),
        "autoimmune_case_review": ActionSpec(
            name="autoimmune_case_review",
            description="Legacy composite workflow action (composite evidence pipeline).",
            input_schema={
                "required": ["case_text", "suspected_diagnosis", "candidate_drug", "safety_focus", "phenotypes"],
                "properties": {
                    "case_text": {"type": "string"},
                    "suspected_diagnosis": {"type": "string"},
                    "candidate_drug": {"type": "string"},
                    "safety_focus": {"type": "array"},
                    "phenotypes": {"type": "array"},
                },
            },
            output_schema={"type": "object"},
            callable=_workflow_case_review,
            category="workflow",
            safe_for_benchmark=False,
            safe_for_ask=True,
        ),
        "final_answer": ActionSpec(
            name="final_answer",
            description="Terminate loop and emit final answer JSON.",
            input_schema={
                "required": ["answer", "diagnoses", "evidence_used", "limitations"],
                "properties": {
                    "answer": {"type": "string"},
                    "diagnoses": {"type": "array", "minItems": 1, "maxItems": 5},
                    "evidence_used": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["observation_id", "component", "claim"],
                            "properties": {
                                "observation_id": {"type": "string"},
                                "component": {"type": "string"},
                                "claim": {"type": "string"},
                            },
                        },
                    },
                    "limitations": {"type": "array"},
                },
            },
            output_schema={"type": "object"},
            callable=_final_action,
            category="final",
            safe_for_benchmark=True,
            safe_for_ask=True,
        ),
    }
