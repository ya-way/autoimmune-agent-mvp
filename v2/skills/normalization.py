from __future__ import annotations

from time import perf_counter
from typing import Any

from v2.config import V2Config
from v2.core.logger import V2RunLogger
from v2.tools import TOOLS


def medication_normalization_skill(
    medications: list[dict[str, Any]],
    logger: V2RunLogger,
    caller: str = "skill.medication_normalization",
    config: V2Config | None = None,
) -> dict[str, Any]:
    start = perf_counter()
    normalized_medications: list[dict[str, Any]] = []
    unresolved_medications: list[dict[str, Any]] = []
    for med in medications:
        if not isinstance(med, dict):
            continue
        text = str(med.get("text", "")).strip()
        role = str(med.get("role", "")).strip()
        dose_modifier = str(med.get("dose_modifier", "")).strip()
        if not text:
            continue
        norm = TOOLS["rxnorm_normalize_drug"](
            drug_text=text,
            logger=logger,
            caller=f"{caller}.rxnorm",
            config=config,
        )
        row = {
            "original_text": text,
            "role": role,
            "dose_modifier": dose_modifier,
            "normalized_name": str(norm.get("normalized_name", "")).strip(),
            "rxcui": str(norm.get("rxcui", "")).strip(),
            "match_type": str(norm.get("match_type", "")).strip() or "unresolved",
            "requires_confirmation": bool(norm.get("requires_confirmation", False)),
            "candidates": norm.get("candidates", []),
            "limitations": norm.get("limitations", []),
        }
        normalized_medications.append(row)
        if row["match_type"] in {"unresolved", "class_entity"}:
            unresolved_medications.append(row)
    requires_clarification = any(
        bool(item.get("requires_confirmation"))
        or item.get("match_type") in {"unresolved", "class_entity"}
        for item in normalized_medications
    )
    output = {
        "normalized_medications": normalized_medications,
        "unresolved_medications": unresolved_medications,
        "requires_clarification": requires_clarification,
    }
    latency_ms = round((perf_counter() - start) * 1000, 2)
    logger.log_skill_call(
        "medication_normalization_skill",
        {
            "caller": caller,
            "medication_count": len(medications),
        },
        output,
        latency_ms,
        True,
        "",
    )
    return output

