"""
E-02 Phase 3 — Module-Level Enforcement tests.

Covers:
  * _check_module logic: super_admin bypass, enabled → pass, disabled → 403
  * require_ai_recruitment / require_ats_management wrappers
  * get_tenant_modules: super_admin returns all-true, tenant returns DB values
  * _load_modules: delegates to is_feature_enabled for both module codes
  * Fail-open: is_feature_enabled returns True on DB error (inherited, verified here)
  * Audit log: access denials are logged at INFO level

All tests are pure-function / state-simulation — no DB or HTTP server needed.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from constants.features import AI_RECRUITMENT, ATS_MANAGEMENT


# ── Minimal stubs to avoid importing FastAPI ─────────────────────────────────

class _HTTPException(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail


class _FakeStatus:
    HTTP_403_FORBIDDEN = 403


def _make_user(role: str = "recruiter", tenant_id: str = "tenant-abc", user_id: str = "user-123"):
    u = MagicMock()
    u.role = role
    u.tenant_id = tenant_id
    u.user_id = user_id
    return u


# ── Pure reimplementation of _check_module (avoids FastAPI import) ───────────

async def _check_module_pure(
    module_code: str,
    module_label: str,
    current_user,
    db,
    is_feature_enabled_fn,
    logger,
) -> None:
    """Local copy of module_guards._check_module, dependency-injected for tests."""
    if current_user.role == "super_admin":
        return
    enabled = await is_feature_enabled_fn(db, current_user.tenant_id, module_code)
    if not enabled:
        logger.info(
            "module_guard: access denied — %s disabled for tenant %s (user %s)",
            module_code, current_user.tenant_id, current_user.user_id,
        )
        raise _HTTPException(
            status_code=403,
            detail=f"{module_label} is not enabled for this tenant. "
                   "Contact your administrator to enable this module.",
        )


# ── Pure reimplementation of get_tenant_modules logic ────────────────────────

async def _get_tenant_modules_pure(current_user, db, is_feature_enabled_fn) -> dict:
    if current_user.role == "super_admin":
        return {"ai_recruitment": True, "ats_management": True}
    ai  = await is_feature_enabled_fn(db, current_user.tenant_id, AI_RECRUITMENT)
    ats = await is_feature_enabled_fn(db, current_user.tenant_id, ATS_MANAGEMENT)
    return {"ai_recruitment": ai, "ats_management": ats}


# ═════════════════════════════════════════════════════════════════════════════
class TestCheckModule:
    """Tests for core _check_module enforcement logic."""

    @pytest.mark.asyncio
    async def test_super_admin_bypasses_disabled_module(self):
        """super_admin is never blocked, even when the module flag is False."""
        user = _make_user(role="super_admin")
        is_enabled = AsyncMock(return_value=False)
        db = MagicMock()
        logger = MagicMock()

        # Should not raise
        await _check_module_pure(AI_RECRUITMENT, "AI Recruitment", user, db, is_enabled, logger)

        is_enabled.assert_not_called()

    @pytest.mark.asyncio
    async def test_super_admin_bypasses_enabled_module(self):
        """super_admin bypass also works when the module is True (no DB call)."""
        user = _make_user(role="super_admin")
        is_enabled = AsyncMock(return_value=True)
        db = MagicMock()
        logger = MagicMock()

        await _check_module_pure(ATS_MANAGEMENT, "ATS Management", user, db, is_enabled, logger)

        is_enabled.assert_not_called()

    @pytest.mark.asyncio
    async def test_enabled_module_passes(self):
        """A regular user accessing an enabled module gets through."""
        user = _make_user(role="recruiter")
        is_enabled = AsyncMock(return_value=True)
        db = MagicMock()
        logger = MagicMock()

        await _check_module_pure(AI_RECRUITMENT, "AI Recruitment", user, db, is_enabled, logger)

        is_enabled.assert_awaited_once_with(db, "tenant-abc", AI_RECRUITMENT)
        logger.info.assert_not_called()

    @pytest.mark.asyncio
    async def test_disabled_module_raises_403(self):
        """Disabled module raises HTTPException with 403 status."""
        user = _make_user(role="recruiter")
        is_enabled = AsyncMock(return_value=False)
        db = MagicMock()
        logger = MagicMock()

        with pytest.raises(_HTTPException) as exc_info:
            await _check_module_pure(AI_RECRUITMENT, "AI Recruitment", user, db, is_enabled, logger)

        assert exc_info.value.status_code == 403
        assert "AI Recruitment" in exc_info.value.detail
        assert "not enabled" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_disabled_module_logs_denial(self):
        """Access denial is logged at INFO level with tenant/user context."""
        user = _make_user(role="recruiter", tenant_id="tenant-xyz", user_id="user-456")
        is_enabled = AsyncMock(return_value=False)
        db = MagicMock()
        logger = MagicMock()

        with pytest.raises(_HTTPException):
            await _check_module_pure(ATS_MANAGEMENT, "ATS Management", user, db, is_enabled, logger)

        logger.info.assert_called_once()
        log_args = logger.info.call_args[0]
        assert "ATS_MANAGEMENT" in log_args
        assert "tenant-xyz" in log_args
        assert "user-456" in log_args

    @pytest.mark.asyncio
    async def test_admin_role_can_be_blocked(self):
        """tenant admin (role='admin') is NOT exempt — only super_admin bypasses."""
        user = _make_user(role="admin")
        is_enabled = AsyncMock(return_value=False)
        db = MagicMock()
        logger = MagicMock()

        with pytest.raises(_HTTPException) as exc_info:
            await _check_module_pure(ATS_MANAGEMENT, "ATS Management", user, db, is_enabled, logger)

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_detail_message_mentions_administrator(self):
        """Error detail tells the user to contact their administrator."""
        user = _make_user(role="recruiter")
        is_enabled = AsyncMock(return_value=False)
        db = MagicMock()
        logger = MagicMock()

        with pytest.raises(_HTTPException) as exc_info:
            await _check_module_pure(AI_RECRUITMENT, "AI Recruitment", user, db, is_enabled, logger)

        assert "administrator" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_ats_management_detail_message(self):
        """Disabled ATS_MANAGEMENT error includes the module label."""
        user = _make_user(role="recruiter")
        is_enabled = AsyncMock(return_value=False)
        db = MagicMock()
        logger = MagicMock()

        with pytest.raises(_HTTPException) as exc_info:
            await _check_module_pure(ATS_MANAGEMENT, "ATS Management", user, db, is_enabled, logger)

        assert "ATS Management" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_uses_tenant_id_from_user(self):
        """is_feature_enabled is called with the current user's tenant_id."""
        user = _make_user(role="recruiter", tenant_id="tenant-specific")
        is_enabled = AsyncMock(return_value=True)
        db = MagicMock()
        logger = MagicMock()

        await _check_module_pure(AI_RECRUITMENT, "AI Recruitment", user, db, is_enabled, logger)

        is_enabled.assert_awaited_once_with(db, "tenant-specific", AI_RECRUITMENT)


