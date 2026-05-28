"""Skill registry and exports."""

from v2.skills.base import build_skill_registry, deeprare_answering, evidence_retrieval, final_answer
from v2.skills.case_review import autoimmune_case_review
from v2.skills.evidence import clinical_evidence_skill, literature_evidence_skill
from v2.skills.mechanism import mechanism_evidence_skill
from v2.skills.normalization import medication_normalization_skill
from v2.skills.phenotype import phenotype_normalization_skill
from v2.skills.safety import drug_safety_skill

SKILLS = build_skill_registry(
    ("evidence_retrieval", evidence_retrieval),
    ("final_answer", final_answer),
    ("deeprare_answering", deeprare_answering),
    ("clinical_evidence", clinical_evidence_skill),
    ("clinical_evidence_skill", clinical_evidence_skill),
    ("literature_evidence_skill", literature_evidence_skill),
    ("phenotype_normalization_skill", phenotype_normalization_skill),
    ("mechanism_evidence", mechanism_evidence_skill),
    ("mechanism_evidence_skill", mechanism_evidence_skill),
    ("drug_safety", drug_safety_skill),
    ("drug_safety_skill", drug_safety_skill),
    ("medication_normalization", medication_normalization_skill),
    ("medication_normalization_skill", medication_normalization_skill),
    ("autoimmune_case_review", autoimmune_case_review),
)

__all__ = [
    "SKILLS",
    "evidence_retrieval",
    "final_answer",
    "deeprare_answering",
    "clinical_evidence_skill",
    "literature_evidence_skill",
    "phenotype_normalization_skill",
    "mechanism_evidence_skill",
    "drug_safety_skill",
    "medication_normalization_skill",
    "autoimmune_case_review",
]
