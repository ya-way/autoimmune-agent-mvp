from __future__ import annotations

import json
from time import perf_counter
from typing import Any

from v2.config import V2Config
from v2.core.action_protocol import ActionProtocol, get_protocol
from v2.core.action_registry import build_action_registry
from v2.core.action_schema import ActionSpec
from v2.core.actions import build_action_protocol_prompt, parse_react_json, summarize_observation
from v2.core.llm import LLMClient
from v2.core.logger import V2RunLogger
from v2.schemas import ReActResult, ReActState, ReActStep


class ReActAgent:
    def __init__(self, llm: LLMClient, logger: V2RunLogger, config: V2Config) -> None:
        self.llm = llm
        self.logger = logger
        self.config = config
        self.registry = build_action_registry()

    def _action_specs(self, allowed_actions: list[str], mode: str) -> dict[str, ActionSpec]:
        specs: dict[str, ActionSpec] = {}
        for name in allowed_actions:
            spec = self.registry.get(name)
            if spec is None:
                continue
            if mode == "ask" and not spec.safe_for_ask:
                continue
            if mode.startswith("benchmark") and not spec.safe_for_benchmark:
                continue
            specs[name] = spec
        return specs

    def _tool_effective_status(self, steps: list[ReActStep]) -> str:
        tool_steps = [s for s in steps if (s.action_name in self.registry and self.registry[s.action_name].category == "tool")]
        if not tool_steps:
            return "not_applicable"
        success_count = sum(1 for s in tool_steps if s.success)
        if success_count == len(tool_steps):
            return "all_success"
        if success_count == 0:
            return "all_failed"
        return "partial_success"

    def _stable_args_key(self, args: dict[str, Any]) -> str:
        try:
            return json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            return str(args)

    def _required_actions_satisfied(self, state: ReActState, protocol: ActionProtocol) -> bool:
        required = set(protocol.required_before_final)
        if not required:
            return True
        return required.issubset(set(state.used_actions))

    def _build_context(self, state: ReActState, allowed_specs: dict[str, ActionSpec], protocol: ActionProtocol) -> dict[str, Any]:
        return {
            "task": state.task,
            "mode": state.mode,
            "user_request": state.user_request,
            "extracted_entities": state.extracted_entities,
            "observations": state.observations,
            "used_actions": state.used_actions,
            "unresolved_questions": state.unresolved_questions,
            "protocol": protocol.to_dict(),
            "allowed_actions": [
                {
                    "name": spec.name,
                    "description": spec.description,
                    "input_schema": spec.input_schema,
                    "category": spec.category,
                }
                for spec in allowed_specs.values()
            ],
        }

    def _execute_action(
        self,
        spec: ActionSpec,
        action_args: dict[str, Any],
        state: ReActState,
        step_index: int,
    ) -> tuple[dict[str, Any], bool, str, list[str], float]:
        start = perf_counter()
        tool_before = len(self.logger.tool_calls)
        try:
            observation = spec.callable(
                args=action_args,
                state=state.to_dict(),
                logger=self.logger,
                caller=f"react_agent.step{step_index}.{spec.name}",
                config=self.config,
            )
            latency_ms = round((perf_counter() - start) * 1000, 2)
            tool_after = len(self.logger.tool_calls)
            tool_call_ids = [f"tool_{i:04d}" for i in range(tool_before + 1, tool_after + 1)]
            value = observation if isinstance(observation, dict) else {"value": observation}
            return value, True, "", tool_call_ids, latency_ms
        except Exception as exc:
            latency_ms = round((perf_counter() - start) * 1000, 2)
            tool_after = len(self.logger.tool_calls)
            tool_call_ids = [f"tool_{i:04d}" for i in range(tool_before + 1, tool_after + 1)]
            return {"error": str(exc)}, False, str(exc), tool_call_ids, latency_ms

    def _is_no_new_information(self, observation: dict[str, Any], observation_summary: str, last_summary: str) -> tuple[bool, str]:
        if not observation:
            return True, "empty_observation"
        if observation.get("error"):
            return True, "observation_error_only"
        if observation_summary and observation_summary == last_summary:
            return True, "same_summary_as_previous"
        if isinstance(observation.get("items"), list) and not observation.get("items"):
            return True, "items_empty"
        if isinstance(observation.get("results"), list) and not observation.get("results"):
            return True, "results_empty"
        return False, "new_information_detected"

    def _validate_final_answer_payload(
        self,
        payload: dict[str, Any],
        protocol: ActionProtocol,
        state: ReActState,
    ) -> tuple[bool, str, list[str], list[str]]:
        evidence_used = payload.get("evidence_used", [])
        evidence_links: list[str] = []
        warnings: list[str] = []
        if not isinstance(evidence_used, list):
            return False, "final_answer evidence_used must be a list", evidence_links, warnings

        observed_ids = {str(obs.get("observation_id", "")).strip() for obs in state.observations if obs.get("observation_id")}
        for item in evidence_used:
            if not isinstance(item, dict):
                warnings.append("evidence_used item is not an object")
                continue
            obs_id = str(item.get("observation_id", "")).strip()
            component = str(item.get("component", "")).strip()
            claim = str(item.get("claim", "")).strip()
            if obs_id:
                evidence_links.append(obs_id)
                if obs_id not in observed_ids:
                    warnings.append(f"unknown observation_id: {obs_id}")
            if not component:
                warnings.append("evidence_used missing component")
            if not claim:
                warnings.append("evidence_used missing claim")

        non_final_observations = [o for o in state.observations if str(o.get("action_name", "")) != "final_answer"]
        require_links = bool(protocol.final_answer_requirements.get("require_observation_links", False))
        if require_links and non_final_observations and not evidence_links:
            return False, "final_answer must cite at least one observation_id", evidence_links, warnings

        diagnoses = payload.get("diagnoses", [])
        if not isinstance(diagnoses, list):
            return False, "final_answer diagnoses must be a list", evidence_links, warnings
        max_diagnoses = protocol.final_answer_requirements.get("max_diagnoses")
        if isinstance(max_diagnoses, int) and len(diagnoses) > max_diagnoses:
            return False, f"final_answer diagnoses exceeds max {max_diagnoses}", evidence_links, warnings
        if protocol.final_answer_requirements.get("require_top5_diagnoses", False) and len(diagnoses) != 5:
            return False, "final_answer diagnoses must contain exactly top-5 items", evidence_links, warnings
        if state.mode == "benchmark_deeprare":
            bad_markers = [
                "insufficient",
                "unknown",
                "unable",
                "no matching",
                "not enough",
                "evidence",
                "data",
                "diagnosis list",
                "symptom",
                "mechanism",
                "hpo:",
            ]
            for d in diagnoses:
                value = str(d).strip()
                low = value.lower()
                if not value:
                    return False, "final_answer diagnoses contains empty item", evidence_links, warnings
                if any(m in low for m in bad_markers):
                    return False, f"diagnosis item is not a disease name: {value}", evidence_links, warnings
                if len(value.split()) > 10:
                    return False, f"diagnosis item is too verbose: {value}", evidence_links, warnings
        return True, "", evidence_links, warnings

    def _allowed_observation_ids(self, state: ReActState) -> list[str]:
        return [str(obs.get("observation_id", "")).strip() for obs in state.observations if str(obs.get("observation_id", "")).strip()]

    def _required_final_answer_skeleton(self, mode: str) -> str:
        answer_text = (
            "1. Disease A\\n2. Disease B\\n3. Disease C\\n4. Disease D\\n5. Disease E"
            if mode.startswith("benchmark")
            else "Natural-language synthesis based on cited observations."
        )
        payload = {
            "thought": "I have enough evidence to provide a constrained final answer.",
            "action": "final_answer",
            "args": {
                "diagnoses": ["Disease A", "Disease B", "Disease C", "Disease D", "Disease E"],
                "answer": answer_text,
                "evidence_used": [
                    {"observation_id": "obs_0001", "component": "hpo_search", "claim": "Key evidence used for ranking."}
                ],
                "limitations": ["Evidence is limited to available observations and retrieved sources."],
            },
            "stop": True,
        }
        return json.dumps(payload, ensure_ascii=False)

    def _final_answer_rejection_feedback(self, error: str, state: ReActState, mode: str) -> str:
        obs_ids = self._allowed_observation_ids(state)
        obs_text = ", ".join(obs_ids) if obs_ids else "none"
        benchmark_reminder = (
            "Benchmark reminder: args.diagnoses must be exactly 5 rare disease names. "
            "Do not output symptoms, HPO terms, mechanisms, or placeholders.\n"
            if mode == "benchmark_deeprare"
            else ""
        )
        return (
            "Your previous action was rejected.\n"
            "rejected_action=final_answer\n"
            f"exact_validation_error={error}\n"
            f"allowed_observation_ids={obs_text}\n"
            f"{benchmark_reminder}"
            "Fix only the JSON format and required fields. Do not invent a new action.\n"
            "Required final_answer JSON skeleton:\n"
            f"{self._required_final_answer_skeleton(mode)}\n"
            "Return strict JSON only.\n"
        )

    def _benchmark_step_hint(self, state: ReActState, protocol: ActionProtocol, step_index: int, max_steps: int) -> str:
        if protocol.name != "benchmark_react_agent_with_tool":
            return ""
        hpo_non_empty = False
        pubmed_non_empty = False
        for obs in state.observations:
            action_name = str(obs.get("action_name", "")).strip()
            data = obs.get("observation", {})
            summary = str(obs.get("observation_summary", "")).strip().lower()
            if action_name == "hpo_search":
                if isinstance(data, dict) and (data.get("result_count") or data.get("results") or data.get("matches")):
                    hpo_non_empty = True
                if summary and "empty" not in summary and "error" not in summary:
                    hpo_non_empty = True
            if action_name == "pubmed_search":
                if isinstance(data, dict) and (data.get("result_count") or data.get("items") or data.get("results")):
                    pubmed_non_empty = True
                if summary and "empty" not in summary and "error" not in summary:
                    pubmed_non_empty = True
        hints: list[str] = []
        if hpo_non_empty and pubmed_non_empty:
            hints.append("Benchmark hint: both non-empty HPO and PubMed observations exist; strongly prefer final_answer now.")
        if "pubmed_search" in state.used_actions:
            hints.append("Benchmark hint: pubmed_search already executed; do not continue searching unless required by hard validation.")
        if (max_steps - step_index) <= 0:
            hints.append("Benchmark hint: this is the last step; you must choose action=final_answer with stop=true.")
        return "\n".join(hints)

    def _repair_final_answer_payload(
        self,
        *,
        raw_candidate: dict[str, Any],
        task: str,
        state: ReActState,
        mode: str,
        protocol: ActionProtocol,
        final_spec: ActionSpec | None,
        caller: str,
    ) -> tuple[dict[str, Any], str]:
        repaired = dict(raw_candidate) if isinstance(raw_candidate, dict) else {}
        if "answer" not in repaired or not str(repaired.get("answer", "")).strip():
            repaired["answer"] = str(raw_candidate.get("answer", "")).strip() if isinstance(raw_candidate, dict) else ""
        if "diagnoses" not in repaired or not isinstance(repaired.get("diagnoses"), list):
            repaired["diagnoses"] = []
        if protocol.final_answer_requirements.get("require_top5_diagnoses", False):
            diagnoses = [str(x).strip() for x in repaired.get("diagnoses", []) if str(x).strip()]
            if diagnoses:
                while len(diagnoses) < 5:
                    diagnoses.append(diagnoses[-1])
                repaired["diagnoses"] = diagnoses[:5]
        if "limitations" not in repaired or not isinstance(repaired.get("limitations"), list):
            repaired["limitations"] = ["final_answer_schema_repair_applied"]
        if "evidence_used" not in repaired or not isinstance(repaired.get("evidence_used"), list):
            repaired["evidence_used"] = []
        if not repaired["evidence_used"]:
            obs_ids = self._allowed_observation_ids(state)
            if obs_ids:
                repaired["evidence_used"] = [
                    {
                        "observation_id": obs_ids[0],
                        "component": "react_agent",
                        "claim": "Evidence reference repaired to satisfy required schema link.",
                    }
                ]

        if final_spec is not None:
            ok, err = final_spec.validate_args(repaired)
            if ok:
                ok2, err2, _, _ = self._validate_final_answer_payload(repaired, protocol, state)
                if ok2:
                    return repaired, ""
                err = err2
            feedback = self._final_answer_rejection_feedback(err, state, mode)
        else:
            feedback = self._final_answer_rejection_feedback("missing final spec", state, mode)

        repair_prompt = (
            "Final answer repair mode. Do not change medical meaning.\n"
            "Only repair JSON structure and required fields.\n"
            f"task={task}\n"
            f"current_payload={json.dumps(raw_candidate, ensure_ascii=False)}\n"
            f"{feedback}"
        )
        raw = self.llm.complete(
            prompt=repair_prompt,
            caller=caller,
            system_prompt="You repair final_answer JSON schema only. Output strict JSON only.",
        )
        parsed, parse_err = parse_react_json(raw)
        if parse_err:
            return {}, parse_err
        if str(parsed.get("action", "")).strip() != "final_answer":
            return {}, "final_answer_repair must keep action=final_answer"
        repaired_payload = parsed.get("args", {})
        if not isinstance(repaired_payload, dict):
            return {}, "final_answer_repair args must be object"
        if final_spec is not None:
            ok, err = final_spec.validate_args(repaired_payload)
            if not ok:
                return {}, err
        ok2, err2, _, _ = self._validate_final_answer_payload(repaired_payload, protocol, state)
        if not ok2:
            return {}, err2
        return repaired_payload, ""

    def run(
        self,
        task: str,
        allowed_actions: list[str],
        initial_context: dict[str, Any],
        max_steps: int = 6,
        mode: str = "ask",
        protocol_name: str | None = None,
        low_level_debug: bool = False,
    ) -> ReActResult:
        protocol = get_protocol(protocol_name or "default", low_level_debug=low_level_debug)
        protocol_allowed = [a for a in protocol.allowed_actions if a in allowed_actions or not allowed_actions]
        allowed_specs = self._action_specs(protocol_allowed or allowed_actions, mode)
        if "final_answer" not in allowed_specs and "final_answer" in self.registry:
            allowed_specs["final_answer"] = self.registry["final_answer"]
        effective_max_steps = min(max_steps, protocol.max_steps) if protocol.max_steps > 0 else max_steps
        state = ReActState(
            task=task,
            mode=mode,
            user_request=initial_context.get("user_request", {}),
            extracted_entities=initial_context.get("extracted_entities", {}),
            observations=[],
            used_actions=[],
            unresolved_questions=[],
            max_steps=effective_max_steps,
            final_answer={},
        )
        steps: list[ReActStep] = []
        failed_actions: list[str] = []
        action_counts: dict[str, int] = {}
        action_args_counts: dict[str, int] = {}
        no_new_info_streak = 0
        rejected_early_final_once = False
        last_observation_summary = ""
        final_answer_rejection_streak = 0
        final_answer_rejection_count = 0
        best_raw_final_answer = ""
        parser_warnings: list[str] = []

        for step_index in range(1, effective_max_steps + 1):
            planning_prompt = build_action_protocol_prompt(
                task=task,
                mode=mode,
                state_context=self._build_context(state, allowed_specs, protocol),
                allowed_actions=[
                    {
                        "name": spec.name,
                        "description": spec.description,
                        "input_schema": spec.input_schema,
                        "category": spec.category,
                    }
                    for spec in allowed_specs.values()
                ],
                step_index=step_index,
                max_steps=effective_max_steps,
            )
            benchmark_hint = self._benchmark_step_hint(state, protocol, step_index, effective_max_steps)
            if benchmark_hint:
                planning_prompt = planning_prompt + "\n" + benchmark_hint + "\n"
            llm_call_id = ""
            raw_output = ""
            parsed: dict[str, Any] = {}
            validation_error = ""
            previous_validation_error = ""
            protocol_validation: dict[str, Any] = {"accepted": False}
            for attempt in range(1, 3):
                validation_error = ""
                llm_before = len(self.logger.llm_calls)
                prompt = planning_prompt
                if attempt == 2:
                    correction = (
                        self._final_answer_rejection_feedback(previous_validation_error, state, mode)
                        if "final_answer" in previous_validation_error.lower() or "diagnoses" in previous_validation_error.lower()
                        else (
                            "Previous output was rejected.\n"
                            f"rejected_action={str(parsed.get('action', '')).strip() or 'unknown'}\n"
                            f"exact_validation_error={previous_validation_error}\n"
                            "Return strict JSON only.\n"
                        )
                    )
                    prompt = (
                        planning_prompt
                        + "\n"
                        + correction
                    )
                raw_output = self.llm.complete(
                    prompt=prompt,
                    caller=f"react_agent.step{step_index}.plan.attempt{attempt}",
                    system_prompt="You are a constrained medical ReAct planner. Output strict JSON only.",
                )
                llm_after = len(self.logger.llm_calls)
                llm_call_id = f"llm_{llm_after:04d}" if llm_after > llm_before else f"llm_{llm_after:04d}"
                parsed, parse_error = parse_react_json(raw_output)
                if parse_error:
                    validation_error = parse_error
                    previous_validation_error = validation_error
                    continue
                action_name = str(parsed.get("action", "")).strip()
                if action_name not in allowed_specs:
                    validation_error = f"unknown action: {action_name}"
                    previous_validation_error = validation_error
                    continue
                args = parsed.get("args", {})
                if not isinstance(args, dict):
                    validation_error = "args must be object"
                    previous_validation_error = validation_error
                    continue
                if action_name == "final_answer" and not bool(parsed.get("stop", False)):
                    validation_error = "final_answer requires stop=true"
                    previous_validation_error = validation_error
                    continue
                ok, err = allowed_specs[action_name].validate_args(args)
                if not ok:
                    validation_error = err
                    previous_validation_error = validation_error
                    continue
                key = f"{action_name}|{self._stable_args_key(args)}"
                if action_args_counts.get(key, 0) >= protocol.max_repeats_same_args:
                    validation_error = "This action with the same arguments was already executed. Choose a different action or final_answer."
                    previous_validation_error = validation_error
                    continue
                limit = protocol.max_repeats_per_action.get(action_name)
                if isinstance(limit, int) and action_counts.get(action_name, 0) >= limit:
                    validation_error = f"Action {action_name} exceeded max repeats ({limit}). Choose another action or final_answer."
                    previous_validation_error = validation_error
                    continue
                if steps:
                    last_action = steps[-1].action_name
                    for disallowed in protocol.disallowed_sequences:
                        if len(disallowed) == 2 and last_action == disallowed[0] and action_name == disallowed[1]:
                            validation_error = f"Disallowed sequence: {disallowed[0]} -> {disallowed[1]}"
                            previous_validation_error = validation_error
                            break
                if validation_error:
                    continue
                if action_name == "final_answer" and not self._required_actions_satisfied(state, protocol):
                    missing = sorted(set(protocol.required_before_final) - set(state.used_actions))
                    if not rejected_early_final_once:
                        rejected_early_final_once = True
                        validation_error = f"final_answer too early, required actions missing: {', '.join(missing)}"
                        previous_validation_error = validation_error
                        continue
                    protocol_validation["early_final_answer"] = True
                validation_error = ""
                protocol_validation = {
                    "accepted": True,
                    "required_actions_satisfied": self._required_actions_satisfied(state, protocol),
                    "max_repeats_per_action": protocol.max_repeats_per_action,
                    "max_repeats_same_args": protocol.max_repeats_same_args,
                }
                break

            if not parsed or validation_error:
                repeat_detected = "same arguments was already executed" in validation_error
                rejected_action = str(parsed.get("action", "")).strip() if parsed else ""
                if rejected_action == "final_answer":
                    final_answer_rejection_streak += 1
                    final_answer_rejection_count += 1
                    best_raw_final_answer = raw_output or best_raw_final_answer
                else:
                    final_answer_rejection_streak = 0
                step = ReActStep(
                    step_index=step_index,
                    thought=str(parsed.get("thought", "")) if parsed else "",
                    action_name=str(parsed.get("action", "")) if parsed else "",
                    action_args=parsed.get("args", {}) if parsed else {},
                    action_valid=False,
                    observation={"error": validation_error or "action planning failed"},
                    observation_summary=validation_error or "action planning failed",
                    success=False,
                    error=validation_error or "action planning failed",
                    llm_call_id=llm_call_id,
                    tool_call_ids=[],
                    latency_ms=0.0,
                    mode=mode,
                    input_summary=summarize_observation(task, 120),
                    protocol_validation={"accepted": False, "error": validation_error or "action planning failed"},
                    rejected_action_reason=validation_error or "action planning failed",
                    repeat_action_detected=repeat_detected,
                    no_new_information=False,
                    observation_id="",
                    observation_delta_summary="rejected_before_execution",
                    required_actions_satisfied=self._required_actions_satisfied(state, protocol),
                    final_answer_evidence_links=[],
                )
                steps.append(step)
                failed_actions.append(step.action_name or "invalid_action")
                state.unresolved_questions.append(validation_error or "invalid_action")
                self.logger.log_workflow_event(
                    "react_agent_step",
                    {
                        "step_index": step_index,
                        "prompt": planning_prompt,
                        "llm_raw_output": raw_output,
                        "parsed": parsed,
                        "validation_result": {"ok": False, "error": validation_error or "action planning failed"},
                        "protocol_validation": step.protocol_validation,
                        "rejected_action_reason": step.rejected_action_reason,
                        "repeat_action_detected": step.repeat_action_detected,
                        "next_step_context": self._build_context(state, allowed_specs, protocol),
                    },
                )
                if rejected_action == "final_answer" and final_answer_rejection_streak >= 1:
                    repaired_payload, repair_err = self._repair_final_answer_payload(
                        raw_candidate=parsed.get("args", {}) if isinstance(parsed.get("args", {}), dict) else {},
                        task=task,
                        state=state,
                        mode=mode,
                        protocol=protocol,
                        final_spec=allowed_specs.get("final_answer"),
                        caller=f"react_agent.step{step_index}.final_answer_repair",
                    )
                    if repaired_payload:
                        links = [
                            str(x.get("observation_id", "")).strip()
                            for x in repaired_payload.get("evidence_used", [])
                            if isinstance(x, dict) and str(x.get("observation_id", "")).strip()
                        ]
                        repaired_step = ReActStep(
                            step_index=step_index,
                            thought="final_answer repaired after repeated schema rejections",
                            action_name="final_answer",
                            action_args=repaired_payload,
                            action_valid=True,
                            observation={"final_answer": repaired_payload},
                            observation_summary=summarize_observation(repaired_payload, 220),
                            success=True,
                            error="",
                            llm_call_id=f"llm_{len(self.logger.llm_calls):04d}",
                            tool_call_ids=[],
                            latency_ms=0.0,
                            mode=mode,
                            input_summary=summarize_observation(task, 120),
                            protocol_validation={"accepted": True, "final_answer_repaired": True},
                            rejected_action_reason="",
                            repeat_action_detected=False,
                            no_new_information=False,
                            observation_id="",
                            observation_delta_summary="final_answer_repaired",
                            required_actions_satisfied=self._required_actions_satisfied(state, protocol),
                            final_answer_evidence_links=links,
                        )
                        steps.append(repaired_step)
                        state.used_actions.append("final_answer")
                        state.final_answer = repaired_payload
                        state.observations.append(
                            {"step_index": step_index, "action_name": "final_answer", "success": True, "observation": {"final_answer": state.final_answer}}
                        )
                        self.logger.log_workflow_event(
                            "react_agent_step",
                            {
                                "step_index": step_index,
                                "parsed": {"action": "final_answer", "args": repaired_payload, "stop": True},
                                "validation_result": {"ok": True, "error": ""},
                                "protocol_validation": {"accepted": True, "final_answer_repaired": True},
                                "executed_action": "final_answer",
                                "observation": {"final_answer": repaired_payload},
                                "final_answer_evidence_links": links,
                                "next_step_context": self._build_context(state, allowed_specs, protocol),
                            },
                        )
                        return ReActResult(
                            status="success",
                            final_answer=state.final_answer,
                            react_steps=[s.to_dict() for s in steps],
                            observations=state.observations,
                            used_actions=state.used_actions,
                            failed_actions=failed_actions,
                            tool_effective_status=self._tool_effective_status(steps),
                            log_path=str(self.logger.run_dir),
                            best_raw_final_answer=best_raw_final_answer,
                            parser_warnings=parser_warnings,
                        )
                    parser_warnings.append("final_answer_schema_failed")
                    return ReActResult(
                        status="partial",
                        final_answer={},
                        react_steps=[s.to_dict() for s in steps],
                        observations=state.observations,
                        used_actions=state.used_actions,
                        failed_actions=failed_actions + ["final_answer"],
                        tool_effective_status=self._tool_effective_status(steps),
                        log_path=str(self.logger.run_dir),
                        best_raw_final_answer=best_raw_final_answer,
                        parser_warnings=parser_warnings + [repair_err or "final_answer_schema_failed"],
                    )
                continue

            action_name = str(parsed.get("action", "")).strip()
            action_args = parsed.get("args", {})
            thought = str(parsed.get("thought", "")).strip()
            should_stop = bool(parsed.get("stop", False))
            spec = allowed_specs[action_name]
            args_key = f"{action_name}|{self._stable_args_key(action_args)}"
            action_counts[action_name] = action_counts.get(action_name, 0) + 1
            action_args_counts[args_key] = action_args_counts.get(args_key, 0) + 1

            if action_name == "final_answer" and should_stop:
                ok, final_err, links, warnings = self._validate_final_answer_payload(action_args, protocol, state)
                if not ok:
                    final_answer_rejection_streak += 1
                    final_answer_rejection_count += 1
                    best_raw_final_answer = raw_output or best_raw_final_answer
                    step = ReActStep(
                        step_index=step_index,
                        thought=thought,
                        action_name=action_name,
                        action_args=action_args,
                        action_valid=False,
                        observation={"error": final_err},
                        observation_summary=final_err,
                        success=False,
                        error=final_err,
                        llm_call_id=llm_call_id,
                        tool_call_ids=[],
                        latency_ms=0.0,
                        mode=mode,
                        input_summary=summarize_observation(task, 120),
                        protocol_validation={"accepted": False, "error": final_err},
                        rejected_action_reason=final_err,
                        repeat_action_detected=False,
                        no_new_information=False,
                        observation_id="",
                        observation_delta_summary="invalid_final_answer_payload",
                        required_actions_satisfied=self._required_actions_satisfied(state, protocol),
                        final_answer_evidence_links=links,
                    )
                    steps.append(step)
                    failed_actions.append(action_name)
                    state.unresolved_questions.append(final_err)
                    self.logger.log_workflow_event(
                        "react_agent_step",
                        {
                            "step_index": step_index,
                            "prompt": planning_prompt,
                            "llm_raw_output": raw_output,
                            "parsed": parsed,
                            "validation_result": {"ok": False, "error": final_err},
                            "protocol_validation": step.protocol_validation,
                            "rejected_action_reason": step.rejected_action_reason,
                            "next_step_context": self._build_context(state, allowed_specs, protocol),
                        },
                    )
                    if final_answer_rejection_streak >= 1:
                        repaired_payload, repair_err = self._repair_final_answer_payload(
                            raw_candidate=action_args if isinstance(action_args, dict) else {},
                            task=task,
                            state=state,
                            mode=mode,
                            protocol=protocol,
                            final_spec=allowed_specs.get("final_answer"),
                            caller=f"react_agent.step{step_index}.final_answer_repair",
                        )
                        if repaired_payload:
                            repaired_links = [
                                str(x.get("observation_id", "")).strip()
                                for x in repaired_payload.get("evidence_used", [])
                                if isinstance(x, dict) and str(x.get("observation_id", "")).strip()
                            ]
                            repaired_step = ReActStep(
                                step_index=step_index,
                                thought="final_answer repaired after repeated schema rejections",
                                action_name="final_answer",
                                action_args=repaired_payload,
                                action_valid=True,
                                observation={"final_answer": repaired_payload},
                                observation_summary=summarize_observation(repaired_payload, 220),
                                success=True,
                                error="",
                                llm_call_id=f"llm_{len(self.logger.llm_calls):04d}",
                                tool_call_ids=[],
                                latency_ms=0.0,
                                mode=mode,
                                input_summary=summarize_observation(task, 120),
                                protocol_validation={"accepted": True, "final_answer_repaired": True},
                                rejected_action_reason="",
                                repeat_action_detected=False,
                                no_new_information=False,
                                observation_id="",
                                observation_delta_summary="final_answer_repaired",
                                required_actions_satisfied=self._required_actions_satisfied(state, protocol),
                                final_answer_evidence_links=repaired_links,
                            )
                            steps.append(repaired_step)
                            state.used_actions.append("final_answer")
                            state.final_answer = repaired_payload
                            state.observations.append(
                                {"step_index": step_index, "action_name": "final_answer", "success": True, "observation": {"final_answer": state.final_answer}}
                            )
                            self.logger.log_workflow_event(
                                "react_agent_step",
                                {
                                    "step_index": step_index,
                                    "parsed": {"action": "final_answer", "args": repaired_payload, "stop": True},
                                    "validation_result": {"ok": True, "error": ""},
                                    "protocol_validation": {"accepted": True, "final_answer_repaired": True},
                                    "executed_action": "final_answer",
                                    "observation": {"final_answer": repaired_payload},
                                    "final_answer_evidence_links": repaired_links,
                                    "next_step_context": self._build_context(state, allowed_specs, protocol),
                                },
                            )
                            return ReActResult(
                                status="success",
                                final_answer=state.final_answer,
                                react_steps=[s.to_dict() for s in steps],
                                observations=state.observations,
                                used_actions=state.used_actions,
                                failed_actions=failed_actions,
                                tool_effective_status=self._tool_effective_status(steps),
                                log_path=str(self.logger.run_dir),
                                best_raw_final_answer=best_raw_final_answer,
                                parser_warnings=parser_warnings,
                            )
                        parser_warnings.append("final_answer_schema_failed")
                        return ReActResult(
                            status="partial",
                            final_answer={},
                            react_steps=[s.to_dict() for s in steps],
                            observations=state.observations,
                            used_actions=state.used_actions,
                            failed_actions=failed_actions + ["final_answer"],
                            tool_effective_status=self._tool_effective_status(steps),
                            log_path=str(self.logger.run_dir),
                            best_raw_final_answer=best_raw_final_answer,
                            parser_warnings=parser_warnings + [repair_err or "final_answer_schema_failed"],
                        )
                    continue
                final_answer_rejection_streak = 0
                protocol_validation["warnings"] = warnings
                step = ReActStep(
                    step_index=step_index,
                    thought=thought,
                    action_name=action_name,
                    action_args=action_args,
                    action_valid=True,
                    observation={"final_answer": action_args},
                    observation_summary=summarize_observation(action_args, 220),
                    success=True,
                    error="",
                    llm_call_id=llm_call_id,
                    tool_call_ids=[],
                    latency_ms=0.0,
                    mode=mode,
                    input_summary=summarize_observation(task, 120),
                    protocol_validation=protocol_validation,
                    rejected_action_reason="",
                    repeat_action_detected=False,
                    no_new_information=False,
                    observation_id="",
                    observation_delta_summary="final_answer",
                    required_actions_satisfied=self._required_actions_satisfied(state, protocol),
                    final_answer_evidence_links=links,
                )
                steps.append(step)
                state.used_actions.append(action_name)
                state.final_answer = action_args if isinstance(action_args, dict) else {}
                state.observations.append(
                    {"step_index": step_index, "action_name": action_name, "success": True, "observation": {"final_answer": state.final_answer}}
                )
                self.logger.log_workflow_event(
                    "react_agent_step",
                    {
                        "step_index": step_index,
                        "prompt": planning_prompt,
                        "llm_raw_output": raw_output,
                        "parsed": parsed,
                        "validation_result": {"ok": True, "error": ""},
                        "protocol_validation": protocol_validation,
                        "executed_action": action_name,
                        "observation": {"final_answer": state.final_answer},
                        "final_answer_evidence_links": links,
                        "next_step_context": self._build_context(state, allowed_specs, protocol),
                    },
                )
                return ReActResult(
                    status="success",
                    final_answer=state.final_answer,
                    react_steps=[s.to_dict() for s in steps],
                    observations=state.observations,
                    used_actions=state.used_actions,
                    failed_actions=failed_actions,
                    tool_effective_status=self._tool_effective_status(steps),
                    log_path=str(self.logger.run_dir),
                    best_raw_final_answer=best_raw_final_answer,
                    parser_warnings=parser_warnings,
                )

            observation, success, error, tool_call_ids, latency_ms = self._execute_action(spec, action_args, state, step_index)
            obs_summary = summarize_observation(observation, 220)
            no_new_info, delta_summary = self._is_no_new_information(observation, obs_summary, last_observation_summary)
            last_observation_summary = obs_summary
            no_new_info_streak = (no_new_info_streak + 1) if no_new_info else 0
            observation_id = f"obs_{len(state.observations) + 1:04d}"
            protocol_validation["observation_warning"] = ""
            max_streak = int(protocol.observation_requirements.get("max_no_new_information_streak", 99))
            if no_new_info_streak >= max_streak:
                warning = "No new information in consecutive observations; choose different action or final_answer."
                state.unresolved_questions.append(warning)
                protocol_validation["observation_warning"] = warning

            step = ReActStep(
                step_index=step_index,
                thought=thought,
                action_name=action_name,
                action_args=action_args if isinstance(action_args, dict) else {},
                action_valid=True,
                observation=observation,
                observation_summary=obs_summary,
                success=success,
                error=error,
                llm_call_id=llm_call_id,
                tool_call_ids=tool_call_ids,
                latency_ms=latency_ms,
                mode=mode,
                input_summary=summarize_observation(task, 120),
                protocol_validation=protocol_validation,
                rejected_action_reason="",
                repeat_action_detected=False,
                no_new_information=no_new_info,
                observation_id=observation_id,
                observation_delta_summary=delta_summary,
                required_actions_satisfied=self._required_actions_satisfied(state, protocol),
                final_answer_evidence_links=[],
            )
            steps.append(step)
            state.used_actions.append(action_name)
            if not success:
                failed_actions.append(action_name)
            state.observations.append(
                {
                    "step_index": step_index,
                    "action_name": action_name,
                    "success": success,
                    "observation_id": observation_id,
                    "observation": observation,
                    "observation_summary": obs_summary,
                }
            )
            self.logger.log_workflow_event(
                "react_agent_step",
                {
                    "step_index": step_index,
                    "prompt": planning_prompt,
                    "llm_raw_output": raw_output,
                    "parsed": parsed,
                    "validation_result": {"ok": True, "error": ""},
                    "protocol_validation": protocol_validation,
                    "executed_action": action_name,
                    "observation": observation,
                    "observation_id": observation_id,
                    "observation_delta_summary": delta_summary,
                    "no_new_information": no_new_info,
                    "latency_ms": latency_ms,
                    "next_step_context": self._build_context(state, allowed_specs, protocol),
                },
            )
            if action_name != "final_answer":
                final_answer_rejection_streak = 0

        forced_prompt = build_action_protocol_prompt(
            task=task,
            mode=mode,
            state_context=self._build_context(state, allowed_specs, protocol),
            allowed_actions=[
                {
                    "name": "final_answer",
                    "description": allowed_specs["final_answer"].description if "final_answer" in allowed_specs else "Finalize answer",
                    "input_schema": allowed_specs["final_answer"].input_schema if "final_answer" in allowed_specs else {},
                    "category": "final",
                }
            ],
            step_index=effective_max_steps + 1,
            max_steps=effective_max_steps,
        ) + "\nYou must choose action=final_answer with stop=true.\n"
        raw = ""
        parsed: dict[str, Any] = {}
        err = ""
        final_payload: dict[str, Any] = {}
        final_spec = allowed_specs.get("final_answer")
        for attempt in range(1, 3):
            prompt = forced_prompt
            if attempt == 2:
                prompt = forced_prompt + "\n" + self._final_answer_rejection_feedback(err, state, mode)
            raw = self.llm.complete(
                prompt=prompt,
                caller=f"react_agent.step{effective_max_steps + 1}.force_final.attempt{attempt}",
                system_prompt="You are a constrained medical ReAct planner. Output strict JSON only.",
            )
            parsed, err = parse_react_json(raw)
            if err:
                continue
            if str(parsed.get("action", "")) != "final_answer":
                err = "forced_final must use action=final_answer"
                continue
            candidate_payload = parsed.get("args", {})
            if not isinstance(candidate_payload, dict):
                err = "final_answer args must be object"
                continue
            if final_spec is not None:
                ok, schema_error = final_spec.validate_args(candidate_payload)
                if not ok:
                    err = schema_error
                    continue
            ok, protocol_err, _, _ = self._validate_final_answer_payload(candidate_payload, protocol, state)
            if not ok:
                err = protocol_err
                continue
            final_payload = candidate_payload
            err = ""
            break
        if not final_payload:
            parser_warnings.append("final_answer_schema_failed")
            final_payload = {
                "answer": "Partial result: max steps reached before complete evidence synthesis.",
                "diagnoses": [],
                "evidence_used": [],
                "limitations": ["max_steps_reached", err or "invalid_forced_final_answer_payload"],
            }
        links = []
        if isinstance(final_payload.get("evidence_used"), list):
            links = [str(x.get("observation_id", "")).strip() for x in final_payload["evidence_used"] if isinstance(x, dict)]
        step = ReActStep(
            step_index=effective_max_steps + 1,
            thought=str(parsed.get("thought", "force final answer")) if isinstance(parsed, dict) else "force final answer",
            action_name="final_answer",
            action_args=final_payload,
            action_valid=True,
            observation={"final_answer": final_payload},
            observation_summary=summarize_observation(final_payload, 220),
            success=True,
            error="",
            llm_call_id=f"llm_{len(self.logger.llm_calls):04d}",
            tool_call_ids=[],
            latency_ms=0.0,
            mode=mode,
            input_summary=summarize_observation(task, 120),
            protocol_validation={"accepted": True, "forced_final": True},
            rejected_action_reason="",
            repeat_action_detected=False,
            no_new_information=False,
            observation_id="",
            observation_delta_summary="forced_final_answer",
            required_actions_satisfied=self._required_actions_satisfied(state, protocol),
            final_answer_evidence_links=links,
        )
        steps.append(step)
        state.used_actions.append("final_answer")
        state.final_answer = final_payload
        state.observations.append({"step_index": effective_max_steps + 1, "action_name": "final_answer", "success": True, "observation": {"final_answer": final_payload}})
        self.logger.log_workflow_event(
            "react_agent_step",
            {
                "step_index": effective_max_steps + 1,
                "prompt": forced_prompt,
                "llm_raw_output": raw,
                "parsed": parsed,
                "validation_result": {"ok": True, "error": ""},
                "protocol_validation": step.protocol_validation,
                "executed_action": "final_answer",
                "observation": {"final_answer": final_payload},
                "final_answer_evidence_links": links,
                "next_step_context": self._build_context(state, allowed_specs, protocol),
            },
        )
        return ReActResult(
            status="partial",
            final_answer=state.final_answer,
            react_steps=[s.to_dict() for s in steps],
            observations=state.observations,
            used_actions=state.used_actions,
            failed_actions=failed_actions,
            tool_effective_status=self._tool_effective_status(steps),
            log_path=str(self.logger.run_dir),
            best_raw_final_answer=best_raw_final_answer,
            parser_warnings=parser_warnings,
        )

    def run_official_deeprare_single_step(
        self,
        *,
        case_id: str,
        phenotype_text: str,
        system_prompt: str,
        prompt: str,
        diagnoses: list[str],
        auxiliary_observations: list[dict[str, Any]] | None = None,
        caller_prefix: str = "react_agent_official",
    ) -> ReActResult:
        raw_answer = self.llm.complete(
            prompt=prompt,
            caller=f"{caller_prefix}.step1.final_answer",
            system_prompt=system_prompt,
        )
        final_payload = {
            "answer": raw_answer,
            "diagnoses": [str(x).strip() for x in diagnoses if str(x).strip()][:5],
            "evidence_used": [],
            "limitations": [],
        }
        observations = list(auxiliary_observations or [])
        for idx, obs in enumerate(observations, start=1):
            if not isinstance(obs, dict):
                continue
            if not str(obs.get("observation_id", "")).strip():
                obs["observation_id"] = f"obs_{idx:04d}"
        if observations:
            final_payload["evidence_used"] = [
                {
                    "observation_id": str(obs.get("observation_id", "")).strip(),
                    "component": str(obs.get("action_name", "")).strip() or "auxiliary_tool",
                    "claim": str(obs.get("observation_summary", "")).strip() or "Auxiliary retrieval context.",
                }
                for obs in observations
                if isinstance(obs, dict) and str(obs.get("observation_id", "")).strip()
            ][:5]
        else:
            final_payload["limitations"] = ["no external evidence used"]
        step = ReActStep(
            step_index=1,
            thought="DeepRare official-compatible single-step finalization.",
            action_name="final_answer",
            action_args=final_payload,
            action_valid=True,
            observation={"final_answer": final_payload},
            observation_summary=summarize_observation(final_payload, 220),
            success=True,
            error="",
            llm_call_id=f"llm_{len(self.logger.llm_calls):04d}",
            tool_call_ids=[],
            latency_ms=0.0,
            mode="benchmark_deeprare_official",
            input_summary=summarize_observation(phenotype_text, 120),
            protocol_validation={"accepted": True, "deeprare_official_single_step": True},
            rejected_action_reason="",
            repeat_action_detected=False,
            no_new_information=False,
            observation_id="",
            observation_delta_summary="final_answer",
            required_actions_satisfied=True,
            final_answer_evidence_links=[
                str(x.get("observation_id", "")).strip()
                for x in final_payload.get("evidence_used", [])
                if isinstance(x, dict) and str(x.get("observation_id", "")).strip()
            ],
        )
        self.logger.log_workflow_event(
            "react_agent_step",
            {
                "step_index": 1,
                "mode": "benchmark_deeprare_official",
                "case_id": case_id,
                "prompt": prompt,
                "executed_action": "final_answer",
                "observation": {"final_answer": final_payload},
                "protocol_validation": {"accepted": True, "deeprare_official_single_step": True},
            },
        )
        return ReActResult(
            status="success",
            final_answer=final_payload,
            react_steps=[step.to_dict()],
            observations=observations + [{"step_index": 1, "action_name": "final_answer", "success": True, "observation": {"final_answer": final_payload}}],
            used_actions=["final_answer"],
            failed_actions=[],
            tool_effective_status="not_applicable",
            log_path=str(self.logger.run_dir),
            best_raw_final_answer=raw_answer,
            parser_warnings=[],
        )
