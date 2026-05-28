from __future__ import annotations

import json
from typing import Any


def strip_json_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[len("```json") :].strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned[len("```") :].strip()
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()
    return cleaned


def parse_react_json(raw_output: str) -> tuple[dict[str, Any], str]:
    try:
        parsed = json.loads(strip_json_fence(raw_output))
    except Exception as exc:
        return {}, f"invalid_json: {exc}"
    if not isinstance(parsed, dict):
        return {}, "invalid_json: payload must be object"
    thought = str(parsed.get("thought", "")).strip()
    action = str(parsed.get("action", "")).strip()
    args = parsed.get("args", {})
    stop = bool(parsed.get("stop", False))
    if not isinstance(args, dict):
        return {}, "invalid_json: args must be object"
    if not action:
        return {}, "invalid_json: missing action"
    return {"thought": thought, "action": action, "args": args, "stop": stop}, ""


def summarize_observation(observation: Any, max_len: int = 220) -> str:
    text = str(observation).replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def build_action_protocol_prompt(
    *,
    task: str,
    mode: str,
    state_context: dict[str, Any],
    allowed_actions: list[dict[str, Any]],
    step_index: int,
    max_steps: int,
) -> str:
    protocol_name = str((state_context.get("protocol", {}) or {}).get("name", "")).strip().lower()
    is_deeprare = mode == "benchmark_deeprare"
    is_with_tool = protocol_name == "benchmark_react_agent_with_tool"
    is_without_tool = protocol_name == "benchmark_react_agent_without_tool"
    final_answer_example_benchmark = {
        "thought": "I have enough evidence to provide a ranked differential.",
        "action": "final_answer",
        "args": {
            "diagnoses": ["Disease A", "Disease B", "Disease C", "Disease D", "Disease E"],
            "answer": "1. Disease A\n2. Disease B\n3. Disease C\n4. Disease D\n5. Disease E",
            "evidence_used": [
                {
                    "observation_id": "obs_0001",
                    "component": "hpo_search",
                    "claim": "Phenotype terms were normalized.",
                }
            ],
            "limitations": ["Evidence is limited to available phenotype and retrieved sources."],
        },
        "stop": True,
    }
    final_answer_example_ask = {
        "thought": "I have enough observations to synthesize an answer safely.",
        "action": "final_answer",
        "args": {
            "diagnoses": ["Disease A", "Disease B", "Disease C", "Disease D", "Disease E"],
            "answer": "Natural-language synthesis based on cited observations.",
            "evidence_used": [
                {
                    "observation_id": "obs_0001",
                    "component": "clinical_evidence_skill",
                    "claim": "Observed features support the leading differential.",
                }
            ],
            "limitations": ["Answer is constrained by available observations and retrieved evidence."],
        },
        "stop": True,
    }
    deeprare_block = ""
    if is_deeprare:
        deeprare_block = (
            "DeepRare benchmark final-answer requirements:\n"
            "A) You are solving a rare disease diagnosis benchmark.\n"
            "B) Produce exactly five candidate rare disease diagnoses in args.diagnoses.\n"
            "C) Do not output HPO terms, symptoms, mechanisms, departments, or generic placeholders as diagnoses.\n"
            "D) Use canonical disease names where possible; rank best diagnosis first.\n"
            "E) Phenotype text remains primary input; observations are auxiliary support.\n"
        )
        if is_without_tool:
            deeprare_block += (
                "F) No external tool evidence is expected in this mode; evidence_used can be [] and limitations should note no external evidence used.\n"
            )
        if is_with_tool:
            deeprare_block += (
                "F) Use observations only as supporting evidence; do not let sparse/empty observations override phenotype_text.\n"
            )
        deeprare_block += "\n"
    return (
        "You are a constrained ReAct agent.\n"
        "Protocol requirements:\n"
        "1) Choose exactly one action from allowed_actions.\n"
        "2) Do not invent tools or actions.\n"
        "3) Use prior observations before deciding next action.\n"
        "4) Return strict JSON only.\n"
        "5) If enough evidence exists, choose action=final_answer and stop=true.\n"
        "6) final_answer args must include: answer, diagnoses (top 5 list), evidence_used, limitations.\n\n"
        "7) For drug names and entities, do not invent unseen entities; use user/extracted/observed entities.\n"
        "8) If key entities are missing, use final_answer to request clarification.\n"
        "9) Explicitly reason: what information is still missing, why selected action is best, and why final_answer now/not now.\n"
        "10) Do not repeat same action with same args.\n"
        "11) In final_answer, cite observation IDs in evidence_used entries.\n\n"
        f"mode={mode}\n"
        f"step_index={step_index}\n"
        f"max_steps={max_steps}\n"
        f"task={task}\n\n"
        f"{deeprare_block}"
        f"state_context={json.dumps(state_context, ensure_ascii=False)}\n\n"
        f"allowed_actions={json.dumps(allowed_actions, ensure_ascii=False)}\n\n"
        "final_answer JSON example (benchmark style, schema only):\n"
        f"{json.dumps(final_answer_example_benchmark, ensure_ascii=False)}\n\n"
        "final_answer JSON example (ask style, schema only):\n"
        f"{json.dumps(final_answer_example_ask, ensure_ascii=False)}\n\n"
        "Output JSON schema:\n"
        "{\n"
        '  "thought": "brief reasoning",\n'
        '  "action": "one_allowed_action_name",\n'
        '  "args": {},\n'
        '  "stop": false\n'
        "}\n"
    )
