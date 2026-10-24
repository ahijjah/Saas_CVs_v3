"""Final Candidate Matrix: a job-specific, transparent longlist.

Every route is tenant-scoped (row-level security plus an explicit tenant predicate) and job-scoped with the same client-assignment
access rule as the Candidates workspace. Reads are open to every tenant role; saving the shared tenant or job layout needs an admin or
HR manager; saving one's own view needs an admin, HR manager or recruiter; the CSV export follows the existing export roles (a viewer may
not take data out). Request bodies are strict: an unknown field is a 422. Nothing here scores, ranks or decides: the numbers come from
the stored scoring run and gate results (services/matrix/query.py).
"""
from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep
from auth.roles import REQUIREMENTS_EDITOR_ROLES, MATRIX_PERSONAL_ROLES, EXPORT_ROLES, RequireExport, RequireMatrixPersonal, \
    RequireRequirementsEditor, RequireRequirementsReader, role_allowed
from database import get_db, set_rls_context
from routers.applications import _build_candidate_filter_clause
from services.audit_service import log_action
from services.eligibility_service import load_job_gates
from services.matrix import config as C
from services.matrix import export as X
from services.matrix import query as Q
from services.matrix import registry as R

router = APIRouter(prefix="/candidate-matrix", tags=["candidate-matrix"])


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RowsRequest(_Strict):
    columns: list[C.Ref] | None = Field(None, min_length=1, max_length=C.MAX_COLUMNS)
    filters: C.Filters | None = None
    sort: C.Sort | None = None
    page: int = Field(1, ge=1, le=100000)
    page_size: int | None = Field(None, ge=10, le=100)


class SaveRequest(_Strict):
    layout: C.Layout
    expected_version: int | None = Field(None, ge=1)


class ExportRequest(_Strict):
    columns: list[C.Ref] | None = Field(None, min_length=1, max_length=C.MAX_COLUMNS)
    filters: C.Filters | None = None
    sort: C.Sort | None = None
    lang: str = Field("en", pattern=r"^(en|ar)$")
    include_metadata: bool = True


