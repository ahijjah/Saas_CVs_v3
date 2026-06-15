"""
E-02 Phase 1 — Product Module & Feature Flag constants.

Single source of truth for all feature codes and product module definitions.
Future modules/features are added here; no schema change is required.

Naming convention:
  <MODULE_PREFIX>_<FEATURE_NAME>   e.g. AI_SCORING, ATS_WORKFLOWS
  Module sentinels share their prefix:  AI_RECRUITMENT, ATS_MANAGEMENT
"""
from __future__ import annotations

# ── Module A: AI Recruitment Evaluation ──────────────────────────────────────

AI_RECRUITMENT    = "AI_RECRUITMENT"     # module sentinel
AI_JOB_ANALYSIS   = "AI_JOB_ANALYSIS"
AI_SCORING        = "AI_SCORING"
AI_KNOCKOUTS      = "AI_KNOCKOUTS"
AI_EXPLAINABILITY = "AI_EXPLAINABILITY"
AI_COMPARISON     = "AI_COMPARISON"
AI_BULK_UPLOAD    = "AI_BULK_UPLOAD"
AI_INTAKE_EMAIL   = "AI_INTAKE_EMAIL"
AI_INTAKE_PUBLIC  = "AI_INTAKE_PUBLIC"
AI_INTAKE_MANUAL  = "AI_INTAKE_MANUAL"

# ── Module B: ATS Management ──────────────────────────────────────────────────

ATS_MANAGEMENT     = "ATS_MANAGEMENT"    # module sentinel
ATS_WORKFLOWS      = "ATS_WORKFLOWS"
ATS_NOTES          = "ATS_NOTES"
ATS_COMMUNICATIONS = "ATS_COMMUNICATIONS"
ATS_ASSIGNMENTS    = "ATS_ASSIGNMENTS"
ATS_EMAIL_TEMPLATES = "ATS_EMAIL_TEMPLATES"
ATS_REPORTING      = "ATS_REPORTING"

# ── Module registry ───────────────────────────────────────────────────────────
# Maps module sentinel → ordered list of feature codes in that module.
# New modules/features: add an entry here. No DB schema change needed.

MODULES: dict[str, list[str]] = {
    AI_RECRUITMENT: [
        AI_JOB_ANALYSIS,
        AI_SCORING,
        AI_KNOCKOUTS,
        AI_EXPLAINABILITY,
        AI_COMPARISON,
        AI_BULK_UPLOAD,
        AI_INTAKE_EMAIL,
        AI_INTAKE_PUBLIC,
        AI_INTAKE_MANUAL,
    ],
    ATS_MANAGEMENT: [
        ATS_WORKFLOWS,
        ATS_NOTES,
        ATS_COMMUNICATIONS,
        ATS_ASSIGNMENTS,
        ATS_EMAIL_TEMPLATES,
        ATS_REPORTING,
    ],
}

# ── Derived sets (computed once at import) ────────────────────────────────────

# All leaf feature codes (excludes module sentinels)
ALL_FEATURES: frozenset[str] = frozenset(
    code for features in MODULES.values() for code in features
)

# All known codes including module sentinels
ALL_CODES: frozenset[str] = frozenset(MODULES.keys()) | ALL_FEATURES

# Reverse lookup: feature_code → module_code
FEATURE_TO_MODULE: dict[str, str] = {
    feature: module
    for module, features in MODULES.items()
    for feature in features
}
