from __future__ import annotations

import argparse
import csv
import uuid
from pathlib import Path
from time import perf_counter, sleep
from datetime import UTC, datetime

from v2.benchmark.deeprare import _adapt_row_to_benchmark_item
from v2.config import get_config
from v2.core.intent import parse_intent
from v2.core.llm import LLMClient
from v2.core.logger import V2RunLogger
from v2.core.react import ReActRunner
from v2.core.router import route_request
from v2.schemas import BenchmarkItem, UserRequest
from v2.skills import SKILLS
from v2.tools import TOOLS


def main() -> None:
    parser = argparse.ArgumentParser(description="V2 minimal CLI")
    parser.add_argument("--query", type=str, default="")
    parser.add_argument("--mode", type=str, default="plain", choices=["plain", "react_with_tool"])
    parser.add_argument("--ask", type=str, default="")
    parser.add_argument("--check-llm", action="store_true")
    parser.add_argument("--check-search", action="store_true")
    parser.add_argument("--check-rarebench-local", action="store_true")
    parser.add_argument("--check-tool", type=str, default="", choices=["", "pubmed", "hpo", "reactome", "opentargets", "openfda"])
    parser.add_argument("--check-skill", type=str, default="", choices=["", "clinical_evidence", "mechanism_evidence", "drug_safety"])
    parser.add_argument("--check-workflow", type=str, default="", choices=["", "autoimmune_case_review"])
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()

    if args.check_llm:
        run_check_llm()
        return
    if args.ask:
        run_ask(args.ask)
        return
    if args.check_search:
        run_check_search(repeat=max(1, args.repeat))
        return
    if args.check_rarebench_local:
        run_check_rarebench_local()
        return
    if args.check_tool:
        run_check_tool(args.check_tool)
        return
    if args.check_skill:
        run_check_skill(args.check_skill)
        return
    if args.check_workflow:
        run_check_workflow(args.check_workflow)
        return
    if not args.query:
        raise ValueError("--query is required when no check flag is used")

    logger = V2RunLogger(mode=f"cli_{args.mode}")
    llm = LLMClient(logger=logger)
    item = BenchmarkItem(
        case_id="cli_case",
        phenotype_text=args.query,
        phenotype_ids=[],
        phenotype_names=[],
        golden_diagnosis="",
        raw={"source": "cli"},
    )
    if args.mode == "plain":
        answer = SKILLS["deeprare_answering"](item=item, llm=llm, logger=logger, caller="cli.plain")
    else:
        runner = ReActRunner(mode="react_with_tool", available_tools=["web_search"])
        answer = runner.run(item=item, llm=llm, logger=logger)["raw_answer"]
    logger.finalize(
        metrics={
            "recall_at_1": 0.0,
            "recall_at_3": 0.0,
            "recall_at_5": 0.0,
            "invalid_answer_rate": 0.0,
            "avg_llm_calls": float(len(logger.llm_calls)),
            "avg_tool_calls": float(len(logger.tool_calls)),
            "avg_latency_ms": 0.0,
        },
        dataset_source="cli_query",
    )
    print(answer)


def run_ask(raw_input: str) -> None:
    request_id = str(uuid.uuid4())
    parsed = parse_intent(raw_input)
    user_request = UserRequest(
        request_id=request_id,
        raw_input=raw_input,
        input_type="free_text",
        intent=parsed.intent,
        case_text=raw_input,
        suspected_diagnosis=parsed.extracted_fields.get("suspected_diagnosis"),
        candidate_drug=parsed.extracted_fields.get("candidate_drug"),
        phenotypes=list(parsed.extracted_fields.get("phenotypes") or []),
        safety_focus=list(parsed.extracted_fields.get("safety_focus") or []),
        benchmark_config=None,
        metadata={},
    )
    result = route_request(user_request)
    print(f"request_id={request_id}")
    print(f"parsed_intent={parsed.intent}")
    print(f"intent_confidence={parsed.confidence}")
    print(f"intent_reason={parsed.reason_summary}")
    print(f"routed_to={result.routed_to}")
    print(f"direct_answer={result.answer}")
    print(f"evidence_summary={result.evidence_summary}")
    print(f"safety_notes={result.safety_notes}")
    print(f"limitations={result.limitations}")
    print(f"failed_components={result.failed_components}")
    print(f"log_path={result.log_path}")
    print(f"readable_log_path={result.readable_log_path}")


