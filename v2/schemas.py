from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class BenchmarkItem:
    case_id: str
    phenotype_text: str
    phenotype_ids: list[str]
    phenotype_names: list[str]
    golden_diagnosis: str
    golden_diagnosis_code: str | None = None
    golden_diagnosis_name: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReActStep:
    # new constrained-react fields
    step_index: int = 0
    thought: str = ""
    action_name: str = ""
    action_args: dict[str, Any] = field(default_factory=dict)
    action_valid: bool = False
    observation: dict[str, Any] = field(default_factory=dict)
    observation_summary: str = ""
    success: bool = True
    error: str = ""
    llm_call_id: str = ""
    tool_call_ids: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    protocol_validation: dict[str, Any] = field(default_factory=dict)
    rejected_action_reason: str = ""
    repeat_action_detected: bool = False
    no_new_information: bool = False
    observation_id: str = ""
    observation_delta_summary: str = ""
    required_actions_satisfied: bool = False
    final_answer_evidence_links: list[str] = field(default_factory=list)
    # compatibility fields for legacy pipeline
    step_id: int = 0
    mode: str = ""
    action: str = ""
    input_summary: str = ""

    def __post_init__(self) -> None:
        if self.step_id == 0 and self.step_index > 0:
            self.step_id = self.step_index
        if self.step_index == 0 and self.step_id > 0:
            self.step_index = self.step_id
        if not self.action_name and self.action:
            self.action_name = self.action
        if not self.action and self.action_name:
            self.action = self.action_name

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReActState:
    task: str
    mode: str
    user_request: dict[str, Any]
    extracted_entities: dict[str, Any] = field(default_factory=dict)
    observations: list[dict[str, Any]] = field(default_factory=list)
    used_actions: list[str] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)
    max_steps: int = 6
    final_answer: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReActResult:
    status: str
    final_answer: dict[str, Any]
    react_steps: list[dict[str, Any]]
    observations: list[dict[str, Any]]
    used_actions: list[str]
    failed_actions: list[str]
    tool_effective_status: str
    log_path: str = ""
    best_raw_final_answer: str = ""
    parser_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkPrediction:
    case_id: str
    mode: str
    raw_answer: str
    prediction_topk: list[str]
    normalized_prediction_topk: list[str]
    final_diagnosis: str
    final_diagnois: str
    normalized_top1: str
    golden_diagnosis: str
    matched_by: str | None
    gold_names_used: list[str]
    correct_at_1: bool
    correct_at_3: bool
    correct_at_5: bool
    llm_calls: int
    tool_calls: int
    skill_calls: int
    latency_ms: float
    trace_path: str
    parser_warnings: list[str] = field(default_factory=list)
    prediction_parse_status: str = "ok"
    raw_candidate_lines: list[str] = field(default_factory=list)
    tool_effective_status: str = "not_applicable"
    tool_successes: list[str] = field(default_factory=list)
    tool_failures: list[str] = field(default_factory=list)
    evidence_available: bool = False
    critical_tool_failures: list[str] = field(default_factory=list)
    react_steps: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UserRequest:
    request_id: str
    raw_input: str
    input_type: str
    intent: str | None = None
    case_text: str | None = None
    suspected_diagnosis: str | None = None
    candidate_drug: str | None = None
    phenotypes: list[str] = field(default_factory=list)
    safety_focus: list[str] = field(default_factory=list)
    benchmark_config: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IntentResult:
    intent: str
    confidence: float
    routed_to: str
    extracted_fields: dict[str, Any]
    missing_fields: list[str]
    reason_summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DirectAnswer:
    request_id: str
    intent: str
    routed_to: str
    status: str
    answer: str
    evidence_summary: str | None
    safety_notes: list[str]
    limitations: list[str]
    failed_components: list[str]
    missing_fields: list[str]
    structured_output: dict[str, Any]
    log_path: str | None = None
    readable_log_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
