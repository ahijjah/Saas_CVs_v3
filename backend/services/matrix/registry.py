"""The controlled column registry of the Final Candidate Matrix.

A column is addressed ONLY by a stable id from this registry; nothing the client sends is ever used as SQL or as a field name. The
static columns are fixed here. The dynamic columns are built from the job's CURRENT requirements document and gates, and use the
immutable requirement item id or gate key, never a label:

    mandatory:<requirement item id>      one Required item
    preferred:<requirement item id>      one Preferred item
    gate:<gate key>                      one Mandatory Gate (gate key is req:<item id> or q:<question id>)

A saved layout may name a dynamic column that no longer exists (the requirement was removed or superseded): it stays readable and is
reported as unavailable; it is never queried or exported.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

CATEGORIES = ("skills", "experience", "education", "certifications", "soft_skills", "domain_knowledge", "other_requirements")

GROUP_CANDIDATE, GROUP_DECISION, GROUP_MANDATORY, GROUP_PREFERRED, GROUP_GATES, GROUP_WORKFLOW = (
    "candidate", "decision", "mandatory", "preferred", "gates", "workflow")
GROUPS = (GROUP_CANDIDATE, GROUP_DECISION, GROUP_MANDATORY, GROUP_PREFERRED, GROUP_GATES, GROUP_WORKFLOW)

NAME = "candidate.name"          # the frozen identity column: always first, cannot be hidden or reordered away
_ID_PART = r"[A-Za-z0-9_-]{1,64}"
_DYNAMIC = (
    ("mandatory", re.compile(rf"^mandatory:({_ID_PART})$")),
    ("preferred", re.compile(rf"^preferred:({_ID_PART})$")),
    ("gate", re.compile(rf"^gate:((?:req|q):{_ID_PART})$")),
)

MANDATORY_RESULTS = ("met", "partially_met", "not_met", "unclear", "not_assessed")
PREFERRED_RESULTS = ("yes", "no", "unclear", "not_assessed")      # a Preferred item never exposes partial compliance
GATE_RESULTS = ("passed", "failed", "review_required", "not_assessed")
ELIGIBILITY = ("eligible", "not_eligible", "review_required", "not_assessed")
SCORING_STATUSES = ("scored", "review_required", "not_scored", "legacy", "none")
FLAG_KINDS = ("unclear_criteria", "conflicting_evidence", "evidence_not_found", "low_confidence", "processing_error", "unresolved_review")
SEVERITIES = ("high", "medium", "low")


@dataclass(frozen=True)
class Column:
    id: str
    group: str
    label_en: str
    label_ar: str
    sortable: bool = False
    available: bool = True
    unavailable_reason: str | None = None
    kind: str = "static"                  # static | mandatory | preferred | gate
    ref: str | None = None                # the item id / gate key of a dynamic column
    default: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "group": self.group, "label": {"en": self.label_en, "ar": self.label_ar}, "sortable": self.sortable,
                "available": self.available, "unavailable_reason": self.unavailable_reason, "kind": self.kind, "ref": self.ref,
                "default": self.default, "required": self.id == NAME}


_NOT_STORED = "This information is not stored for candidates, and the system does not infer it from the CV."

STATIC: tuple[Column, ...] = (
    Column(NAME, GROUP_CANDIDATE, "Candidate", "المرشح", sortable=True, default=True),
    Column("candidate.position", GROUP_CANDIDATE, "Current / latest position", "المسمى الوظيفي الحالي / الأخير",
           available=False, unavailable_reason=_NOT_STORED),
    Column("candidate.location", GROUP_CANDIDATE, "Location", "الموقع", available=False, unavailable_reason=_NOT_STORED),
    Column("candidate.applied_at", GROUP_CANDIDATE, "Application date", "تاريخ التقديم", sortable=True, default=True),
    Column("candidate.source", GROUP_CANDIDATE, "Application source", "مصدر الطلب"),
    Column("candidate.assigned_to", GROUP_CANDIDATE, "Assigned to", "المسؤول"),
    Column("candidate.tags", GROUP_CANDIDATE, "Tags", "الوسوم"),
    Column("decision.eligibility", GROUP_DECISION, "Eligibility", "الأهلية", sortable=True, default=True),
    Column("decision.score", GROUP_DECISION, "Overall score", "الدرجة الإجمالية", sortable=True, default=True),
    Column("decision.scoring", GROUP_DECISION, "Scoring status", "حالة التقييم", sortable=True, default=True),
    Column("decision.flags", GROUP_DECISION, "AI / review flags", "تنبيهات الذكاء الاصطناعي / المراجعة", sortable=True, default=True),
    Column("mandatory.summary", GROUP_MANDATORY, "Mandatory criteria", "المعايير الإلزامية", sortable=True, default=True),
    Column("preferred.summary", GROUP_PREFERRED, "Preferred criteria", "المعايير المفضلة", sortable=True, default=True),
    Column("gates.summary", GROUP_GATES, "Gates", "بوابات الأهلية", sortable=True, default=True),
    Column("workflow.status", GROUP_WORKFLOW, "Workflow status", "حالة سير العمل", sortable=True, default=True),
)
STATIC_BY_ID = {c.id: c for c in STATIC}
DEFAULT_COLUMNS = tuple(c.id for c in STATIC if c.default and c.available)
# Shown in the order a recruiter reads them: identity, the decision elements side by side, then the workflow.
DEFAULT_ORDER = (NAME, "decision.eligibility", "decision.score", "decision.scoring", "mandatory.summary", "preferred.summary",
                 "gates.summary", "decision.flags", "candidate.applied_at", "workflow.status")
assert set(DEFAULT_ORDER) == set(DEFAULT_COLUMNS)


def parse_dynamic(column_id: str) -> tuple[str, str] | None:
    """(kind, ref) for a well-formed dynamic column id, else None. Says nothing about whether the job has it."""
    for kind, pattern in _DYNAMIC:
        match = pattern.match(column_id)
        if match:
            return kind, match.group(1)
    return None


def column_id(kind: str, ref: str) -> str:
    return f"{kind}:{ref}"


def well_formed(column_id_: str) -> bool:
    return column_id_ in STATIC_BY_ID or parse_dynamic(column_id_) is not None


def _short(text: Any, limit: int = 80) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[:limit - 1] + "…"


def requirement_items(document: Any) -> list[dict[str, str]]:
    """[{id, importance, text}] of a requirements document, in document order. Defensive: a document that is missing or not shaped like
    a Requirements V2 document yields nothing rather than failing the matrix."""
    out: list[dict[str, str]] = []
    categories = document.get("categories") if isinstance(document, dict) else None
    if not isinstance(categories, dict):
        return out
    for category in CATEGORIES:
        block = categories.get(category)
        items = block.get("items") if isinstance(block, dict) else None
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item.get("importance") in ("required", "preferred") \
                    and isinstance(item.get("text"), str) and parse_dynamic(f"mandatory:{item['id']}"):
                out.append({"id": item["id"], "importance": item["importance"], "text": item["text"]})
    return out


def job_columns(document: Any, gates: Iterable[Any]) -> list[Column]:
    """The full registry for one job: the static columns plus one column per Required item, Preferred item and enforced gate."""
    columns = list(STATIC)
    for item in requirement_items(document):
        kind = "mandatory" if item["importance"] == "required" else "preferred"
        group = GROUP_MANDATORY if kind == "mandatory" else GROUP_PREFERRED
        label = _short(item["text"])
        columns.append(Column(column_id(kind, item["id"]), group, label, label, sortable=True, kind=kind, ref=item["id"]))
    for gate in gates:
        label = _short(gate.label)
        columns.append(Column(column_id("gate", gate.key), GROUP_GATES, label, label, sortable=True, kind="gate", ref=gate.key))
    return columns


def partition(layout_columns: Iterable[str], registry: dict[str, Column]) -> tuple[list[str], list[str]]:
    """(usable column ids in order, unavailable ids). A saved id that the registry does not offer is unavailable, not an error."""
    usable: list[str] = []
    unavailable: list[str] = []
    for cid in layout_columns:
        column = registry.get(cid)
        (usable if column is not None and column.available else unavailable).append(cid)
    return usable, unavailable
