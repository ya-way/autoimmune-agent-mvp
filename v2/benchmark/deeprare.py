from __future__ import annotations

import argparse
import ast
import csv
import json
import os
import random
import re
import subprocess
from pathlib import Path
from time import perf_counter
from typing import Any

from v2.config import get_config
from v2.core.action_protocol import get_protocol
from v2.core.actions import summarize_observation
from v2.core.llm import LLMClient
from v2.core.logger import V2RunLogger
from v2.core.react import ReActRunner
from v2.core.react_agent import ReActAgent
from v2.core.readable_logger import write_readable_log
from v2.tools import TOOLS
from v2.schemas import BenchmarkItem, BenchmarkPrediction

LAST_DATASET_SOURCE = "smoke"
LAST_DATASET_NAME = ""
LAST_DATASET_FILE = ""
LAST_GOLD_FIELD = ""
LAST_DATA_NOTE = ""
LAST_ACTUAL_SPLIT = ""
LAST_REAL_DATA = False
LAST_FALLBACK_REASON = ""
CODE_NAME_CACHE_PATH = Path(__file__).resolve().parent.parent / "resources" / "code_name_cache.json"
OFFICIAL_BENCHMARK_MODES = {
    "plain_llm_deeprare_official",
    "react_agent_without_tool_deeprare_official",
    "react_agent_with_tool_deeprare_official",
}
LEGACY_EXPERIMENTAL_MODES = {
    "react_without_tool",
    "react_with_tool",
    "fixed_without_tool",
    "fixed_with_tool",
    "legacy_react_without_tool",
    "legacy_react_with_tool",
}
TOP5_EXTRACTORS = {"deterministic", "llm"}


def _assert_official_benchmark_is_clean(mode: str, *, config: Any, data_source: str, allow_smoke: bool) -> None:
    if mode not in OFFICIAL_BENCHMARK_MODES:
        return
    model = str(getattr(config, "llm_model", "")).strip()
    if model != "deepseek-chat":
        raise RuntimeError(f"Official benchmark requires deepseek-chat, got: {model}")
    if allow_smoke:
        raise RuntimeError("Official benchmark forbids --allow-smoke.")
    if str(data_source).strip().lower() == "smoke":
        raise RuntimeError("Official benchmark forbids data_source=smoke.")


def load_deeprare_items(
    limit: int,
    data_source: str,
    dataset_name: str,
    split: str,
    dataset_file: str,
    sample_order: str = "sequential",
    seed: int = 42,
    allow_smoke: bool = False,
) -> list[BenchmarkItem]:
    global LAST_DATASET_SOURCE
    global LAST_DATASET_NAME
    global LAST_DATASET_FILE
    global LAST_GOLD_FIELD
    global LAST_DATA_NOTE
    global LAST_ACTUAL_SPLIT
    global LAST_REAL_DATA
    global LAST_FALLBACK_REASON

    cfg = get_config()
    LAST_DATASET_NAME = dataset_name
    LAST_DATASET_FILE = dataset_file
    LAST_GOLD_FIELD = ""
    LAST_DATA_NOTE = ""
    LAST_ACTUAL_SPLIT = split
    LAST_REAL_DATA = False
    LAST_FALLBACK_REASON = ""

    source = data_source.strip().lower()
    if source not in {"auto", "hf", "local", "smoke"}:
        raise ValueError(f"Unsupported data_source: {data_source}")

    hf_errors: list[str] = []
    local_errors: list[str] = []
    real_items: list[BenchmarkItem] = []

    if source in {"auto", "hf"}:
        real_items, hf_note = _load_from_hf(dataset_name=dataset_name, split=split, limit=limit)
        if real_items:
            LAST_DATASET_SOURCE = "hf"
            LAST_DATA_NOTE = hf_note
            LAST_REAL_DATA = True
            return _select_items(real_items, limit=limit, sample_order=sample_order, seed=seed)
        hf_errors.append(hf_note)

    if source in {"auto", "local"}:
        real_items, local_note = _load_from_local_csv(
            repo_path=cfg.deeprare_repo_path,
            dataset_file=dataset_file,
            limit=limit,
        )
        if real_items:
            LAST_DATASET_SOURCE = "local"
            LAST_DATA_NOTE = local_note
            LAST_REAL_DATA = True
            return _select_items(real_items, limit=limit, sample_order=sample_order, seed=seed)
        local_errors.append(local_note)

    if source == "smoke":
        print("[DEEPRARE] forced smoke data source")
        LAST_DATASET_SOURCE = "smoke"
        LAST_REAL_DATA = False
        LAST_DATA_NOTE = "explicit_smoke_source"
        return _select_items(_smoke_items(max(limit, 1)), limit=limit, sample_order=sample_order, seed=seed)

    reasons = "; ".join([msg for msg in hf_errors + local_errors if msg])
    if reasons:
        LAST_FALLBACK_REASON = reasons
    if allow_smoke:
        print("[DEEPRARE] real dataset unavailable, fallback to smoke because --allow-smoke is enabled")
        if reasons:
            print(f"[DEEPRARE] data loading note: {reasons}")
        LAST_DATASET_SOURCE = "smoke"
        LAST_REAL_DATA = False
        LAST_DATA_NOTE = "fallback_to_smoke_allow_smoke"
        return _select_items(_smoke_items(max(limit, 1)), limit=limit, sample_order=sample_order, seed=seed)

    detail = reasons or "unknown_data_loading_error"
    raise RuntimeError(f"Real dataset loading failed and smoke fallback is disabled: {detail}")


def _select_items(items: list[BenchmarkItem], limit: int, sample_order: str, seed: int) -> list[BenchmarkItem]:
    order = sample_order.strip().lower()
    if order not in {"sequential", "random"}:
        raise ValueError(f"Unsupported sample_order: {sample_order}")
    selected = list(items)
    if order == "random":
        rng = random.Random(seed)
        rng.shuffle(selected)
    target = max(1, limit)
    return selected[:target]


def _load_from_hf(dataset_name: str, split: str, limit: int) -> tuple[list[BenchmarkItem], str]:
    global LAST_ACTUAL_SPLIT
    try:
        from datasets import get_dataset_config_names, get_dataset_split_names, load_dataset  # type: ignore
    except Exception:
        return [], "datasets package unavailable"

    errors: list[str] = []
    config_names: list[str] = [""]
    try:
        names = get_dataset_config_names(dataset_name)
        if names:
            config_names = names
    except Exception as exc:
        errors.append(f"config_probe_failed:{exc}")

    if not config_names:
        config_names = [""]

    preferred = ["HMS", "RAMEDIS", "MME", "LIRICAL"]
    ordered_configs: list[str] = []
    for c in preferred:
        if c in config_names:
            ordered_configs.append(c)
    for c in config_names:
        if c not in ordered_configs:
            ordered_configs.append(c)

    for config_name in ordered_configs:
        try:
            split_names = get_dataset_split_names(dataset_name, config_name) if config_name else get_dataset_split_names(dataset_name)
            if not split_names:
                split_names = [split]
        except Exception as exc:
            errors.append(f"split_probe_failed[{config_name or 'default'}]:{exc}")
            split_names = [split]

        actual_split = split if split in split_names else split_names[0]
        try:
            if config_name:
                ds = load_dataset(dataset_name, config_name, split=actual_split)
            else:
                ds = load_dataset(dataset_name, split=actual_split)
            rows: list[dict[str, Any]] = []
            for row in ds:
                if isinstance(row, dict):
                    rows.append(row)
            if not rows:
                errors.append(f"empty_split[{config_name or 'default'}:{actual_split}]")
                continue
            items = _rows_to_items(rows, source_name=f"hf:{dataset_name}:{config_name or 'default'}")
            if items:
                LAST_ACTUAL_SPLIT = actual_split
                return (
                    items,
                    f"hf_loaded dataset={dataset_name} config={config_name or 'default'} split={actual_split}"
                    f" requested_split={split} sample_count={len(items)}",
                )
            errors.append(f"mapping_failed[{config_name or 'default'}:{actual_split}]")
        except Exception as exc:
            errors.append(f"load_failed[{config_name or 'default'}:{actual_split}]:{exc}")
            continue

    return [], "hf_load_failed " + " | ".join(errors[:5])


def _load_from_local_csv(repo_path: str, dataset_file: str, limit: int) -> tuple[list[BenchmarkItem], str]:
    file_path: Path | None = None
    if dataset_file:
        p = Path(dataset_file).expanduser().resolve()
        if p.exists() and p.is_file():
            file_path = p
        else:
            return [], f"dataset_file_not_found:{p}"
    else:
        if not repo_path:
            return [], "DEEPRARE_REPO_PATH_empty"
        root = Path(repo_path).expanduser().resolve()
        dataset_dir = root / "dataset"
        if not dataset_dir.exists() or not dataset_dir.is_dir():
            return [], f"dataset_dir_not_found:{dataset_dir}"
        csv_files = sorted(dataset_dir.glob("*.csv"))
        if not csv_files:
            return [], f"no_csv_in:{dataset_dir}"
        file_path = csv_files[0]

    rows: list[dict[str, Any]] = []
    with file_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    items = _rows_to_items(rows, source_name=f"local:{file_path.name}")
    if not items:
        return [], f"csv_rows_unmappable:{file_path}"
    return items, f"local_loaded file={file_path}"