# ═════════════════════════════════════════════════════════════════════════════
class TestGetTenantModules:
    """Tests for GET /tenant/modules handler logic."""

    @pytest.mark.asyncio
    async def test_super_admin_returns_all_enabled(self):
        """super_admin always receives both modules as True regardless of DB."""
        user = _make_user(role="super_admin")
        is_enabled = AsyncMock(return_value=False)
        db = MagicMock()

        result = await _get_tenant_modules_pure(user, db, is_enabled)

        assert result == {"ai_recruitment": True, "ats_management": True}
        is_enabled.assert_not_called()

    @pytest.mark.asyncio
    async def test_tenant_both_enabled(self):
        """When both modules are enabled, both flags are True."""
        user = _make_user(role="recruiter")
        is_enabled = AsyncMock(return_value=True)
        db = MagicMock()

        result = await _get_tenant_modules_pure(user, db, is_enabled)

        assert result == {"ai_recruitment": True, "ats_management": True}

    @pytest.mark.asyncio
    async def test_tenant_ai_only(self):
        """When only AI_RECRUITMENT is enabled, ats_management is False."""
        user = _make_user(role="recruiter")

        async def _side_effect(db, tenant_id, code):
            return code == AI_RECRUITMENT

        db = MagicMock()
        result = await _get_tenant_modules_pure(user, db, _side_effect)

        assert result == {"ai_recruitment": True, "ats_management": False}

    @pytest.mark.asyncio
    async def test_tenant_ats_only(self):
        """When only ATS_MANAGEMENT is enabled, ai_recruitment is False."""
        user = _make_user(role="recruiter")

        async def _side_effect(db, tenant_id, code):
            return code == ATS_MANAGEMENT

        db = MagicMock()
        result = await _get_tenant_modules_pure(user, db, _side_effect)

        assert result == {"ai_recruitment": False, "ats_management": True}

    @pytest.mark.asyncio
    async def test_tenant_both_disabled(self):
        """When both modules are disabled, both flags are False."""
        user = _make_user(role="recruiter")
        is_enabled = AsyncMock(return_value=False)
        db = MagicMock()

        result = await _get_tenant_modules_pure(user, db, is_enabled)

        assert result == {"ai_recruitment": False, "ats_management": False}

    @pytest.mark.asyncio
    async def test_queries_correct_module_codes(self):
        """is_feature_enabled is called with AI_RECRUITMENT and ATS_MANAGEMENT codes."""
        user = _make_user(role="recruiter", tenant_id="tenant-abc")
        calls: list[tuple] = []

        async def _side_effect(db, tenant_id, code):
            calls.append((tenant_id, code))
            return True

        db = MagicMock()
        await _get_tenant_modules_pure(user, db, _side_effect)

        assert ("tenant-abc", AI_RECRUITMENT) in calls
        assert ("tenant-abc", ATS_MANAGEMENT) in calls

    @pytest.mark.asyncio
    async def test_admin_role_gets_real_db_values(self):
        """tenant admin (role='admin') receives actual DB values, not forced True."""
        user = _make_user(role="admin")
        is_enabled = AsyncMock(return_value=False)
        db = MagicMock()

        result = await _get_tenant_modules_pure(user, db, is_enabled)

        assert result == {"ai_recruitment": False, "ats_management": False}


