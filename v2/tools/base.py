from __future__ import annotations

import re
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable

from v2.core.logger import V2RunLogger


@dataclass
class ToolResult:
    source: str
    results: list[Any]


def normalize_answer(raw_answer: str, top_k: int = 5, logger: V2RunLogger | None = None) -> list[str]:
    start = perf_counter()
    lines = [line.strip() for line in raw_answer.splitlines() if line.strip()]
    results: list[str] = []
    for line in lines:
        cleaned = re.sub(r"^\d+[\)\.\:\-]\s*", "", line).strip()
        cleaned = re.sub(r"^#+\s*", "", cleaned).strip()
        cleaned = re.sub(r"\(Rank\s*#?\d+/?\d*\)", "", cleaned, flags=re.IGNORECASE).strip()
        if cleaned:
            results.append(cleaned)
        if len(results) >= top_k:
            break
    if not results:
        chunks = re.split(r"[;\n,]+", raw_answer)
        for chunk in chunks:
            c = chunk.strip()
            if c:
                results.append(c)
            if len(results) >= top_k:
                break
    if logger is not None:
        latency_ms = round((perf_counter() - start) * 1000, 2)
        logger.log_tool_call(
            "normalize_answer",
            {"raw_answer_preview": raw_answer[:240], "top_k": top_k},
            {"prediction_topk": results[:top_k]},
            latency_ms,
            True,
            "",
        )
    return results[:top_k]


def build_tool_registry(*entries: tuple[str, Callable[..., dict[str, Any]]]) -> dict[str, Callable[..., dict[str, Any]]]:
    return {name: fn for name, fn in entries}
