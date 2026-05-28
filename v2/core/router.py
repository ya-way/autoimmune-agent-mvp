from __future__ import annotations

import uuid
from typing import Any

from v2.config import get_config
from v2.core.action_protocol import get_protocol
from v2.core.extraction import extract_medical_entities
from v2.core.intent import parse_intent
from v2.core.llm import LLMClient
from v2.core.logger import V2RunLogger
from v2.core.react_agent import ReActAgent
from v2.core.readable_logger import write_readable_log
from v2.schemas import DirectAnswer, IntentResult, UserRequest


def _allowed_actions_for_intent(intent: str, *, low_level_debug: bool = False) -> list[str]:
    return get_protocol(intent, low_level_debug=low_level_debug).allowed_actions


def route_request(user_request: UserRequest) -> DirectAnswer:
    req_id = user_request.request_id or str(uuid.uuid4())
    logger = V2RunLogger(mode="ask")
    logger.log_workflow_event("raw_request", user_request.to_dict())

    intent_result: IntentResult = parse_intent(user_request.raw_input, metadata=user_request.metadata)
    logger.log_workflow_event("parsed_intent", intent_result.to_dict())
    cfg = get_config()
    llm = LLMClient(logger=logger, config=cfg)
    extraction = extract_medical_entities(
        user_request.raw_input,
        llm_client=llm,
        mode="llm_json",
        logger=logger,
        caller=f"router.{intent_result.intent}.extraction",
    )

    low_level_debug = bool(user_request.metadata.get("low_level_debug")) if isinstance(user_request.metadata, dict) else False
    allowed_actions = _allowed_actions_for_intent(intent_result.intent, low_level_debug=low_level_debug)
    react_agent = ReActAgent(llm=llm, logger=logger, config=cfg)
    react_result = react_agent.run(
        task=user_request.raw_input,
        allowed_actions=allowed_actions,
        initial_context={
            "user_request": user_request.to_dict(),
            "intent_result": intent_result.to_dict(),
            "extracted_entities": extraction.get("entities", {}),
            "extraction_status": extraction.get("status", ""),
            "extracted_fields": intent_result.extracted_fields,
            "missing_fields": intent_result.missing_fields,
        },
        max_steps=6,
        mode="ask",
        protocol_name=intent_result.intent,
        low_level_debug=low_level_debug,
    )

    final_payload = react_result.final_answer if isinstance(react_result.final_answer, dict) else {}
    answer = str(final_payload.get("answer", "")).strip() or "No final answer generated."
    limitations = [str(x) for x in (final_payload.get("limitations", []) if isinstance(final_payload.get("limitations", []), list) else [])]
    evidence_used = final_payload.get("evidence_used", []) if isinstance(final_payload.get("evidence_used", []), list) else []
    missing_fields = [str(x) for x in (final_payload.get("missing_fields", []) if isinstance(final_payload.get("missing_fields", []), list) else [])]
    answer_status = str(final_payload.get("status", "")).strip()
    status = answer_status or react_result.status
    if status not in {"success", "partial", "failed", "needs_clarification"}:
        status = react_result.status

    structured_output: dict[str, Any] = {
        "status": status,
        "intent_result": intent_result.to_dict(),
        "allowed_actions": allowed_actions,
        "extraction": extraction,
        "react_result": react_result.to_dict(),
        "final_answer_payload": final_payload,
    }

    direct = DirectAnswer(
        request_id=req_id,
        intent=intent_result.intent,
        routed_to="react_agent",
        status=status,
        answer=answer,
        evidence_summary=" | ".join(
            [
                f"{str(x.get('observation_id', ''))}:{str(x.get('claim', '')).strip()}"
                for x in evidence_used
                if isinstance(x, dict)
            ]
        )
        if evidence_used
        else None,
        safety_notes=[],
        limitations=list(dict.fromkeys(["Not medical advice.", "External API coverage may be incomplete."] + limitations)),
        failed_components=list(dict.fromkeys([str(x) for x in react_result.failed_actions])),
        missing_fields=missing_fields,
        structured_output=structured_output,
        log_path=None,
    )
    logger.log_workflow_event("direct_answer", {"direct_answer": direct.to_dict()})
    run_dir = logger.finalize(
        metrics={
            "interface": "ask",
            "intent": direct.intent,
            "routed_to": direct.routed_to,
            "status": direct.status,
            "missing_fields": direct.missing_fields,
            "failed_components_count": len(direct.failed_components),
            "react_steps": len(react_result.react_steps),
        },
        dataset_source="ask_interface",
    )
    direct.log_path = str(run_dir)
    try:
        direct.readable_log_path = write_readable_log(run_dir=str(run_dir))
        if isinstance(direct.structured_output, dict):
            direct.structured_output["readable_log_path"] = direct.readable_log_path
            direct.structured_output["full_trace_json_path"] = str(run_dir / "full_trace.json")
            direct.structured_output["full_trace_md_path"] = str(run_dir / "full_trace.md")
    except Exception as exc:
        logger.log_workflow_event("warning", {"type": "readable_log_write_failed", "message": str(exc)})
    return direct