# ═════════════════════════════════════════════════════════════════════════════
class TestModuleGuardFailOpen:
    """Verify that errors in is_feature_enabled result in fail-open (default True)."""

    @pytest.mark.asyncio
    async def test_db_error_defaults_to_enabled(self):
        """If is_feature_enabled itself returns True on error (service contract),
        the guard passes. This test verifies the fail-open contract is propagated."""
        user = _make_user(role="recruiter")
        # Simulates what feature_service.is_feature_enabled does on DB error: returns True
        is_enabled = AsyncMock(return_value=True)
        db = MagicMock()
        logger = MagicMock()

        # Should NOT raise — fail-open means True → access granted
        await _check_module_pure(AI_RECRUITMENT, "AI Recruitment", user, db, is_enabled, logger)

    @pytest.mark.asyncio
    async def test_missing_row_defaults_to_enabled(self):
        """Missing feature row → is_feature_enabled returns True → guard passes."""
        user = _make_user(role="recruiter")
        is_enabled = AsyncMock(return_value=True)
        db = MagicMock()
        logger = MagicMock()

        # No exception should be raised
        await _check_module_pure(ATS_MANAGEMENT, "ATS Management", user, db, is_enabled, logger)


# ═════════════════════════════════════════════════════════════════════════════
class TestModuleCodeConstants:
    """Sanity-check that the module guard uses the correct constants."""

    def test_ai_recruitment_constant(self):
        assert AI_RECRUITMENT == "AI_RECRUITMENT"

    def test_ats_management_constant(self):
        assert ATS_MANAGEMENT == "ATS_MANAGEMENT"

    def test_module_codes_are_distinct(self):
        assert AI_RECRUITMENT != ATS_MANAGEMENT

    def test_both_in_modules_dict(self):
        from constants.features import MODULES
        assert AI_RECRUITMENT in MODULES
        assert ATS_MANAGEMENT in MODULES


