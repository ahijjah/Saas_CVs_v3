"""
AI Model Registry & Stage Defaults — super_admin only.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from routers.admin import require_role

router = APIRouter(prefix="/admin/ai-models", tags=["ai-models"])
RequireSuperAdmin = Depends(require_role("super_admin"))

VALID_STAGES = frozenset([
    "cv_scoring", "cv_comparison", "criteria_extraction",
    "level2_screening", "translation_explanation", "fallback",
])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _model_row(r) -> dict[str, Any]:
    return {
        "model_id":                   str(r["model_id"]),
        "provider":                   r["provider"],
        "model_name":                 r["model_name"],
        "display_name":               r["display_name"],
        "provider_secret_key":        r["provider_secret_key"],
        "base_url":                   r["base_url"],
        "supported_stages":           list(r["supported_stages"] or []),
        "enabled":                    r["enabled"],
        "max_context_tokens":         r["max_context_tokens"],
        "input_price_per_1m_tokens":  float(r["input_price_per_1m_tokens"])  if r["input_price_per_1m_tokens"]  is not None else None,
        "output_price_per_1m_tokens": float(r["output_price_per_1m_tokens"]) if r["output_price_per_1m_tokens"] is not None else None,
        "notes":                      r["notes"],
        "created_at":                 r["created_at"].isoformat() if r["created_at"] else None,
        "updated_at":                 r["updated_at"].isoformat() if r["updated_at"] else None,
    }


# ── Model Registry ─────────────────────────────────────────────────────────────

@router.get("/registry", dependencies=[RequireSuperAdmin])
async def list_models(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rows = (await db.execute(text("""
        SELECT model_id, provider, model_name, display_name, provider_secret_key,
               base_url, supported_stages, enabled, max_context_tokens,
               input_price_per_1m_tokens, output_price_per_1m_tokens, notes,
               created_at, updated_at
        FROM cv_analyzer.ai_model_registry
        ORDER BY provider, model_name
    """))).mappings().all()
    return [_model_row(r) for r in rows]


class ModelCreate(BaseModel):
    provider: str
    model_name: str
    display_name: str
    provider_secret_key: str | None = None
    base_url: str | None = None
    supported_stages: list[str] = []
    enabled: bool = True
    max_context_tokens: int | None = None
    input_price_per_1m_tokens: float | None = None
    output_price_per_1m_tokens: float | None = None
    notes: str | None = None


@router.post("/registry", dependencies=[RequireSuperAdmin])
async def create_model(body: ModelCreate, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    invalid = [s for s in body.supported_stages if s not in VALID_STAGES]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid stages: {invalid}")
    row = (await db.execute(text("""
        INSERT INTO cv_analyzer.ai_model_registry (
            provider, model_name, display_name, provider_secret_key, base_url,
            supported_stages, enabled, max_context_tokens,
            input_price_per_1m_tokens, output_price_per_1m_tokens, notes
        ) VALUES (
            :provider, :model_name, :display_name, :secret_key, :base_url,
            :stages, :enabled, :max_ctx, :in_price, :out_price, :notes
        )
        RETURNING model_id, provider, model_name, display_name, provider_secret_key,
                  base_url, supported_stages, enabled, max_context_tokens,
                  input_price_per_1m_tokens, output_price_per_1m_tokens, notes,
                  created_at, updated_at
    """), {
        "provider": body.provider, "model_name": body.model_name,
        "display_name": body.display_name, "secret_key": body.provider_secret_key,
        "base_url": body.base_url, "stages": body.supported_stages,
        "enabled": body.enabled, "max_ctx": body.max_context_tokens,
        "in_price": body.input_price_per_1m_tokens,
        "out_price": body.output_price_per_1m_tokens, "notes": body.notes,
    })).mappings().first()
    await db.commit()
    return _model_row(row)


class ModelUpdate(BaseModel):
    display_name: str | None = None
    provider_secret_key: str | None = None
    base_url: str | None = None
    supported_stages: list[str] | None = None
    enabled: bool | None = None
    max_context_tokens: int | None = None
    input_price_per_1m_tokens: float | None = None
    output_price_per_1m_tokens: float | None = None
    notes: str | None = None


@router.put("/registry/{model_id}", dependencies=[RequireSuperAdmin])
async def update_model(
    model_id: str, body: ModelUpdate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    if body.supported_stages is not None:
        invalid = [s for s in body.supported_stages if s not in VALID_STAGES]
        if invalid:
            raise HTTPException(status_code=400, detail=f"Invalid stages: {invalid}")

    updates: dict[str, Any] = {}
    if body.display_name               is not None: updates["display_name"]               = body.display_name
    if body.provider_secret_key        is not None: updates["provider_secret_key"]        = body.provider_secret_key
    if body.base_url                   is not None: updates["base_url"]                   = body.base_url
    if body.supported_stages           is not None: updates["supported_stages"]           = body.supported_stages
    if body.enabled                    is not None: updates["enabled"]                    = body.enabled
    if body.max_context_tokens         is not None: updates["max_context_tokens"]         = body.max_context_tokens
    if body.input_price_per_1m_tokens  is not None: updates["input_price_per_1m_tokens"]  = body.input_price_per_1m_tokens
    if body.output_price_per_1m_tokens is not None: updates["output_price_per_1m_tokens"] = body.output_price_per_1m_tokens
    if body.notes                      is not None: updates["notes"]                      = body.notes
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Build SET clause; supported_stages needs explicit CAST for asyncpg
    set_parts = []
    for k in updates:
        if k == "supported_stages":
            set_parts.append("supported_stages = CAST(:supported_stages AS TEXT[])")
        else:
            set_parts.append(f"{k} = :{k}")
    set_clause = ", ".join(set_parts)
    updates["model_id"] = model_id

    row = (await db.execute(text(f"""
        UPDATE cv_analyzer.ai_model_registry
        SET {set_clause}, updated_at = now()
        WHERE model_id = :model_id
        RETURNING model_id, provider, model_name, display_name, provider_secret_key,
                  base_url, supported_stages, enabled, max_context_tokens,
                  input_price_per_1m_tokens, output_price_per_1m_tokens, notes,
                  created_at, updated_at
    """), updates)).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Model not found")
    await db.commit()
    return _model_row(row)


# ── Stage Defaults ─────────────────────────────────────────────────────────────

@router.get("/stage-defaults", dependencies=[RequireSuperAdmin])
async def get_stage_defaults(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rows = (await db.execute(text("""
        SELECT sd.stage,
               sd.primary_model_id,   pm.model_name  AS primary_model_name,  pm.provider  AS primary_provider,  pm.enabled AS primary_enabled,
               sd.fallback_model_id,  fm.model_name  AS fallback_model_name, fm.provider  AS fallback_provider, fm.enabled AS fallback_enabled,
               sd.updated_at
        FROM cv_analyzer.ai_stage_defaults sd
        LEFT JOIN cv_analyzer.ai_model_registry pm ON pm.model_id = sd.primary_model_id
        LEFT JOIN cv_analyzer.ai_model_registry fm ON fm.model_id = sd.fallback_model_id
        ORDER BY sd.stage
    """))).mappings().all()
    return [
        {
            "stage":               r["stage"],
            "primary_model_id":    str(r["primary_model_id"])  if r["primary_model_id"]  else None,
            "primary_model_name":  r["primary_model_name"],
            "primary_provider":    r["primary_provider"],
            "primary_enabled":     r["primary_enabled"],
            "fallback_model_id":   str(r["fallback_model_id"]) if r["fallback_model_id"] else None,
            "fallback_model_name": r["fallback_model_name"],
            "fallback_provider":   r["fallback_provider"],
            "fallback_enabled":    r["fallback_enabled"],
            "updated_at":          r["updated_at"].isoformat() if r["updated_at"] else None,
        }
        for r in rows
    ]


class StageDefaultUpdate(BaseModel):
    primary_model_id: str | None = None
    fallback_model_id: str | None = None


@router.put("/stage-defaults/{stage}", dependencies=[RequireSuperAdmin])
async def update_stage_default(
    stage: str, body: StageDefaultUpdate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    if stage not in VALID_STAGES:
        raise HTTPException(status_code=400, detail=f"Invalid stage: {stage}")

    # Validate: model must be enabled and support the stage
    for field_name, model_id in [("primary_model_id", body.primary_model_id), ("fallback_model_id", body.fallback_model_id)]:
        if not model_id:
            continue
        row = (await db.execute(text("""
            SELECT enabled, supported_stages FROM cv_analyzer.ai_model_registry WHERE model_id = :mid
        """), {"mid": model_id})).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail=f"Model {model_id} not found")
        if not row["enabled"]:
            raise HTTPException(status_code=400, detail=f"Model {model_id} is disabled and cannot be set as default")
        if stage not in list(row["supported_stages"] or []) and stage != "fallback":
            raise HTTPException(status_code=400, detail=f"Model does not support stage '{stage}'")

    await db.execute(text("""
        INSERT INTO cv_analyzer.ai_stage_defaults (stage, primary_model_id, fallback_model_id, updated_at)
        VALUES (:stage, :primary, :fallback, now())
        ON CONFLICT (stage) DO UPDATE SET
            primary_model_id  = EXCLUDED.primary_model_id,
            fallback_model_id = EXCLUDED.fallback_model_id,
            updated_at        = now()
    """), {"stage": stage, "primary": body.primary_model_id, "fallback": body.fallback_model_id})
    await db.commit()
    return {"ok": True, "stage": stage}


# ── Provider secret status (never exposes values) ──────────────────────────────

@router.get("/provider-secrets-status", dependencies=[RequireSuperAdmin])
async def provider_secrets_status(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    from services.ai_model_registry_service import check_provider_secret_status
    status = await check_provider_secret_status(db)
    return {"secrets": status}