def _load_items_from_csv(csv_path: Path, max_items: int) -> list[BenchmarkItem]:
    results: list[BenchmarkItem] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader):
            if len(results) >= max_items:
                break
            item = _adapt_row_to_benchmark_item(row=row, row_idx=idx, file_stem=csv_path.stem)
            if item is not None:
                results.append(item)
    return results


def _rows_to_items(rows: list[dict[str, Any]], source_name: str) -> list[BenchmarkItem]:
    items: list[BenchmarkItem] = []
    global LAST_GOLD_FIELD
    for idx, row in enumerate(rows):
        item = _adapt_row_to_benchmark_item(row=row, row_idx=idx, file_stem=source_name)
        if item is not None:
            if not LAST_GOLD_FIELD:
                LAST_GOLD_FIELD = item.raw.get("__gold_field__", "")
            items.append(item)
    return items


def _adapt_row_to_benchmark_item(row: dict[str, str], row_idx: int, file_stem: str) -> BenchmarkItem | None:
    case_id = _first_non_empty(row, ["id", "case_id", "patient_id", "index", "note_id", "rag_id", "Department"]) or f"{file_stem}_{row_idx}"
    phenotype_names_raw = _first_non_empty(
        row,
        ["phenotype_names", "phenotypes", "phenotype_name_list", "symptoms", "Phenotype", "phenotype_text", "patient_info"],
    )
    phenotype_text = _first_non_empty(
        row,
        [
            "phenotype_text",
            "patient_info",
            "clinical_text",
            "ehr",
            "text",
            "symptoms",
            "phenotype",
            "Phenotype",
            "Phenotype_detailed",
            "hpo",
            "HPO",
            "free_text",
        ],
    )
    if not phenotype_text and phenotype_names_raw:
        phenotype_text = ", ".join(_split_tokens(phenotype_names_raw))
    golden_diagnosis = _first_non_empty(
        row,
        [
            "golden_diagnosis",
            "diagnosis",
            "disease",
            "disease_text",
            "label",
            "answer",
            "rare_disease",
            "RareDisease",
            "orpha",
            "Disease_detailed",
        ],
    )
    if not golden_diagnosis:
        return None
    gold_field = _first_non_empty_key(
        row,
        [
            "golden_diagnosis",
            "diagnosis",
            "disease",
            "disease_text",
            "label",
            "answer",
            "rare_disease",
            "RareDisease",
            "orpha",
            "Disease_detailed",
        ],
    )
    phenotype_ids_raw = _first_non_empty(row, ["phenotype_ids", "hpo_ids", "hpo", "HPO", "phenotype_id_list"]) or ""
    phenotype_ids = _split_tokens(phenotype_ids_raw)
    phenotype_names = _split_tokens(phenotype_names_raw)
    if not phenotype_names and phenotype_text:
        phenotype_names = _split_tokens(phenotype_text)
    if not phenotype_names:
        phenotype_names = []
    if not phenotype_ids:
        phenotype_ids = [token for token in phenotype_names if token.upper().startswith("HP:")]
    if not phenotype_text and phenotype_names:
        phenotype_text = ", ".join(phenotype_names)
    if not phenotype_text:
        phenotype_text = ""
    golden_code = _extract_primary_code(golden_diagnosis)
    golden_name = _extract_gold_name_from_row(row)
    return BenchmarkItem(
        case_id=str(case_id),
        phenotype_text=str(phenotype_text),
        phenotype_ids=phenotype_ids,
        phenotype_names=phenotype_names,
        golden_diagnosis=str(golden_diagnosis),
        golden_diagnosis_code=golden_code,
        golden_diagnosis_name=golden_name,
        raw={**{k: v for k, v in row.items()}, "__gold_field__": gold_field},
    )


def _first_non_empty(row: dict[str, str], keys: list[str]) -> str:
    for k in keys:
        val = row.get(k)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def _first_non_empty_key(row: dict[str, Any], keys: list[str]) -> str:
    for k in keys:
        val = row.get(k)
        if val is None:
            continue
        if isinstance(val, (list, tuple)) and len(val) == 0:
            continue
        if str(val).strip():
            return k
    return ""


def _split_tokens(text: str) -> list[str]:
    if isinstance(text, (list, tuple)):
        values: list[str] = []
        for t in text:
            values.extend(_split_tokens(str(t)))
        return values
    clean = str(text).strip()
    if not clean:
        return []
    clean = clean.strip("[]")
    parts = re.split(r"[,\|;]+", clean)
    return [p.strip(" '\"") for p in parts if p.strip(" '\"")]


def _extract_primary_code(text: str) -> str | None:
    codes = re.findall(r"\b(?:OMIM|ORPHA|CCRD):[A-Za-z0-9\.\-]+\b", str(text))
    return codes[0] if codes else None


def _deeprare_original_system_prompt() -> str:
    return (
        "You are a specialist in the field of rare diseases."
        " You will be provided and asked about a complicated clinical case; "
        "read it carefully and then provide a diverse and comprehensive differential diagnosis. "
        "Also, you will be provided some knowledge about the patient's phenotype and online diagnosis suggestions as reference, please read it carefully."
    )


def _deeprare_original_user_prompt(phenotype_text: str, auxiliary_context: str = "") -> str:
    prompt = ""
    prompt += f"Patient's phenotype: {phenotype_text}\n"
    prompt += "Enumerate the top 5 most likely diagnoses. Be precise, and try to cover many unique possibilities. "
    prompt += "Each diagnosis should be a rare disease. "
    prompt += "Use ## to tag the disease name. "
    prompt += "Make sure to reorder the diagnoses from most likely to least likely. "
    prompt += "The top 5 diagnoses are:"
    if auxiliary_context.strip():
        prompt += (
            "\n\nAuxiliary context (optional support; phenotype remains primary):\n"
            f"{auxiliary_context.strip()}\n"
            "If auxiliary context is sparse or weak, rely primarily on patient phenotype."
        )
    return prompt


def _extract_deeprare_top5_from_text(raw_answer: str) -> tuple[list[str], list[str], str, list[str]]:
    lines = [line.strip() for line in str(raw_answer).splitlines() if line.strip()]
    candidates: list[str] = []
    raw_candidate_lines: list[str] = []
    for line in lines:
        value = line
        if value.startswith("##"):
            value = value.strip("#").strip()
        m = re.match(r"^\s*(\d+)[\)\.\:\-]\s*(.+)$", value)
        if m:
            value = m.group(2).strip()
        if not value:
            continue
        low = value.lower()
        if low.startswith("references"):
            continue
        if low.startswith("diagnostic reasoning"):
            continue
        if len(value.split()) > 12:
            continue
        if value in raw_candidate_lines:
            continue
        raw_candidate_lines.append(value)
        candidates.append(value)
        if len(candidates) >= 5:
            break
    parse_status = "ok" if len(candidates) >= 5 else ("partial" if len(candidates) > 0 else "failed")
    warnings: list[str] = []
    if len(candidates) < 5:
        warnings.append(f"only_{len(candidates)}_candidates_from_raw_answer")
    normalized = [_norm(c) for c in candidates if _norm(c)]
    return candidates[:5], normalized[:5], parse_status, warnings


def _extract_deeprare_top5_with_llm(
    raw_answer: str,
    llm: LLMClient,
    caller: str,
) -> tuple[list[str], list[str], str, list[str]]:
    warnings: list[str] = []
    system_prompt = (
        "You are a strict medical diagnosis name extractor. "
        "Extract exactly five rare disease diagnoses from text."
    )
    prompt = (
        "Extract the top 5 diagnosis names from the following answer.\n"
        "Output ONLY a JSON array of 5 strings.\n"
        "Do not output HPO terms, symptoms, explanations, headings, or markdown.\n\n"
        "ANSWER:\n"
        f"{raw_answer}\n"
    )
    try:
        llm_output = llm.complete(
            prompt=prompt,
            caller=caller,
            system_prompt=system_prompt,
        )
    except Exception as exc:
        warnings.append(f"llm_extractor_call_failed:{exc}")
        return [], [], "failed", warnings

    raw_candidates = _try_extract_json_candidates(str(llm_output))
    if not raw_candidates:
        fallback_candidates, _, fallback_warnings, fallback_status, _ = _extract_prediction_candidates(
            raw_answer=str(llm_output),
            top_k=5,
        )
        warnings.append("llm_extractor_non_json_output")
        warnings.extend(fallback_warnings)
        normalized = [_norm(c) for c in fallback_candidates if _norm(c)]
        return fallback_candidates[:5], normalized[:5], fallback_status, warnings

    candidates: list[str] = []
    for item in raw_candidates:
        candidate = _extract_candidate_from_line(str(item))
        if not candidate:
            continue
        if _should_reject_candidate(candidate):
            continue
        if not _looks_like_disease_candidate(candidate):
            continue
        if candidate in candidates:
            continue
        candidates.append(candidate)
        if len(candidates) >= 5:
            break
    status = "ok" if len(candidates) >= 5 else ("partial" if len(candidates) > 0 else "failed")
    if len(candidates) < 5:
        warnings.append(f"llm_extractor_only_{len(candidates)}_candidates")
    normalized = [_norm(c) for c in candidates if _norm(c)]
    return candidates[:5], normalized[:5], status, warnings


