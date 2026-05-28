from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from v2.config import get_config
from v2.schemas import BenchmarkPrediction, ReActStep


class V2RunLogger:
    def __init__(self, mode: str) -> None:
        cfg = get_config()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.run_id = f"{ts}_{mode}"
        self.run_dir = cfg.logs_root / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self.created_at = self._ts()
        self.llm_calls: list[dict[str, Any]] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.skill_calls: list[dict[str, Any]] = []
        self.react_steps: list[dict[str, Any]] = []
        self.predictions: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self._full_events: list[dict[str, Any]] = []
        self._event_seq = 0
        self._last_event_by_caller: dict[str, str] = {}
        self._special_event_ids: dict[str, str] = {}

    def _ts(self) -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")

    def _next_event_id(self) -> str:
        self._event_seq += 1
        return f"event_{self._event_seq:04d}"

    def _infer_parent_id(self, caller: str, event_type: str) -> str | None:
        if not caller:
            return None
        if caller.startswith("react_agent.step"):
            # Prefer attaching react internals to request root for a stable tree.
            return self._special_event_ids.get("raw_request")
        if event_type != "tool_call" and caller in self._last_event_by_caller:
            return self._last_event_by_caller[caller]
        parts = caller.split(".")
        for i in range(len(parts) - 1, 0, -1):
            prefix = ".".join(parts[:i])
            if prefix in self._last_event_by_caller:
                return self._last_event_by_caller[prefix]
        return None

    def _add_full_event(
        self,
        *,
        event_type: str,
        component: str,
        caller: str,
        input_payload: Any,
        output_payload: Any,
        latency_ms: float,
        success: bool,
        error: str,
        metadata: dict[str, Any] | None = None,
        parent_id: str | None = None,
    ) -> None:
        event_id = self._next_event_id()
        event = {
            "event_id": event_id,
            "parent_id": parent_id if parent_id is not None else self._infer_parent_id(caller, event_type),
            "event_type": event_type,
            "component": component,
            "timestamp": self._ts(),
            "latency_ms": float(latency_ms),
            "success": bool(success),
            "input": input_payload if input_payload is not None else {},
            "output": output_payload if output_payload is not None else {},
            "error": error or "",
            "metadata": metadata or {},
            "caller": caller or "",
        }
        self._full_events.append(event)
        if caller:
            self._last_event_by_caller[caller] = event_id
        if event_type == "raw_request":
            self._special_event_ids["raw_request"] = event_id
        subtype = str((metadata or {}).get("subtype", ""))
        if subtype == "parsed_intent":
            self._special_event_ids["parsed_intent"] = event_id
        if subtype == "direct_answer":
            self._special_event_ids["direct_answer"] = event_id

    def _append_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append({"timestamp": self._ts(), "event_type": event_type, "payload": payload})

    def log_workflow_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self._append_event(event_type=event_type, payload=payload)
        lowered = event_type.strip().lower()
        event_type_map = {
            "raw_request": "raw_request",
            "parsed_intent": "intent_parse",
            "direct_answer": "direct_answer",
            "warning": "error",
            "error": "error",
        }
        full_type = event_type_map.get(lowered, lowered if lowered else "workflow")
        success = lowered not in {"warning", "error"}
        parent_id: str | None = None
        if full_type == "raw_request":
            parent_id = None
        elif full_type == "intent_parse":
            parent_id = self._special_event_ids.get("raw_request")
        elif full_type == "direct_answer":
            parent_id = self._special_event_ids.get("parsed_intent") or self._special_event_ids.get("raw_request")
        elif lowered == "react_agent_step":
            parent_id = self._special_event_ids.get("raw_request")
        caller = ""
        if full_type in {"raw_request", "intent_parse", "direct_answer"}:
            caller = "router"
        elif lowered == "react_agent_step":
            step_idx = payload.get("step_index") if isinstance(payload, dict) else ""
            caller = f"react_agent.step{step_idx}" if step_idx else "react_agent.step"
        self._add_full_event(
            event_type=full_type,
            component=event_type,
            caller=caller,
            input_payload=payload if full_type == "raw_request" else {},
            output_payload=payload if full_type != "raw_request" else {},
            latency_ms=float(payload.get("latency_ms", 0.0)) if isinstance(payload, dict) else 0.0,
            success=success,
            error=str(payload.get("message", "")) if (isinstance(payload, dict) and not success) else "",
            metadata={"subtype": event_type},
            parent_id=parent_id,
        )

    def log_llm_call(
        self,
        caller: str,
        prompt: str,
        messages: list[dict[str, str]],
        output: str,
        latency_ms: float,
        success: bool,
        error: str = "",
        usage: dict[str, Any] | None = None,
    ) -> None:
        entry = {
            "timestamp": self._ts(),
            "caller": caller,
            "prompt": prompt,
            "messages": messages,
            "output": output,
            "latency_ms": latency_ms,
            "success": success,
            "error": error,
            "usage": usage or {},
        }
        self.llm_calls.append(entry)
        self._add_full_event(
            event_type="llm_call",
            component=caller or "llm_call",
            caller=caller,
            input_payload={"prompt": prompt, "messages": messages},
            output_payload={"raw_output": output, "usage": usage or {}},
            latency_ms=latency_ms,
            success=success,
            error=error,
        )

    def log_tool_call(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        output: Any,
        latency_ms: float,
        success: bool,
        error: str = "",
    ) -> None:
        entry = {
            "timestamp": self._ts(),
            "tool_name": tool_name,
            "input": tool_input,
            "output": output,
            "latency_ms": latency_ms,
            "success": success,
            "error": error,
        }
        self.tool_calls.append(entry)
        caller = str(tool_input.get("caller", "")) if isinstance(tool_input, dict) else ""
        self._add_full_event(
            event_type="tool_call",
            component=tool_name,
            caller=caller,
            input_payload=tool_input,
            output_payload=output,
            latency_ms=latency_ms,
            success=success,
            error=error,
        )

    def log_skill_call(
        self,
        skill_name: str,
        skill_input: dict[str, Any],
        output: Any,
        latency_ms: float,
        success: bool,
        error: str = "",
    ) -> None:
        entry = {
            "timestamp": self._ts(),
            "skill_name": skill_name,
            "input": skill_input,
            "output": output,
            "latency_ms": latency_ms,
            "success": success,
            "error": error,
        }
        self.skill_calls.append(entry)
        caller = str(skill_input.get("caller", "")) if isinstance(skill_input, dict) else ""
        self._add_full_event(
            event_type="skill_call",
            component=skill_name,
            caller=caller,
            input_payload=skill_input,
            output_payload=output,
            latency_ms=latency_ms,
            success=success,
            error=error,
        )

    def log_react_step(self, step: ReActStep) -> None:
        step_dict = step.to_dict()
        self.react_steps.append(step_dict)
        self._add_full_event(
            event_type="workflow_call",
            component="react_step",
            caller=f"react.{step.mode}.step{step.step_id}",
            input_payload={"action": step.action, "input_summary": step.input_summary},
            output_payload={"observation_summary": step.observation_summary},
            latency_ms=step.latency_ms,
            success=step.success,
            error=step.error,
            metadata={"step_id": step.step_id},
        )

    def add_prediction(self, prediction: BenchmarkPrediction) -> None:
        self.predictions.append(prediction.to_dict())

    def _derive_run_level_fields(self) -> dict[str, Any]:
        raw_request = ""
        intent = ""
        routed_to = ""
        answer_preview = ""
        answer_status = ""
        for event in self._full_events:
            subtype = str((event.get("metadata") or {}).get("subtype", ""))
            output = event.get("output", {}) if isinstance(event.get("output"), dict) else {}
            input_payload = event.get("input", {}) if isinstance(event.get("input"), dict) else {}
            if subtype == "raw_request":
                raw_request = str(input_payload.get("raw_input", "")) or str(output.get("raw_input", ""))
            elif subtype == "parsed_intent":
                intent = str(output.get("intent", ""))
                routed_to = str(output.get("routed_to", routed_to))
            elif subtype == "direct_answer":
                answer = output.get("direct_answer")
                if isinstance(answer, dict):
                    answer_preview = str(answer.get("answer", ""))[:500]
                    routed_to = str(answer.get("routed_to", routed_to))
                    answer_status = str(answer.get("status", "")).strip()
                else:
                    answer_preview = str(output.get("answer_preview", ""))[:500]
        return {
            "input_summary": raw_request[:500],
            "intent": intent,
            "routed_to": routed_to,
            "answer_preview": answer_preview,
            "answer_status": answer_status,
        }

    def _derive_counts_and_latency(self) -> tuple[dict[str, int], dict[str, float], dict[str, list[str]], list[str], list[str], str]:
        llm_total = sum(float(e.get("latency_ms", 0.0)) for e in self._full_events if e.get("event_type") == "llm_call")
        tool_total = sum(float(e.get("latency_ms", 0.0)) for e in self._full_events if e.get("event_type") == "tool_call")
        skill_total = sum(float(e.get("latency_ms", 0.0)) for e in self._full_events if e.get("event_type") == "skill_call")
        total_ms = sum(float(e.get("latency_ms", 0.0)) for e in self._full_events)
        wall_time_ms = total_ms
        if self._full_events:
            try:
                created = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
                latest = max(
                    datetime.fromisoformat(str(e.get("timestamp", "")).replace("Z", "+00:00"))
                    for e in self._full_events
                    if str(e.get("timestamp", "")).strip()
                )
                wall_time_ms = max(0.0, (latest - created).total_seconds() * 1000.0)
            except Exception:
                wall_time_ms = total_ms
        errors = [e for e in self._full_events if not bool(e.get("success", True))]
        failed_components = list(dict.fromkeys([str(e.get("component", "")) for e in errors if str(e.get("component", ""))]))
        warnings = [str(e.get("error", "")) for e in errors if str(e.get("error", "")).strip()]
        status = "success"
        if errors:
            has_answer = any(e.get("event_type") == "direct_answer" and bool(e.get("success")) for e in self._full_events)
            status = "partial" if has_answer else "failed"
        counts = {
            "llm_calls": len(self.llm_calls),
            "tool_calls": len(self.tool_calls),
            "skill_calls": len(self.skill_calls),
            "react_steps": len([e for e in self._full_events if e.get("event_type") == "react_agent_step"]),
            "workflow_calls": len([e for e in self._full_events if e.get("event_type") == "workflow_call"]),
            "errors": len(errors),
        }
        latency = {
            "wall_time_ms": round(wall_time_ms, 2),
            "sum_llm_latency_ms": round(llm_total, 2),
            "sum_tool_latency_ms": round(tool_total, 2),
            "sum_skill_latency_ms": round(skill_total, 2),
            "note": "sum_* fields are cumulative nested latencies and may exceed wall time.",
            "deprecated_total_ms": round(total_ms, 2),
        }
        react_actions = []
        for e in self._full_events:
            if e.get("event_type") != "react_agent_step":
                continue
            out = e.get("output", {}) if isinstance(e.get("output"), dict) else {}
            parsed = out.get("parsed", {}) if isinstance(out.get("parsed", {}), dict) else {}
            action = str(out.get("executed_action", parsed.get("action", ""))).strip()
            if action:
                react_actions.append(action)
        components = {
            "llms": sorted(list(dict.fromkeys([str(e.get("component")) for e in self._full_events if e.get("event_type") == "llm_call"]))),
            "tools": sorted(list(dict.fromkeys([str(e.get("component")) for e in self._full_events if e.get("event_type") == "tool_call"]))),
            "skills": sorted(list(dict.fromkeys([str(e.get("component")) for e in self._full_events if e.get("event_type") == "skill_call"]))),
            "workflows": sorted(
                list(dict.fromkeys([str(e.get("component")) for e in self._full_events if e.get("event_type") == "workflow_call"]))
            ),
            "react_actions": sorted(list(dict.fromkeys(react_actions))),
        }
        return counts, latency, components, failed_components, warnings, status

    def _build_full_trace(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "events": self._full_events}

    def _build_full_trace_markdown(self, trace: dict[str, Any], status: str, total_latency_ms: float) -> str:
        lines = [
            "# Full Trace",
            "",
            "## Run Metadata",
            f"- run_id: `{self.run_id}`",
            f"- mode: `{self.mode}`",
            f"- created_at: `{self.created_at}`",
            f"- status: `{status}`",
            f"- total latency: `{round(total_latency_ms, 2)} ms`",
            "",
            "## Event Timeline",
            "",
            "| ID | Type | Component | Parent | Status | Latency |",
            "|---|---|---|---|---|---|",
        ]
        events = trace.get("events", []) if isinstance(trace.get("events"), list) else []
        for event in events:
            lines.append(
                f"| {event.get('event_id', '')} | {event.get('event_type', '')} | {event.get('component', '')} | "
                f"{event.get('parent_id', '') or '-'} | {'success' if event.get('success') else 'error'} | "
                f"{round(float(event.get('latency_ms', 0.0)), 2)} |"
            )
        lines.extend(["", "## Detailed Events", ""])
        for event in events:
            lines.extend(
                [
                    f"### {event.get('event_id', '')} / {event.get('event_type', '')} / {event.get('component', '')}",
                    f"- Timestamp: `{event.get('timestamp', '')}`",
                    f"- Parent: `{event.get('parent_id', '') or 'null'}`",
                    f"- Caller: `{event.get('caller', '')}`",
                    f"- Success: `{event.get('success', False)}`",
                    f"- Latency: `{round(float(event.get('latency_ms', 0.0)), 2)} ms`",
                    f"- Error: `{event.get('error', '')}`",
                    "",
                    "Input:",
                    "```json",
                    json.dumps(event.get("input", {}), ensure_ascii=False, indent=2),
                    "```",
                    "",
                    "Output:",
                    "```json",
                    json.dumps(event.get("output", {}), ensure_ascii=False, indent=2),
                    "```",
                    "",
                    "Metadata:",
                    "```json",
                    json.dumps(event.get("metadata", {}), ensure_ascii=False, indent=2),
                    "```",
                    "",
                ]
            )
        return "\n".join(lines)

    def _build_calls_jsonl(self) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        seq = 0
        for event in self._full_events:
            if event.get("event_type") not in {"tool_call", "llm_call"}:
                continue
            seq += 1
            ts_end = str(event.get("timestamp", ""))
            latency_ms = float(event.get("latency_ms", 0.0))
            ts_start = ts_end
            try:
                dt_end = datetime.fromisoformat(ts_end.replace("Z", "+00:00"))
                dt_start = dt_end - timedelta(milliseconds=latency_ms)
                ts_start = dt_start.isoformat().replace("+00:00", "Z")
            except Exception:
                ts_start = ts_end
            etype = "tool" if event.get("event_type") == "tool_call" else "llm"
            call = {
                "call_id": f"call_{seq:04d}",
                "parent_id": event.get("parent_id"),
                "type": etype,
                "component": event.get("component", ""),
                "caller": event.get("caller", ""),
                "timestamp_start": ts_start,
                "timestamp_end": ts_end,
                "latency_ms": latency_ms,
                "success": bool(event.get("success", False)),
                "error": str(event.get("error", "")),
                "input": event.get("input", {}),
                "output": event.get("output", {}),
                "usage": (event.get("output", {}) or {}).get("usage", {}) if isinstance(event.get("output", {}), dict) else {},
                "metadata": event.get("metadata", {}),
            }
            calls.append(call)
        return calls

    def finalize(self, metrics: dict[str, Any], dataset_source: str) -> Path:
        run_fields = self._derive_run_level_fields()
        counts, latency, components, failed_components, warnings, status = self._derive_counts_and_latency()
        answer_status = str(run_fields.get("answer_status", "")).strip()
        if answer_status in {"partial", "needs_clarification"} and status == "success":
            status = "partial"
        if answer_status == "failed":
            status = "failed"
        trace = self._build_full_trace()
        calls = self._build_calls_jsonl()

        full_trace_json_path = self.run_dir / "full_trace.json"
        full_trace_md_path = self.run_dir / "full_trace.md"
        calls_jsonl_path = self.run_dir / "calls.jsonl"
        case_report_path = self.run_dir / "case_report.md"
        summary_path = self.run_dir / "summary.json"
        manifest_path = self.run_dir / "artifacts_manifest.json"
        trace_md_deprecated = self.run_dir / "trace.md"

        full_trace_json_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
        full_trace_md_path.write_text(
            self._build_full_trace_markdown(trace=trace, status=status, total_latency_ms=latency["wall_time_ms"]),
            encoding="utf-8",
        )
        with calls_jsonl_path.open("w", encoding="utf-8") as f:
            for call in calls:
                f.write(json.dumps(call, ensure_ascii=False) + "\n")

        summary = {
            "run_id": self.run_id,
            "mode": self.mode,
            "status": status,
            "created_at": self.created_at,
            "input_summary": run_fields["input_summary"],
            "intent": run_fields["intent"],
            "routed_to": run_fields["routed_to"],
            "answer_preview": run_fields["answer_preview"],
            "counts": counts,
            "latency": latency,
            "components": components,
            "warnings": warnings,
            "failed_components": failed_components,
            "metrics": metrics,
            "dataset_source": dataset_source,
            "artifacts": {
                "calls_jsonl": "calls.jsonl",
                "full_trace_json": "full_trace.json",
                "full_trace_md": "full_trace.md",
                "case_report": "case_report.md",
            },
        }
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

        if self.predictions:
            (self.run_dir / "predictions.json").write_text(
                json.dumps(self.predictions, indent=2, ensure_ascii=False), encoding="utf-8"
            )

        trace_md_deprecated.write_text("This file is deprecated. See full_trace.md.\n", encoding="utf-8")
        manifest = {
            "run_id": self.run_id,
            "files": {
                "summary": "summary.json",
                "calls": "calls.jsonl",
                "full_trace_json": "full_trace.json",
                "full_trace_md": "full_trace.md",
                "case_report": "case_report.md",
            },
            "deprecated_files": ["trace.md"],
            "mode": self.mode,
            "status": status,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        if not case_report_path.exists():
            case_report_path.write_text("# Case Report\n\nPending generation by readable logger.\n", encoding="utf-8")
        return self.run_dir