def run_check_llm() -> None:
    cfg = get_config()
    logger = V2RunLogger(mode="cli_check_llm")
    llm = LLMClient(logger=logger, config=cfg)
    prompt = "Return exactly: LLM_OK"
    try:
        raw = llm.complete(prompt=prompt, caller="cli.check_llm")
        print(f"raw_response={raw}")
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", "N/A")
        print(f"[LLM ERROR] type={type(exc).__name__} status={status} message={exc}")


def _call_with_retry_cli(
    *,
    name: str,
    action,
    logger: V2RunLogger,
    max_retries: int = 2,
    retry_delay_seconds: int = 10,
):
    attempts: list[dict[str, object]] = []
    total_attempts = max(1, int(max_retries))
    last_exc: Exception | None = None
    for idx in range(1, total_attempts + 1):
        start = perf_counter()
        try:
            result = action()
            latency_ms = round((perf_counter() - start) * 1000, 2)
            entry = {
                "name": name,
                "attempt_index": idx,
                "success": True,
                "latency_ms": latency_ms,
                "error_message": "",
            }
            attempts.append(entry)
            logger.log_workflow_event("retry_attempt", entry)
            return result, attempts
        except Exception as exc:
            latency_ms = round((perf_counter() - start) * 1000, 2)
            entry = {
                "name": name,
                "attempt_index": idx,
                "success": False,
                "latency_ms": latency_ms,
                "error_message": str(exc),
            }
            attempts.append(entry)
            logger.log_workflow_event("retry_attempt", entry)
            last_exc = exc
            if idx < total_attempts:
                sleep(max(0, int(retry_delay_seconds)))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"{name} failed without exception")


def run_check_search(repeat: int = 1) -> None:
    cfg = get_config()
    logger = V2RunLogger(mode="cli_check_search")
    query = "systemic lupus erythematosus infection mimic"
    attempts: list[dict[str, object]] = []
    for i in range(1, repeat + 1):
        print(f"[TOOL CALL] web_search attempt={i}")
        start = perf_counter()
        attempt: dict[str, object] = {
            "attempt": i,
            "provider": "unknown",
            "success": False,
            "count": 0,
            "latency_ms": 0.0,
            "error_type": "",
            "error_message": "",
            "http_status": "N/A",
            "top_snippets": [],
        }
        try:
            result = TOOLS["web_search"](query=query, logger=logger, caller=f"cli.check_search.attempt_{i}", config=cfg)
            latency_ms = round((perf_counter() - start) * 1000, 2)
            results = result.get("results", []) if isinstance(result, dict) else []
            provider = result.get("source", "unknown") if isinstance(result, dict) else "unknown"
            count = len(results) if isinstance(results, list) else 0
            attempt["provider"] = provider
            attempt["success"] = count > 0
            attempt["count"] = count
            attempt["latency_ms"] = latency_ms
            attempt["top_snippets"] = results[:3] if isinstance(results, list) else []
            print(
                f"[TOOL RESULT] attempt={i} web_search count={count} provider={provider} "
                f"latency_ms={latency_ms} success={count > 0}"
            )
            for idx, item in enumerate((results[:3] if isinstance(results, list) else []), start=1):
                print(f"attempt={i} top{idx}: {item}")
            if count <= 0:
                attempt["error_type"] = "EmptyResultError"
                attempt["error_message"] = "count=0"
                print("[TOOL CHECK] count=0, please verify query/API/provider before benchmark.")
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", "N/A")
            latency_ms = round((perf_counter() - start) * 1000, 2)
            attempt["latency_ms"] = latency_ms
            attempt["error_type"] = type(exc).__name__
            attempt["error_message"] = str(exc)
            attempt["http_status"] = status
            print(
                f"[TOOL ERROR] attempt={i} type={type(exc).__name__} "
                f"status={status} message={exc} latency_ms={latency_ms}"
            )
        attempts.append(attempt)

    success_count = sum(1 for a in attempts if a.get("success"))
    failed_count = len(attempts) - success_count
    _write_connectivity_markdown(query=query, attempts=attempts, success_count=success_count, failed_count=failed_count)
    print(f"[TOOL CHECK SUMMARY] success={success_count} failed={failed_count}")


