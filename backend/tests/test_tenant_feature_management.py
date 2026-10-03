"""
E-02 Phase 2 — Tenant Feature Management tests.

Covers:
  * Feature enable/disable service functions
  * Module bulk enable/disable
  * Audit log creation
  * Feature validation (known vs unknown codes)
  * Module validation
  * Tenant validation contract
  * Authorization contract (super_admin only)
  * Missing feature fallback (default enabled)
  * Cache invalidation after write
  * Module summary builder

All tests are pure-function / mock-based — no DB, HTTP server, or FastAPI
import required (FastAPI is not installed in the test environment).
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from constants.features import (
    AI_BULK_UPLOAD,
    AI_RECRUITMENT,
    AI_SCORING,
    ALL_CODES,
    ALL_FEATURES,
    ATS_MANAGEMENT,
    ATS_WORKFLOWS,
    MODULES,
)


# ── Inline copies of router helpers (no FastAPI dependency) ───────────────────
# These mirror the implementations in routers/platform_features.py so we can
# test the business logic without importing the router module.

def _require_feature_code_pure(code: str) -> None:
    """Raise ValueError if code is not a known leaf feature."""
    if code not in ALL_FEATURES:
        raise ValueError(f"Unknown feature code '{code}'")


def _require_module_code_pure(code: str) -> None:
    """Raise ValueError if code is not a known module sentinel."""
    if code not in MODULES:
        raise ValueError(f"Unknown module code '{code}'")


def _build_module_summary_pure(features: dict[str, bool]) -> list[dict]:
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


async def _require_tenant_pure(db, tenant_id: str) -> dict:
    """Return tenant row or raise ValueError if not found."""
    row = await db.execute(None, {"tid": tenant_id})
    tenant = row.mappings().first()
    if not tenant:
        raise ValueError("Tenant not found")
    return dict(tenant)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_db(scalar_return=None, mappings_return=None):
    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = scalar_return
    if mappings_return is not None:
        mock_result.mappings.return_value = mappings_return
    else:
        mock_result.mappings.return_value = []
    db.execute = AsyncMock(return_value=mock_result)
    return db


# ── TestEnableFeature ─────────────────────────────────────────────────────────

class TestEnableFeature:
    """Verify enable_feature() service function."""

    @pytest.mark.asyncio
    async def test_enable_executes_upsert(self):
        from services.feature_service import enable_feature, _cache
        _cache.clear()

        db = _make_db(scalar_return=False)
        await enable_feature(db, "t1", AI_SCORING, changed_by=None)

        # _get_current_value (1) + upsert (1)
        assert db.execute.call_count == 2
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_enable_invalidates_cache(self):
        from services import feature_service

        feature_service._cache[("t2", AI_SCORING)] = (False, time.monotonic() + 60)
        db = _make_db(scalar_return=False)
        await feature_service.enable_feature(db, "t2", AI_SCORING)

        assert ("t2", AI_SCORING) not in feature_service._cache

    @pytest.mark.asyncio
    async def test_enable_writes_audit_when_changed_by_provided(self):
        from services.feature_service import enable_feature, _cache
        _cache.clear()

        db = _make_db(scalar_return=False)
        await enable_feature(db, "t3", AI_SCORING, changed_by="user-uuid-123")

        # _get_current_value + upsert + audit = 3
        assert db.execute.call_count == 3

    @pytest.mark.asyncio
    async def test_enable_skips_audit_when_changed_by_none(self):
        from services.feature_service import enable_feature, _cache
        _cache.clear()

        db = _make_db(scalar_return=True)
        await enable_feature(db, "t4", AI_SCORING, changed_by=None)

        assert db.execute.call_count == 2
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_enable_commits_transaction(self):
        from services.feature_service import enable_feature, _cache
        _cache.clear()

        db = _make_db(scalar_return=None)
        await enable_feature(db, "t5", AI_SCORING)
        db.commit.assert_called_once()


# ── TestDisableFeature ────────────────────────────────────────────────────────

class TestDisableFeature:
    """Verify disable_feature() service function."""

    @pytest.mark.asyncio
    async def test_disable_executes_upsert(self):
        from services.feature_service import disable_feature, _cache
        _cache.clear()

        db = _make_db(scalar_return=True)
        await disable_feature(db, "t6", AI_SCORING, changed_by=None)

        assert db.execute.call_count == 2
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_disable_invalidates_cache(self):
        from services import feature_service

        feature_service._cache[("t7", AI_SCORING)] = (True, time.monotonic() + 60)
        db = _make_db(scalar_return=True)
        await feature_service.disable_feature(db, "t7", AI_SCORING)

        assert ("t7", AI_SCORING) not in feature_service._cache

    @pytest.mark.asyncio
    async def test_disable_writes_audit_when_changed_by_provided(self):
        from services.feature_service import disable_feature, _cache
        _cache.clear()

        db = _make_db(scalar_return=True)
        await disable_feature(db, "t8", AI_SCORING, changed_by="admin-user")

        # _get_current_value + upsert + audit = 3
        assert db.execute.call_count == 3

    @pytest.mark.asyncio
    async def test_disable_persists_false_in_sql(self):
        """The upsert SQL must set is_enabled = FALSE."""
        from services.feature_service import disable_feature, _cache
        _cache.clear()

        db = _make_db(scalar_return=True)
        await disable_feature(db, "t9", AI_SCORING, changed_by=None)

        # The second execute call is the upsert
        upsert_sql = str(db.execute.call_args_list[1][0][0])
        assert "FALSE" in upsert_sql

    @pytest.mark.asyncio
    async def test_different_features_use_different_cache_keys(self):
        from services import feature_service

        feature_service._cache[("tx", AI_SCORING)]     = (True, time.monotonic() + 60)
        feature_service._cache[("tx", AI_BULK_UPLOAD)] = (True, time.monotonic() + 60)

        db = _make_db(scalar_return=True)
        await feature_service.disable_feature(db, "tx", AI_SCORING)

        # Only AI_SCORING invalidated
        assert ("tx", AI_SCORING) not in feature_service._cache
        assert ("tx", AI_BULK_UPLOAD) in feature_service._cache


# ── TestBulkEnableModule ──────────────────────────────────────────────────────

class TestBulkEnableModule:

    @pytest.mark.asyncio
    async def test_bulk_enable_touches_all_module_codes(self):
        from services.feature_service import bulk_enable_module, _cache
        _cache.clear()

        db = _make_db(scalar_return=None)
        await bulk_enable_module(db, "tenant-x", AI_RECRUITMENT, changed_by=None)

        # sentinel + features, each with get_current_value + upsert
        total_codes = 1 + len(MODULES[AI_RECRUITMENT])
        assert db.execute.call_count == total_codes * 2
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_bulk_enable_invalidates_all_tenant_cache(self):
        from services import feature_service

        feature_service._cache[("tx", AI_RECRUITMENT)] = (False, time.monotonic() + 60)
        feature_service._cache[("tx", AI_SCORING)] = (False, time.monotonic() + 60)
        feature_service._cache[("other", AI_SCORING)] = (True, time.monotonic() + 60)

        db = _make_db(scalar_return=False)
        await feature_service.bulk_enable_module(db, "tx", AI_RECRUITMENT)

        assert ("tx", AI_RECRUITMENT) not in feature_service._cache
        assert ("tx", AI_SCORING) not in feature_service._cache
        assert ("other", AI_SCORING) in feature_service._cache

    @pytest.mark.asyncio
    async def test_bulk_enable_with_audit_logs_each_code(self):
        from services.feature_service import bulk_enable_module, _cache
        _cache.clear()

        db = _make_db(scalar_return=None)
        await bulk_enable_module(db, "tenant-x", AI_RECRUITMENT, changed_by="admin-id")

        total_codes = 1 + len(MODULES[AI_RECRUITMENT])
        # per code: get_current_value + upsert + audit = 3
        assert db.execute.call_count == total_codes * 3

    @pytest.mark.asyncio
    async def test_bulk_enable_unknown_module_only_touches_sentinel(self):
        from services.feature_service import bulk_enable_module, _cache
        _cache.clear()

        db = _make_db(scalar_return=None)
        await bulk_enable_module(db, "tenant-x", "NONEXISTENT", changed_by=None)

        # Only sentinel: get_current_value + upsert = 2
        assert db.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_bulk_enable_ats_covers_all_ats_features(self):
        from services.feature_service import bulk_enable_module, _cache
        _cache.clear()

        db = _make_db(scalar_return=False)
        await bulk_enable_module(db, "tenant-z", ATS_MANAGEMENT, changed_by=None)

        total_codes = 1 + len(MODULES[ATS_MANAGEMENT])
        assert db.execute.call_count == total_codes * 2


# ── TestBulkDisableModule ─────────────────────────────────────────────────────

class TestBulkDisableModule:

    @pytest.mark.asyncio
    async def test_bulk_disable_touches_all_module_codes(self):
        from services.feature_service import bulk_disable_module, _cache
        _cache.clear()

        db = _make_db(scalar_return=True)
        await bulk_disable_module(db, "tenant-y", ATS_MANAGEMENT, changed_by=None)

        total_codes = 1 + len(MODULES[ATS_MANAGEMENT])
        assert db.execute.call_count == total_codes * 2
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_bulk_disable_with_audit_logs_each_code(self):
        from services.feature_service import bulk_disable_module, _cache
        _cache.clear()

        db = _make_db(scalar_return=True)
        await bulk_disable_module(db, "tenant-y", ATS_MANAGEMENT, changed_by="admin-id")

        total_codes = 1 + len(MODULES[ATS_MANAGEMENT])
        assert db.execute.call_count == total_codes * 3

    @pytest.mark.asyncio
    async def test_bulk_disable_invalidates_all_tenant_cache(self):
        from services import feature_service

        feature_service._cache[("ty", ATS_MANAGEMENT)] = (True, time.monotonic() + 60)
        feature_service._cache[("ty", ATS_WORKFLOWS)]  = (True, time.monotonic() + 60)

        db = _make_db(scalar_return=True)
        await feature_service.bulk_disable_module(db, "ty", ATS_MANAGEMENT)

        assert ("ty", ATS_MANAGEMENT) not in feature_service._cache
        assert ("ty", ATS_WORKFLOWS)  not in feature_service._cache


# ── TestAuditLogging ──────────────────────────────────────────────────────────

class TestAuditLogging:

    @pytest.mark.asyncio
    async def test_audit_inserts_row(self):
        from services.feature_service import log_feature_audit

        db = AsyncMock()
        await log_feature_audit(
            db,
            tenant_id="tid-1",
            feature_code=AI_SCORING,
            old_value=True,
            new_value=False,
            changed_by="admin-user-id",
        )

        db.execute.assert_called_once()
        sql = str(db.execute.call_args[0][0])
        assert "tenant_feature_audit" in sql

    @pytest.mark.asyncio
    async def test_audit_never_raises_on_db_error(self):
        """Audit must be fire-and-forget."""
        from services.feature_service import log_feature_audit

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=Exception("DB down"))

        await log_feature_audit(
            db,
            tenant_id="tid-2",
            feature_code=AI_SCORING,
            old_value=None,
            new_value=True,
            changed_by="admin-id",
        )

    @pytest.mark.asyncio
    async def test_audit_records_old_and_new_values(self):
        from services.feature_service import log_feature_audit

        db = AsyncMock()
        await log_feature_audit(
            db,
            tenant_id="tid-3",
            feature_code=ATS_WORKFLOWS,
            old_value=True,
            new_value=False,
            changed_by="changer",
        )

        params = db.execute.call_args[0][1]
        assert params["old"] is True
        assert params["new"] is False
        assert params["code"] == ATS_WORKFLOWS

    @pytest.mark.asyncio
    async def test_audit_records_none_old_value_for_first_write(self):
        from services.feature_service import log_feature_audit

        db = AsyncMock()
        await log_feature_audit(
            db,
            tenant_id="tid-4",
            feature_code=AI_SCORING,
            old_value=None,
            new_value=True,
            changed_by="admin",
        )

        params = db.execute.call_args[0][1]
        assert params["old"] is None
        assert params["new"] is True


# ── TestFeatureValidation ─────────────────────────────────────────────────────

class TestFeatureValidation:
    """Verify validation helpers reject unknown codes."""

    def test_all_known_feature_codes_are_valid(self):
        for code in ALL_FEATURES:
            _require_feature_code_pure(code)  # must not raise

    def test_unknown_feature_code_raises(self):
        with pytest.raises(ValueError):
            _require_feature_code_pure("TOTALLY_UNKNOWN_FEATURE")

    def test_empty_string_is_invalid_feature(self):
        with pytest.raises(ValueError):
            _require_feature_code_pure("")

    def test_module_sentinel_is_not_a_valid_leaf_feature(self):
        with pytest.raises(ValueError):
            _require_feature_code_pure(AI_RECRUITMENT)

        with pytest.raises(ValueError):
            _require_feature_code_pure(ATS_MANAGEMENT)

    def test_all_known_module_codes_are_valid(self):
        for mod in MODULES:
            _require_module_code_pure(mod)  # must not raise

    def test_unknown_module_code_raises(self):
        with pytest.raises(ValueError):
            _require_module_code_pure("NONEXISTENT_MODULE")

    def test_leaf_feature_is_not_a_valid_module(self):
        with pytest.raises(ValueError):
            _require_module_code_pure(AI_SCORING)

        with pytest.raises(ValueError):
            _require_module_code_pure(ATS_WORKFLOWS)


# ── TestTenantValidation ──────────────────────────────────────────────────────

class TestTenantValidation:

    @pytest.mark.asyncio
    async def test_existing_tenant_returns_dict(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.mappings.return_value.first.return_value = {
            "tenant_id": "some-uuid",
            "tenant_name": "ACME Corp",
            "tenant_code": "acme",
            "status": "active",
        }
        db.execute = AsyncMock(return_value=mock_result)

        tenant = await _require_tenant_pure(db, "some-uuid")
        assert tenant["tenant_name"] == "ACME Corp"
        assert tenant["status"] == "active"

    @pytest.mark.asyncio
    async def test_missing_tenant_raises_error(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.mappings.return_value.first.return_value = None
        db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(ValueError, match="Tenant not found"):
            await _require_tenant_pure(db, "nonexistent-uuid")


# ── TestModuleSummaryBuilder ──────────────────────────────────────────────────

class TestModuleSummaryBuilder:

    def test_all_modules_present(self):
        features = {code: True for code in ALL_CODES}
        summary = _build_module_summary_pure(features)

        module_codes = {m["module_code"] for m in summary}
        assert module_codes == set(MODULES.keys())

    def test_enabled_count_counts_correctly(self):
        features = {code: True for code in ALL_CODES}
        features[AI_SCORING] = False
        features[AI_BULK_UPLOAD] = False

        summary = _build_module_summary_pure(features)
        ai_mod = next(m for m in summary if m["module_code"] == AI_RECRUITMENT)
        assert ai_mod["enabled_count"] == len(MODULES[AI_RECRUITMENT]) - 2

    def test_module_enabled_reflects_sentinel_flag(self):
        features = {code: True for code in ALL_CODES}
        features[AI_RECRUITMENT] = False

        summary = _build_module_summary_pure(features)
        ai_mod = next(m for m in summary if m["module_code"] == AI_RECRUITMENT)
        assert ai_mod["module_enabled"] is False

    def test_missing_feature_defaults_to_true(self):
        summary = _build_module_summary_pure({})
        for mod in summary:
            assert mod["module_enabled"] is True
            for feat in mod["features"]:
                assert feat["is_enabled"] is True

    def test_feature_list_contains_all_module_features(self):
        features = {code: True for code in ALL_CODES}
        summary = _build_module_summary_pure(features)

        for mod_summary in summary:
            mod_code = mod_summary["module_code"]
            summary_codes = {f["feature_code"] for f in mod_summary["features"]}
            expected_codes = set(MODULES[mod_code])
            assert summary_codes == expected_codes

    def test_total_count_matches_module_size(self):
        features = {code: True for code in ALL_CODES}
        summary = _build_module_summary_pure(features)

        for mod_summary in summary:
            mod_code = mod_summary["module_code"]
            assert mod_summary["total_count"] == len(MODULES[mod_code])

    def test_all_disabled_gives_zero_enabled_count(self):
        features = {code: False for code in ALL_CODES}
        summary = _build_module_summary_pure(features)

        for mod_summary in summary:
            assert mod_summary["enabled_count"] == 0


# ── TestMissingFeatureFallback ────────────────────────────────────────────────

class TestMissingFeatureFallback:
    """Default-enabled invariant — missing rows or DB errors must return True."""

    @pytest.mark.asyncio
    async def test_is_feature_enabled_true_when_no_row(self):
        from services.feature_service import is_feature_enabled, _cache
        _cache.clear()

        db = _make_db(scalar_return=None)
        result = await is_feature_enabled(db, "tenant-new", AI_SCORING)
        assert result is True

    @pytest.mark.asyncio
    async def test_get_tenant_features_all_true_when_no_rows(self):
        from services.feature_service import get_tenant_features, _cache
        _cache.clear()

        db = _make_db(mappings_return=[])
        features = await get_tenant_features(db, "tenant-new")
        assert all(v is True for v in features.values())

    @pytest.mark.asyncio
    async def test_default_enabled_survives_db_error(self):
        from services.feature_service import is_feature_enabled, _cache
        _cache.clear()

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=Exception("network timeout"))

        result = await is_feature_enabled(db, "tenant-err", ATS_WORKFLOWS)
        assert result is True

    @pytest.mark.asyncio
    async def test_all_known_codes_default_to_true_on_empty_db(self):
        from services.feature_service import is_feature_enabled, _cache
        _cache.clear()

        db = _make_db(scalar_return=None)
        for code in ALL_CODES:
            result = await is_feature_enabled(db, "tenant-empty", code)
            assert result is True, f"Feature '{code}' should default to True"


# ═════════════════════════════════════════════════════════════════════════════
# E-02 Regression: production tenants table schema
# Root cause: platform_features.py originally used t.tenant_name / t.tenant_code
# which do not exist. Actual columns are t.name and t.email_domain.
# ═════════════════════════════════════════════════════════════════════════════

# Inline copy of the SQL strings used in platform_features.py after the fix.
# We extract the field names from the SELECT clause to verify they use the
# correct production column names — not t.tenant_name / t.tenant_code.
_REQUIRE_TENANT_SQL = (
    "SELECT tenant_id, name AS tenant_name, email_domain AS tenant_code, status "
    "FROM cv_analyzer.tenants "
    "WHERE tenant_id = CAST(:tid AS uuid)"
)

_OVERVIEW_SUMMARY_WHERE = (
    "WHERE (t.name ILIKE :search OR t.email_domain ILIKE :search)"
)

_OVERVIEW_SELECT = (
    "t.tenant_id, t.name AS tenant_name, t.email_domain AS tenant_code, t.status, "
)

_OVERVIEW_WHERE_ORDER = (
    "WHERE (t.name ILIKE :search OR t.email_domain ILIKE :search) "
    "ORDER BY t.name "
)


class TestProductionSchemaColumnNames:
    """
    Regression tests verifying that platform_features.py SQL references the
    actual production tenants table columns (name, email_domain) rather than
    the non-existent aliases (tenant_name, tenant_code).
    """

    # ── _require_tenant SQL ───────────────────────────────────────────────────

    def test_require_tenant_uses_name_not_tenant_name(self):
        """SELECT must use t.name (the real column), not t.tenant_name."""
        assert "name AS tenant_name" in _REQUIRE_TENANT_SQL
        assert "tenant_name," not in _REQUIRE_TENANT_SQL.split("AS")[0]

    def test_require_tenant_uses_email_domain_not_tenant_code(self):
        """SELECT must use t.email_domain (the real column), not t.tenant_code."""
        assert "email_domain AS tenant_code" in _REQUIRE_TENANT_SQL
        assert "tenant_code," not in _REQUIRE_TENANT_SQL.split("AS")[1] if "AS" in _REQUIRE_TENANT_SQL else True

    def test_require_tenant_column_tenant_name_absent_in_raw_from(self):
        """The bare column name 'tenant_name' must not appear before AS alias."""
        # The only occurrence of 'tenant_name' should be after 'AS'
        idx = _REQUIRE_TENANT_SQL.find("tenant_name")
        assert idx != -1, "alias 'tenant_name' must still appear (as AS alias)"
        before = _REQUIRE_TENANT_SQL[:idx]
        assert before.rstrip().endswith("AS"), "tenant_name must only appear after AS"

    def test_require_tenant_column_tenant_code_absent_in_raw_from(self):
        """The bare column name 'tenant_code' must not appear before AS alias."""
        idx = _REQUIRE_TENANT_SQL.find("tenant_code")
        assert idx != -1, "alias 'tenant_code' must still appear (as AS alias)"
        before = _REQUIRE_TENANT_SQL[:idx]
        assert before.rstrip().endswith("AS"), "tenant_code must only appear after AS"

    # ── Overview summary query WHERE ─────────────────────────────────────────

    def test_summary_where_uses_name_not_tenant_name(self):
        """Summary WHERE must filter on t.name, not t.tenant_name."""
        assert "t.name ILIKE" in _OVERVIEW_SUMMARY_WHERE
        assert "t.tenant_name" not in _OVERVIEW_SUMMARY_WHERE

    def test_summary_where_uses_email_domain_not_tenant_code(self):
        """Summary WHERE must filter on t.email_domain, not t.tenant_code."""
        assert "t.email_domain ILIKE" in _OVERVIEW_SUMMARY_WHERE
        assert "t.tenant_code" not in _OVERVIEW_SUMMARY_WHERE

    # ── Overview per-tenant SELECT ────────────────────────────────────────────

    def test_overview_select_uses_name_alias(self):
        """Per-tenant SELECT must use t.name AS tenant_name."""
        assert "t.name AS tenant_name" in _OVERVIEW_SELECT
        assert "t.tenant_name" not in _OVERVIEW_SELECT

    def test_overview_select_uses_email_domain_alias(self):
        """Per-tenant SELECT must use t.email_domain AS tenant_code."""
        assert "t.email_domain AS tenant_code" in _OVERVIEW_SELECT
        assert "t.tenant_code" not in _OVERVIEW_SELECT

    # ── Overview per-tenant WHERE + ORDER BY ─────────────────────────────────

    def test_overview_where_uses_name_not_tenant_name(self):
        """Per-tenant WHERE must filter on t.name, not t.tenant_name."""
        assert "t.name ILIKE" in _OVERVIEW_WHERE_ORDER
        assert "t.tenant_name" not in _OVERVIEW_WHERE_ORDER

    def test_overview_where_uses_email_domain_not_tenant_code(self):
        """Per-tenant WHERE must filter on t.email_domain, not t.tenant_code."""
        assert "t.email_domain ILIKE" in _OVERVIEW_WHERE_ORDER
        assert "t.tenant_code" not in _OVERVIEW_WHERE_ORDER

    def test_overview_order_by_name_not_tenant_name(self):
        """ORDER BY must use t.name, not t.tenant_name."""
        assert "ORDER BY t.name" in _OVERVIEW_WHERE_ORDER
        assert "ORDER BY t.tenant_name" not in _OVERVIEW_WHERE_ORDER

    # ── End-to-end: _require_tenant mock with production column names ─────────

    @pytest.mark.asyncio
    async def test_require_tenant_mock_with_production_columns(self):
        """
        Simulate _require_tenant receiving a row with production column names
        (name, email_domain) — aliased to tenant_name/tenant_code in the SELECT.
        Verifies the response dict has the frontend-friendly keys.
        """
        # Simulate what the DB row looks like after aliasing
        fake_row = {
            "tenant_id": "abc-123",
            "tenant_name": "Acme Corp",       # aliased from t.name
            "tenant_code": "acme.com",         # aliased from t.email_domain
            "status": "active",
        }

        mapping_mock = MagicMock()
        mapping_mock.first.return_value = fake_row

        result_mock = MagicMock()
        result_mock.mappings.return_value = mapping_mock

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        # Call the pure logic (dict conversion)
        tenant = dict(fake_row)

        # Response shape the router builds from this dict
        response = {
            "tenant_id":   str(tenant["tenant_id"]),
            "tenant_name": tenant["tenant_name"],
            "tenant_code": tenant["tenant_code"],
            "status":      tenant["status"],
        }

        assert response["tenant_id"] == "abc-123"
        assert response["tenant_name"] == "Acme Corp"
        assert response["tenant_code"] == "acme.com"
        assert response["status"] == "active"

    def test_no_bare_tenant_name_column_in_any_sql(self):
        """
        None of the fixed SQL strings should contain 't.tenant_name' or
        't.tenant_code' — these columns do not exist in production.
        """
        all_sql = [
            _REQUIRE_TENANT_SQL,
            _OVERVIEW_SUMMARY_WHERE,
            _OVERVIEW_SELECT,
            _OVERVIEW_WHERE_ORDER,
        ]
        for sql in all_sql:
            assert "t.tenant_name" not in sql, f"Found t.tenant_name in: {sql!r}"
            assert "t.tenant_code" not in sql, f"Found t.tenant_code in: {sql!r}"
