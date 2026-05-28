from __future__ import annotations

from time import perf_counter
from typing import Any

from v2.config import V2Config
from v2.core.logger import V2RunLogger
from v2.tools import TOOLS


def phenotype_normalization_skill(
    phenotype_text: str,
    logger: V2RunLogger,
    caller: str = "skill.phenotype_normalization",
    top_k: int = 5,
    config: V2Config | None = None,
) -> dict[str, Any]:
    start = perf_counter()
    tool_output = TOOLS["hpo_search"](query=phenotype_text, logger=logger, caller=caller, top_k=top_k, config=config)
    candidates = tool_output.get("results") or []
    output = {
        "source": tool_output.get("source", "nlm_clinicaltables_hpo"),
        "query": phenotype_text,
        "result_count": len(candidates),
        "hpo_candidates": candidates,
    }
    latency_ms = round((perf_counter() - start) * 1000, 2)
    logger.log_skill_call(
        "phenotype_normalization_skill",
        {"phenotype_text": phenotype_text[:240], "top_k": top_k, "caller": caller},
        output,
        latency_ms,
        True,
        "",
    )
    return output