def _write_connectivity_markdown(
    query: str,
    attempts: list[dict[str, object]],
    success_count: int,
    failed_count: int,
) -> None:
    md_path = Path(__file__).resolve().parent / "logs" / "brave_connectivity_check.md"
    lines = [
        "# Brave Connectivity Check",
        "",
        f"- checked_at: `{datetime.now(UTC).isoformat().replace('+00:00', 'Z')}`",
        f"- query: `{query}`",
        f"- success_count: `{success_count}`",
        f"- failed_count: `{failed_count}`",
        "",
        "| attempt | provider | success | count | latency_ms | http_status | error_type | error_message |",
        "|---:|---|---|---:|---:|---|---|---|",
    ]
    for a in attempts:
        lines.append(
            f"| {a.get('attempt')} | {a.get('provider')} | {a.get('success')} | {a.get('count')} | "
            f"{a.get('latency_ms')} | {a.get('http_status')} | {a.get('error_type')} | "
            f"{str(a.get('error_message', '')).replace('|', '/')} |"
        )
    lines.extend(["", "## Top snippets (first 3 per success attempt)", ""])
    for a in attempts:
        if not a.get("success"):
            continue
        lines.append(f"### attempt {a.get('attempt')}")
        for idx, s in enumerate((a.get("top_snippets") or []), start=1):
            lines.append(f"- top{idx}: {s}")
        lines.append("")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_check_rarebench_local() -> None:
    cfg = get_config()
    csv_path = cfg.rarebench_local_csv or "/home/shuotong/DeepRare/dataset/rarebench_local/rarebench_local_sample.csv"
    fp = Path(csv_path).expanduser().resolve()
    if not fp.exists():
        raise FileNotFoundError(f"RareBench local CSV not found: {fp}")
    with fp.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        columns = list(reader.fieldnames or [])
        first_row: dict[str, str] | None = None
        total = 0
        for idx, row in enumerate(reader):
            total += 1
            if idx == 0:
                first_row = dict(row)
    print(f"total_rows={total}")
    print(f"columns={columns}")
    if first_row is None:
        raise RuntimeError("CSV is empty.")
    mapped = _adapt_row_to_benchmark_item(first_row, row_idx=0, file_stem="rarebench_local")
    if mapped is None:
        raise RuntimeError("Failed to map first row to BenchmarkItem.")
    if not mapped.phenotype_text or not mapped.golden_diagnosis:
        raise RuntimeError("Missing phenotype_text or golden_diagnosis in mapped item.")
    print(f"first_item_mapped={mapped.to_dict()}")
    print("real_data=true")