def _extract_official_top5(
    raw_answer: str,
    *,
    extractor: str,
    llm: LLMClient,
    caller_prefix: str,
) -> tuple[list[str], list[str], str, list[str]]:
    if extractor == "deterministic":
        return _extract_deeprare_top5_from_text(raw_answer)
    if extractor == "llm":
        llm_topk, llm_norm, llm_status, llm_warnings = _extract_deeprare_top5_with_llm(
            raw_answer=raw_answer,
            llm=llm,
            caller=f"{caller_prefix}.top5_extractor_llm",
        )
        if llm_topk:
            return llm_topk, llm_norm, llm_status, llm_warnings
        det_topk, det_norm, det_status, det_warnings = _extract_deeprare_top5_from_text(raw_answer)
        return det_topk, det_norm, det_status, llm_warnings + ["llm_extractor_fallback_deterministic"] + det_warnings
    raise ValueError(f"Unsupported top5 extractor: {extractor}")


def _extract_gold_name_from_row(row: dict[str, Any]) -> str | None:
    candidates = [
        "golden_diagnosis_name",
        "diagnosis_name",
        "disease_name",
        "DiseaseName",
        "label_text",
        "diagnosis_text",
    ]
    for k in candidates:
        val = row.get(k)
        if val is None:
            continue
        s = str(val).strip()
        if not s:
            continue
        if re.search(r"\b(?:OMIM|ORPHA|CCRD):", s):
            continue
        return s
    return None


def _load_code_name_cache() -> dict[str, list[str]]:
    if not CODE_NAME_CACHE_PATH.exists():
        return {}
    try:
        data = json.loads(CODE_NAME_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, list[str]] = {}
    if not isinstance(data, dict):
        return out
    for k, v in data.items():
        code = str(k).strip()
        if not code:
            continue
        names: list[str] = []
        if isinstance(v, list):
            for item in v:
                name = str(item).strip()
                if name:
                    names.append(name)
        elif isinstance(v, str):
            if v.strip():
                names.append(v.strip())
        out[code] = names
    return out


def _extract_prediction_candidates(
    raw_answer: str, top_k: int = 5
) -> tuple[list[str], list[str], list[str], str, list[str]]:
    warnings: list[str] = []
    text = raw_answer.strip()
    raw_candidate_lines: list[str] = []
    candidates: list[str] = []
    json_candidates = _try_extract_json_candidates(text)
    if json_candidates:
        raw_candidate_lines = list(json_candidates)
    else:
        cleaned = text.replace("```json", "").replace("```", "").replace("**", "")
        raw_candidate_lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    for line in raw_candidate_lines:
        candidate = _extract_candidate_from_line(line)
        if not candidate:
            continue
        if _should_reject_candidate(candidate):
            continue
        if not _looks_like_disease_candidate(candidate):
            continue
        candidates.append(candidate)
        if len(candidates) >= top_k:
            break
    if len(candidates) < 3:
        warnings.append(f"only_{len(candidates)}_candidates_after_filtering")
    parse_status = "ok"
    if len(candidates) == 0:
        parse_status = "failed"
    elif len(candidates) < top_k:
        parse_status = "partial"
    if warnings and parse_status == "ok":
        parse_status = "partial"
    normalized = [_norm(c) for c in candidates if _norm(c)]
    return candidates[:top_k], normalized[:top_k], warnings, parse_status, raw_candidate_lines


def _try_extract_json_candidates(text: str) -> list[str]:
    candidate_text = text.strip()
    if candidate_text.startswith("```"):
        candidate_text = candidate_text.replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(candidate_text)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    out: list[str] = []
    for item in parsed:
        value = str(item).strip()
        if value:
            out.append(value)
    return out


def _extract_candidate_from_line(line: str) -> str:
    stripped = line.strip()
    m = re.match(r"^\s*(\d+)[\)\.\:\-]\s*(.+)$", stripped)
    if m:
        stripped = m.group(2).strip()
    stripped = re.sub(r"^#+\s*", "", stripped).strip()
    stripped = re.sub(r"\s+", " ", stripped).strip(" -")
    return stripped


def _should_reject_candidate(line: str) -> bool:
    low = line.lower().strip()
    if not low:
        return True
    if low.startswith("hp:"):
        return True
    if re.match(r"^hp:\d+\s*=\s*.+$", low):
        return True
    heading_terms = [
        "top 5",
        "likely diagnoses",
        "differential diagnosis",
        "based on",
        "phenotype",
        "symptom",
        "department",
        "the patient",
        "here are",
    ]
    if any(term in low for term in heading_terms):
        if not re.search(r"\b(?:syndrome|disease|disorder|vasculitis|lupus|arthritis|anemia)\b", low):
            return True
    if low in {"---", "diagnosis", "diagnoses", "differential"}:
        return True
    if len(line.split()) > 18 and not re.search(r"\b(?:omim|orpha|ccrd):", low):
        return True
    return False


def _looks_like_disease_candidate(line: str) -> bool:
    low = line.lower()
    if re.search(r"\b(?:omim|orpha|ccrd):", low):
        return True
    disease_markers = [
        "syndrome",
        "disease",
        "disorder",
        "vasculitis",
        "lupus",
        "arthritis",
        "anemia",
        "deficiency",
        "neuropathy",
        "ataxia",
        "dysplasia",
        "malformation",
        "progeria",
    ]
    if any(marker in low for marker in disease_markers):
        return True
    tokens = [t for t in re.split(r"\s+", low) if t]
    if len(tokens) == 1:
        token = tokens[0]
        if re.match(r"^[a-z][a-z\-]{5,}$", token):
            return True
    return len(tokens) >= 2


def _has_non_empty_evidence(evidence: dict[str, Any] | None) -> bool:
    if not isinstance(evidence, dict):
        return False
    hpo = evidence.get("hpo")
    pubmed = evidence.get("pubmed")
    web = evidence.get("web")
    hpo_count = int((hpo or {}).get("result_count", 0)) if isinstance(hpo, dict) else 0
    pubmed_count = int((pubmed or {}).get("result_count", 0)) if isinstance(pubmed, dict) else 0
    web_count = 0
    if isinstance(web, dict):
        web_count = len(web.get("results", []) or [])
    return (hpo_count + pubmed_count + web_count) > 0


def _assess_tool_effectiveness(mode: str, evidence: dict[str, Any] | None) -> dict[str, Any]:
    if mode not in {"react_with_tool", "legacy_react_with_tool"}:
        return {
            "tool_effective_status": "not_applicable",
            "tool_successes": [],
            "tool_failures": [],
            "evidence_available": False,
            "critical_tool_failures": [],
        }
    if not isinstance(evidence, dict):
        return {
            "tool_effective_status": "all_failed",
            "tool_successes": [],
            "tool_failures": ["evidence_retrieval"],
            "evidence_available": False,
            "critical_tool_failures": ["hpo_search", "pubmed_search", "web_search"],
        }
    tool_status = evidence.get("tool_status", {})
    if not isinstance(tool_status, dict):
        tool_status = {}
    successes = [name for name, status in tool_status.items() if str(status) == "success"]
    failures = [name for name, status in tool_status.items() if str(status) == "failed"]
    critical_tools = ["hpo_search", "pubmed_search"]
    if "web_search" in tool_status:
        critical_tools.append("web_search")
    critical_called = [name for name in critical_tools if name in tool_status]
    critical_failures = [name for name in critical_called if name in failures]
    critical_successes = [name for name in critical_called if name in successes]
    evidence_available = _has_non_empty_evidence(evidence)
    if not critical_called:
        status = "no_evidence"
    elif len(critical_successes) == len(critical_called) and evidence_available:
        status = "all_success"
    elif len(critical_failures) == len(critical_called):
        status = "all_failed"
    elif evidence_available and len(critical_successes) > 0:
        status = "partial_success"
    else:
        status = "no_evidence"
    return {
        "tool_effective_status": status,
        "tool_successes": successes,
        "tool_failures": failures,
        "evidence_available": evidence_available,
        "critical_tool_failures": critical_failures,
    }


