"""
E-02 Phase 1 — Platform Features Admin Endpoint.

Read-only endpoint that exposes the feature registry and module mappings.
Requires super_admin role — no tenant-scoped RLS needed since this is
a static read of the feature constants, not tenant data.

GET /platform/features
"""
from fastapi import APIRouter, Depends

from auth.dependencies import RequireSuperAdmin
from constants.features import ALL_CODES, ALL_FEATURES, FEATURE_TO_MODULE, MODULES

router = APIRouter(prefix="/platform", tags=["platform-features"])


@router.get("/features", dependencies=[RequireSuperAdmin])
async def get_platform_features():
    """
    Return the full feature registry: modules, features, and their mappings.

    This is a static view of backend/constants/features.py — no DB call.
    """
    return {
        "modules": [
            {
                "module_code": module_code,
                "features": features,
            }
            for module_code, features in MODULES.items()
        ],
        "features": sorted(ALL_FEATURES),
        "all_codes": sorted(ALL_CODES),
        "feature_to_module": FEATURE_TO_MODULE,
    }