def run_check_tool(tool_name: str) -> None:
    cfg = get_config()
    logger = V2RunLogger(mode=f"cli_check_tool_{tool_name}")
    tool = tool_name.strip().lower()
    attempt_log: list[dict[str, object]] = []
    if tool == "pubmed":
        query = "systemic lupus erythematosus infection mimic"
        result, attempt_log = _call_with_retry_cli(
            name="check_tool.pubmed",
            action=lambda: TOOLS["pubmed_search"](query=query, logger=logger, caller="cli.check_tool.pubmed", top_k=5, config=cfg),
            logger=logger,
        )
    elif tool == "hpo":
        query = "malar rash"
        result, attempt_log = _call_with_retry_cli(
            name="check_tool.hpo",
            action=lambda: TOOLS["hpo_search"](query=query, term=query, logger=logger, caller="cli.check_tool.hpo", max_results=5, config=cfg),
            logger=logger,
        )
    elif tool == "reactome":
        query = "complement activation"
        result, attempt_log = _call_with_retry_cli(
            name="check_tool.reactome",
            action=lambda: TOOLS["reactome_search"](query=query, logger=logger, caller="cli.check_tool.reactome", top_k=5, config=cfg),
            logger=logger,
        )
    elif tool == "opentargets":
        query = "systemic lupus erythematosus"
        try:
            result, attempt_log = _call_with_retry_cli(
                name="check_tool.opentargets",
                action=lambda: TOOLS["opentargets_search"](
                    disease_query=query,
                    logger=logger,
                    caller="cli.check_tool.opentargets",
                    top_k=5,
                    config=cfg,
                ),
                logger=logger,
            )
        except Exception:
            query = "rheumatoid arthritis"
            result, attempt_log = _call_with_retry_cli(
                name="check_tool.opentargets_retry",
                action=lambda: TOOLS["opentargets_search"](
                    disease_query=query,
                    logger=logger,
                    caller="cli.check_tool.opentargets_retry",
                    top_k=5,
                    config=cfg,
                ),
                logger=logger,
            )
    elif tool == "openfda":
        drug = "prednisone"
        reaction = "infection"
        query = f"drug={drug};reaction={reaction}"
        try:
            result, attempt_log = _call_with_retry_cli(
                name="check_tool.openfda",
                action=lambda: TOOLS["openfda_drug_event_search"](
                    drug=drug,
                    reaction=reaction,
                    limit=10,
                    logger=logger,
                    caller="cli.check_tool.openfda",
                    config=cfg,
                ),
                logger=logger,
            )
        except Exception:
            drug = "methotrexate"
            reaction = "pneumonitis"
            query = f"drug={drug};reaction={reaction}"
            result, attempt_log = _call_with_retry_cli(
                name="check_tool.openfda_retry",
                action=lambda: TOOLS["openfda_drug_event_search"](
                    drug=drug,
                    reaction=reaction,
                    limit=10,
                    logger=logger,
                    caller="cli.check_tool.openfda_retry",
                    config=cfg,
                ),
                logger=logger,
            )
    else:
        raise ValueError(f"Unsupported --check-tool value: {tool_name}")

    call = logger.tool_calls[-1] if logger.tool_calls else {}
    provider = result.get("source", "unknown") if isinstance(result, dict) else "unknown"
    count = len(result.get("results", [])) if isinstance(result, dict) else 0
    if tool == "opentargets" and isinstance(result, dict):
        count = len(result.get("target_associations", []))
    if tool == "openfda" and isinstance(result, dict):
        count = int(result.get("result_count", 0))
    latency_ms = call.get("latency_ms", "N/A")
    success = bool(call.get("success", False))
    error = call.get("error", "")
    print(f"provider={provider}")
    print(f"query={query}")
    print(f"success={success}")
    print(f"latency_ms={latency_ms}")
    print(f"result_count={count}")
    print(f"attempts={attempt_log}")
    if tool == "opentargets":
        print(f"disease_candidates={(result.get('disease_candidates', []) if isinstance(result, dict) else [])[:3]}")
        top_targets = (result.get("target_associations", []) if isinstance(result, dict) else [])[:3]
        for i, item in enumerate(top_targets, start=1):
            print(f"target{i}: {item}")
        top_drugs = (result.get("known_drugs", []) if isinstance(result, dict) else [])[:3]
        for i, item in enumerate(top_drugs, start=1):
            print(f"drug{i}: {item}")
        print(f"limitations={result.get('limitations', []) if isinstance(result, dict) else []}")
    if tool == "openfda":
        top_reactions = (result.get("top_reactions", []) if isinstance(result, dict) else [])[:5]
        event_examples = (result.get("event_examples", []) if isinstance(result, dict) else [])[:3]
        print(f"top_reactions={top_reactions}")
        for i, item in enumerate(event_examples, start=1):
            print(f"event{i}: {item}")
        print(f"limitations={result.get('limitations', []) if isinstance(result, dict) else []}")
    top3 = (result.get("results", []) if isinstance(result, dict) else [])[:3]
    for i, item in enumerate(top3, start=1):
        print(f"top{i}: {item}")
    if error:
        print(f"error={error}")


