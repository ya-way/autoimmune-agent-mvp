"""Tool registry and exports."""

from v2.tools.base import ToolResult, build_tool_registry, normalize_answer
from v2.tools.drug import rxnorm_normalize_drug
from v2.tools.literature import pubmed_search
from v2.tools.pathway import opentargets_search, reactome_search
from v2.tools.phenotype import hpo_search
from v2.tools.safety import openfda_drug_event_search
from v2.tools.search import web_search

TOOLS = build_tool_registry(
    ("web_search", web_search),
    ("pubmed_search", pubmed_search),
    ("hpo_search", hpo_search),
    ("reactome_search", reactome_search),
    ("opentargets_search", opentargets_search),
    ("openfda_drug_event_search", openfda_drug_event_search),
    ("rxnorm_normalize_drug", rxnorm_normalize_drug),
    ("normalize_answer", normalize_answer),
)

__all__ = [
    "ToolResult",
    "TOOLS",
    "web_search",
    "pubmed_search",
    "hpo_search",
    "reactome_search",
    "opentargets_search",
    "openfda_drug_event_search",
    "rxnorm_normalize_drug",
    "normalize_answer",
]
