"""
E-02 Phase 1 — Feature Flag Foundation tests.

Covers:
  * Feature registry integrity (constants/features.py)
  * Module-to-feature mappings
  * Feature service logic (unit-testable without a DB)
  * Default-enabled behaviour
  * Cache invalidation contract
  * Migration compatibility (seed codes match registry)

All tests are pure-function / state-simulation — no DB or HTTP server needed.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from constants.features import (
    AI_BULK_UPLOAD,
    AI_COMPARISON,
    AI_EXPLAINABILITY,
    AI_INTAKE_EMAIL,
    AI_INTAKE_MANUAL,
    AI_INTAKE_PUBLIC,
    AI_JOB_ANALYSIS,
    AI_KNOCKOUTS,
    AI_RECRUITMENT,
    AI_SCORING,
    ALL_CODES,
    ALL_FEATURES,
    ATS_ASSIGNMENTS,
    ATS_COMMUNICATIONS,
    ATS_EMAIL_TEMPLATES,
    ATS_MANAGEMENT,
    ATS_NOTES,
    ATS_REPORTING,
    ATS_WORKFLOWS,
    FEATURE_TO_MODULE,
    MODULES,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

# Feature codes seeded by migration 102 — must stay in sync with the SQL file
_SEEDED_CODES = {
    "AI_RECRUITMENT", "AI_JOB_ANALYSIS", "AI_SCORING", "AI_KNOCKOUTS",
    "AI_EXPLAINABILITY", "AI_COMPARISON", "AI_BULK_UPLOAD",
    "AI_INTAKE_EMAIL", "AI_INTAKE_PUBLIC", "AI_INTAKE_MANUAL",
    "ATS_MANAGEMENT", "ATS_WORKFLOWS", "ATS_NOTES", "ATS_COMMUNICATIONS",
    "ATS_ASSIGNMENTS", "ATS_EMAIL_TEMPLATES", "ATS_REPORTING",
}


# ── TestFeatureRegistry ───────────────────────────────────────────────────────

class TestFeatureRegistry:
    """Verify constants/features.py integrity."""

    def test_ai_recruitment_module_sentinel_defined(self):
        assert AI_RECRUITMENT == "AI_RECRUITMENT"

    def test_ats_management_module_sentinel_defined(self):
        assert ATS_MANAGEMENT == "ATS_MANAGEMENT"

    def test_all_ai_features_defined(self):
        expected = {
            AI_JOB_ANALYSIS, AI_SCORING, AI_KNOCKOUTS, AI_EXPLAINABILITY,
            AI_COMPARISON, AI_BULK_UPLOAD, AI_INTAKE_EMAIL, AI_INTAKE_PUBLIC,
            AI_INTAKE_MANUAL,
        }
        assert expected.issubset(ALL_FEATURES)

    def test_all_ats_features_defined(self):
        expected = {
            ATS_WORKFLOWS, ATS_NOTES, ATS_COMMUNICATIONS, ATS_ASSIGNMENTS,
            ATS_EMAIL_TEMPLATES, ATS_REPORTING,
        }
        assert expected.issubset(ALL_FEATURES)

    def test_module_sentinels_not_in_all_features(self):
        # Sentinels are NOT leaf features; they live in ALL_CODES only
        assert AI_RECRUITMENT not in ALL_FEATURES
        assert ATS_MANAGEMENT not in ALL_FEATURES

    def test_module_sentinels_in_all_codes(self):
        assert AI_RECRUITMENT in ALL_CODES
        assert ATS_MANAGEMENT in ALL_CODES

    def test_all_features_in_all_codes(self):
        assert ALL_FEATURES.issubset(ALL_CODES)

    def test_no_duplicate_feature_codes(self):
        codes: list[str] = []
        for features in MODULES.values():
            codes.extend(features)
        assert len(codes) == len(set(codes)), "Duplicate feature code found across modules"

    def test_no_feature_code_equals_module_sentinel(self):
        for module_code, features in MODULES.items():
            assert module_code not in features, (
                f"Module sentinel '{module_code}' must not appear in its own feature list"
            )

    def test_every_module_has_at_least_one_feature(self):
        for module_code, features in MODULES.items():
            assert len(features) >= 1, f"Module '{module_code}' has no features"

    def test_feature_codes_are_uppercase(self):
        for code in ALL_CODES:
            assert code == code.upper(), f"Feature code '{code}' is not uppercase"

    def test_feature_codes_no_spaces(self):
        for code in ALL_CODES:
            assert " " not in code, f"Feature code '{code}' contains a space"


# ── TestModuleMappings ────────────────────────────────────────────────────────

class TestModuleMappings:
    """Verify MODULES and FEATURE_TO_MODULE are consistent."""

    def test_ai_module_contains_expected_features(self):
        ai_features = MODULES[AI_RECRUITMENT]
        assert AI_JOB_ANALYSIS in ai_features
        assert AI_SCORING in ai_features
        assert AI_KNOCKOUTS in ai_features
        assert AI_BULK_UPLOAD in ai_features
        assert AI_INTAKE_EMAIL in ai_features
        assert AI_INTAKE_PUBLIC in ai_features
        assert AI_INTAKE_MANUAL in ai_features

    def test_ats_module_contains_expected_features(self):
        ats_features = MODULES[ATS_MANAGEMENT]
        assert ATS_WORKFLOWS in ats_features
        assert ATS_NOTES in ats_features
        assert ATS_COMMUNICATIONS in ats_features
        assert ATS_ASSIGNMENTS in ats_features
        assert ATS_EMAIL_TEMPLATES in ats_features
        assert ATS_REPORTING in ats_features

    def test_feature_to_module_covers_all_features(self):
        for feature in ALL_FEATURES:
            assert feature in FEATURE_TO_MODULE, f"'{feature}' missing from FEATURE_TO_MODULE"

    def test_feature_to_module_correct_mapping(self):
        assert FEATURE_TO_MODULE[AI_SCORING] == AI_RECRUITMENT
        assert FEATURE_TO_MODULE[ATS_WORKFLOWS] == ATS_MANAGEMENT
        assert FEATURE_TO_MODULE[AI_BULK_UPLOAD] == AI_RECRUITMENT
        assert FEATURE_TO_MODULE[ATS_NOTES] == ATS_MANAGEMENT

    def test_module_sentinels_not_in_feature_to_module(self):
        assert AI_RECRUITMENT not in FEATURE_TO_MODULE
        assert ATS_MANAGEMENT not in FEATURE_TO_MODULE

    def test_feature_to_module_size_matches_all_features(self):
        assert len(FEATURE_TO_MODULE) == len(ALL_FEATURES)

    def test_two_modules_defined(self):
        assert len(MODULES) == 2

    def test_modules_keys_are_sentinels(self):
        assert AI_RECRUITMENT in MODULES
        assert ATS_MANAGEMENT in MODULES


# ── TestMigrationCompatibility ────────────────────────────────────────────────

class TestMigrationCompatibility:
    """
    Ensure migration 102 seeds exactly the codes defined in the feature registry.
    If you add a new feature code, you must also add it to the migration seed.
    """

    def test_seeded_codes_match_all_codes(self):
        assert _SEEDED_CODES == ALL_CODES, (
            f"Mismatch between migration seed and ALL_CODES.\n"
            f"In migration but not registry: {_SEEDED_CODES - ALL_CODES}\n"
            f"In registry but not migration: {ALL_CODES - _SEEDED_CODES}"
        )

    def test_seed_includes_both_module_sentinels(self):
        assert AI_RECRUITMENT in _SEEDED_CODES
        assert ATS_MANAGEMENT in _SEEDED_CODES

    def test_seed_includes_all_ai_features(self):
        for code in MODULES[AI_RECRUITMENT]:
            assert code in _SEEDED_CODES, f"AI feature '{code}' missing from migration seed"

    def test_seed_includes_all_ats_features(self):
        for code in MODULES[ATS_MANAGEMENT]:
            assert code in _SEEDED_CODES, f"ATS feature '{code}' missing from migration seed"

    def test_seed_count_matches_all_codes_count(self):
        assert len(_SEEDED_CODES) == len(ALL_CODES)


# ── TestFeatureServiceDefaultBehaviour ───────────────────────────────────────

class TestFeatureServiceDefaultBehaviour:
    """
    Unit-test feature_service functions without a real DB.

    is_feature_enabled() must return True when:
      a) No row exists in the DB (default open)
      b) A DB error occurs (fail open)
    """

    @pytest.mark.asyncio
    async def test_returns_true_when_no_db_row(self):
        """Missing row → default enabled."""
        from services import feature_service

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None  # no row
        mock_db.execute = AsyncMock(return_value=mock_result)

        feature_service._cache.clear()
        result = await feature_service.is_feature_enabled(mock_db, "tenant-abc", AI_SCORING)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_when_db_has_enabled_row(self):
        from services import feature_service

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = True
        mock_db.execute = AsyncMock(return_value=mock_result)

        feature_service._cache.clear()
        result = await feature_service.is_feature_enabled(mock_db, "tenant-abc", AI_SCORING)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_db_has_disabled_row(self):
        from services import feature_service

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = False
        mock_db.execute = AsyncMock(return_value=mock_result)

        feature_service._cache.clear()
        result = await feature_service.is_feature_enabled(mock_db, "tenant-xyz", AI_SCORING)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_on_db_error(self):
        """DB failure must not block the feature — fail open."""
        from services import feature_service

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=Exception("DB connection refused"))

        feature_service._cache.clear()
        result = await feature_service.is_feature_enabled(mock_db, "tenant-err", AI_SCORING)
        assert result is True

    @pytest.mark.asyncio
    async def test_get_tenant_features_returns_all_codes(self):
        """get_tenant_features returns an entry for every known code."""
        from services import feature_service

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.mappings.return_value = []  # empty DB result
        mock_db.execute = AsyncMock(return_value=mock_result)

        feature_service._cache.clear()
        features = await feature_service.get_tenant_features(mock_db, "tenant-abc")

        assert set(features.keys()) == ALL_CODES

    @pytest.mark.asyncio
    async def test_get_tenant_features_defaults_all_to_true(self):
        """With no DB rows, every feature defaults to True."""
        from services import feature_service

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.mappings.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        feature_service._cache.clear()
        features = await feature_service.get_tenant_features(mock_db, "tenant-abc")

        assert all(v is True for v in features.values()), (
            "All features should default to True when no DB rows exist"
        )

    @pytest.mark.asyncio
    async def test_get_tenant_features_on_db_error_defaults_all_true(self):
        from services import feature_service

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=Exception("timeout"))

        feature_service._cache.clear()
        features = await feature_service.get_tenant_features(mock_db, "tenant-err")

        assert all(v is True for v in features.values())


# ── TestFeatureServiceEnableDisable ──────────────────────────────────────────

class TestFeatureServiceEnableDisable:
    """Verify enable/disable/bulk operations call DB correctly."""

    @pytest.mark.asyncio
    async def test_enable_feature_executes_upsert(self):
        from services import feature_service

        mock_db = AsyncMock()
        feature_service._cache.clear()

        await feature_service.enable_feature(mock_db, "tenant-abc", AI_SCORING)

        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_disable_feature_executes_upsert(self):
        from services import feature_service

        mock_db = AsyncMock()
        feature_service._cache.clear()

        await feature_service.disable_feature(mock_db, "tenant-abc", AI_SCORING)

        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_bulk_enable_module_touches_all_codes(self):
        from services import feature_service

        mock_db = AsyncMock()
        feature_service._cache.clear()

        await feature_service.bulk_enable_module(mock_db, "tenant-abc", AI_RECRUITMENT)

        expected_calls = 1 + len(MODULES[AI_RECRUITMENT])  # sentinel + features
        assert mock_db.execute.call_count == expected_calls
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_bulk_disable_module_touches_all_codes(self):
        from services import feature_service

        mock_db = AsyncMock()
        feature_service._cache.clear()

        await feature_service.bulk_disable_module(mock_db, "tenant-abc", ATS_MANAGEMENT)

        expected_calls = 1 + len(MODULES[ATS_MANAGEMENT])
        assert mock_db.execute.call_count == expected_calls
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_bulk_enable_unknown_module_does_not_crash(self):
        """Unknown module code: only the sentinel upsert runs, no error."""
        from services import feature_service

        mock_db = AsyncMock()
        feature_service._cache.clear()

        await feature_service.bulk_enable_module(mock_db, "tenant-abc", "NONEXISTENT_MODULE")

        mock_db.execute.assert_called_once()  # just the sentinel
        mock_db.commit.assert_called_once()


# ── TestFeatureServiceCache ───────────────────────────────────────────────────

class TestFeatureServiceCache:
    """Verify TTL cache behaviour."""

    @pytest.mark.asyncio
    async def test_second_call_hits_cache_not_db(self):
        from services import feature_service

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = True
        mock_db.execute = AsyncMock(return_value=mock_result)

        feature_service._cache.clear()

        await feature_service.is_feature_enabled(mock_db, "tenant-cache", AI_SCORING)
        await feature_service.is_feature_enabled(mock_db, "tenant-cache", AI_SCORING)

        assert mock_db.execute.call_count == 1, "Second call should use cache"

    @pytest.mark.asyncio
    async def test_invalidation_clears_single_entry(self):
        from services import feature_service

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = True
        mock_db.execute = AsyncMock(return_value=mock_result)

        feature_service._cache.clear()

        await feature_service.is_feature_enabled(mock_db, "tenant-inv", AI_SCORING)
        assert mock_db.execute.call_count == 1

        feature_service._cache_invalidate("tenant-inv", AI_SCORING)

        await feature_service.is_feature_enabled(mock_db, "tenant-inv", AI_SCORING)
        assert mock_db.execute.call_count == 2, "Cache miss after invalidation"

    @pytest.mark.asyncio
    async def test_invalidation_of_all_tenant_entries(self):
        from services import feature_service

        feature_service._cache.clear()
        t = time.monotonic() + 60
        feature_service._cache[("tenant-bulk", AI_SCORING)] = (True, t)
        feature_service._cache[("tenant-bulk", AI_KNOCKOUTS)] = (True, t)
        feature_service._cache[("other-tenant", AI_SCORING)] = (True, t)

        feature_service._cache_invalidate("tenant-bulk")

        assert ("tenant-bulk", AI_SCORING) not in feature_service._cache
        assert ("tenant-bulk", AI_KNOCKOUTS) not in feature_service._cache
        # Other tenant untouched
        assert ("other-tenant", AI_SCORING) in feature_service._cache

    @pytest.mark.asyncio
    async def test_stale_cache_entry_triggers_db_call(self):
        from services import feature_service

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = True
        mock_db.execute = AsyncMock(return_value=mock_result)

        feature_service._cache.clear()
        # Plant a stale entry
        feature_service._cache[("tenant-stale", AI_SCORING)] = (True, time.monotonic() - 1)

        result = await feature_service.is_feature_enabled(mock_db, "tenant-stale", AI_SCORING)

        assert result is True
        mock_db.execute.assert_called_once()  # DB was hit due to stale cache


# ── TestDefaultEnabledInvariant ───────────────────────────────────────────────

class TestDefaultEnabledInvariant:
    """
    Cross-cutting invariant: the feature service must NEVER block existing
    functionality.  All code paths must return True when the DB has no row
    or an error occurs.
    """

    @pytest.mark.asyncio
    async def test_all_known_features_default_enabled_with_empty_db(self):
        from services import feature_service

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        feature_service._cache.clear()

        for code in ALL_CODES:
            result = await feature_service.is_feature_enabled(mock_db, "tenant-abc", code)
            assert result is True, f"Feature '{code}' returned False with no DB row"

    @pytest.mark.asyncio
    async def test_all_known_features_default_enabled_on_db_error(self):
        from services import feature_service

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=RuntimeError("connection pool exhausted"))

        feature_service._cache.clear()

        for code in ALL_CODES:
            result = await feature_service.is_feature_enabled(mock_db, "tenant-err", code)
            assert result is True, f"Feature '{code}' returned False on DB error"