def _detail(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


async def _job(db: AsyncSession, user, job_id: str) -> dict[str, Any]:
    """The job (title, code, client name, organization name) when it belongs to this tenant and the user may see it; else 404."""
    try:
        uuid.UUID(str(job_id))
    except ValueError:
        raise _detail(404, "job_not_found", "Job not found.")
    await set_rls_context(db, user.tenant_id, user.role)
    row = (await db.execute(text("""
        SELECT j.job_id::text AS job_id, j.title, j.job_code, co.organization_name AS client_name, t.name AS organization_name
        FROM jobs j JOIN tenants t ON t.tenant_id = j.tenant_id
        LEFT JOIN client_organizations co ON co.client_organization_id = j.client_organization_id
        WHERE j.job_id = CAST(:jid AS uuid) AND j.tenant_id = CAST(:tid AS uuid)
          AND (:is_admin = TRUE OR j.client_organization_id IS NULL OR EXISTS (
                 SELECT 1 FROM agency_user_clients auc WHERE auc.user_id = CAST(:uid AS uuid)
                   AND auc.client_organization_id = j.client_organization_id AND auc.tenant_id = CAST(:tid AS uuid)))"""),
        {"jid": job_id, "tid": user.tenant_id, "uid": user.user_id, "is_admin": (user.role or "").lower() in ("admin", "super_admin")})).mappings().first()
    if row is None:
        raise _detail(404, "job_not_found", "Job not found.")
    return dict(row)


async def _registry(db: AsyncSession, job_id: str) -> dict[str, R.Column]:
    analysis = (await db.execute(text("SELECT analysis_json FROM job_criteria WHERE job_id = CAST(:jid AS uuid)"), {"jid": job_id})).scalar_one_or_none()
    document = analysis.get("requirements") if isinstance(analysis, dict) else None
    gates, _ = await load_job_gates(db, job_id)
    return {c.id: c for c in R.job_columns(document, gates)}


def _check(layout: C.Layout, registry: dict[str, R.Column], scope: str) -> None:
    try:
        C.validate_layout(layout, registry, scope=scope)
    except C.LayoutProblem as exc:
        raise _detail(422, exc.code, str(exc)) from exc


def _permissions(role: str | None) -> dict[str, bool]:
    return {"edit_shared": role_allowed(role, REQUIREMENTS_EDITOR_ROLES), "edit_personal": role_allowed(role, MATRIX_PERSONAL_ROLES),
            "export": role_allowed(role, EXPORT_ROLES)}


def _layer_view(layer: dict | None) -> dict | None:
    return None if layer is None else {"version": layer["version"], "updated_at": layer["updated_at"], "updated_by": layer["updated_by"],
                                       "readable": layer["layout"] is not None}


@router.get("/jobs/{job_id}/columns", dependencies=[RequireRequirementsReader])
async def get_columns(job_id: str, current_user: CurrentUserDep, db: Annotated[AsyncSession, Depends(get_db)]):
    """The column definitions of this job: the standard columns and one column per Required item, Preferred item and enforced gate."""
    try:
        job = await _job(db, current_user, job_id)
        registry = await _registry(db, job["job_id"])
    finally:
        await db.rollback()
    return {"job_id": job["job_id"], "groups": list(R.GROUPS), "columns": [c.as_dict() for c in registry.values()],
            "vocabulary": {"mandatory": list(R.MANDATORY_RESULTS), "preferred": list(R.PREFERRED_RESULTS), "gates": list(R.GATE_RESULTS),
                           "eligibility": list(R.ELIGIBILITY), "flags": list(R.FLAG_KINDS)}}


@router.get("/jobs/{job_id}/config", dependencies=[RequireRequirementsReader])
async def get_config(job_id: str, current_user: CurrentUserDep, db: Annotated[AsyncSession, Depends(get_db)]):
    """The layout in effect for this user and job (personal, then job, then tenant, then system), the layers that exist, and what the user may change."""
    try:
        job = await _job(db, current_user, job_id)
        registry = await _registry(db, job["job_id"])
        layers = await C.load_layers(db, tenant_id=current_user.tenant_id, job_id=job["job_id"], user_id=current_user.user_id)
    finally:
        await db.rollback()
    layout, source = C.effective_layout(layers)
    shown, unavailable = C.resolve_columns(layout, registry)
    return {"job_id": job["job_id"], "source": source, "effective": shown.model_dump(mode="json"), "unavailable_columns": unavailable,
            "layers": {s: _layer_view(layers[s]) for s in C.SCOPES}, "system_default": C.SYSTEM_DEFAULT.model_dump(mode="json"),
            "permissions": _permissions(current_user.role)}


async def _save(db: AsyncSession, user, *, scope: str, job: dict | None, body: SaveRequest) -> dict[str, Any]:
    registry = await _registry(db, job["job_id"]) if job else {c.id: c for c in R.STATIC}
    _check(body.layout, registry, scope)
    try:
        version = await C.save_layout(db, tenant_id=user.tenant_id, scope=scope, job_id=job["job_id"] if job else None, user_id=user.user_id,
                                      layout=body.layout, expected_version=body.expected_version)
        if scope != "personal":
            await log_action(db, user.tenant_id, user.user_id, getattr(user, "email", None), "matrix_config_saved",
                             "job" if job else "tenant", job["job_id"] if job else user.tenant_id,
                             {"scope": scope, "version": version, "columns": len(body.layout.columns)})
        await db.commit()
    except C.VersionConflict as exc:
        await db.rollback()
        raise _detail(409, "version_conflict", "The layout was changed by someone else. Reload it and try again.") from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise _detail(500, "write_failed", "The layout could not be saved.") from exc
    return {"scope": scope, "version": version}


async def _reset(db: AsyncSession, user, *, scope: str, job: dict | None) -> dict[str, Any]:
    try:
        removed = await C.delete_layout(db, tenant_id=user.tenant_id, scope=scope, job_id=job["job_id"] if job else None, user_id=user.user_id)
        if scope != "personal" and removed:
            await log_action(db, user.tenant_id, user.user_id, getattr(user, "email", None), "matrix_config_reset",
                             "job" if job else "tenant", job["job_id"] if job else user.tenant_id, {"scope": scope})
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise _detail(500, "write_failed", "The layout could not be reset.") from exc
    return {"scope": scope, "removed": removed}


@router.put("/jobs/{job_id}/config/job", dependencies=[RequireRequirementsEditor])
async def put_job_config(job_id: str, body: SaveRequest, current_user: CurrentUserDep, db: Annotated[AsyncSession, Depends(get_db)]):
    try:
        job = await _job(db, current_user, job_id)
        return await _save(db, current_user, scope="job", job=job, body=body)
    except HTTPException:
        await db.rollback()
        raise


@router.put("/jobs/{job_id}/config/personal", dependencies=[RequireMatrixPersonal])
async def put_personal_config(job_id: str, body: SaveRequest, current_user: CurrentUserDep, db: Annotated[AsyncSession, Depends(get_db)]):
    try:
        job = await _job(db, current_user, job_id)
        return await _save(db, current_user, scope="personal", job=job, body=body)
    except HTTPException:
        await db.rollback()
        raise


@router.delete("/jobs/{job_id}/config/job", dependencies=[RequireRequirementsEditor])
async def delete_job_config(job_id: str, current_user: CurrentUserDep, db: Annotated[AsyncSession, Depends(get_db)]):
    try:
        job = await _job(db, current_user, job_id)
        return await _reset(db, current_user, scope="job", job=job)
    except HTTPException:
        await db.rollback()
        raise


@router.delete("/jobs/{job_id}/config/personal", dependencies=[RequireMatrixPersonal])
async def delete_personal_config(job_id: str, current_user: CurrentUserDep, db: Annotated[AsyncSession, Depends(get_db)]):
    try:
        job = await _job(db, current_user, job_id)
        return await _reset(db, current_user, scope="personal", job=job)
    except HTTPException:
        await db.rollback()
        raise


@router.get("/tenant-config", dependencies=[RequireRequirementsReader])
async def get_tenant_config(current_user: CurrentUserDep, db: Annotated[AsyncSession, Depends(get_db)]):
    try:
        await set_rls_context(db, current_user.tenant_id, current_user.role)
        row = (await db.execute(text("SELECT config, version, updated_at FROM candidate_matrix_configs WHERE tenant_id = CAST(:tid AS uuid) AND scope = 'tenant'"),
                                {"tid": current_user.tenant_id})).mappings().first()
    finally:
        await db.rollback()
    layout = C._parse(row["config"]) if row else None
    return {"layout": (layout or C.SYSTEM_DEFAULT).model_dump(mode="json"), "stored": layout is not None, "version": row["version"] if row else None,
            "permissions": _permissions(current_user.role)}


@router.put("/tenant-config", dependencies=[RequireRequirementsEditor])
async def put_tenant_config(body: SaveRequest, current_user: CurrentUserDep, db: Annotated[AsyncSession, Depends(get_db)]):
    try:
        await set_rls_context(db, current_user.tenant_id, current_user.role)
        return await _save(db, current_user, scope="tenant", job=None, body=body)
    except HTTPException:
        await db.rollback()
        raise


@router.delete("/tenant-config", dependencies=[RequireRequirementsEditor])
async def delete_tenant_config(current_user: CurrentUserDep, db: Annotated[AsyncSession, Depends(get_db)]):
    try:
        await set_rls_context(db, current_user.tenant_id, current_user.role)
        return await _reset(db, current_user, scope="tenant", job=None)
    except HTTPException:
        await db.rollback()
        raise


async def _plan(db: AsyncSession, user, job: dict, registry: dict[str, R.Column], body: RowsRequest | ExportRequest) -> tuple[C.Layout, list[str]]:
    """The layout a request asks for: what it names, the rest from the layout in effect. Validated against this job's registry."""
    layers = await C.load_layers(db, tenant_id=user.tenant_id, job_id=job["job_id"], user_id=user.user_id)
    base, _ = C.effective_layout(layers)
    shown, _ = C.resolve_columns(base, registry)
    named = body.columns or shown.columns
    named = [R.NAME, *[c for c in named if c != R.NAME]]                 # the candidate identity column is always present and first
    try:
        layout = C.Layout(columns=named, frozen_candidate=shown.frozen_candidate, sort=body.sort if body.sort is not None else shown.sort,
                          filters=body.filters if body.filters is not None else shown.filters,
                          page_size=getattr(body, "page_size", None) or shown.page_size)
    except ValueError as exc:                                              # a repeated column or a malformed id
        raise _detail(422, "invalid_layout", str(exc)) from exc
    _check(layout, registry, "job")
    return layout, list(layout.columns)


@router.post("/jobs/{job_id}/rows", dependencies=[RequireRequirementsReader])
async def get_rows(job_id: str, body: RowsRequest, current_user: CurrentUserDep, db: Annotated[AsyncSession, Depends(get_db)]):
    """One page of the matrix. Sorting is the recruiter's explicit choice; ties break by newest application, then id, so paging is stable."""
    try:
        job = await _job(db, current_user, job_id)
        registry = await _registry(db, job["job_id"])
        layout, visible = await _plan(db, current_user, job, registry, body)
        where, params = _build_candidate_filter_clause(current_user, (current_user.role or "").lower() in ("admin", "super_admin"), job_id=job["job_id"])
        rows, total = await Q.fetch_rows(db, access_where=where, access_params=params, user_id=current_user.user_id, filters=layout.filters,
                                         sort=layout.sort, registry=registry, visible=visible, page=body.page, page_size=layout.page_size)
    finally:
        await db.rollback()
    return {"job_id": job["job_id"], "rows": rows, "total": total, "page": body.page, "page_size": layout.page_size, "columns": visible,
            "sort": layout.sort.model_dump() if layout.sort else None}


@router.post("/jobs/{job_id}/export", dependencies=[RequireExport])
async def export_matrix(job_id: str, body: ExportRequest, current_user: CurrentUserDep, db: Annotated[AsyncSession, Depends(get_db)]):
    """The matrix as UTF-8 CSV: the requested (or effective) visible columns in order, for the requested filters and sort."""
    try:
        job = await _job(db, current_user, job_id)
        registry = await _registry(db, job["job_id"])
        layout, visible = await _plan(db, current_user, job, registry, body)
        where, params = _build_candidate_filter_clause(current_user, (current_user.role or "").lower() in ("admin", "super_admin"), job_id=job["job_id"])
        rows, total = await Q.fetch_rows(db, access_where=where, access_params=params, user_id=current_user.user_id, filters=layout.filters,
                                         sort=layout.sort, registry=registry, visible=visible, page=None, page_size=None)
        if total > Q.EXPORT_ROW_LIMIT:
            raise _detail(422, "too_many_rows", f"The export is limited to {Q.EXPORT_ROW_LIMIT} candidates. Narrow the filters.")
        columns = [registry[c] for c in visible]
        sort = f"{layout.sort.column} {layout.sort.direction}" if layout.sort else None
        meta = {"job": f"{job['title']} ({job['job_code']})" if job.get("job_code") else job["title"], "client": job.get("client_name"),
                "organization": job.get("organization_name"), "filters": X.describe_filters(body.lang, layout.filters.model_dump(mode="json", exclude_none=True)),
                "sort": sort} if body.include_metadata else None
        data = X.build_csv(lang=body.lang, columns=columns, rows=rows, meta=meta)
        await log_action(db, current_user.tenant_id, current_user.user_id, getattr(current_user, "email", None), "matrix_exported", "job",
                         job["job_id"], {"rows": len(rows), "columns": visible})
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    finally:
        await db.rollback()
    name = "candidate-matrix-" + (job.get("job_code") or job["job_id"][:8]).replace(" ", "-")
    return Response(content=data, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
