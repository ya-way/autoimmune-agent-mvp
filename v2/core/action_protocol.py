from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ActionProtocol:
    name: str
    allowed_actions: list[str]
    action_groups: dict[str, list[str]] = field(default_factory=dict)
    max_steps: int = 6
    max_repeats_per_action: dict[str, int] = field(default_factory=dict)
    max_repeats_same_args: int = 1
    required_before_final: list[str] = field(default_factory=list)
    disallowed_sequences: list[list[str]] = field(default_factory=list)
    stop_conditions: dict[str, Any] = field(default_factory=dict)
    observation_requirements: dict[str, Any] = field(default_factory=dict)
    final_answer_requirements: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_protocol(name: str, *, low_level_debug: bool = False) -> ActionProtocol:
    key = name.strip().lower()
    if key == "autoimmune_case_review":
        actions = [
            "clinical_evidence_skill",
            "mechanism_evidence_skill",
            "drug_safety_skill",
            "medication_normalization_skill",
            "final_answer",
        ]
        if low_level_debug:
            actions.extend(
                [
                    "hpo_search",
                    "pubmed_search",
                    "opentargets_search",
                    "reactome_search",
                    "rxnorm_normalize_drug",
                    "openfda_drug_event_search",
                ]
            )
        return ActionProtocol(
            name="autoimmune_case_review",
            allowed_actions=actions,
            action_groups={
                "preferred": [
                    "clinical_evidence_skill",
                    "mechanism_evidence_skill",
                    "drug_safety_skill",
                ]
            },
            max_steps=6,
            max_repeats_per_action={
                "clinical_evidence_skill": 1,
                "mechanism_evidence_skill": 1,
                "drug_safety_skill": 1,
                "medication_normalization_skill": 1,
            },
            required_before_final=["clinical_evidence_skill", "mechanism_evidence_skill", "drug_safety_skill"],
            observation_requirements={"max_no_new_information_streak": 2},
            final_answer_requirements={"require_observation_links": True, "max_diagnoses": 5},
        )
    if key == "drug_safety":
        actions = ["medication_normalization_skill", "drug_safety_skill", "final_answer"]
        return ActionProtocol(
            name="drug_safety",
            allowed_actions=actions,
            action_groups={"preferred": ["medication_normalization_skill", "drug_safety_skill"]},
            max_steps=4,
            max_repeats_per_action={"medication_normalization_skill": 1, "drug_safety_skill": 1},
            disallowed_sequences=[["medication_normalization_skill", "rxnorm_normalize_drug"]],
            required_before_final=["medication_normalization_skill"],
            observation_requirements={"max_no_new_information_streak": 1},
            final_answer_requirements={"require_observation_links": True, "max_diagnoses": 5},
        )
    if key == "clinical_evidence":
        return ActionProtocol(
            name="clinical_evidence",
            allowed_actions=["clinical_evidence_skill", "final_answer"],
            action_groups={"preferred": ["clinical_evidence_skill"]},
            max_steps=3,
            max_repeats_per_action={"clinical_evidence_skill": 1},
            required_before_final=["clinical_evidence_skill"],
            observation_requirements={"max_no_new_information_streak": 1},
            final_answer_requirements={"require_observation_links": True, "max_diagnoses": 5},
        )
    if key == "mechanism_evidence":
        return ActionProtocol(
            name="mechanism_evidence",
            allowed_actions=["mechanism_evidence_skill", "final_answer"],
            action_groups={"preferred": ["mechanism_evidence_skill"]},
            max_steps=3,
            max_repeats_per_action={"mechanism_evidence_skill": 1},
            required_before_final=["mechanism_evidence_skill"],
            observation_requirements={"max_no_new_information_streak": 1},
            final_answer_requirements={"require_observation_links": True, "max_diagnoses": 5},
        )
    if key == "benchmark_react_agent_with_tool":
        return ActionProtocol(
            name="benchmark_react_agent_with_tool",
            allowed_actions=["hpo_search", "pubmed_search", "final_answer"],
            action_groups={"preferred": ["hpo_search", "pubmed_search"]},
            max_steps=5,
            max_repeats_per_action={"hpo_search": 3, "pubmed_search": 1},
            max_repeats_same_args=1,
            observation_requirements={"max_no_new_information_streak": 2, "require_action_switch_after_no_new_information": True},
            final_answer_requirements={"require_observation_links": True, "max_diagnoses": 5, "require_top5_diagnoses": True},
        )
    if key == "benchmark_react_agent_without_tool":
        return ActionProtocol(
            name="benchmark_react_agent_without_tool",
            allowed_actions=["final_answer"],
            action_groups={"preferred": ["final_answer"]},
            max_steps=1,
            final_answer_requirements={"require_observation_links": False, "max_diagnoses": 5, "require_top5_diagnoses": True},
        )
    return ActionProtocol(
        name="default",
        allowed_actions=["final_answer"],
        action_groups={"preferred": ["final_answer"]},
        max_steps=2,
        final_answer_requirements={"require_observation_links": False, "max_diagnoses": 5},
    )
