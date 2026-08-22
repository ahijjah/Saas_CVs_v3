"""
E-02 Phase 1 & 2 — Platform Features Admin Endpoints.

All endpoints require super_admin role.

Phase 1 (static):
  GET  /platform/features                       — feature registry (no DB)

Phase 2 (management):
  GET  /platform/tenants/features               — paginated tenant overview
  GET  /platform/tenants/{tenant_id}/features   — full feature state for one tenant
  POST /platform/tenants/{tenant_id}/features/{feature_code}/enable
  POST /platform/tenants/{tenant_id}/features/{feature_code}/disable
  POST /platform/tenants/{tenant_id}/modules/{module_code}/enable
  POST /platform/tenants/{tenant_id}/modules/{module_code}/disable

No feature enforcement happens here — this is administration only.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep, RequireSuperAdmin
from constants.features import ALL_CODES, ALL_FEATURES, FEATURE_TO_MODULE, MODULES
from database import get_db
from services.feature_service import (
    bulk_disable_module,
    bulk_enable_module,
    disable_feature,
    enable_feature,
    get_tenant_features,
)

router = APIRouter(prefix="/platform", tags=["platform-features"])

DbDep = Annotated[AsyncSession, Depends(get_db)]


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _require_tenant(db: AsyncSession, tenant_id: str) -> dict:
    """Return tenant row or raise 404."""
    row = await db.execute(
        text(
            "SELECT tenant_id, name AS tenant_name, email_domain AS tenant_code, status "
            "FROM cv_analyzer.tenants "
            "WHERE tenant_id = CAST(:tid AS uuid)"
        ),
        {"tid": tenant_id},
    )
    tenant = row.mappings().first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return dict(tenant)


def _require_feature_code(code: str) -> None:
    """Raise 422 if code is not a known leaf feature."""
    if code not in ALL_FEATURES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown feature code '{code}'. Valid codes: {sorted(ALL_FEATURES)}",
        )


def _require_module_code(code: str) -> None:
    """Raise 422 if code is not a known module sentinel."""
    if code not in MODULES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown module code '{code}'. Valid modules: {sorted(MODULES.keys())}",
        )


def _build_module_summary(features: dict[str, bool]) -> list[dict]:
    """Shape feature dict into per-module summary list."""
    return [
        {
            "module_code": mod,
            "module_enabled": features.get(mod, True),
            "features": [
                {"feature_code": fc, "is_enabled": features.get(fc, True)}
                for fc in mod_features
            ],
            "enabled_count": sum(1 for fc in mod_features if features.get(fc, True)),
            "total_count": len(mod_features),
        }
        for mod, mod_features in MODULES.items()
    ]


# ── Phase 1: Static registry ──────────────────────────────────────────────────

@router.get("/features", dependencies=[RequireSuperAdmin])
async def get_platform_features():
    """Return the full feature registry: modules, features, and their mappings."""
    return {
        "modules": [
            {"module_code": mod, "features": feats}
            for mod, feats in MODULES.items()
        ],
        "features": sorted(ALL_FEATURES),
        "all_codes": sorted(ALL_CODES),
        "feature_to_module": FEATURE_TO_MODULE,
    }


# ── Phase 2: Tenant feature overview ─────────────────────────────────────────

@router.get("/tenants/features", dependencies=[RequireSuperAdmin])
async def list_tenant_features_overview(
    db: DbDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str = Query("", description="Filter by tenant name or code"),
):
    """
    Paginated overview of all tenants with their module enable states.

    Returns summary cards data (totals) and per-tenant rows showing
    AI_RECRUITMENT and ATS_MANAGEMENT enabled status.
    """
    offset = (page - 1) * page_size
    search_param = f"%{search}%" if search else "%"

    # Summary card counts
    summary_row = await db.execute(
        text(
            "SELECT "
            "  COUNT(DISTINCT t.tenant_id) AS total_tenants, "
            "  COUNT(DISTINCT CASE WHEN COALESCE(ai.is_enabled, true) THEN t.tenant_id END) AS ai_enabled, "
            "  COUNT(DISTINCT CASE WHEN COALESCE(ats.is_enabled, true) THEN t.tenant_id END) AS ats_enabled, "
            "  COUNT(DISTINCT CASE "
            "    WHEN EXISTS (SELECT 1 FROM cv_analyzer.tenant_features tf "
            "                 WHERE tf.tenant_id = t.tenant_id AND tf.is_enabled = false) "
            "    THEN t.tenant_id END) AS custom_configs "
            "FROM cv_analyzer.tenants t "
            "LEFT JOIN cv_analyzer.tenant_features ai  ON ai.tenant_id  = t.tenant_id AND ai.feature_code  = 'AI_RECRUITMENT' "
            "LEFT JOIN cv_analyzer.tenant_features ats ON ats.tenant_id = t.tenant_id AND ats.feature_code = 'ATS_MANAGEMENT' "
            "WHERE (t.name ILIKE :search OR t.email_domain ILIKE :search)"
        ),
        {"search": search_param},
    )
    summary = dict(summary_row.mappings().first() or {})

    # Per-tenant rows
    rows_result = await db.execute(
        text(
            "SELECT "
            "  t.tenant_id, t.name AS tenant_name, t.email_domain AS tenant_code, t.status, "
            "  COALESCE(ai.is_enabled,  true) AS ai_recruitment_enabled, "
            "  COALESCE(ats.is_enabled, true) AS ats_management_enabled, "
            "  COALESCE(feat_counts.enabled_count, 0) AS enabled_features_count, "
            "  COALESCE(feat_counts.total_count,   0) AS configured_features_count "
            "FROM cv_analyzer.tenants t "
            "LEFT JOIN cv_analyzer.tenant_features ai  ON ai.tenant_id  = t.tenant_id AND ai.feature_code  = 'AI_RECRUITMENT' "
            "LEFT JOIN cv_analyzer.tenant_features ats ON ats.tenant_id = t.tenant_id AND ats.feature_code = 'ATS_MANAGEMENT' "
            "LEFT JOIN ( "
            "  SELECT tenant_id, "
            "    SUM(CASE WHEN is_enabled THEN 1 ELSE 0 END) AS enabled_count, "
            "    COUNT(*) AS total_count "
            "  FROM cv_analyzer.tenant_features "
            "  GROUP BY tenant_id "
            ") feat_counts ON feat_counts.tenant_id = t.tenant_id "
            "WHERE (t.name ILIKE :search OR t.email_domain ILIKE :search) "
            "ORDER BY t.name "
            "LIMIT :lim OFFSET :off"
        ),
        {"search": search_param, "lim": page_size, "off": offset},
    )
    tenants = [dict(r) for r in rows_result.mappings()]

    return {
        "summary": {
            "total_tenants":   int(summary.get("total_tenants", 0)),
            "ai_enabled":      int(summary.get("ai_enabled", 0)),
            "ats_enabled":     int(summary.get("ats_enabled", 0)),
            "custom_configs":  int(summary.get("custom_configs", 0)),
        },
        "tenants": [
            {
                "tenant_id":                str(t["tenant_id"]),
                "tenant_name":              t["tenant_name"],
                "tenant_code":              t["tenant_code"],
                "status":                   t["status"],
                "ai_recruitment_enabled":   bool(t["ai_recruitment_enabled"]),
                "ats_management_enabled":   bool(t["ats_management_enabled"]),
                "enabled_features_count":   int(t["enabled_features_count"]),
                "configured_features_count": int(t["configured_features_count"]),
            }
            for t in tenants
        ],
        "pagination": {
            "page":      page,
            "page_size": page_size,
            "total":     int(summary.get("total_tenants", 0)),
        },
    }


@router.get("/tenants/{tenant_id}/features", dependencies=[RequireSuperAdmin])
async def get_tenant_feature_detail(
    tenant_id: str,
    db: DbDep,
):
    """Full feature state for a single tenant, grouped by module."""
    tenant = await _require_tenant(db, tenant_id)
    features = await get_tenant_features(db, tenant_id)

    return {
        "tenant_id":   str(tenant["tenant_id"]),
        "tenant_name": tenant["tenant_name"],
        "tenant_code": tenant["tenant_code"],
        "status":      tenant["status"],
        "modules":     _build_module_summary(features),
        "all_features": {code: features.get(code, True) for code in sorted(ALL_CODES)},
    }


# ── Phase 2: Individual feature toggles ──────────────────────────────────────

@router.post("/tenants/{tenant_id}/features/{feature_code}/enable")
async def enable_tenant_feature(
    tenant_id: str,
    feature_code: str,
    db: DbDep,
    current_user: CurrentUserDep,
    _guard: None = RequireSuperAdmin,
):
    """Enable a single feature for a tenant."""
    _require_feature_code(feature_code)
    await _require_tenant(db, tenant_id)

    await enable_feature(db, tenant_id, feature_code, changed_by=current_user.user_id)

    return {"tenant_id": tenant_id, "feature_code": feature_code, "is_enabled": True}


@router.post("/tenants/{tenant_id}/features/{feature_code}/disable")
async def disable_tenant_feature(
    tenant_id: str,
    feature_code: str,
    db: DbDep,
    current_user: CurrentUserDep,
    _guard: None = RequireSuperAdmin,
):
    """Disable a single feature for a tenant."""
    _require_feature_code(feature_code)
    await _require_tenant(db, tenant_id)

    await disable_feature(db, tenant_id, feature_code, changed_by=current_user.user_id)

    return {"tenant_id": tenant_id, "feature_code": feature_code, "is_enabled": False}


# ── Phase 2: Module-level bulk toggles ───────────────────────────────────────

@router.post("/tenants/{tenant_id}/modules/{module_code}/enable")
async def enable_tenant_module(
    tenant_id: str,
    module_code: str,
    db: DbDep,
    current_user: CurrentUserDep,
    _guard: None = RequireSuperAdmin,
):
    """Enable a module sentinel and all its leaf features for a tenant."""
    _require_module_code(module_code)
    await _require_tenant(db, tenant_id)

    await bulk_enable_module(db, tenant_id, module_code, changed_by=current_user.user_id)

    return {
        "tenant_id":   tenant_id,
        "module_code": module_code,
        "is_enabled":  True,
        "features_updated": [module_code] + list(MODULES[module_code]),
    }


@router.post("/tenants/{tenant_id}/modules/{module_code}/disable")
async def disable_tenant_module(
    tenant_id: str,
    module_code: str,
    db: DbDep,
    current_user: CurrentUserDep,
    _guard: None = RequireSuperAdmin,
):
    """Disable a module sentinel and all its leaf features for a tenant."""
    _require_module_code(module_code)
    await _require_tenant(db, tenant_id)

    await bulk_disable_module(db, tenant_id, module_code, changed_by=current_user.user_id)

    return {
        "tenant_id":   tenant_id,
        "module_code": module_code,
        "is_enabled":  False,
        "features_updated": [module_code] + list(MODULES[module_code]),
    }