def _assess_tool_effectiveness_from_react_steps(mode: str, react_steps: list[dict[str, Any]]) -> dict[str, Any]:
    if mode not in {"react_agent_with_tool", "react_agent_with_tool_deeprare_official"}:
        return {
            "tool_effective_status": "not_applicable",
            "tool_successes": [],
            "tool_failures": [],
            "evidence_available": False,
            "critical_tool_failures": [],
        }
    tool_step_names: list[str] = []
    tool_successes: list[str] = []
    tool_failures: list[str] = []
    for step in react_steps:
        action_name = str(step.get("action_name", "")).strip()
        if action_name not in {"hpo_search", "pubmed_search"}:
            continue
        tool_step_names.append(action_name)
        if bool(step.get("success", False)):
            tool_successes.append(action_name)
        else:
            tool_failures.append(action_name)
    if not tool_step_names:
        status = "no_evidence"
    elif len(tool_successes) == len(tool_step_names):
        status = "all_success"
    elif len(tool_failures) == len(tool_step_names):
        status = "all_failed"
    else:
        status = "partial_success"
    return {
        "tool_effective_status": status,
        "tool_successes": list(dict.fromkeys(tool_successes)),
        "tool_failures": list(dict.fromkeys(tool_failures)),
        "evidence_available": len(tool_successes) > 0,
        "critical_tool_failures": list(dict.fromkeys(tool_failures)),
    }


def _build_gold_name_candidates(item: BenchmarkItem, code_name_cache: dict[str, list[str]]) -> list[str]:
    names: list[str] = []
    if item.golden_diagnosis_name:
        names.append(item.golden_diagnosis_name)
    if item.golden_diagnosis_code and item.golden_diagnosis_code in code_name_cache:
        names.extend(code_name_cache[item.golden_diagnosis_code])
    seen: set[str] = set()
    uniq: list[str] = []
    for name in names:
        n = name.strip()
        if not n:
            continue
        if n in seen:
            continue
        seen.add(n)
        uniq.append(n)
    return uniq


def _match_prediction(
    normalized_predictions: list[str],
    raw_predictions: list[str],
    item: BenchmarkItem,
    gold_names: list[str],
    k: int,
) -> tuple[bool, str]:
    preds_norm = normalized_predictions[:k]
    preds_raw = raw_predictions[:k]
    gold_code = (item.golden_diagnosis_code or "").upper()
    if not preds_norm:
        return False, "no_match"
    for i, pred_raw in enumerate(preds_raw):
        pred_norm = preds_norm[i] if i < len(preds_norm) else _norm(pred_raw)
        if gold_code and gold_code in pred_raw.upper():
            return True, "code_exact"
        for gold_name in gold_names:
            gnorm = _norm(gold_name)
            if not gnorm:
                continue
            if pred_norm == gnorm:
                return True, "name_exact"
            if gnorm in pred_norm or pred_norm in gnorm:
                return True, "name_contains"
    return False, "no_match"


def _smoke_items(limit: int) -> list[BenchmarkItem]:
    base = [
        BenchmarkItem(
            case_id="smoke_sle_001",
            phenotype_text="Photosensitive rash, oral ulcers, low C3/C4, proteinuria, anti-dsDNA positive.",
            phenotype_ids=["HP:0000992", "HP:0002829", "HP:0000100"],
            phenotype_names=["photosensitive rash", "oral ulcers", "proteinuria"],
            golden_diagnosis="Systemic lupus erythematosus",
            raw={"source": "smoke"},
        ),
        BenchmarkItem(
            case_id="smoke_ra_002",
            phenotype_text="Symmetric MCP/PIP arthritis, morning stiffness, RF and anti-CCP high titer.",
            phenotype_ids=["HP:0002829", "HP:0001370", "HP:0003236"],
            phenotype_names=["symmetric arthritis", "morning stiffness", "anti-CCP positive"],
            golden_diagnosis="Rheumatoid arthritis",
            raw={"source": "smoke"},
        ),
        BenchmarkItem(
            case_id="smoke_anca_003",
            phenotype_text="Fever, hematuria, elevated creatinine, PR3-ANCA positive, pulmonary symptoms.",
            phenotype_ids=["HP:0001945", "HP:0000790", "HP:0001944"],
            phenotype_names=["fever", "hematuria", "renal impairment"],
            golden_diagnosis="ANCA-associated vasculitis",
            raw={"source": "smoke"},
        ),
    ]
    target = max(1, limit)
    if target <= len(base):
        return base[:target]
    items: list[BenchmarkItem] = list(base)
    i = 0
    while len(items) < target:
        template = base[i % len(base)]
        dup_idx = len(items) + 1
        items.append(
            BenchmarkItem(
                case_id=f"{template.case_id}_dup_{dup_idx}",
                phenotype_text=template.phenotype_text,
                phenotype_ids=list(template.phenotype_ids),
                phenotype_names=list(template.phenotype_names),
                golden_diagnosis=template.golden_diagnosis,
                raw={"source": "smoke", "duplicate_of": template.case_id},
            )
        )
        i += 1
    return items


