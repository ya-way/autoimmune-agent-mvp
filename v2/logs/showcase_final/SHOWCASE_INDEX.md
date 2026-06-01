# SHOWCASE INDEX

| Case | Intent / Mode | Status | Action Sequence | LLM Calls | Tool Calls | Observation Citations | Key Point | Report |
|---|---|---|---|---:|---:|---|---|---|
| case1_autoimmune_evidence_convergence | autoimmune_case_review | success | `clinical_evidence_skill -> drug_safety_skill -> mechanism_evidence_skill -> final_answer` | 5 | 9 | obs_0001, obs_0002, obs_0003 | Constrained ReAct evidence convergence | [case_report](./case1_autoimmune_evidence_convergence/case_report.md) |
| case2_drug_safety_no_silent_default | drug_safety | success | `medication_normalization_skill -> final_answer` | 3 | 2 | obs_0001 | No silent default; unresolved class medication is surfaced explicitly | [case_report](./case2_drug_safety_no_silent_default/case_report.md) |