def run_check_skill(skill_name: str) -> None:
    cfg = get_config()
    logger = V2RunLogger(mode=f"cli_check_skill_{skill_name}")
    skill = skill_name.strip().lower()
    if skill not in {"clinical_evidence", "mechanism_evidence", "drug_safety"}:
        raise ValueError(f"Unsupported --check-skill value: {skill_name}")

    if skill == "mechanism_evidence":
        disease = "systemic lupus erythematosus"
        mechanism_focus = "immune pathway, target, drug evidence"
        start = perf_counter()
        try:
            result, attempt_log = _call_with_retry_cli(
                name="check_skill.mechanism_evidence",
                action=lambda: SKILLS["mechanism_evidence"](
                    disease=disease,
                    mechanism_focus=mechanism_focus,
                    logger=logger,
                    caller="cli.check_skill.mechanism_evidence",
                    top_k=5,
                    config=cfg,
                ),
                logger=logger,
            )
            latency_ms = round((perf_counter() - start) * 1000, 2)
            print("skill=mechanism_evidence")
            print("success=True")
            print(f"latency_ms={latency_ms}")
            print(f"attempts={attempt_log}")
            targets = (result.get("target_evidence") or [])[:3]
            pathways = (result.get("pathway_evidence") or [])[:3]
            drugs = (result.get("drug_hints") or [])[:3]
            for i, item in enumerate(targets, start=1):
                print(f"target{i}: {item}")
            for i, item in enumerate(pathways, start=1):
                print(f"pathway{i}: {item}")
            for i, item in enumerate(drugs, start=1):
                print(f"drug{i}: {item}")
            print(f"mechanism_summary={result.get('mechanism_summary', '')}")
            print(f"limitations={result.get('limitations', [])}")
        except Exception as exc:
            latency_ms = round((perf_counter() - start) * 1000, 2)
            print("skill=mechanism_evidence")
            print("success=False")
            print(f"latency_ms={latency_ms}")
            print(f"error={type(exc).__name__}: {exc}")
        return

    if skill == "drug_safety":
        drug = "prednisone"
        condition_context = "systemic lupus erythematosus with infection risk"
        adverse_event_focus = ["infection", "sepsis", "hyperglycemia"]
        start = perf_counter()
        try:
            result, attempt_log = _call_with_retry_cli(
                name="check_skill.drug_safety",
                action=lambda: SKILLS["drug_safety"](
                    drug=drug,
                    condition_context=condition_context,
                    adverse_event_focus=adverse_event_focus,
                    logger=logger,
                    caller="cli.check_skill.drug_safety",
                    top_k=10,
                    config=cfg,
                ),
                logger=logger,
            )
            latency_ms = round((perf_counter() - start) * 1000, 2)
            print("skill=drug_safety")
            print("success=True")
            print(f"latency_ms={latency_ms}")
            print(f"attempts={attempt_log}")
            signals = (result.get("signals") or [])[:5]
            for i, item in enumerate(signals, start=1):
                print(f"signal{i}: {item}")
            examples = (result.get("event_examples") or [])[:3]
            for i, item in enumerate(examples, start=1):
                print(f"event{i}: {item}")
            print(f"safety_summary={result.get('safety_summary', '')}")
            print(f"limitations={result.get('limitations', [])}")
        except Exception as exc:
            latency_ms = round((perf_counter() - start) * 1000, 2)
            print("skill=drug_safety")
            print("success=False")
            print(f"latency_ms={latency_ms}")
            print(f"error={type(exc).__name__}: {exc}")
        return

    clinical_question = (
        "Could systemic lupus erythematosus explain fever, malar rash and proteinuria, "
        "and what mimics should be considered?"
    )
    phenotypes = ["malar rash", "proteinuria", "oral ulcers"]
    suspected_diagnosis = "systemic lupus erythematosus"
    start = perf_counter()
    try:
        result, attempt_log = _call_with_retry_cli(
            name="check_skill.clinical_evidence",
            action=lambda: SKILLS["clinical_evidence"](
                clinical_question=clinical_question,
                phenotypes=phenotypes,
                suspected_diagnosis=suspected_diagnosis,
                logger=logger,
                caller="cli.check_skill.clinical_evidence",
                top_k=5,
                config=cfg,
            ),
            logger=logger,
        )
        latency_ms = round((perf_counter() - start) * 1000, 2)
        print("skill=clinical_evidence")
        print("success=True")
        print(f"latency_ms={latency_ms}")
        print(f"attempts={attempt_log}")
        print(f"normalized_phenotypes={result.get('normalized_phenotypes', [])}")
        print(f"pubmed_query={result.get('pubmed_query', '')}")
        top_items = (result.get("evidence_items") or [])[:3]
        for i, item in enumerate(top_items, start=1):
            print(f"top{i}: {item}")
        print(f"evidence_summary={result.get('evidence_summary', '')}")
        print(f"limitations={result.get('limitations', [])}")
    except Exception as exc:
        latency_ms = round((perf_counter() - start) * 1000, 2)
        print("skill=clinical_evidence")
        print("success=False")
        print(f"latency_ms={latency_ms}")
        print(f"error={type(exc).__name__}: {exc}")