def run_benchmark(
    mode: str,
    limit: int,
    sample_order: str,
    seed: int,
    data_source: str,
    dataset_name: str,
    split: str,
    dataset_file: str,
    official_eval: bool = False,
    allow_smoke: bool = False,
    top5_extractor: str = "deterministic",
) -> Path:
    if top5_extractor not in TOP5_EXTRACTORS:
        raise ValueError(f"Unsupported top5_extractor: {top5_extractor}")
    cfg = get_config()
    _assert_official_benchmark_is_clean(mode, config=cfg, data_source=data_source, allow_smoke=allow_smoke)
    supported_modes = {
        "plain_llm",
        "plain_llm_deeprare_official",
        "react_agent_without_tool",
        "react_agent_without_tool_deeprare_official",
        "react_agent_with_tool",
        "react_agent_with_tool_deeprare_official",
        "legacy_react_without_tool",
        "legacy_react_with_tool",
        "react_without_tool",
        "react_with_tool",
        "fixed_without_tool",
        "fixed_with_tool",
    }
    if mode not in supported_modes:
        raise ValueError(f"Unsupported mode: {mode}")
    normalized_mode = mode
    if mode in {"react_without_tool", "fixed_without_tool"}:
        normalized_mode = "legacy_react_without_tool"
    if mode in {"react_with_tool", "fixed_with_tool"}:
        normalized_mode = "legacy_react_with_tool"
    if normalized_mode in LEGACY_EXPERIMENTAL_MODES:
        print("[WARNING] This is a legacy/experimental mode. It is not part of the official benchmark mainline.")
    logger = V2RunLogger(mode=mode)
    llm = LLMClient(logger=logger, config=cfg)
    items = load_deeprare_items(
        limit=limit,
        data_source=data_source,
        dataset_name=dataset_name,
        split=split,
        dataset_file=dataset_file,
        sample_order=sample_order,
        seed=seed,
        allow_smoke=allow_smoke,
    )

    predictions: list[BenchmarkPrediction] = []
    for item in items:
        sample_start = perf_counter()
        llm_before = len(logger.llm_calls)
        tool_before = len(logger.tool_calls)
        skill_before = len(logger.skill_calls)
        react_output: dict[str, Any] | None = None
        final_action_diagnoses: list[str] = []
        agent_parser_warnings: list[str] = []

        react_steps: list[dict[str, Any]] = []
        if normalized_mode == "plain_llm":
            prompt = (
                "You are a rare disease specialist.\n"
                "Based on the phenotype text, list top 5 likely diagnoses.\n"
                "Output one diagnosis per line with ranking number.\n\n"
                f"Case ID: {item.case_id}\n"
                f"phenotype_text: {item.phenotype_text}\n"
            )
            raw_answer = llm.complete(prompt=prompt, caller="plain_llm")
        elif normalized_mode == "plain_llm_deeprare_official":
            system_prompt = _deeprare_original_system_prompt()
            prompt = _deeprare_original_user_prompt(item.phenotype_text)
            raw_answer = llm.complete(prompt=prompt, caller="plain_llm_deeprare_official", system_prompt=system_prompt)
        elif normalized_mode == "legacy_react_without_tool":
            runner = ReActRunner(mode="react_without_tool", available_tools=[])
            react_output = runner.run(item=item, llm=llm, logger=logger)
            raw_answer = str(react_output.get("raw_answer", ""))
            react_steps = [s.to_dict() for s in (react_output.get("steps") or [])]
        elif normalized_mode == "legacy_react_with_tool":
            runner = ReActRunner(mode="react_with_tool", available_tools=["web_search"])
            react_output = runner.run(item=item, llm=llm, logger=logger)
            raw_answer = str(react_output.get("raw_answer", ""))
            react_steps = [s.to_dict() for s in (react_output.get("steps") or [])]
        elif normalized_mode == "react_agent_without_tool":
            agent = ReActAgent(llm=llm, logger=logger, config=get_config())
            protocol = get_protocol("benchmark_react_agent_without_tool")
            agent_result = agent.run(
                task=item.phenotype_text,
                allowed_actions=protocol.allowed_actions,
                initial_context={
                    "user_request": {
                        "case_id": item.case_id,
                        "phenotype_text": item.phenotype_text,
                        "phenotype_ids": item.phenotype_ids,
                        "phenotype_names": item.phenotype_names,
                    },
                    "extracted_entities": {},
                    "golden_diagnosis": item.golden_diagnosis,
                },
                max_steps=protocol.max_steps,
                mode="benchmark_deeprare",
                protocol_name=protocol.name,
            )
            react_steps = list(agent_result.react_steps)
            agent_parser_warnings = [str(x) for x in (agent_result.parser_warnings or [])]
            final_payload = agent_result.final_answer if isinstance(agent_result.final_answer, dict) else {}
            diagnoses = final_payload.get("diagnoses", []) if isinstance(final_payload.get("diagnoses", []), list) else []
            final_action_diagnoses = [str(x).strip() for x in diagnoses if str(x).strip()][:5]
            raw_answer = str(final_payload.get("answer", "")).strip() or str(agent_result.best_raw_final_answer or "").strip()
            if not raw_answer:
                raw_answer = "\n".join([f"{i+1}. {str(x)}" for i, x in enumerate(diagnoses[:5])])
            if not final_action_diagnoses and raw_answer:
                fallback_topk, _, _, _, _ = _extract_prediction_candidates(raw_answer=raw_answer, top_k=5)
                final_action_diagnoses = fallback_topk
        elif normalized_mode == "react_agent_without_tool_deeprare_official":
            system_prompt = _deeprare_original_system_prompt()
            prompt = _deeprare_original_user_prompt(item.phenotype_text)
            agent = ReActAgent(llm=llm, logger=logger, config=get_config())
            parsed_topk, _, _, _ = _extract_deeprare_top5_from_text("")
            agent_result = agent.run_official_deeprare_single_step(
                case_id=item.case_id,
                phenotype_text=item.phenotype_text,
                system_prompt=system_prompt,
                prompt=prompt,
                diagnoses=parsed_topk,
                auxiliary_observations=[],
                caller_prefix="react_agent_without_tool_deeprare_official",
            )
            react_steps = list(agent_result.react_steps)
            raw_answer = str(agent_result.final_answer.get("answer", "")).strip()
            parsed_topk, _, _, _ = _extract_deeprare_top5_from_text(raw_answer)
            final_action_diagnoses = parsed_topk
        elif normalized_mode == "react_agent_with_tool_deeprare_official":
            system_prompt = _deeprare_original_system_prompt()
            official_allowed_actions = ["hpo_search", "pubmed_search", "final_answer"]
            tool_observations: list[dict[str, Any]] = []
            tool_before = len(logger.tool_calls)
            try:
                hpo_obs = TOOLS["hpo_search"](
                    query=(item.phenotype_ids[0] if item.phenotype_ids else item.phenotype_text),
                    term=(item.phenotype_ids[0] if item.phenotype_ids else item.phenotype_text),
                    top_k=5,
                    logger=logger,
                    caller=f"react_agent_with_tool_deeprare_official.{item.case_id}.hpo_search",
                    config=get_config(),
                )
                tool_observations.append(
                    {
                        "action_name": "hpo_search",
                        "success": True,
                        "observation_id": "obs_0001",
                        "observation_summary": summarize_observation(hpo_obs, 220),
                    }
                )
            except Exception as exc:
                tool_observations.append(
                    {
                        "action_name": "hpo_search",
                        "success": False,
                        "observation_id": "obs_0001",
                        "observation_summary": f"hpo_search_failed: {exc}",
                    }
                )
            try:
                pubmed_obs = TOOLS["pubmed_search"](
                    query=item.phenotype_text,
                    retmax=5,
                    logger=logger,
                    caller=f"react_agent_with_tool_deeprare_official.{item.case_id}.pubmed_search",
                    config=get_config(),
                )
                tool_observations.append(
                    {
                        "action_name": "pubmed_search",
                        "success": True,
                        "observation_id": "obs_0002",
                        "observation_summary": summarize_observation(pubmed_obs, 220),
                    }
                )
            except Exception as exc:
                tool_observations.append(
                    {
                        "action_name": "pubmed_search",
                        "success": False,
                        "observation_id": "obs_0002",
                        "observation_summary": f"pubmed_search_failed: {exc}",
                    }
                )
            auxiliary_lines: list[str] = []
            for obs in tool_observations:
                auxiliary_lines.append(
                    f"- {obs.get('action_name')}: {obs.get('observation_summary')}"
                )
            prompt = _deeprare_original_user_prompt(
                item.phenotype_text,
                auxiliary_context="\n".join(auxiliary_lines),
            )
            agent = ReActAgent(llm=llm, logger=logger, config=get_config())
            parsed_topk, _, _, _ = _extract_deeprare_top5_from_text("")
            retry_count = 0
            while True:
                try:
                    agent_result = agent.run_official_deeprare_single_step(
                        case_id=item.case_id,
                        phenotype_text=item.phenotype_text,
                        system_prompt=system_prompt,
                        prompt=prompt,
                        diagnoses=parsed_topk,
                        auxiliary_observations=tool_observations,
                        caller_prefix="react_agent_with_tool_deeprare_official",
                    )
                    break
                except RuntimeError as exc:
                    err_text = str(exc)
                    is_timeout = "timed out" in err_text.lower()
                    if retry_count >= 1 or not is_timeout:
                        raise
                    retry_count += 1
                    agent_parser_warnings.append("react_with_tool_official_retry_after_timeout")
                    print(
                        f"[WARNING] react_agent_with_tool_deeprare_official timeout for case {item.case_id}; "
                        "retrying once."
                    )
            if official_allowed_actions != ["hpo_search", "pubmed_search", "final_answer"]:
                raise RuntimeError("Official react_with_tool actions are polluted by non-whitelisted actions.")
            react_steps = list(agent_result.react_steps)
            for obs in tool_observations:
                react_steps.insert(
                    max(0, len(react_steps) - 1),
                    {
                        "step_index": len(react_steps),
                        "action_name": str(obs.get("action_name", "")),
                        "action_args": {},
                        "action_valid": True,
                        "observation": {"summary": str(obs.get("observation_summary", ""))},
                        "observation_summary": str(obs.get("observation_summary", "")),
                        "success": bool(obs.get("success", False)),
                        "error": "" if bool(obs.get("success", False)) else str(obs.get("observation_summary", "")),
                    },
                )
            tool_after = len(logger.tool_calls)
            raw_answer = str(agent_result.final_answer.get("answer", "")).strip()
            parsed_topk, _, _, _ = _extract_deeprare_top5_from_text(raw_answer)
            final_action_diagnoses = parsed_topk
            if tool_after == tool_before:
                agent_parser_warnings = agent_parser_warnings + ["with_tool_official_no_tool_calls_logged"]
        else:
            agent = ReActAgent(llm=llm, logger=logger, config=get_config())
            protocol = get_protocol("benchmark_react_agent_with_tool")
            agent_result = agent.run(
                task=item.phenotype_text,
                allowed_actions=protocol.allowed_actions,
                initial_context={
                    "user_request": {
                        "case_id": item.case_id,
                        "phenotype_text": item.phenotype_text,
                        "phenotype_ids": item.phenotype_ids,
                        "phenotype_names": item.phenotype_names,
                    },
                    "extracted_entities": {},
                    "golden_diagnosis": item.golden_diagnosis,
                },
                max_steps=protocol.max_steps,
                mode="benchmark_deeprare",
                protocol_name=protocol.name,
            )
            react_steps = list(agent_result.react_steps)
            agent_parser_warnings = [str(x) for x in (agent_result.parser_warnings or [])]
            final_payload = agent_result.final_answer if isinstance(agent_result.final_answer, dict) else {}
            diagnoses = final_payload.get("diagnoses", []) if isinstance(final_payload.get("diagnoses", []), list) else []
            final_action_diagnoses = [str(x).strip() for x in diagnoses if str(x).strip()][:5]
            raw_answer = str(final_payload.get("answer", "")).strip() or str(agent_result.best_raw_final_answer or "").strip()
            if not raw_answer:
                raw_answer = "\n".join([f"{i+1}. {str(x)}" for i, x in enumerate(diagnoses[:5])])
            if not final_action_diagnoses and raw_answer:
                fallback_topk, _, _, _, _ = _extract_prediction_candidates(raw_answer=raw_answer, top_k=5)
                final_action_diagnoses = fallback_topk

        if normalized_mode in OFFICIAL_BENCHMARK_MODES:
            parsed_topk, parsed_norm, parse_status, parse_warn = _extract_official_top5(
                raw_answer=raw_answer,
                extractor=top5_extractor,
                llm=llm,
                caller_prefix=f"{normalized_mode}.{item.case_id}",
            )
            if parsed_topk:
                prediction_topk = parsed_topk
                normalized_prediction_topk = parsed_norm
            elif final_action_diagnoses:
                prediction_topk = final_action_diagnoses[:5]
                normalized_prediction_topk = [_norm(x) for x in prediction_topk if _norm(x)]
            else:
                prediction_topk = []
                normalized_prediction_topk = []
            prediction_parse_status = parse_status
            parser_warnings = parse_warn
            raw_candidate_lines = [line.strip() for line in str(raw_answer).splitlines() if line.strip()][:20]
        elif final_action_diagnoses:
            prediction_topk = final_action_diagnoses[:5]
            normalized_prediction_topk = [_norm(x) for x in prediction_topk if _norm(x)]
            parser_warnings = []
            prediction_parse_status = "ok"
            raw_candidate_lines = list(prediction_topk)
        else:
            (
                prediction_topk,
                normalized_prediction_topk,
                parser_warnings,
                prediction_parse_status,
                raw_candidate_lines,
            ) = _extract_prediction_candidates(raw_answer=raw_answer, top_k=5)
        if agent_parser_warnings:
            parser_warnings = list(dict.fromkeys(parser_warnings + agent_parser_warnings))
        tool_effect = _assess_tool_effectiveness(
            mode=normalized_mode,
            evidence=(react_output or {}).get("evidence") if isinstance(react_output, dict) else None,
        )
        if normalized_mode in {"react_agent_with_tool", "react_agent_with_tool_deeprare_official"}:
            tool_effect = _assess_tool_effectiveness_from_react_steps(normalized_mode, react_steps)
        norm_top1 = normalized_prediction_topk[0] if normalized_prediction_topk else ""
        c1 = _correct_at_k(prediction_topk, item.golden_diagnosis, k=1)
        c3 = _correct_at_k(prediction_topk, item.golden_diagnosis, k=3)
        c5 = _correct_at_k(prediction_topk, item.golden_diagnosis, k=5)
        latency_ms = round((perf_counter() - sample_start) * 1000, 2)
        final_diagnosis_value = (prediction_topk[0] if prediction_topk else raw_answer)
        final_diagnois_value = final_diagnosis_value
        if normalized_mode in OFFICIAL_BENCHMARK_MODES:
            final_diagnois_value = raw_answer
        prediction = BenchmarkPrediction(
            case_id=item.case_id,
            mode=normalized_mode,
            raw_answer=raw_answer,
            prediction_topk=prediction_topk,
            normalized_prediction_topk=normalized_prediction_topk,
            final_diagnosis=final_diagnosis_value,
            final_diagnois=final_diagnois_value,
            normalized_top1=norm_top1,
            golden_diagnosis=item.golden_diagnosis,
            matched_by=None,
            gold_names_used=[],
            correct_at_1=c1,
            correct_at_3=c3,
            correct_at_5=c5,
            llm_calls=len(logger.llm_calls) - llm_before,
            tool_calls=len(logger.tool_calls) - tool_before,
            skill_calls=len(logger.skill_calls) - skill_before,
            latency_ms=latency_ms,
            trace_path=str(logger.run_dir / "full_trace.md"),
            parser_warnings=parser_warnings,
            prediction_parse_status=prediction_parse_status,
            raw_candidate_lines=raw_candidate_lines,
            tool_effective_status=str(tool_effect.get("tool_effective_status", "not_applicable")),
            tool_successes=[str(x) for x in tool_effect.get("tool_successes", [])],
            tool_failures=[str(x) for x in tool_effect.get("tool_failures", [])],
            evidence_available=bool(tool_effect.get("evidence_available", False)),
            critical_tool_failures=[str(x) for x in tool_effect.get("critical_tool_failures", [])],
            react_steps=react_steps,
        )
        predictions.append(prediction)
        logger.add_prediction(prediction)

    item_by_case = {item.case_id: item for item in items}
    deeprare_results_dir = _write_deeprare_compatible_results(
        predictions=predictions,
        run_dir=logger.run_dir,
        item_by_case=item_by_case,
    )
    official_eval_status = "not_run"
    official_eval_exit_code = -1
    official_eval_output_path = str(logger.run_dir / "deeprare_official_eval_output.txt")
    official_eval_command = ""
    cleaned_predict_rank_count = _clean_eval_artifacts(deeprare_results_dir)
    official_metrics: dict[str, Any] = {}
    smoke_mode = LAST_DATASET_SOURCE == "smoke"
    if smoke_mode:
        official_eval_status = "not_run"
    elif official_eval:
        (
            official_eval_status,
            official_eval_exit_code,
            official_eval_command,
            official_eval_output_path,
        ) = _run_deeprare_official_eval(
            deeprare_results_dir=deeprare_results_dir,
            run_dir=logger.run_dir,
        )
        try:
            official_text = Path(official_eval_output_path).read_text(encoding="utf-8")
            official_metrics = _extract_official_metrics(official_text)
        except Exception:
            official_metrics = {}

    minimal_metrics = _evaluate_predictions(predictions)
    parse_tool_metrics = _build_parse_and_tool_metrics(predictions)
    metrics: dict[str, Any] = {
        "avg_llm_calls": minimal_metrics.get("avg_llm_calls", 0.0),
        "avg_tool_calls": minimal_metrics.get("avg_tool_calls", 0.0),
        "avg_skill_calls": minimal_metrics.get("avg_skill_calls", 0.0),
        "avg_latency_ms": minimal_metrics.get("avg_latency_ms", 0.0),
        "invalid_answer_rate": minimal_metrics.get("invalid_answer_rate", 0.0),
    }
    metrics.update(parse_tool_metrics)
    metrics["data_source"] = LAST_DATASET_SOURCE
    metrics["dataset_name"] = LAST_DATASET_NAME
    metrics["split"] = LAST_ACTUAL_SPLIT
    metrics["real_data"] = LAST_REAL_DATA
    metrics["dataset_file"] = LAST_DATASET_FILE
    metrics["limit"] = limit
    metrics["sample_order"] = sample_order
    metrics["seed"] = seed
    metrics["minimal_eval_is_smoke_only"] = True
    metrics["top5_extractor"] = top5_extractor
    metrics["smoke_metrics"] = minimal_metrics if smoke_mode else {}
    metrics["official_metrics"] = official_metrics
    metrics["official_eval_disabled"] = smoke_mode
    metrics["official_deeprare_eval_status"] = official_eval_status
    metrics["official_deeprare_eval_exit_code"] = official_eval_exit_code
    metrics["official_deeprare_eval_output_path"] = official_eval_output_path
    metrics["official_deeprare_eval_command"] = official_eval_command
    metrics["official_eval_fresh_run"] = True
    metrics["official_eval_cleaned_predict_rank_count"] = cleaned_predict_rank_count
    metrics["cleaned_predict_rank_count"] = cleaned_predict_rank_count
    metrics["deeprare_results_dir"] = str(deeprare_results_dir)
    if smoke_mode:
        metrics["status"] = "smoke_only"
        metrics["primary_metrics_source"] = "smoke_only"
    elif official_eval_status == "success":
        metrics["status"] = "success"
        metrics["primary_metrics_source"] = "deeprare_official_eval"
    elif official_eval:
        metrics["status"] = "partial"
        metrics["primary_metrics_source"] = "no_eval"
    else:
        metrics["status"] = "success"
        metrics["primary_metrics_source"] = "no_eval"
    metrics["official_metrics_all_cases"] = official_metrics
    metrics["official_metrics_tool_all_success"] = {
        "available": False,
        "reason": "requires case-level official predict_rank mapping",
        "case_ids": parse_tool_metrics.get("tool_all_success_case_ids", []),
    }
    metrics["official_metrics_tool_partial_or_failed"] = {
        "available": False,
        "reason": "requires case-level official predict_rank mapping",
        "case_ids": parse_tool_metrics.get("tool_partial_or_failed_case_ids", []),
    }
    if LAST_FALLBACK_REASON:
        metrics["fallback_reason"] = LAST_FALLBACK_REASON
    if normalized_mode in LEGACY_EXPERIMENTAL_MODES:
        metrics["mode_scope"] = "legacy_experimental"
        metrics["mode_scope_warning"] = "This is a legacy/experimental mode. It is not part of the official benchmark mainline."
    if normalized_mode in OFFICIAL_BENCHMARK_MODES:
        metrics["official_benchmark_mainline"] = True
    print("[EVAL] minimal evaluator is smoke-only and not primary.")
    log_path = logger.finalize(metrics=metrics, dataset_source=LAST_DATASET_SOURCE)
    try:
        write_readable_log(run_dir=str(log_path))
    except Exception:
        pass
    _print_metrics(mode=mode, total=len(predictions), metrics=metrics, log_path=log_path)
    if items:
        first = items[0]
        print(
            "first_item_preview:"
            f" case_id={first.case_id}"
            f" phenotype_text={first.phenotype_text[:120]}"
            f" golden_diagnosis={first.golden_diagnosis[:120]}"
        )
    print(f"data_source: {LAST_DATASET_SOURCE}")
    print(f"dataset_name: {LAST_DATASET_NAME}")
    print(f"split: {LAST_ACTUAL_SPLIT}")
    print(f"real_data: {LAST_REAL_DATA}")
    print(f"dataset_file: {LAST_DATASET_FILE or 'auto-select'}")
    print(f"limit: {limit}")
    print(f"sample_order: {sample_order}")
    print(f"seed: {seed}")
    print(f"gold_field: {LAST_GOLD_FIELD or 'unknown'}")
    if LAST_FALLBACK_REASON:
        print(f"fallback_reason: {LAST_FALLBACK_REASON}")
    if LAST_DATA_NOTE:
        print(f"data_note: {LAST_DATA_NOTE}")
    print(f"deeprare_results_dir: {deeprare_results_dir}")
    print(f"official_deeprare_eval_status: {official_eval_status}")
    print(f"official_deeprare_eval_exit_code: {official_eval_exit_code}")
    print(f"official_deeprare_eval_output_path: {official_eval_output_path}")
    return log_path


