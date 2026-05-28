from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def summarize_input(obj: Any, max_len: int = 220) -> str:
    return _safe_str(obj, max_len=max_len)


def summarize_output(obj: Any, component: str, max_len: int = 220) -> str:
    if component == "llm":
        if isinstance(obj, dict):
            raw = str(obj.get("raw_output", ""))
            parsed = _parse_llm_output(raw)
            if parsed.get("action"):
                return _safe_str(f"action={parsed.get('action')}; stop={parsed.get('stop', False)}", max_len=max_len)
        return _safe_str(obj, max_len=max_len)
    return _tool_output_summary(component, obj)[:max_len]


def summarize_tool_call(event: dict[str, Any]) -> dict[str, str]:
    return _summarize_tool_call(event)


def summarize_llm_call(event: dict[str, Any]) -> dict[str, str]:
    return _summarize_llm_call(event)


def detect_request_execution_mismatch(raw_request: str, parsed_intent: dict[str, Any], events: list[dict[str, Any]]) -> list[str]:
    mentioned = [d for d in ["glucocorticoids", "cyclophosphamide", "prednisone", "methotrexate"] if d in raw_request.lower()]
    queried: list[str] = []
    for event in events:
        if str(event.get("event_type", "")) != "tool_call":
            continue
        if str(event.get("component", "")) != "openfda_drug_event_search":
            continue
        payload = event.get("input", {}) if isinstance(event.get("input"), dict) else {}
        drug = str(payload.get("drug", "")).strip().lower()
        if drug:
            queried.append(drug)
    queried = list(dict.fromkeys(queried))
    mismatch: list[str] = []
    if mentioned and queried and not any(m in queried for m in mentioned):
        mismatch.append(f"Potential mismatch: mentioned {', '.join(mentioned)} but queried {', '.join(queried)}.")
    if mentioned and not queried and str(parsed_intent.get("intent", "")) == "drug_safety":
        mismatch.append("Potential mismatch: drug safety intent has no queried drug.")
    return mismatch


def _clean_text(text: str, max_len: int = 220) -> str:
    compact = " ".join(str(text).replace("\n", " ").split())
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 3] + "..."


def _safe_str(value: Any, max_len: int = 220) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, (str, int, float, bool)):
        return _clean_text(str(value), max_len=max_len)
    if isinstance(value, list):
        parts = [_safe_str(v, 80) for v in value[:3]]
        text = ", ".join([p for p in parts if p]) + (", ..." if len(value) > 3 else "")
        return _clean_text(text, max_len=max_len)
    if isinstance(value, dict):
        parts = []
        for k, v in list(value.items())[:4]:
            parts.append(f"{k}: {_safe_str(v, 80)}")
        text = "; ".join(parts) + ("; ..." if len(value) > 4 else "")
        return _clean_text(text, max_len=max_len)
    return _clean_text(str(value), max_len=max_len)


def _load_trace_summary(run_dir: str) -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(run_dir)
    trace = json.loads((root / "full_trace.json").read_text(encoding="utf-8")) if (root / "full_trace.json").exists() else {"events": []}
    summary = json.loads((root / "summary.json").read_text(encoding="utf-8")) if (root / "summary.json").exists() else {}
    return trace, summary


def _extract_context(events: list[dict[str, Any]]) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    raw_request = ""
    parsed_intent: dict[str, Any] = {}
    direct_answer: dict[str, Any] = {}
    extraction: dict[str, Any] = {}
    for event in events:
        subtype = str((event.get("metadata") or {}).get("subtype", ""))
        output = event.get("output", {}) if isinstance(event.get("output"), dict) else {}
        input_payload = event.get("input", {}) if isinstance(event.get("input"), dict) else {}
        if subtype == "raw_request":
            raw_request = str(input_payload.get("raw_input", "")) or str(output.get("raw_input", ""))
        elif subtype == "parsed_intent":
            parsed_intent = output
        elif subtype == "direct_answer":
            answer = output.get("direct_answer", output)
            direct_answer = answer if isinstance(answer, dict) else {}
        elif str(event.get("component", "")) == "extraction":
            extraction = output
    return raw_request, parsed_intent, direct_answer, extraction