def run_check_workflow(workflow_name: str) -> None:
    cfg = get_config()
    logger = V2RunLogger(mode=f"cli_check_workflow_{workflow_name}")
    workflow = workflow_name.strip().lower()
    if workflow != "autoimmune_case_review":
        raise ValueError(f"Unsupported --check-workflow value: {workflow_name}")

    case_text = (
        "A patient with suspected systemic lupus erythematosus has fever, malar rash, oral ulcers, "
        "proteinuria and positive anti-dsDNA. The clinician is considering prednisone."
    )
    suspected_diagnosis = "systemic lupus erythematosus"
    candidate_drug = "prednisone"
    safety_focus = ["infection", "sepsis", "hyperglycemia"]
    phenotypes = ["malar rash", "oral ulcers", "proteinuria", "fever"]
    start = perf_counter()
    try:
        result, attempt_log = _call_with_retry_cli(
            name="check_workflow.autoimmune_case_review",
            action=lambda: SKILLS["autoimmune_case_review"](
                case_text=case_text,
                suspected_diagnosis=suspected_diagnosis,
                candidate_drug=candidate_drug,
                safety_focus=safety_focus,
                phenotypes=phenotypes,
                logger=logger,
                caller="cli.check_workflow.autoimmune_case_review",
                config=cfg,
                max_retries=2,
                retry_delay_seconds=10,
            ),
            logger=logger,
        )
        latency_ms = round((perf_counter() - start) * 1000, 2)
        run_dir = logger.finalize(
            metrics={
                "workflow": "autoimmune_case_review",
                "success": len(result.get("failed_components", [])) == 0,
                "failed_components_count": len(result.get("failed_components", [])),
                "latency_ms": latency_ms,
            },
            dataset_source="cli_workflow_check",
        )
        print("workflow=autoimmune_case_review")
        print("success=True")
        print(f"latency_ms={latency_ms}")
        print(f"attempts={attempt_log}")
        print(f"clinical_summary={((result.get('clinical_evidence') or {}).get('evidence_summary', ''))}")
        print(f"mechanism_summary={((result.get('mechanism_evidence') or {}).get('mechanism_summary', ''))}")
        print(f"drug_safety_summary={((result.get('drug_safety_evidence') or {}).get('safety_summary', ''))}")
        print(f"failed_components={result.get('failed_components', [])}")
        print(f"safety_gate={result.get('safety_gate', [])}")
        print(f"log_path={run_dir}")
    except Exception as exc:
        latency_ms = round((perf_counter() - start) * 1000, 2)
        run_dir = logger.finalize(
            metrics={
                "workflow": "autoimmune_case_review",
                "success": False,
                "latency_ms": latency_ms,
                "error": str(exc),
            },
            dataset_source="cli_workflow_check",
        )
        print("workflow=autoimmune_case_review")
        print("success=False")
        print(f"latency_ms={latency_ms}")
        print(f"error={type(exc).__name__}: {exc}")
        print(f"log_path={run_dir}")


if __name__ == "__main__":
    main()