def _evaluate_predictions(preds: list[BenchmarkPrediction]) -> dict[str, Any]:
    if not preds:
        return {
            "recall_at_1": 0.0,
            "recall_at_3": 0.0,
            "recall_at_5": 0.0,
            "invalid_answer_rate": 1.0,
            "avg_llm_calls": 0.0,
            "avg_tool_calls": 0.0,
            "avg_skill_calls": 0.0,
            "avg_latency_ms": 0.0,
            "evaluator": "minimal_contains_match",
        }
    n = len(preds)
    return {
        "recall_at_1": round(sum(1 for p in preds if p.correct_at_1) / n, 4),
        "recall_at_3": round(sum(1 for p in preds if p.correct_at_3) / n, 4),
        "recall_at_5": round(sum(1 for p in preds if p.correct_at_5) / n, 4),
        "invalid_answer_rate": round(sum(1 for p in preds if len(p.normalized_prediction_topk) == 0) / n, 4),
        "avg_llm_calls": round(sum(p.llm_calls for p in preds) / n, 4),
        "avg_tool_calls": round(sum(p.tool_calls for p in preds) / n, 4),
        "avg_skill_calls": round(sum(p.skill_calls for p in preds) / n, 4),
        "avg_latency_ms": round(sum(p.latency_ms for p in preds) / n, 2),
        "evaluator": "minimal_contains_match",
    }


