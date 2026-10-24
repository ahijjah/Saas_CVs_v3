"""Matrix layouts: the strict model, validation against the job's column registry, precedence and persistence.

A layout is {columns, frozen_candidate, sort, filters, page_size}. `columns` is both the visibility and the order. Precedence for the
layout shown to a user is whole-layout, first one found wins:

    personal view  ->  job configuration  ->  tenant default  ->  system default

Writing validates every column id, sort column and filter reference against the registry for the job (the tenant default may name only
static columns, because it applies to every job). Saved rows are tenant-scoped by row-level security and versioned; a write can carry
`expected_version` and is refused with a conflict when somebody else saved first.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.matrix import registry as R

SCOPES = ("tenant", "job", "personal")
MAX_COLUMNS = 80
MAX_ITEM_FILTERS = 20
Ref = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_:.-]{1,70}$")]
Workflow = Annotated[str, StringConstraints(pattern=r"^[a-z_]{1,40}$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ItemFilter(_Strict):
    item_id: Ref
    results: list[Literal["met", "partially_met", "not_met", "unclear", "not_assessed"]] = Field(min_length=1, max_length=5)


class PreferredFilter(_Strict):
    item_id: Ref
    results: list[Literal["yes", "no", "unclear", "not_assessed"]] = Field(min_length=1, max_length=4)


class GateFilter(_Strict):
    gate_key: Ref
    results: list[Literal["passed", "failed", "review_required", "not_assessed"]] = Field(min_length=1, max_length=4)


class Filters(_Strict):
    search: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    eligibility: list[Literal["eligible", "not_eligible", "review_required", "not_assessed"]] | None = Field(None, max_length=4)
    scoring_status: list[Literal["scored", "review_required", "not_scored", "legacy", "none"]] | None = Field(None, max_length=5)
    score_min: int | None = Field(None, ge=0, le=100)
    score_max: int | None = Field(None, ge=0, le=100)
    workflow_status: list[Workflow] | None = Field(None, max_length=20)
    source: list[Annotated[str, StringConstraints(pattern=r"^[a-z_]{1,40}$")]] | None = Field(None, max_length=10)
    flags: list[Literal["unclear_criteria", "conflicting_evidence", "evidence_not_found", "low_confidence", "processing_error",
                        "unresolved_review"]] | None = Field(None, max_length=6)
    applied_after: date | None = None
    applied_before: date | None = None
    assigned_to: Annotated[str, StringConstraints(pattern=r"^(me|unassigned|[0-9a-fA-F-]{36})$")] | None = None
    mandatory: list[ItemFilter] = Field(default_factory=list, max_length=MAX_ITEM_FILTERS)
    preferred: list[PreferredFilter] = Field(default_factory=list, max_length=MAX_ITEM_FILTERS)
    gates: list[GateFilter] = Field(default_factory=list, max_length=MAX_ITEM_FILTERS)

    @model_validator(mode="after")
    def _ranges(self) -> "Filters":
        if self.score_min is not None and self.score_max is not None and self.score_min > self.score_max:
            raise ValueError("score_min is greater than score_max")
        if self.applied_after and self.applied_before and self.applied_after > self.applied_before:
            raise ValueError("applied_after is later than applied_before")
        return self


class Sort(_Strict):
    column: Ref
    direction: Literal["asc", "desc"] = "desc"


class Layout(_Strict):
    columns: list[Ref] = Field(min_length=1, max_length=MAX_COLUMNS)
    frozen_candidate: bool = True
    sort: Sort | None = None
    filters: Filters = Field(default_factory=Filters)
    page_size: int = Field(25, ge=10, le=100)

    @model_validator(mode="after")
    def _shape(self) -> "Layout":
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("a column appears twice")
        if self.columns[0] != R.NAME:
            raise ValueError(f"the first column must be {R.NAME}")
        for cid in self.columns:
            if not R.well_formed(cid):
                raise ValueError(f"{cid!r} is not a column id")
        if self.sort is not None and not R.well_formed(self.sort.column):
            raise ValueError("the sort column is not a column id")
        return self


SYSTEM_DEFAULT = Layout(columns=list(R.DEFAULT_ORDER), sort=Sort(column="candidate.applied_at", direction="desc"))


class LayoutProblem(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_layout(layout: Layout, registry: dict[str, R.Column], *, scope: str) -> None:
    """Every named column and reference must exist for this job (tenant scope: static columns only). Raises LayoutProblem."""
    for cid in layout.columns:
        column = registry.get(cid)
        if column is None and scope == "tenant" and R.parse_dynamic(cid):
            raise LayoutProblem("tenant_dynamic_column", "The tenant default can use only the standard columns; criteria columns are chosen per job.")
        if column is None:
            raise LayoutProblem("unknown_column", f"{cid!r} is not a column of this matrix.")
        if not column.available:
            raise LayoutProblem("column_unavailable", f"{cid!r} is not available: {column.unavailable_reason}")
        if scope == "tenant" and column.kind != "static":
            raise LayoutProblem("tenant_dynamic_column", "The tenant default can use only the standard columns; criteria columns are chosen per job.")
    if layout.sort is not None:
        column = registry.get(layout.sort.column)
        if column is None or not column.available or not column.sortable:
            raise LayoutProblem("sort_not_allowed", f"The matrix cannot be sorted by {layout.sort.column!r}.")
    f = layout.filters
    if scope == "tenant" and (f.mandatory or f.preferred or f.gates):
        raise LayoutProblem("tenant_dynamic_filter", "The tenant default cannot filter on individual criteria.")
    for flt in f.mandatory:
        column = registry.get(R.column_id("mandatory", flt.item_id))
        if column is None or column.kind != "mandatory":
            raise LayoutProblem("unknown_filter_target", f"{flt.item_id!r} is not a Required item of this job.")
    for flt in f.preferred:
        column = registry.get(R.column_id("preferred", flt.item_id))
        if column is None or column.kind != "preferred":
            raise LayoutProblem("unknown_filter_target", f"{flt.item_id!r} is not a Preferred item of this job.")
    for flt in f.gates:
        column = registry.get(R.column_id("gate", flt.gate_key))
        if column is None or column.kind != "gate":
            raise LayoutProblem("unknown_filter_target", f"{flt.gate_key!r} is not a gate of this job.")


def resolve_columns(layout: Layout, registry: dict[str, R.Column]) -> tuple[Layout, list[str]]:
    """The layout with columns this job no longer offers removed (they stay in the stored layout), and the removed ids."""
    usable, unavailable = R.partition(layout.columns, registry)
    if R.NAME not in usable:
        usable.insert(0, R.NAME)
    sort = layout.sort
    if sort is not None and (sort.column not in registry or not registry[sort.column].available):
        sort = None
    shown = layout.model_copy(update={"columns": usable, "sort": sort})
    return shown, unavailable


# ── persistence ────────────────────────────────────────────────────────────────────────────────────────────────────

_SELECT = text("""
    SELECT c.scope, c.config, c.version, c.updated_at, COALESCE(u.full_name, u.email) AS updated_by_name
    FROM candidate_matrix_configs c LEFT JOIN users u ON u.user_id = c.updated_by
    WHERE c.tenant_id = CAST(:tid AS uuid)
      AND ((c.scope = 'tenant')
        OR (c.scope = 'job' AND c.job_id = CAST(:jid AS uuid))
        OR (c.scope = 'personal' AND c.job_id = CAST(:jid AS uuid) AND c.user_id = CAST(:uid AS uuid)))""")


def _parse(raw) -> Layout | None:
    """A stored layout, or None when it no longer parses (it is then ignored, never an error for the reader)."""
    try:
        return Layout.model_validate(raw if isinstance(raw, dict) else json.loads(raw))
    except Exception:                                               # noqa: BLE001 - an unreadable stored layout falls through to the next layer
        return None


async def load_layers(db: AsyncSession, *, tenant_id: str, job_id: str, user_id: str) -> dict[str, dict | None]:
    layers: dict[str, dict | None] = {s: None for s in SCOPES}
    for row in (await db.execute(_SELECT, {"tid": tenant_id, "jid": job_id, "uid": user_id})).mappings():
        layout = _parse(row["config"])
        layers[row["scope"]] = {"layout": layout, "raw": row["config"], "version": row["version"],
                                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                                "updated_by": row["updated_by_name"]}
    return layers


def effective_layout(layers: dict[str, dict | None]) -> tuple[Layout, str]:
    """(layout, source) by precedence: personal, job, tenant, system. An unreadable layer is skipped."""
    for scope in ("personal", "job", "tenant"):
        layer = layers.get(scope)
        if layer is not None and layer["layout"] is not None:
            return layer["layout"], scope
    return SYSTEM_DEFAULT, "system"


class VersionConflict(Exception):
    pass


async def save_layout(db: AsyncSession, *, tenant_id: str, scope: str, job_id: str | None, user_id: str, layout: Layout,
                      expected_version: int | None) -> int:
    """Insert or update the layout of one scope; returns the new version. expected_version None = create (conflict if one exists);
    otherwise it must equal the stored version. The caller owns the transaction and has authorised the scope."""
    params = {"tid": tenant_id, "scope": scope, "jid": job_id if scope != "tenant" else None,
              "owner": user_id if scope == "personal" else None, "uid": user_id,
              "config": json.dumps(layout.model_dump(mode="json"), ensure_ascii=False)}
    where = {"tenant": "scope = 'tenant'", "job": "scope = 'job' AND job_id = CAST(:jid AS uuid)",
             "personal": "scope = 'personal' AND job_id = CAST(:jid AS uuid) AND user_id = CAST(:owner AS uuid)"}[scope]
    current = (await db.execute(text(f"SELECT version FROM candidate_matrix_configs WHERE tenant_id = CAST(:tid AS uuid) AND {where} FOR UPDATE"),
                                params)).scalar_one_or_none()
    if current is None:
        if expected_version is not None:
            raise VersionConflict()
        return (await db.execute(text("""
            INSERT INTO candidate_matrix_configs (tenant_id, scope, job_id, user_id, config, created_by, updated_by)
            VALUES (CAST(:tid AS uuid), :scope, CAST(:jid AS uuid), CAST(:owner AS uuid), CAST(:config AS jsonb), CAST(:uid AS uuid), CAST(:uid AS uuid))
            RETURNING version"""), params)).scalar_one()
    if expected_version != current:
        raise VersionConflict()
    return (await db.execute(text(f"""
        UPDATE candidate_matrix_configs SET config = CAST(:config AS jsonb), version = version + 1, updated_by = CAST(:uid AS uuid), updated_at = now()
        WHERE tenant_id = CAST(:tid AS uuid) AND {where} RETURNING version"""), params)).scalar_one()


async def delete_layout(db: AsyncSession, *, tenant_id: str, scope: str, job_id: str | None, user_id: str) -> bool:
    where = {"tenant": "scope = 'tenant'", "job": "scope = 'job' AND job_id = CAST(:jid AS uuid)",
             "personal": "scope = 'personal' AND job_id = CAST(:jid AS uuid) AND user_id = CAST(:uid AS uuid)"}[scope]
    result = await db.execute(text(f"DELETE FROM candidate_matrix_configs WHERE tenant_id = CAST(:tid AS uuid) AND {where}"),
                              {"tid": tenant_id, "jid": job_id, "uid": user_id})
    return (result.rowcount or 0) > 0
