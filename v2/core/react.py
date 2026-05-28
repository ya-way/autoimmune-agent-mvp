from __future__ import annotations

from time import perf_counter
from typing import Any

from v2.core.llm import LLMClient
from v2.core.logger import V2RunLogger
from v2.schemas import BenchmarkItem, ReActStep
from v2.skills import SKILLS


class ReActRunner:
    """Legacy fixed-pipeline runner (not the primary constrained ReAct path)."""

    def __init__(self, mode: str, available_tools: list[str]) -> None:
        self.mode = mode
        self.available_tools = available_tools

    def run(self, item: BenchmarkItem, llm: LLMClient, logger: V2RunLogger) -> dict[str, Any]:
        steps: list[ReActStep] = []
        mode = self.mode
        if mode in {"legacy_react_without_tool", "fixed_without_tool"}:
            mode = "react_without_tool"
        if mode in {"legacy_react_with_tool", "fixed_with_tool"}:
            mode = "react_with_tool"
        if mode == "react_without_tool":
            answer = self._step_final_answer_only(item=item, llm=llm, logger=logger, steps=steps)
            return {"raw_answer": answer, "steps": steps, "evidence": None}
        if mode == "react_with_tool":
            evidence = self._step_evidence(item=item, logger=logger, steps=steps)
            answer = self._step_final_answer(item=item, llm=llm, logger=logger, evidence=evidence, steps=steps)
            return {"raw_answer": answer, "steps": steps, "evidence": evidence}
        raise ValueError(f"Unsupported react mode: {self.mode}")

    def _step_final_answer_only(
        self,
        item: BenchmarkItem,
        llm: LLMClient,
        logger: V2RunLogger,
        steps: list[ReActStep],
    ) -> str:
        start = perf_counter()
        try:
            answer = SKILLS["deeprare_answering"](
                item=item,
                llm=llm,
                logger=logger,
                caller="react_without_tool.step1.deeprare_answering",
                evidence_text="",
            )
            latency_ms = round((perf_counter() - start) * 1000, 2)
            step = ReActStep(
                step_id=1,
                mode=self.mode,
                action="skill:final_answer",
                input_summary=item.phenotype_text[:120],
                observation_summary=answer[:140],
                latency_ms=latency_ms,
                success=True,
                error="",
            )
            steps.append(step)
            logger.log_react_step(step)
            return answer
        except Exception as exc:
            latency_ms = round((perf_counter() - start) * 1000, 2)
            step = ReActStep(
                step_id=1,
                mode=self.mode,
                action="skill:final_answer",
                input_summary=item.phenotype_text[:120],
                observation_summary="",
                latency_ms=latency_ms,
                success=False,
                error=str(exc),
            )
            steps.append(step)
            logger.log_react_step(step)
            raise

    def _step_evidence(self, item: BenchmarkItem, logger: V2RunLogger, steps: list[ReActStep]) -> dict[str, Any]:
        start = perf_counter()
        try:
            enable_web_tool = "web_search" in self.available_tools
            evidence = SKILLS["evidence_retrieval"](
                item=item,
                logger=logger,
                caller="react_with_tool.step1.evidence_retrieval",
                enable_web_tool=enable_web_tool,
            )
            latency_ms = round((perf_counter() - start) * 1000, 2)
            summary = evidence.get("evidence_summary", "")
            step = ReActStep(
                step_id=1,
                mode=self.mode,
                action="skill:evidence_retrieval -> tools:database/hpo/pubmed/web",
                input_summary=item.phenotype_text[:120],
                observation_summary=summary[:140],
                latency_ms=latency_ms,
                success=True,
                error="",
            )
            steps.append(step)
            logger.log_react_step(step)
            return evidence
        except Exception as exc:
            latency_ms = round((perf_counter() - start) * 1000, 2)
            step = ReActStep(
                step_id=1,
                mode=self.mode,
                action="skill:evidence_retrieval -> tools:database/hpo/pubmed/web",
                input_summary=item.phenotype_text[:120],
                observation_summary="",
                latency_ms=latency_ms,
                success=False,
                error=str(exc),
            )
            steps.append(step)
            logger.log_react_step(step)
            raise

    def _step_final_answer(
        self,
        item: BenchmarkItem,
        llm: LLMClient,
        logger: V2RunLogger,
        evidence: dict[str, Any],
        steps: list[ReActStep],
    ) -> str:
        start = perf_counter()
        try:
            evidence_text = evidence.get("evidence_summary", "")
            answer = SKILLS["final_answer"](
                item=item,
                llm=llm,
                logger=logger,
                caller="react_with_tool.step2.final_answer",
                evidence_text=evidence_text,
            )
            latency_ms = round((perf_counter() - start) * 1000, 2)
            step = ReActStep(
                step_id=2,
                mode=self.mode,
                action="skill:final_answer",
                input_summary=evidence_text[:120],
                observation_summary=answer[:140],
                latency_ms=latency_ms,
                success=True,
                error="",
            )
            steps.append(step)
            logger.log_react_step(step)
            return answer
        except Exception as exc:
            latency_ms = round((perf_counter() - start) * 1000, 2)
            step = ReActStep(
                step_id=2,
                mode=self.mode,
                action="skill:final_answer",
                input_summary=str(evidence)[:120],
                observation_summary="",
                latency_ms=latency_ms,
                success=False,
                error=str(exc),
            )
            steps.append(step)
            logger.log_react_step(step)
            raise