def _build_parse_and_tool_metrics(preds: list[BenchmarkPrediction]) -> dict[str, Any]:
    parse_failed = sum(1 for p in preds if p.prediction_parse_status == "failed")
    parse_partial = sum(1 for p in preds if p.prediction_parse_status == "partial")
    parse_warning = sum(1 for p in preds if len(p.parser_warnings) > 0)
    all_success_cases = [p.case_id for p in preds if p.tool_effective_status == "all_success"]
    partial_cases = [p.case_id for p in preds if p.tool_effective_status == "partial_success"]
    all_failed_cases = [p.case_id for p in preds if p.tool_effective_status == "all_failed"]
    no_evidence_cases = [p.case_id for p in preds if p.tool_effective_status == "no_evidence"]
    total_with_tool = sum(
        1
        for p in preds
        if p.tool_effective_status in {"all_success", "partial_success", "all_failed", "no_evidence"}
    )
    evidence_non_empty_count = sum(
        1
        for p in preds
        if p.tool_effective_status in {"all_success", "partial_success", "all_failed", "no_evidence"} and p.evidence_available
    )
    effective = len(all_success_cases) + len(partial_cases)
    ratio = round(effective / total_with_tool, 4) if total_with_tool > 0 else 0.0
    evidence_non_empty_ratio = round(evidence_non_empty_count / total_with_tool, 4) if total_with_tool > 0 else 0.0
    return {
        "prediction_parse_failed_count": parse_failed,
        "prediction_parse_partial_count": parse_partial,
        "prediction_parse_warning_count": parse_warning,
        "tool_call_success_ratio": ratio,
        "evidence_non_empty_ratio": evidence_non_empty_ratio,
        "diagnosis_relevant_evidence_ratio": None,
        "tool_effective_ratio": ratio,
        "tool_effective_ratio_note": "deprecated_semantics: indicates tool call/evidence availability, not diagnostic gain",
        "tool_all_success_count": len(all_success_cases),
        "tool_partial_success_count": len(partial_cases),
        "tool_all_failed_count": len(all_failed_cases),
        "tool_no_evidence_count": len(no_evidence_cases),
        "tool_failed_cases": all_failed_cases + no_evidence_cases,
        "tool_all_success_case_ids": all_success_cases,
        "tool_partial_success_case_ids": partial_cases,
        "tool_partial_or_failed_case_ids": partial_cases + all_failed_cases + no_evidence_cases,
    }


def _print_metrics(mode: str, total: int, metrics: dict[str, Any], log_path: Path) -> None:
    official_metrics = metrics.get("official_metrics", {}) if isinstance(metrics.get("official_metrics", {}), dict) else {}
    smoke_metrics = metrics.get("smoke_metrics", {}) if isinstance(metrics.get("smoke_metrics", {}), dict) else {}
    print(f"mode: {mode}")
    print(f"total: {total}")
    print(f"primary_metrics_source: {metrics.get('primary_metrics_source')}")
    if official_metrics:
        print(f"official_recall@1: {official_metrics.get('recall_top_1', 'n/a')}")
        print(f"official_recall@3: {official_metrics.get('recall_top_3', 'n/a')}")
        print(f"official_recall@5: {official_metrics.get('recall_top_5', 'n/a')}")
        print(f"official_median_rank: {official_metrics.get('median_rank', 'n/a')}")
    if smoke_metrics:
        print(f"smoke_recall@1: {smoke_metrics.get('recall_at_1', 'n/a')}")
        print(f"smoke_recall@3: {smoke_metrics.get('recall_at_3', 'n/a')}")
        print(f"smoke_recall@5: {smoke_metrics.get('recall_at_5', 'n/a')}")
    print(f"invalid_answer_rate: {metrics.get('invalid_answer_rate')}")
    print(f"avg_llm_calls: {metrics.get('avg_llm_calls')}")
    print(f"avg_tool_calls: {metrics.get('avg_tool_calls')}")
    print(f"avg_skill_calls: {metrics.get('avg_skill_calls')}")
    print(f"avg_latency_ms: {metrics.get('avg_latency_ms')}")
    print(f"prediction_parse_failed_count: {metrics.get('prediction_parse_failed_count')}")
    print(f"prediction_parse_partial_count: {metrics.get('prediction_parse_partial_count')}")
    print(f"prediction_parse_warning_count: {metrics.get('prediction_parse_warning_count')}")
    print(f"tool_call_success_ratio: {metrics.get('tool_call_success_ratio')}")
    print(f"evidence_non_empty_ratio: {metrics.get('evidence_non_empty_ratio')}")
    print(f"diagnosis_relevant_evidence_ratio: {metrics.get('diagnosis_relevant_evidence_ratio')}")
    print(f"tool_effective_ratio: {metrics.get('tool_effective_ratio')}")
    print(f"tool_all_success_count: {metrics.get('tool_all_success_count')}")
    print(f"tool_partial_success_count: {metrics.get('tool_partial_success_count')}")
    print(f"tool_all_failed_count: {metrics.get('tool_all_failed_count')}")
    print(f"tool_no_evidence_count: {metrics.get('tool_no_evidence_count')}")
    print(f"minimal_eval_is_smoke_only: {metrics.get('minimal_eval_is_smoke_only')}")
    print(f"official_deeprare_eval_status: {metrics.get('official_deeprare_eval_status')}")
    print(f"official_deeprare_eval_exit_code: {metrics.get('official_deeprare_eval_exit_code')}")
    print(f"official_eval_cleaned_predict_rank_count: {metrics.get('official_eval_cleaned_predict_rank_count')}")
    print(f"deeprare_results_dir: {metrics.get('deeprare_results_dir')}")
    print(f"log_path: {log_path}")


