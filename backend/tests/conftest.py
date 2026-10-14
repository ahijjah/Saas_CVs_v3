"""
Shared pytest configuration.

Stubs out the asyncpg-dependent database layer so that pure-function test
modules can import services without a live database connection.  The stubs are
injected into sys.modules before any test module is collected, so they only
need to exist once here rather than being repeated in each test file.
"""

import sys
import types


def _make_stub_module(name: str, **attrs) -> types.ModuleType:
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


# ── Stub the database engine (requires asyncpg at import time) ────────────────

# Stubbed ONLY when the real module cannot be imported. A no-op stand-in makes set_rls_context do nothing, so every test that goes through the
# services or workers would run without tenant context (the superuser bypasses row-level security, which hides the gap). The real module is
# what the restricted-role tests (tests/test_runtime_role_plan.py and the isolated suites in runtime mode) exercise.
try:
    import database  # noqa: F401
except Exception:  # noqa: BLE001 - asyncpg or configuration missing: pure-function modules only
    async def _noop_set_rls(*a, **kw):
        pass

    _make_stub_module(
        "database",
        engine=None,
        get_db=None,
        set_rls_context=_noop_set_rls,
    )

# ── Stub application_intake_service ──────────────────────────────────────────

if "services.application_intake_service" not in sys.modules:
    async def _stub(*a, **kw):
        return None

    _make_stub_module(
        "services.application_intake_service",
        create_application_record=_stub,
        insert_file_record=_stub,
        log_intake=_stub,
        save_file=_stub,
    )

# ── Stub knockout_questions_service ──────────────────────────────────────────

if "services.knockout_questions_service" not in sys.modules:
    async def _stub_ko(*a, **kw):
        return None

    _make_stub_module(
        "services.knockout_questions_service",
        save_knockout_answers=_stub_ko,
    )
