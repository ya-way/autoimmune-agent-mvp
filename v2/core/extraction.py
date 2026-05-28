from __future__ import annotations

import json
from time import perf_counter
from typing import Any

from v2.core.llm import LLMClient
from v2.core.logger import V2RunLogger


def _empty_entities() -> dict[str, Any]:
    return {
        "suspected_diagnoses": [],
        "medications": [],
        "phenotypes": [],
        "safety_focus": [],
        "comorbidities_or_risk_context": [],
        "uncertainties": [],
    }


def _strip_json_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[len("```json") :].strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned[len("```") :].strip()
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()
    return cleaned


def _validate_entities_shape(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("extraction output is not a JSON object")
    base = _empty_entities()
    normalized: dict[str, Any] = {}
    for key in base.keys():
        item = value.get(key, [])
        if not isinstance(item, list):
            normalized[key] = []
            continue
        valid_rows: list[dict[str, Any]] = []
        for row in item:
            if not isinstance(row, dict):
                continue
            text = str(row.get("text", "")).strip()
            if not text:
                continue
            out = {"text": text}
            if key in {"medications"}:
                out["role"] = str(row.get("role", "")).strip() or "mentioned_medication"
                out["dose_modifier"] = str(row.get("dose_modifier", "")).strip()
            conf = row.get("confidence", 0.0)
            try:
                out["confidence"] = max(0.0, min(1.0, float(conf)))
            except Exception:
                out["confidence"] = 0.0
            valid_rows.append(out)
        normalized[key] = valid_rows
    return normalized


def extract_medical_entities(
    raw_input: str,
    *,
    llm_client: LLMClient | None = None,
    mode: str = "llm_json",
    logger: V2RunLogger | None = None,
    caller: str = "router.extraction",
) -> dict[str, Any]:
    start = perf_counter()
    prompt = (
        "Extract structured biomedical entities from the user request.\n"
        "Return strict JSON only, no markdown fence.\n"
        "Do not invent medication names.\n"
        "If a text mentions a drug class (e.g., glucocorticoids), keep it as-is and do not convert to a specific drug.\n\n"
        "Output schema:\n"
        "{\n"
        '  "suspected_diagnoses": [{"text": "...", "confidence": 0.0}],\n'
        '  "medications": [{"text": "...", "role": "candidate_treatment | current_medication | mentioned_medication", "dose_modifier": "...", "confidence": 0.0}],\n'
        '  "phenotypes": [{"text": "...", "confidence": 0.0}],\n'
        '  "safety_focus": [{"text": "...", "confidence": 0.0}],\n'
        '  "comorbidities_or_risk_context": [{"text": "...", "confidence": 0.0}],\n'
        '  "uncertainties": [{"text": "...", "confidence": 0.0}]\n'
        "}\n\n"
        f"User request:\n{raw_input}"
    )
    if mode != "llm_json":
        latency_ms = round((perf_counter() - start) * 1000, 2)
        out = {
            "status": "extraction_failed",
            "error": f"unsupported extraction mode: {mode}",
            "entities": _empty_entities(),
            "raw_output": "",
        }
        if logger is not None:
            logger.log_workflow_event(
                "extraction",
                {
                    "caller": caller,
                    "mode": mode,
                    "raw_input": raw_input,
                    "prompt": prompt,
                    "raw_output": "",
                    "parsed_entities": out["entities"],
                    "status": out["status"],
                    "error": out["error"],
                    "latency_ms": latency_ms,
                },
            )
        return out
    if llm_client is None:
        latency_ms = round((perf_counter() - start) * 1000, 2)
        out = {
            "status": "extraction_failed",
            "error": "llm_client_unavailable",
            "entities": _empty_entities(),
            "raw_output": "",
        }
        if logger is not None:
            logger.log_workflow_event(
                "extraction",
                {
                    "caller": caller,
                    "mode": mode,
                    "raw_input": raw_input,
                    "prompt": prompt,
                    "raw_output": "",
                    "parsed_entities": out["entities"],
                    "status": out["status"],
                    "error": out["error"],
                    "latency_ms": latency_ms,
                },
            )
        return out
    try:
        raw_output = llm_client.complete(
            prompt=prompt,
            caller=f"{caller}.llm_json",
            system_prompt="You are a clinical NLP extractor. Output valid JSON only.",
        )
        parsed = json.loads(_strip_json_fence(raw_output))
        entities = _validate_entities_shape(parsed)
        latency_ms = round((perf_counter() - start) * 1000, 2)
        out = {
            "status": "success",
            "error": "",
            "entities": entities,
            "raw_output": raw_output,
        }
        if logger is not None:
            logger.log_workflow_event(
                "extraction",
                {
                    "caller": caller,
                    "mode": mode,
                    "raw_input": raw_input,
                    "prompt": prompt,
                    "raw_output": raw_output,
                    "parsed_entities": entities,
                    "status": "success",
                    "error": "",
                    "latency_ms": latency_ms,
                },
            )
        return out
    except Exception as exc:
        latency_ms = round((perf_counter() - start) * 1000, 2)
        out = {
            "status": "extraction_failed",
            "error": str(exc),
            "entities": _empty_entities(),
            "raw_output": locals().get("raw_output", ""),
        }
        if logger is not None:
            logger.log_workflow_event(
                "extraction",
                {
                    "caller": caller,
                    "mode": mode,
                    "raw_input": raw_input,
                    "prompt": prompt,
                    "raw_output": out["raw_output"],
                    "parsed_entities": out["entities"],
                    "status": out["status"],
                    "error": out["error"],
                    "latency_ms": latency_ms,
                },
            )
        return out