def _write_deeprare_compatible_results(
    predictions: list[BenchmarkPrediction],
    run_dir: Path,
    item_by_case: dict[str, BenchmarkItem],
) -> Path:
    out_dir = run_dir / "deeprare_results"
    out_dir.mkdir(parents=True, exist_ok=True)
    for idx, pred in enumerate(predictions):
        item = item_by_case.get(pred.case_id)
        payload = {
            "case_id": pred.case_id,
            "mode": pred.mode,
            "patient_info": item.phenotype_text if item is not None else "",
            "phenotype_text": item.phenotype_text if item is not None else "",
            "phenotypes": item.phenotype_names if item is not None else [],
            "phenotype_ids": item.phenotype_ids if item is not None else [],
            "final_diagnois": pred.final_diagnois,
            "final_diagnosis": pred.final_diagnosis,
            "golden_diagnosis": pred.golden_diagnosis,
            "zero_shot_llm_response": pred.raw_answer,
            "raw_answer": pred.raw_answer,
            "diagnosis_api_response": "",
            "web_diagnosis": "",
            "similar_cases": "",
            "first_round_result": pred.raw_answer,
            "judge_result": [],
            "judgements": "",
            "prediction_topk": pred.prediction_topk,
            "react_steps": pred.react_steps,
            "v2_trace_path": pred.trace_path,
        }
        (out_dir / f"patient_{idx}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return out_dir


def _clean_eval_artifacts(results_dir: Path) -> int:
    cleaned = 0
    for path in sorted(results_dir.glob("patient_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        changed = False
        for key in ("predict_rank", "judgements", "first_round_result", "web_diagnosis"):
            if key in payload:
                payload.pop(key, None)
                cleaned += 1
                changed = True
        if changed:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return cleaned


def _extract_official_metrics(text: str) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    patterns = {
        "recall_top_1": r"(?:Recall@1|recall_top_1)\s*[:=]\s*([0-9]*\.?[0-9]+)",
        "recall_top_3": r"(?:Recall@3|recall_top_3)\s*[:=]\s*([0-9]*\.?[0-9]+)",
        "recall_top_5": r"(?:Recall@5|recall_top_5)\s*[:=]\s*([0-9]*\.?[0-9]+)",
        "median_rank": r"(?:median rank|median_rank)\s*[:=]\s*([0-9]*\.?[0-9]+|No)",
        "total_evaluated": r"(?:total(?: number of files)?|total_evaluated)\s*[:=]\s*(\d+)",
        "no_count": r"(?:No count|not found count|No)\s*[:=]\s*(\d+)",
    }
    for key, pattern in patterns.items():
        m = re.search(pattern, text, re.I)
        if not m:
            continue
        val = m.group(1).strip()
        if key == "median_rank" and val.lower() == "no":
            metrics[key] = "No"
        else:
            try:
                metrics[key] = float(val) if "." in val else int(val)
            except Exception:
                metrics[key] = val
    dict_match = re.search(r"\{[^{}]*recall_top_1[^{}]*\}", text, re.I)
    if dict_match:
        try:
            parsed = ast.literal_eval(dict_match.group(0))
            if isinstance(parsed, dict):
                if "recall_top_1" in parsed:
                    metrics["recall_top_1"] = parsed["recall_top_1"]
                if "recall_top_3" in parsed:
                    metrics["recall_top_3"] = parsed["recall_top_3"]
                if "recall_top_5" in parsed:
                    metrics["recall_top_5"] = parsed["recall_top_5"]
                if "median_rank" in parsed:
                    metrics["median_rank"] = parsed["median_rank"]
                if "medain_rank" in parsed and "median_rank" not in metrics:
                    metrics["median_rank"] = parsed["medain_rank"]
        except Exception:
            pass
    return metrics


def _run_deeprare_official_eval(deeprare_results_dir: Path, run_dir: Path) -> tuple[str, int, str, str]:
    cfg = get_config()
    output_path = run_dir / "deeprare_official_eval_output.txt"
    deeprare_repo = Path(cfg.deeprare_repo_path).expanduser().resolve()
    eval_script = deeprare_repo / "eval.py"
    deepseek_model = os.getenv("DEEPRARE_EVAL_DEEPSEEK_MODEL", "deepseek-v3-241226").strip()
    safe_command = (
        f"python {eval_script} --results_folder {deeprare_results_dir} "
        f"--model deepseek --deepseek_apikey *** --deepseek_model {deepseek_model}"
    )
    if not eval_script.exists():
        msg = (
            f"failed: eval.py not found at {eval_script}\n"
            f"command={safe_command}\n"
        )
        output_path.write_text(msg, encoding="utf-8")
        return "failed", -1, safe_command, str(output_path)

    command = [
        "python",
        str(eval_script),
        "--results_folder",
        str(deeprare_results_dir),
        "--model",
        "deepseek",
        "--deepseek_apikey",
        cfg.llm_api_key,
        "--deepseek_model",
        deepseek_model,
    ]
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        cwd=str(deeprare_repo),
    )
    body = []
    body.append("command:")
    body.append(safe_command)
    body.append("")
    body.append("exit_code:")
    body.append(str(proc.returncode))
    body.append("")
    body.append("stdout:")
    body.append(proc.stdout.strip())
    body.append("")
    body.append("stderr:")
    body.append(proc.stderr.strip())
    body.append("")
    if proc.returncode == 0:
        body.append("status: success")
        status = "success"
    else:
        body.append("status: failed")
        body.append("possible_blockers:")
        body.append("- missing dependencies (openai/anthropic/google-generativeai)")
        body.append("- invalid API key/model for DeepRare eval LLM judge")
        body.append("- missing required fields: final_diagnois/golden_diagnosis")
        status = "failed"
    output_path.write_text("\n".join(body), encoding="utf-8")
    return status, proc.returncode, safe_command, str(output_path)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _correct_at_k(pred_topk: list[str], golden_diagnosis: str, k: int) -> bool:
    preds = pred_topk[:k]
    if not preds:
        return False
    gold_candidates = [g for g in _split_tokens(golden_diagnosis) if g]
    if not gold_candidates:
        gold_candidates = [golden_diagnosis]
    norm_gold = [_norm(g) for g in gold_candidates]
    for p in preds:
        np = _norm(p)
        for ng in norm_gold:
            if not ng:
                continue
            if ng in np or np in ng:
                return True
    return False


def run_parser_check() -> int:
    samples = [
        {
            "name": "Case A",
            "text": (
                "The top 5 likely rare diagnoses are:\n"
                "1. Marfan syndrome\n"
                "2. Ehlers-Danlos syndrome\n"
                "3. Loeys-Dietz syndrome"
            ),
            "expected_contains": ["Marfan syndrome", "Ehlers-Danlos syndrome", "Loeys-Dietz syndrome"],
            "expected_excludes": ["The top 5 likely rare diagnoses are:"],
        },
        {
            "name": "Case B",
            "text": (
                "HP:0001250 = Seizures\n"
                "HP:0004322 = Short stature\n"
                "1. Achondroplasia\n"
                "2. Hypochondroplasia"
            ),
            "expected_contains": ["Achondroplasia", "Hypochondroplasia"],
            "expected_excludes": ["HP:0001250 = Seizures", "HP:0004322 = Short stature"],
        },
        {
            "name": "Case C",
            "text": (
                "Based on the phenotype, the patient may have:\n"
                "1. Systemic lupus erythematosus\n"
                "2. ANCA-associated vasculitis"
            ),
            "expected_contains": ["Systemic lupus erythematosus", "ANCA-associated vasculitis"],
            "expected_excludes": ["Based on the phenotype, the patient may have:"],
        },
    ]
    all_ok = True
    for sample in samples:
        pred, _, warnings, status, raw_lines = _extract_prediction_candidates(sample["text"], top_k=5)
        missing = [x for x in sample["expected_contains"] if x not in pred]
        unexpected = [x for x in sample["expected_excludes"] if x in pred]
        ok = (len(missing) == 0) and (len(unexpected) == 0)
        all_ok = all_ok and ok
        print(f"[PARSER-CHECK] {sample['name']} pass={ok} status={status} warnings={warnings}")
        print(f"raw_lines={raw_lines}")
        print(f"parsed={pred}")
        if missing:
            print(f"missing_expected={missing}")
        if unexpected:
            print(f"unexpected_items={unexpected}")
    print(f"[PARSER-CHECK] overall_pass={all_ok}")
    return 0 if all_ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Minimal DeepRare-style benchmark runner")
    parser.add_argument(
        "--mode",
        type=str,
        default="plain_llm",
        choices=[
            "plain_llm",
            "plain_llm_deeprare_official",
            "react_agent_without_tool",
            "react_agent_without_tool_deeprare_official",
            "react_agent_with_tool",
            "react_agent_with_tool_deeprare_official",
            "legacy_react_without_tool",
            "legacy_react_with_tool",
            "react_without_tool",
            "react_with_tool",
            "fixed_without_tool",
            "fixed_with_tool",
        ],
    )
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--sample-order", type=str, default="sequential", choices=["sequential", "random"])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--data-source", type=str, default="auto", choices=["auto", "hf", "local", "smoke"])
    parser.add_argument("--dataset-name", type=str, default="chenxz/RareBench")
    parser.add_argument("--dataset-file", type=str, default="")
    parser.add_argument("--split", type=str, default="test")
    parser.add_argument("--official-eval", action="store_true")
    parser.add_argument("--allow-smoke", action="store_true")
    parser.add_argument("--top5-extractor", type=str, default="deterministic", choices=["deterministic", "llm"])
    parser.add_argument("--check-parser", action="store_true")
    args = parser.parse_args()
    if args.check_parser:
        raise SystemExit(run_parser_check())
    run_benchmark(
        mode=args.mode,
        limit=args.limit,
        sample_order=args.sample_order,
        seed=args.seed,
        data_source=args.data_source,
        dataset_name=args.dataset_name,
        split=args.split,
        dataset_file=args.dataset_file,
        official_eval=args.official_eval,
        allow_smoke=args.allow_smoke,
        top5_extractor=args.top5_extractor,
    )


if __name__ == "__main__":
    main()