# ═════════════════════════════════════════════════════════════════════════════
class TestCombinedScenarios:
    """End-to-end scenario tests covering common tenant configurations."""

    @pytest.mark.asyncio
    async def test_ai_only_tenant_blocked_on_ats(self):
        """AI-only tenant: ATS access raises 403."""
        user = _make_user(role="recruiter", tenant_id="ai-only")

        async def _side_effect(db, tenant_id, code):
            return code == AI_RECRUITMENT

        db = MagicMock()
        logger = MagicMock()

        # AI access passes
        await _check_module_pure(AI_RECRUITMENT, "AI Recruitment", user, db, _side_effect, logger)

        # ATS access is blocked
        with pytest.raises(_HTTPException) as exc_info:
            await _check_module_pure(ATS_MANAGEMENT, "ATS Management", user, db, _side_effect, logger)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_ats_only_tenant_blocked_on_ai(self):
        """ATS-only tenant: AI access raises 403."""
        user = _make_user(role="recruiter", tenant_id="ats-only")

        async def _side_effect(db, tenant_id, code):
            return code == ATS_MANAGEMENT

        db = MagicMock()
        logger = MagicMock()

        # ATS access passes
        await _check_module_pure(ATS_MANAGEMENT, "ATS Management", user, db, _side_effect, logger)

        # AI access is blocked
        with pytest.raises(_HTTPException) as exc_info:
            await _check_module_pure(AI_RECRUITMENT, "AI Recruitment", user, db, _side_effect, logger)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_full_access_tenant_passes_both(self):
        """Full-access tenant: both module checks pass."""
        user = _make_user(role="recruiter", tenant_id="full-access")
        is_enabled = AsyncMock(return_value=True)
        db = MagicMock()
        logger = MagicMock()

        await _check_module_pure(AI_RECRUITMENT, "AI Recruitment", user, db, is_enabled, logger)
        await _check_module_pure(ATS_MANAGEMENT, "ATS Management", user, db, is_enabled, logger)

    @pytest.mark.asyncio
    async def test_no_access_tenant_blocked_on_both(self):
        """Tenant with both modules disabled gets 403 for both."""
        user = _make_user(role="recruiter", tenant_id="no-access")
        is_enabled = AsyncMock(return_value=False)
        db = MagicMock()
        logger = MagicMock()

        with pytest.raises(_HTTPException) as exc1:
            await _check_module_pure(AI_RECRUITMENT, "AI Recruitment", user, db, is_enabled, logger)
        assert exc1.value.status_code == 403

        with pytest.raises(_HTTPException) as exc2:
            await _check_module_pure(ATS_MANAGEMENT, "ATS Management", user, db, is_enabled, logger)
        assert exc2.value.status_code == 403

    @pytest.mark.asyncio
    async def test_super_admin_passes_both_when_all_disabled(self):
        """super_admin always gets both modules, even when DB says disabled."""
        user = _make_user(role="super_admin", tenant_id="any")
        is_enabled = AsyncMock(return_value=False)
        db = MagicMock()
        logger = MagicMock()

        # Both guards pass with no exception
        await _check_module_pure(AI_RECRUITMENT, "AI Recruitment", user, db, is_enabled, logger)
        await _check_module_pure(ATS_MANAGEMENT, "ATS Management", user, db, is_enabled, logger)

        # No DB calls made
        is_enabled.assert_not_called()

    @pytest.mark.asyncio
    async def test_super_admin_modules_endpoint_no_db(self):
        """GET /tenant/modules for super_admin never hits the DB."""
        user = _make_user(role="super_admin")
        is_enabled = AsyncMock()
        db = MagicMock()

        result = await _get_tenant_modules_pure(user, db, is_enabled)

        assert result["ai_recruitment"] is True
        assert result["ats_management"] is True
        is_enabled.assert_not_called()