def _parse_llm_output(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _summarize_llm_call(event: dict[str, Any]) -> dict[str, str]:
    component = str(event.get("component", ""))
    inp = event.get("input", {}) if isinstance(event.get("input"), dict) else {}
    out = event.get("output", {}) if isinstance(event.get("output"), dict) else {}
    prompt = str(inp.get("prompt", ""))
    raw_output = str(out.get("raw_output", ""))

    if "extraction" in component:
        input_summary = f"request length={len(prompt)}; output schema=medical_entities"
    elif "force_final" in component:
        input_summary = f"final_answer repair planning; request length={len(prompt)}"
    else:
        input_summary = f"planner step; request length={len(prompt)}"

    parsed = _parse_llm_output(raw_output)
    if parsed.get("action"):
        action = str(parsed.get("action", ""))
        stop = bool(parsed.get("stop", False))
        thought = _safe_str(parsed.get("thought", ""), 110)
        output_summary = f"action={action}; stop={stop}; thought={thought}"
    elif parsed:
        keys = ", ".join([str(k) for k in list(parsed.keys())[:5]])
        output_summary = f"json fields={keys}"
    else:
        output_summary = _safe_str(raw_output, 140) if raw_output else "no output"

    return {
        "type": "llm",
        "component": component,
        "input": input_summary,
        "output": output_summary,
        "latency": f"{round(float(event.get('latency_ms', 0.0)), 2)}",
        "status": "success" if event.get("success") else f"error: {_safe_str(event.get('error', ''), 120)}",
    }


def _tool_input_summary(component: str, payload: dict[str, Any]) -> str:
    if component == "hpo_search":
        return f'hpo_search: term="{_safe_str(payload.get("term", ""), 80)}"'
    if component == "pubmed_search":
        query = _safe_str(payload.get("query", ""), 120)
        retmax = payload.get("retmax", "n/a")
        return f'pubmed_search: query="{query}"; retmax={retmax}'
    if component == "opentargets_search":
        return f'opentargets_search: disease_query="{_safe_str(payload.get("disease_query", ""), 100)}"'
    if component == "reactome_search":
        return f'reactome_search: query="{_safe_str(payload.get("query", ""), 100)}"'
    if component == "openfda_drug_event_search":
        drug = _safe_str(payload.get("drug", ""), 80)
        reaction = _safe_str(payload.get("reaction", ""), 80)
        return f'openfda: drug="{drug}"; reaction="{reaction}"'
    return f"{component}: {_safe_str(payload, 120)}"


def _tool_output_summary(component: str, output: Any) -> str:
    if not isinstance(output, dict):
        return _safe_str(output, 140)
    if component == "hpo_search":
        results = output.get("results", [])
        if isinstance(results, list) and results:
            first = results[0] if isinstance(results[0], dict) else {}
            hpo = _safe_str(first.get("hpo_id", ""), 40)
            name = _safe_str(first.get("name", ""), 80)
            return f"top HPO={hpo} {name}; result_count={output.get('result_count', 0)}"
        return "no HPO results"
    if component == "pubmed_search":
        results = output.get("results", [])
        if isinstance(results, list) and results:
            first = results[0] if isinstance(results[0], dict) else {}
            pmid = _safe_str(first.get("pmid", ""), 40)
            title = _safe_str(first.get("title", ""), 80)
            return f"PubMed items={len(results)}; top PMID={pmid} {title}"
        return "no PubMed results"
    if component == "opentargets_search":
        results = output.get("results", [])
        symbols = []
        for r in results[:5] if isinstance(results, list) else []:
            if isinstance(r, dict):
                sym = str(r.get("approved_symbol", "")).strip()
                if sym:
                    symbols.append(sym)
        return f"top targets={', '.join(symbols) if symbols else 'none'}"
    if component == "reactome_search":
        results = output.get("results", [])
        names = []
        for r in results[:5] if isinstance(results, list) else []:
            if isinstance(r, dict):
                n = str(r.get("name", "")).strip()
                if n:
                    names.append(n)
        return f"top pathways={', '.join(names) if names else 'none'}"
    if component == "openfda_drug_event_search":
        top = []
        for r in output.get("top_reactions", [])[:5] if isinstance(output.get("top_reactions", []), list) else []:
            if isinstance(r, dict):
                v = str(r.get("reaction", "")).strip()
                if v:
                    top.append(v)
        return f"result_count={output.get('result_count', 0)}; top reactions={', '.join(top) if top else 'none'}"
    return _safe_str(output, 140)


def _summarize_tool_call(event: dict[str, Any]) -> dict[str, str]:
    component = str(event.get("component", ""))
    inp = event.get("input", {}) if isinstance(event.get("input"), dict) else {}
    out = event.get("output", {})
    return {
        "type": "tool",
        "component": component,
        "input": _tool_input_summary(component, inp),
        "output": _tool_output_summary(component, out),
        "latency": f"{round(float(event.get('latency_ms', 0.0)), 2)}",
        "status": "success" if event.get("success") else f"error: {_safe_str(event.get('error', ''), 120)}",
    }


def _collect_call_rows(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for event in events:
        event_type = str(event.get("event_type", ""))
        if event_type == "llm_call":
            rows.append(_summarize_llm_call(event))
        elif event_type == "tool_call":
            rows.append(_summarize_tool_call(event))
    return rows


def _observation_summary(action: str, obs: dict[str, Any]) -> str:
    if action == "clinical_evidence_skill":
        n_pheno = len(obs.get("normalized_phenotypes", [])) if isinstance(obs.get("normalized_phenotypes", []), list) else 0
        n_pubmed = len(obs.get("evidence_items", [])) if isinstance(obs.get("evidence_items", []), list) else 0
        return f"normalized {n_pheno} phenotypes; retrieved {n_pubmed} PubMed items"
    if action == "mechanism_evidence_skill":
        targets = []
        for t in obs.get("target_evidence", [])[:3] if isinstance(obs.get("target_evidence", []), list) else []:
            if isinstance(t, dict):
                sym = str(t.get("approved_symbol", "")).strip()
                if sym:
                    targets.append(sym)
        return f"top targets {', '.join(targets) if targets else 'none'}"
    if action == "drug_safety_skill":
        drug = _safe_str(obs.get("drug", ""), 40)
        signals = obs.get("signals", [])
        n_signals = len(signals) if isinstance(signals, list) else 0
        return f'queried "{drug}"; retrieved {n_signals} safety signal blocks'
    if action == "medication_normalization_skill":
        resolved = len(obs.get("normalized_medications", [])) if isinstance(obs.get("normalized_medications", []), list) else 0
        unresolved = len(obs.get("unresolved_medications", [])) if isinstance(obs.get("unresolved_medications", []), list) else 0
        return f"normalized meds={resolved}; unresolved meds={unresolved}"
    if action == "final_answer":
        return "final answer generated"
    if action == "hpo_search":
        return f"hpo result_count={obs.get('result_count', 0)}"
    if action == "pubmed_search":
        return f"pubmed result_count={obs.get('result_count', 0)}"
    return _safe_str(obs, 140)


def _collect_react_steps(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for event in events:
        if str(event.get("event_type", "")) != "react_agent_step":
            continue
        payload = event.get("output", {}) if isinstance(event.get("output"), dict) else {}
        parsed = payload.get("parsed", {}) if isinstance(payload.get("parsed"), dict) else {}
        validation = payload.get("validation_result", {}) if isinstance(payload.get("validation_result"), dict) else {}
        action = str(payload.get("executed_action", parsed.get("action", ""))).strip()
        step_index = int(payload.get("step_index", 0) or 0)
        thought = _safe_str(parsed.get("thought", ""), 130)
        obs = payload.get("observation", {}) if isinstance(payload.get("observation"), dict) else {}
        obs_id = str(payload.get("observation_id", "")).strip()
        links = payload.get("final_answer_evidence_links", []) if isinstance(payload.get("final_answer_evidence_links"), list) else []
        row = {
            "step_index": step_index,
            "action": action or "unknown_action",
            "thought": thought,
            "observation_id": obs_id,
            "observation_summary": _observation_summary(action, obs),
            "accepted": bool(validation.get("ok", False)),
            "error": _safe_str(validation.get("error", ""), 140),
            "links": [str(x).strip() for x in links if str(x).strip()],
            "protocol_validation": payload.get("protocol_validation", {}),
        }
        if row["accepted"]:
            accepted.append(row)
        else:
            rejected.append(row)
    return accepted, rejected


def _extract_hpo_lines(obs: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    mapping: dict[str, str] = {
        "fever": "Fever",
        "malar rash": "Malar rash",
        "oral ulcers": "Oral ulcers / aphthous stomatitis",
        "proteinuria": "Proteinuria",
    }
    for item in obs.get("normalized_phenotypes", []) if isinstance(obs.get("normalized_phenotypes", []), list) else []:
        if not isinstance(item, dict):
            continue
        term = str(item.get("input_term", "")).strip().lower()
        hpo_id = str(item.get("hpo_id", "")).strip()
        name = str(item.get("name", "")).strip()
        label = mapping.get(term, str(item.get("input_term", "")).strip() or name or "phenotype")
        if hpo_id or name:
            lines.append(f"- {label} -> {hpo_id} {name}".strip())
    return lines[:6]


def _extract_pubmed_lines(obs: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for item in obs.get("evidence_items", []) if isinstance(obs.get("evidence_items", []), list) else []:
        if isinstance(item, str):
            pmid = ""
            m = re.search(r"PMID[:\s]*([0-9]+)", item, re.IGNORECASE)
            if m:
                pmid = m.group(1)
            year_match = re.search(r"\((\d{4})\)", item)
            year = year_match.group(1) if year_match else ""
            title = re.sub(r"\[PMID:[0-9]+\]\s*", "", item).strip()
            title = re.sub(r"\(\d{4}\)\s*$", "", title).strip()
            if pmid:
                lines.append(f"- PMID {pmid} - {title} ({year})".strip())
        elif isinstance(item, dict):
            pmid = str(item.get("pmid", "")).strip()
            title = _safe_str(item.get("title", ""), 100)
            year = str(item.get("year", "")).strip()
            if pmid or title:
                lines.append(f"- PMID {pmid} - {title} ({year})".strip())
    return lines[:5]


def _extract_mechanism_lines(obs: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    targets: list[str] = []
    pathways: list[str] = []
    hints: list[str] = []
    for t in obs.get("target_evidence", []) if isinstance(obs.get("target_evidence", []), list) else []:
        if not isinstance(t, dict):
            continue
        sym = str(t.get("approved_symbol", "")).strip()
        if sym:
            targets.append(sym)
    for p in obs.get("pathway_evidence", []) if isinstance(obs.get("pathway_evidence", []), list) else []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name", "")).strip()
        if name:
            pathways.append(name)
    for h in obs.get("drug_hints", []) if isinstance(obs.get("drug_hints", []), list) else []:
        if isinstance(h, dict):
            name = str(h.get("drug", h.get("name", ""))).strip()
            phase = str(h.get("phase", h.get("status", ""))).strip()
            if name:
                hints.append(f"{name} ({phase})" if phase else name)
        elif isinstance(h, str) and h.strip():
            hints.append(h.strip())
    return targets[:5], pathways[:5], hints[:5]


def _extract_safety_signals(obs: dict[str, Any]) -> list[str]:
    signals: list[str] = []
    for block in obs.get("signals", []) if isinstance(obs.get("signals", []), list) else []:
        if not isinstance(block, dict):
            continue
        top = block.get("top_reactions", []) if isinstance(block.get("top_reactions", []), list) else []
        for r in top:
            if isinstance(r, dict):
                value = str(r.get("reaction", "")).strip()
                if value:
                    signals.append(value)
    if not signals:
        for block in obs.get("per_medication_results", []) if isinstance(obs.get("per_medication_results", []), list) else []:
            if not isinstance(block, dict):
                continue
            for r in block.get("top_reactions", []) if isinstance(block.get("top_reactions", []), list) else []:
                if isinstance(r, dict):
                    value = str(r.get("reaction", "")).strip()
                    if value:
                        signals.append(value)
    return list(dict.fromkeys(signals))[:5]


def _build_evidence_sections(events: list[dict[str, Any]], extraction: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    clinical: list[str] = []
    mechanism: list[str] = []
    safety: list[str] = []
    step_outputs: dict[str, dict[str, Any]] = {}

    for event in events:
        if str(event.get("event_type", "")) != "react_agent_step":
            continue
        out = event.get("output", {}) if isinstance(event.get("output"), dict) else {}
        parsed = out.get("parsed", {}) if isinstance(out.get("parsed"), dict) else {}
        action = str(out.get("executed_action", parsed.get("action", ""))).strip()
        obs = out.get("observation", {}) if isinstance(out.get("observation"), dict) else {}
        if action and obs:
            step_outputs[action] = obs

    clinical_obs = step_outputs.get("clinical_evidence_skill", {})
    if clinical_obs:
        hpo_lines = _extract_hpo_lines(clinical_obs)
        pubmed_lines = _extract_pubmed_lines(clinical_obs)
        if hpo_lines:
            clinical.append("Phenotypes normalized:")
            clinical.extend(hpo_lines)
        if pubmed_lines:
            clinical.append("Literature retrieved:")
            clinical.extend(pubmed_lines)
        clinical.append(f"Clinical notes: {_safe_str(clinical_obs.get('evidence_summary', ''), 160)}")

    mechanism_obs = step_outputs.get("mechanism_evidence_skill", {})
    if mechanism_obs:
        disease = _safe_str(mechanism_obs.get("disease", ""), 100)
        targets, pathways, hints = _extract_mechanism_lines(mechanism_obs)
        mechanism.append(f"Disease entity: {disease}")
        if targets:
            mechanism.append("Top targets:")
            mechanism.extend([f"- {t}" for t in targets])
        if pathways:
            mechanism.append("Top pathways:")
            mechanism.extend([f"- {p}" for p in pathways])
        if hints:
            mechanism.append("Drug / clinical candidate hints:")
            mechanism.extend([f"- {h}" for h in hints])
        mechanism.append("Mechanism notes: target/pathway evidence supports biology; this is not a diagnosis conclusion.")

    safety_obs = step_outputs.get("drug_safety_skill", {})
    norm_obs = step_outputs.get("medication_normalization_skill", {})
    parsed_entities = (extraction.get("parsed_entities", {}) or {}) if isinstance(extraction, dict) else {}
    meds = parsed_entities.get("medications", []) if isinstance(parsed_entities, dict) else []
    mentioned: list[str] = []
    for m in meds if isinstance(meds, list) else []:
        if isinstance(m, dict):
            name = str(m.get("text", "")).strip()
            if name:
                mentioned.append(name)
        elif isinstance(m, str) and m.strip():
            mentioned.append(m.strip())
    mentioned = list(dict.fromkeys(mentioned))

    if safety_obs or norm_obs or mentioned:
        if mentioned:
            safety.append("Mentioned medications:")
            safety.extend([f"- {m}" for m in mentioned[:5]])
        if norm_obs:
            safety.append("Medication normalization:")
            normalized = norm_obs.get("normalized_medications", []) if isinstance(norm_obs.get("normalized_medications", []), list) else []
            unresolved = norm_obs.get("unresolved_medications", []) if isinstance(norm_obs.get("unresolved_medications", []), list) else []
            if normalized:
                for item in normalized[:5]:
                    if isinstance(item, dict):
                        name = _safe_str(item.get("input", item.get("name", "")), 40)
                        rxcui = _safe_str(item.get("rxcui", ""), 20)
                        match = _safe_str(item.get("match_type", item.get("status", "")), 30)
                        safety.append(f"- {name} -> RxNorm rxcui={rxcui}, {match or 'matched'}")
            for item in unresolved[:5]:
                if isinstance(item, dict):
                    name = _safe_str(item.get("input", item.get("name", "")), 40)
                else:
                    name = _safe_str(item, 40)
                if name:
                    note = "class entity, requires confirmation" if "glucocorticoid" in name.lower() else "requires confirmation"
                    safety.append(f"- {name} -> {note}")
            if not normalized and not unresolved:
                safety.append("- Not executed / no evidence returned")
                if any("glucocorticoid" in m.lower() for m in mentioned):
                    safety.append("- glucocorticoids -> class entity, requires confirmation")
                if any("cyclophosphamide" in m.lower() for m in mentioned):
                    safety.append("- cyclophosphamide -> requires normalization confirmation")
        elif mentioned:
            safety.append("Medication normalization:")
            if any("glucocorticoid" in m.lower() for m in mentioned):
                safety.append("- glucocorticoids -> class entity, requires confirmation")
            if any("cyclophosphamide" in m.lower() for m in mentioned):
                safety.append("- cyclophosphamide -> requires normalization confirmation")

        queried: list[str] = []
        for event in events:
            if str(event.get("event_type", "")) != "tool_call":
                continue
            if str(event.get("component", "")) != "openfda_drug_event_search":
                continue
            payload = event.get("input", {}) if isinstance(event.get("input"), dict) else {}
            drug = str(payload.get("drug", "")).strip()
            if drug:
                queried.append(drug)
        queried = list(dict.fromkeys(queried))
        if queried:
            safety.append("Queried medications:")
            safety.extend([f"- {q}" for q in queried[:5]])
        top_signals = _extract_safety_signals(safety_obs if isinstance(safety_obs, dict) else {})
        if top_signals:
            safety.append("Top FAERS/openFDA signals:")
            safety.extend([f"- {s}" for s in top_signals])
        safety.append("Source caution: FAERS/openFDA reports are pharmacovigilance signals, not causality or incidence.")

    return clinical, mechanism, safety


def _build_accepted_trajectory_lines(accepted: list[dict[str, Any]]) -> list[str]:
    lines = ["## 3. Accepted ReAct Trajectory", ""]
    if not accepted:
        lines.extend(["- Not executed / no accepted steps", ""])
        return lines
    for idx, step in enumerate(accepted, start=1):
        lines.append(f"{idx}. Step {step['step_index']} - `{step['action']}`")
        lines.append(f"   - Thought: {_safe_str(step['thought'], 110)}")
        obs_label = step["observation_id"] or "n/a"
        lines.append(f"   - Observation: `{obs_label}` - {_safe_str(step['observation_summary'], 120)}")
        if step["links"]:
            lines.append(f"   - Evidence cited: {', '.join(step['links'])}")
        lines.append("   - Protocol: accepted")
    lines.append("")
    return lines


def _build_rejected_lines(rejected: list[dict[str, Any]]) -> list[str]:
    if not rejected:
        return []
    lines = ["## 4. Rejected / Corrected Attempts", ""]
    for step in rejected:
        lines.append(f"- `{step['action']}` rejected: {_safe_str(step['error'] or 'validation failed', 120)}")
    repaired = any(bool((step.get("protocol_validation") or {}).get("final_answer_repaired")) for step in rejected)
    if repaired:
        lines.append("- final_answer repaired successfully")
    lines.append("")
    return lines


def _final_answer_and_citations(events: list[dict[str, Any]], direct_answer: dict[str, Any], summary: dict[str, Any]) -> tuple[str, list[str]]:
    text = str(direct_answer.get("answer", summary.get("answer_preview", ""))).strip()
    citations: list[str] = []
    for event in events:
        if str(event.get("event_type", "")) != "react_agent_step":
            continue
        out = event.get("output", {}) if isinstance(event.get("output"), dict) else {}
        parsed = out.get("parsed", {}) if isinstance(out.get("parsed"), dict) else {}
        action = str(out.get("executed_action", parsed.get("action", ""))).strip()
        validation = out.get("validation_result", {}) if isinstance(out.get("validation_result"), dict) else {}
        if action == "final_answer" and bool(validation.get("ok", False)):
            links = out.get("final_answer_evidence_links", []) if isinstance(out.get("final_answer_evidence_links"), list) else []
            citations = [str(x).strip() for x in links if str(x).strip()]
            obs = out.get("observation", {}) if isinstance(out.get("observation"), dict) else {}
            final_payload = obs.get("final_answer", {}) if isinstance(obs.get("final_answer"), dict) else {}
            if not text:
                text = str(final_payload.get("answer", "")).strip()
    if not text:
        text = "No final answer captured."
    return text, citations


def write_readable_log(run_dir: str, trace: dict[str, Any] | None = None, summary: dict[str, Any] | None = None) -> str:
    trace_data, summary_data = trace or {}, summary or {}
    if not trace_data or not summary_data:
        trace_data, summary_data = _load_trace_summary(run_dir)
    events = trace_data.get("events", []) if isinstance(trace_data.get("events"), list) else []
    raw_request, parsed_intent, direct_answer, extraction = _extract_context(events)
    accepted, rejected = _collect_react_steps(events)
    rows = _collect_call_rows(events)
    clinical, mechanism, safety = _build_evidence_sections(events, extraction)
    answer_text, citations = _final_answer_and_citations(events, direct_answer, summary_data)

    out = Path(run_dir) / "case_report.md"
    lines = [
        "# Case Report",
        "",
        "## 1. Request",
        "",
        f"> {(raw_request or str(summary_data.get('input_summary', '')))[:500]}",
        "",
        "## 2. Parsed Intent",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Intent | {_safe_str(parsed_intent.get('intent', summary_data.get('intent', '')), 120)} |",
        f"| Routed to | {_safe_str(summary_data.get('routed_to', parsed_intent.get('routed_to', '')), 120)} |",
        f"| Status | {_safe_str(direct_answer.get('status', summary_data.get('status', '')), 120)} |",
        f"| Extracted diagnoses | {_safe_str((extraction.get('parsed_entities', {}) or {}).get('suspected_diagnoses', []), 180)} |",
        f"| Extracted medications | {_safe_str((extraction.get('parsed_entities', {}) or {}).get('medications', []), 180)} |",
        f"| Extracted phenotypes | {_safe_str((extraction.get('parsed_entities', {}) or {}).get('phenotypes', []), 180)} |",
        f"| Safety focus | {_safe_str((extraction.get('parsed_entities', {}) or {}).get('safety_focus', []), 180)} |",
        "",
    ]
    lines.extend(_build_accepted_trajectory_lines(accepted))
    lines.extend(_build_rejected_lines(rejected))
    lines.extend(
        [
            "## 5. Call Summary",
            "",
            "| # | Type | Component | Input | Output | Latency | Status |",
            "|---:|---|---|---|---|---:|---|",
        ]
    )
    if rows:
        for i, row in enumerate(rows, start=1):
            lines.append(
                f"| {i} | {row['type']} | {row['component']} | {_safe_str(row['input'], 150)} | {_safe_str(row['output'], 150)} | {row['latency']} | {row['status']} |"
            )
    else:
        lines.append("| 1 | n/a | n/a | n/a | n/a | 0 | n/a |")

    lines.extend(["", "## 6. Evidence by Layer", ""])
    if clinical:
        lines.extend(["### Clinical Evidence"] + [f"{x}" if x.startswith("- ") else f"- {x}" for x in clinical] + [""])
    if mechanism:
        lines.extend(["### Mechanism Evidence"] + [f"{x}" if x.startswith("- ") else f"- {x}" for x in mechanism] + [""])
    if safety:
        lines.extend(["### Safety Evidence"] + [f"{x}" if x.startswith("- ") else f"- {x}" for x in safety] + [""])
    if not clinical and not mechanism and not safety:
        lines.extend(["- Not executed / no evidence returned", ""])

    lines.extend(["## 7. Final Answer", ""])
    lines.append(_clean_text(answer_text, 2000))
    if citations:
        lines.append("")
        lines.append(f"Observation citations: {', '.join(citations)}")
    lines.extend(["", "## 8. Warnings and Limitations", ""])
    lines.extend(
        [
            "- Not medical advice.",
            "- External API coverage may be incomplete.",
            "- FAERS/openFDA reports are pharmacovigilance signals, not causality or incidence.",
        ]
    )
    unresolved = direct_answer.get("missing_fields", []) if isinstance(direct_answer.get("missing_fields", []), list) else []
    lines.append(f"- Unresolved / partial items: {_safe_str(unresolved, 180)}")
    lines.extend(
        [
            "",
            "## 9. Audit Files",
            "",
            "- `summary.json`",
            "- `calls.jsonl`",
            "- `full_trace.json`",
            "- `full_trace.md`",
            "",
        ]
    )
    out.write_text("\n".join(lines), encoding="utf-8")
    return str(out)
