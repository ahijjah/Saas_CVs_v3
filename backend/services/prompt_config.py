"""
Dynamic Prompt & Weight Configuration

Loads AI prompts, scoring weights, and strictness levels from the database
system_config table. Falls back to hardcoded defaults if DB is unavailable.

This decouples configuration from code — operators can tune the AI behaviour
without a code deployment.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── Default weight profiles ───────────────────────────────────────────────────
# Named profiles that can be referenced by job type.
DEFAULT_WEIGHT_PROFILES: dict[str, dict[str, int]] = {
    "technical": {
        "weight_skills": 35,
        "weight_experience": 25,
        "weight_education": 15,
        "weight_certifications": 10,
        "weight_soft_skills": 5,
        "weight_domain_knowledge": 5,
        "weight_other": 5,
    },
    "managerial": {
        "weight_skills": 15,
        "weight_experience": 30,
        "weight_education": 10,
        "weight_certifications": 5,
        "weight_soft_skills": 25,
        "weight_domain_knowledge": 10,
        "weight_other": 5,
    },
    "sales": {
        "weight_skills": 15,
        "weight_experience": 25,
        "weight_education": 5,
        "weight_certifications": 5,
        "weight_soft_skills": 30,
        "weight_domain_knowledge": 15,
        "weight_other": 5,
    },
    "default": {
        "weight_skills": 30,
        "weight_experience": 25,
        "weight_education": 15,
        "weight_certifications": 10,
        "weight_soft_skills": 10,
        "weight_domain_knowledge": 5,
        "weight_other": 5,
    },
}

# ── Strictness levels ─────────────────────────────────────────────────────────
# Controls how harshly the AI scores candidates.
STRICTNESS_PROMPTS: dict[str, str] = {
    "strict": (
        "Apply STRICT evaluation standards. "
        "Only award high scores (80+) when there is direct, explicit evidence. "
        "Ambiguous or implied skills should score below 60."
    ),
    "balanced": (
        "Apply BALANCED evaluation standards. "
        "Award scores based on the weight of evidence. "
        "Reasonable inferences from related experience are acceptable."
    ),
    "lenient": (
        "Apply LENIENT evaluation standards. "
        "Give candidates the benefit of the doubt for transferable skills. "
        "Potential and learning ability are as important as direct experience."
    ),
}


@dataclass
class PromptConfig:
    """Resolved configuration for a single scoring run."""
    weight_profile: str = "default"
    weights: dict[str, int] = field(default_factory=lambda: DEFAULT_WEIGHT_PROFILES["default"].copy())
    strictness: str = "balanced"
    strictness_instruction: str = ""
    output_language: str = "ar"
    # ── Level 1 Gatekeeper parameters (all DB-controlled by Super Admin) ──────
    gatekeeper_enabled: bool = True
    gatekeeper_threshold: float = 0.40    # semantic similarity floor (0.0–1.0)
    skill_fuzzy_threshold: float = 80.0   # rapidfuzz partial_ratio threshold (0–100)
    min_skill_ratio: float = 0.0          # min % of required skills found (0–100); 0 = disabled
    # ── New bilingual dual-threshold auto-reject policy ───────────────────────
    gatekeeper_auto_reject_enabled: bool = True
    gatekeeper_english_similarity_reject_below: float = 0.25   # 0.0–1.0
    gatekeeper_english_skill_ratio_reject_below: float = 10.0  # 0–100
    gatekeeper_non_english_similarity_reject_below: float = 0.15
    gatekeeper_non_english_skill_ratio_reject_below: float = 5.0
    # ── Extraction quality gate ───────────────────────────────────────────────
    min_extracted_text_chars: int = 300
    # ── AI comparison default ─────────────────────────────────────────────────
    enable_ai_comparison_default: bool = False
    # ── Scoring V2 — LLM Criteria Mapping (D-01) ─────────────────────────────
    llm_criteria_mapping_enabled: bool = False
    # Per-job mandatory skills (if non-empty, missing any = automatic penalty)
    mandatory_skills: list[str] = field(default_factory=list)
    mandatory_skills_weight: float = 0.0
    # ── F-01 — Deterministic Scoring Engine (silent mode) ─────────────────────
    det_partial_credit:                  float = 0.50
    det_required_weight:                 float = 0.70
    det_preferred_weight:                float = 0.30
    det_enable_required_absent_floor:    bool  = False
    det_required_absent_floor_threshold: float = 0.50
    det_required_absent_floor_cap:       float = 0.40


async def load_prompt_config(
    db,
    tenant_id: str,
    job_id: str | None = None,
    *,
    overrides: dict[str, Any] | None = None,
) -> PromptConfig:
    """
    Build a PromptConfig by reading system_config (and optionally job-level
    overrides) from the database, then applying any request-level overrides.

    Priority (highest → lowest):
      1. Request-level overrides (API body params)
      2. Job-level config (jobs.scoring_config JSONB, if column exists)
      3. Tenant-level config (tenants.scoring_config JSONB, if column exists)
      4. system_config table defaults
      5. Hardcoded defaults in this module
    """
    from config import get_settings
    from sqlalchemy import text

    cfg = get_settings()

    # Load system_config — all gatekeeper + scoring parameters
    sys_rows = await db.execute(
        text("""
            SELECT key, value FROM system_config
            WHERE key IN (
                'gatekeeper_semantic_threshold',
                'gatekeeper_skill_fuzzy_threshold', 'skill_fuzzy_threshold',
                'gatekeeper_min_skill_ratio',
                'gatekeeper_enabled', 'level1_gatekeeper_enabled',
                'output_language', 'default_weight_profile', 'default_strictness',
                'gatekeeper_auto_reject_enabled',
                'gatekeeper_english_similarity_reject_below',
                'gatekeeper_english_skill_ratio_reject_below',
                'gatekeeper_non_english_similarity_reject_below',
                'gatekeeper_non_english_skill_ratio_reject_below',
                'min_extracted_text_chars',
                'enable_ai_comparison_default',
                'scoring_v2.llm_criteria_mapping_enabled',
                'scoring_v2.det_partial_credit',
                'scoring_v2.det_required_weight',
                'scoring_v2.det_preferred_weight',
                'scoring_v2.det_enable_required_absent_floor',
                'scoring_v2.det_required_absent_floor_threshold',
                'scoring_v2.det_required_absent_floor_cap'
            )
        """)
    )
    sys_map: dict[str, str] = {r["key"]: r["value"] for r in sys_rows.mappings()}

    gatekeeper_threshold = float(sys_map.get("gatekeeper_semantic_threshold", cfg.gatekeeper_semantic_threshold))

    # level1_gatekeeper_enabled takes precedence over legacy gatekeeper_enabled
    if "level1_gatekeeper_enabled" in sys_map:
        gatekeeper_enabled = sys_map["level1_gatekeeper_enabled"].lower() == "true"
    else:
        gatekeeper_enabled = sys_map.get("gatekeeper_enabled", "true").lower() == "true"

    # gatekeeper_skill_fuzzy_threshold is the canonical key; fall back to legacy skill_fuzzy_threshold
    raw_skill_fuzzy = (
        sys_map.get("gatekeeper_skill_fuzzy_threshold")
        or sys_map.get("skill_fuzzy_threshold")
    )
    skill_fuzzy_threshold = float(raw_skill_fuzzy) if raw_skill_fuzzy else cfg.gatekeeper_skill_fuzzy_threshold

    min_skill_ratio = float(sys_map.get("gatekeeper_min_skill_ratio", "0"))

    output_language = sys_map.get("output_language", cfg.output_language)
    weight_profile_name = sys_map.get("default_weight_profile", "default")
    strictness = sys_map.get("default_strictness", "balanced")

    weights = DEFAULT_WEIGHT_PROFILES.get(weight_profile_name, DEFAULT_WEIGHT_PROFILES["default"]).copy()

    # Apply overrides
    if overrides:
        if "weight_profile" in overrides:
            profile = overrides["weight_profile"]
            if profile in DEFAULT_WEIGHT_PROFILES:
                weights = DEFAULT_WEIGHT_PROFILES[profile].copy()
                weight_profile_name = profile

        # Allow per-dimension weight override
        for wk in ["weight_skills", "weight_experience", "weight_education",
                   "weight_certifications", "weight_soft_skills",
                   "weight_domain_knowledge", "weight_other"]:
            if wk in overrides and isinstance(overrides[wk], int):
                weights[wk] = overrides[wk]

        if "strictness" in overrides and overrides["strictness"] in STRICTNESS_PROMPTS:
            strictness = overrides["strictness"]
        if "gatekeeper_threshold" in overrides:
            gatekeeper_threshold = float(overrides["gatekeeper_threshold"])
        if "skill_fuzzy_threshold" in overrides:
            skill_fuzzy_threshold = float(overrides["skill_fuzzy_threshold"])
        if "min_skill_ratio" in overrides:
            min_skill_ratio = float(overrides["min_skill_ratio"])
        if "gatekeeper_enabled" in overrides:
            gatekeeper_enabled = bool(overrides["gatekeeper_enabled"])
        if "output_language" in overrides:
            output_language = overrides["output_language"]

    # Validate weights sum to 100
    total = sum(weights.values())
    if total != 100:
        diff = 100 - total
        weights["weight_skills"] = weights.get("weight_skills", 0) + diff

    # ── New scoring enhancement parameters ────────────────────────────────────
    gatekeeper_auto_reject_enabled = (
        sys_map.get("gatekeeper_auto_reject_enabled", "true").lower() == "true"
    )
    gk_en_sim = float(sys_map.get("gatekeeper_english_similarity_reject_below", "0.25"))
    gk_en_skill = float(sys_map.get("gatekeeper_english_skill_ratio_reject_below", "10"))
    gk_non_en_sim = float(sys_map.get("gatekeeper_non_english_similarity_reject_below", "0.15"))
    gk_non_en_skill = float(sys_map.get("gatekeeper_non_english_skill_ratio_reject_below", "5"))
    min_extracted_text_chars = int(float(sys_map.get("min_extracted_text_chars", "300")))
    enable_ai_comparison_default = (
        sys_map.get("enable_ai_comparison_default", "false").lower() == "true"
    )
    llm_criteria_mapping_enabled = (
        sys_map.get("scoring_v2.llm_criteria_mapping_enabled", "false").lower() == "true"
    )

    # ── F-01 deterministic scoring parameters ─────────────────────────────────
    det_partial_credit = float(sys_map.get("scoring_v2.det_partial_credit", "0.50"))
    det_required_weight = float(sys_map.get("scoring_v2.det_required_weight", "0.70"))
    det_preferred_weight = float(sys_map.get("scoring_v2.det_preferred_weight", "0.30"))
    det_enable_required_absent_floor = (
        sys_map.get("scoring_v2.det_enable_required_absent_floor", "false").lower() == "true"
    )
    det_required_absent_floor_threshold = float(
        sys_map.get("scoring_v2.det_required_absent_floor_threshold", "0.50")
    )
    det_required_absent_floor_cap = float(
        sys_map.get("scoring_v2.det_required_absent_floor_cap", "0.40")
    )

    return PromptConfig(
        weight_profile=weight_profile_name,
        weights=weights,
        strictness=strictness,
        strictness_instruction=STRICTNESS_PROMPTS.get(strictness, STRICTNESS_PROMPTS["balanced"]),
        output_language=output_language,
        gatekeeper_enabled=gatekeeper_enabled,
        gatekeeper_threshold=gatekeeper_threshold,
        skill_fuzzy_threshold=skill_fuzzy_threshold,
        min_skill_ratio=min_skill_ratio,
        gatekeeper_auto_reject_enabled=gatekeeper_auto_reject_enabled,
        gatekeeper_english_similarity_reject_below=gk_en_sim,
        gatekeeper_english_skill_ratio_reject_below=gk_en_skill,
        gatekeeper_non_english_similarity_reject_below=gk_non_en_sim,
        gatekeeper_non_english_skill_ratio_reject_below=gk_non_en_skill,
        min_extracted_text_chars=min_extracted_text_chars,
        enable_ai_comparison_default=enable_ai_comparison_default,
        llm_criteria_mapping_enabled=llm_criteria_mapping_enabled,
        mandatory_skills=overrides.get("mandatory_skills", []) if overrides else [],
        mandatory_skills_weight=float(overrides.get("mandatory_skills_weight", 0)) if overrides else 0.0,
        det_partial_credit=det_partial_credit,
        det_required_weight=det_required_weight,
        det_preferred_weight=det_preferred_weight,
        det_enable_required_absent_floor=det_enable_required_absent_floor,
        det_required_absent_floor_threshold=det_required_absent_floor_threshold,
        det_required_absent_floor_cap=det_required_absent_floor_cap,
    )
