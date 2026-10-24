"""Release mutation checks.

Each mutant is applied to a throw-away copy of the repository, then the test that must catch it is
run. A mutant is "caught" when that test exits non-zero. The script exits 1 if any mutant survives.
The repository itself is never modified.

    python3 scripts/release_mutation_checks.py            # all mutants
    python3 scripts/release_mutation_checks.py --only ts-config

Requires: python3, pytest (backend), node/npx with node_modules installed (frontend mutants),
Chromium for the E2E mutant (the E2E mutant runs e2e/mutation_cleanup_check.sh in the copy).
"""
from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Backend commands for the security mutants, run from backend/. Each mutant is checked against the module that owns its test.
PYTEST = ["python3", "-m", "pytest", "-q", "-rfE", "-p", "no:cacheprovider"]
SEC = "security"                               # placeholder, resolved by _command_for
SEC_COMMAND = [*PYTEST, "tests/test_security_revalidation_postgres.py", "tests/test_privilege_justifications.py"]
RETRY_COMMAND = [*PYTEST, "tests/test_retry_hash_proof_postgres.py", "tests/test_requirements_v2_worker_postgres.py"]
JOBS_COMMAND = [*PYTEST, "tests/test_job_code_sequence_postgres.py"]
FOLLOWUP_COMMAND = [*PYTEST, "tests/test_requirements_v2_staging_followup.py"]
PREFERRED_COMMAND = [*PYTEST, "tests/test_requirements_v2_preferred_classification.py"]
BINDING_COMMAND = [*PYTEST, "tests/test_requirements_v2_preferred_binding.py"]
JOB_ACCESS_COMMAND = ["npx", "vitest", "run", "tests/frontend/jobDetailsAccess.test.tsx"]
MATRIX_COMMAND = [*PYTEST, "tests/test_role_authorization_matrix.py"]
AUDIT_SCOPE_COMMAND = [*PYTEST, "tests/test_audit_logs_tenant_scope.py"]
ROLE_UI_COMMAND = ["npx", "vitest", "run", "tests/frontend/rolePermissions.test.ts", "tests/frontend/roleVisibility.test.tsx",
                   "tests/frontend/candidatesWorkspaceAccess.test.tsx", "tests/frontend/applicationAccess.test.tsx",
                   "tests/frontend/RequirementsPanel.test.tsx", "tests/frontend/requirementsApi.test.ts"]
GATES_OFFLINE_COMMAND = [*PYTEST, "tests/test_gates_offline.py"]
GATES_PG_COMMAND = [*PYTEST, "tests/test_gates_postgres.py", "tests/test_gates_links_postgres.py", "tests/test_gates_routes_postgres.py",
                    "tests/test_gates_cv_evidence_postgres.py"]
GATES_UI_COMMAND = ["npx", "vitest", "run", "tests/frontend/gates.test.tsx"]
ITEM_OFFLINE_COMMAND = [*PYTEST, "tests/test_item_scoring_offline.py"]
ITEM_PG_COMMAND = [*PYTEST, "tests/test_item_scoring_postgres.py", "tests/test_item_scoring_migration_postgres.py"]
ITEM_UI_COMMAND = ["npx", "vitest", "run", "tests/frontend/itemScoring.test.tsx"]
MATRIX_OFFLINE_COMMAND = [*PYTEST, "tests/test_candidate_matrix_offline.py"]
MATRIX_PG_COMMAND = [*PYTEST, "tests/test_candidate_matrix_postgres.py", "tests/test_candidate_matrix_migration_postgres.py"]
MATRIX_UI_COMMAND = ["npx", "vitest", "run", "tests/frontend/candidateMatrix.test.tsx"]
REUSE_COMMAND = [*PYTEST, "tests/test_candidate_reuse_postgres.py", "-k", "one_file_reuse or multi_file"]
_COMMAND_BY_TEST_PREFIX = (
    ("test_a_rejected_extraction", RETRY_COMMAND),
    ("test_a_stale_claim", RETRY_COMMAND),
    ("test_the_rule_for_each", RETRY_COMMAND),
    ("test_a_rejected_row_is_claimed", RETRY_COMMAND),
    ("test_the_normaliser", RETRY_COMMAND),
    ("test_concurrent_tenants", JOBS_COMMAND),
    ("test_the_sequence_is_a_bigint", JOBS_COMMAND),
    ("test_a_one_file_reuse", REUSE_COMMAND),
)


def _command_for(mutant: "Mutant") -> list[str]:
    if mutant.command != SEC:
        return list(mutant.command)
    for prefix, command in _COMMAND_BY_TEST_PREFIX:
        if mutant.expect.startswith(prefix):
            return list(command)
    return list(SEC_COMMAND)


@dataclass(frozen=True)
class Mutant:
    key: str
    description: str
    file: str            # path relative to the repository root
    old: str             # text to replace (must occur exactly once)
    new: str
    command: list[str] | str   # run inside the copy; must exit non-zero when the mutant is caught (or SEC: see _command_for)
    surface: str         # "backend" or "frontend"
    extra_files: tuple[str, ...] = ()   # added to the copy before the mutation (rare)
    edits: tuple[tuple[str, str, str], ...] = ()   # further (file, old, new) edits applied with the primary one
    expect: str = ""     # backend: the test whose FAILED line must appear for the mutant to count as caught


MUTANTS: tuple[Mutant, ...] = (
    Mutant(
        key="event-loop-leak",
        description="Issue #20: security tests use get_event_loop() again",
        file="backend/tests/test_security_detection.py",
        old="    return asyncio.run(coro)",
        new="    return asyncio.get_event_loop().run_until_complete(coro)",
        command=["python3", "-m", "pytest", "tests/test_criteria_status_postgres.py",
                 "tests/test_security_detection.py", "-q", "-p", "no:cacheprovider"],
        surface="backend",
        expect="test_helper_works_after_another_test_unsets_the_loop",
    ),
    Mutant(
        key="security-test-skipped",
        description="Issue #20: one security test is marked skip",
        file="backend/tests/test_security_detection.py",
        old="    def test_clean_cv_passes(self):",
        new='    @pytest.mark.skip(reason="mutant")\n    def test_clean_cv_passes(self):',
        command=["python3", "-m", "pytest", "tests/test_security_detection.py::TestSuiteIntegrity",
                 "-q", "-p", "no:cacheprovider"],
        surface="backend",
        expect="test_no_security_test_is_skipped_or_expected_to_fail",
    ),
    Mutant(
        key="security-test-removed",
        description="Issue #20: one security test is deleted",
        file="backend/tests/test_security_detection.py",
        old="    def test_summary_contains_no_raw_text(self):",
        new="    def _removed_summary_check(self):",
        command=["python3", "-m", "pytest", "tests/test_security_detection.py::TestSuiteIntegrity",
                 "-q", "-p", "no:cacheprovider"],
        surface="backend",
        expect="test_security_suite_keeps_its_test_count",
    ),
    Mutant(
        key="ts-config-exclude",
        description="TypeScript: configuration excludes a source file from the check",
        file="tsconfig.json",
        old='"noEmit": true\n  }\n}',
        new='"noEmit": true\n  },\n  "exclude": ["pages/JobDetails.tsx"]\n}',
        command=["npx", "vitest", "run", "tests/frontend/typecheckGuard.test.ts"],
        surface="frontend",
    ),
    Mutant(
        key="ts-config-not-strict-off",
        description="TypeScript: implicit any allowed by configuration",
        file="tsconfig.json",
        old='"noEmit": true\n  }\n}',
        new='"noEmit": true,\n    "noImplicitAny": false\n  }\n}',
        command=["npx", "vitest", "run", "tests/frontend/typecheckGuard.test.ts"],
        surface="frontend",
    ),
    Mutant(
        key="ts-ignore-in-source",
        description="TypeScript: an error is suppressed with @ts-ignore in source",
        file="components/Toast.tsx",
        old="export type ToastType",
        new="// @ts-ignore\nexport type ToastType",
        command=["npx", "vitest", "run", "tests/frontend/typecheckGuard.test.ts"],
        surface="frontend",
    ),
    Mutant(
        key="deterministic-encoder-in-production",
        description="Production code returns a deterministic test encoder",
        file="backend/services/local_processor.py",
        old="def _get_model():\n    ensure_model_loaded()\n    return _MODEL",
        new="class DeterministicEncoder:\n    def encode(self, texts, normalize_embeddings=True):\n        return texts\n\n\ndef _get_model():\n    return DeterministicEncoder()",
        command=["python3", "-m", "pytest",
                 "tests/test_embedding_readiness_offline.py::TestTestDoublesStayOutOfProduction",
                 "-q", "-p", "no:cacheprovider"],
        surface="backend",
        expect="test_no_production_module_references_a_test_double",
    ),
    Mutant(
        key="readiness-validation-skipped",
        description="API lifespan no longer validates the embedding model",
        file="backend/main.py",
        old="    local_processor.ensure_model_loaded()\n    yield",
        new="    yield",
        command=["python3", "-m", "pytest",
                 "tests/test_embedding_readiness_offline.py::TestReadiness::test_lifespan_refuses_to_start_without_the_model",
                 "-q", "-p", "no:cacheprovider"],
        surface="backend",
        expect="test_lifespan_refuses_to_start_without_the_model",
    ),
    Mutant(
        key="e2e-cleanup-leak",
        description="E2E teardown stops no processes (backend, frontend, PostgreSQL left running)",
        file="e2e/support/stack.py",
        old="    for service in reversed(services):\n        _stop_group(service)",
        new="    for service in reversed(services):\n        pass",
        command=["bash", "e2e/mutation_cleanup_check.sh"],
        surface="e2e",
    ),
    Mutant(
        key="model-downloads-in-production",
        description="Loader ignores EMBEDDING_MODEL_LOCAL_ONLY and falls back to the model name",
        file="backend/services/local_processor.py",
        old="    if settings.embedding_model_local_only:",
        new="    if False:",
        command=["python3", "-m", "pytest",
                 "tests/test_embedding_readiness_offline.py::TestLocalOnlyLoading::test_unset_path_fails_clearly_and_never_downloads",
                 "-q", "-p", "no:cacheprovider"],
        surface="backend",
        expect="test_unset_path_fails_clearly_and_never_downloads",
    ),
    # ---- security and deployment-hardening mutants (backend) ----
    # Each mutant names the test whose FAILED line must appear. A collection, import or setup error does not count.
    Mutant(
        key="forged-role-claim-trusted",
        description="Platform identity takes the role from the bearer token, not from the database",
        file="backend/auth/dependencies.py",
        old='        role=user["role"],',
        new="        role=role,",
        command=SEC, surface="backend",
        expect="test_a_token_claiming_super_admin_gets_the_database_role_not_the_claim",
    ),
    Mutant(
        key="platform-admin-tenant-admin-allowed",
        description="The administration pool admits tenant admins as well as super admins",
        file="backend/auth/dependencies.py",
        old='Annotated[CurrentUser, Depends(require_role("super_admin"))],',
        new='Annotated[CurrentUser, Depends(require_role("super_admin", "admin"))],',
        command=SEC, surface="backend",
        expect="test_a_token_claiming_super_admin_gets_the_database_role_not_the_claim",
    ),
    Mutant(
        key="platform-check-removed",
        description="The administration pool dependency no longer checks the role at all",
        file="backend/auth/dependencies.py",
        old='Annotated[CurrentUser, Depends(require_role("super_admin"))],',
        new="Annotated[CurrentUser, Depends(get_current_user)],",
        command=SEC, surface="backend",
        expect="test_a_token_claiming_super_admin_gets_the_database_role_not_the_claim",
    ),
    Mutant(
        key="tenant-requests-use-admin-pool",
        description="The tenant request session is bound to the administration engine",
        file="backend/database.py",
        old="AsyncSessionLocal = async_sessionmaker(bind=engine,",
        new="AsyncSessionLocal = async_sessionmaker(bind=admin_engine,",
        command=SEC, surface="backend",
        expect="test_the_tenant_request_engine_is_the_cv_app_login",
    ),
    Mutant(
        key="cv-app-bypasses-rls",
        description="The tenant login is granted BYPASSRLS by the operator script",
        file="db/ops/0004_runtime_roles.sql.template",
        old="ALTER ROLE cv_app     LOGIN NOSUPERUSER NOBYPASSRLS",
        new="ALTER ROLE cv_app     LOGIN NOSUPERUSER BYPASSRLS",
        command=SEC, surface="backend",
        expect="test_the_tenant_login_sees_no_row_of_another_tenant_in_any_tenant_table_it_reads",
    ),
    Mutant(
        key="runtime-roles-related",
        description="A runtime role is made a member of another runtime role",
        file="db/ops/0004_runtime_roles.sql.template",
        # Placed after the operator script's own membership check, so the test assertion (not a setup error) must catch it.
        old="REVOKE CREATE ON SCHEMA public FROM cv_app, cv_worker, cv_admin, cv_secrets;",
        new="REVOKE CREATE ON SCHEMA public FROM cv_app, cv_worker, cv_admin, cv_secrets;\nGRANT cv_app TO cv_secrets;",
        command=SEC, surface="backend",
        expect="test_no_runtime_role_is_related_to_the_operator_or_the_migrator",
    ),
    Mutant(
        key="runtime-role-owns-table",
        description="A runtime role is made the owner of a table",
        file="db/ops/0004_runtime_roles.sql.template",
        old="\\set ON_ERROR_STOP on",
        new="\\set ON_ERROR_STOP on\nALTER TABLE cv_analyzer.jobs OWNER TO cv_app;",
        command=SEC, surface="backend",
        expect="test_no_runtime_role_owns_the_database_a_schema_a_relation_a_routine_or_a_sequence",
    ),
    Mutant(
        key="runtime-role-can-create-in-schema",
        description="A runtime role is granted CREATE on the application schema",
        file="db/ops/0004_runtime_roles.sql.template",
        old="REVOKE CREATE ON SCHEMA public FROM cv_app, cv_worker, cv_admin, cv_secrets;",
        new="REVOKE CREATE ON SCHEMA public FROM cv_app, cv_worker, cv_admin, cv_secrets;\nGRANT CREATE ON SCHEMA cv_analyzer TO cv_app;",
        command=SEC, surface="backend",
        expect="test_a_runtime_role_holds_no_database_schema_or_public_create_privilege",
        # The privilege script revokes schema privileges again (its step 1); the mutant must get past that revoke too.
        edits=(("db/ops/0005_runtime_privileges.sql.template",
                "REVOKE ALL ON SCHEMA cv_analyzer FROM cv_app, cv_worker, cv_admin, cv_secrets, PUBLIC;\n", ""),),
    ),
    Mutant(
        key="rls-disabled-on-jobs",
        description="Row-level security is disabled on the jobs table by the operator script",
        file="db/ops/0004_runtime_roles.sql.template",
        old="\\set ON_ERROR_STOP on",
        new="\\set ON_ERROR_STOP on\nALTER TABLE cv_analyzer.jobs DISABLE ROW LEVEL SECURITY;",
        command=SEC, surface="backend",
        expect="test_the_tenant_login_sees_no_row_of_another_tenant_in_any_tenant_table_it_reads",
    ),
    Mutant(
        key="policy-trusts-role-variable",
        description="A tenant policy grants access when the forgeable role variable is super_admin",
        file="db/migrations/100_runtime_rls_identity.sql",
        old=("CREATE POLICY tenant_scope_ai_usage_log ON cv_analyzer.ai_usage_log FOR ALL TO PUBLIC\n"
             "    USING (current_user = 'cv_admin' OR"),
        new=("CREATE POLICY tenant_scope_ai_usage_log ON cv_analyzer.ai_usage_log FOR ALL TO PUBLIC\n"
             "    USING (current_setting('app.current_role', true) = 'super_admin' OR"),
        command=SEC, surface="backend",
        expect="test_a_forged_role_variable_cannot_write_into_another_tenants_usage_log",
        edits=(("db/migrations/100_runtime_rls_identity.sql",
                "    WITH CHECK (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true));\nCREATE POLICY tenant_scope_candidate_approvals",
                "    WITH CHECK (current_setting('app.current_role', true) = 'super_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true));\nCREATE POLICY tenant_scope_candidate_approvals"),),
    ),
    Mutant(
        key="public-executes-secret-accessors",
        description="PUBLIC is left with EXECUTE on both secret functions",
        file="db/migrations/099_platform_secret_accessors.sql",
        old="REVOKE ALL ON FUNCTION cv_analyzer.get_platform_secret(text) FROM PUBLIC;\n",
        new="",
        command=SEC, surface="backend",
        expect="test_public_cannot_read_modify_or_execute_any_secret_object",
        edits=(
            ("db/migrations/099_platform_secret_accessors.sql",
             "REVOKE ALL ON FUNCTION cv_analyzer.set_platform_secret(text, text, text, text, text) FROM PUBLIC;\n", ""),
            ("db/ops/0005_runtime_privileges.sql.template",
             "ON ALL FUNCTIONS IN SCHEMA cv_analyzer FROM cv_app, cv_worker, cv_admin, cv_secrets, PUBLIC;",
             "ON ALL FUNCTIONS IN SCHEMA cv_analyzer FROM cv_app, cv_worker, cv_admin, cv_secrets;"),
        ),
    ),
    Mutant(
        key="getter-returns-forbidden-key",
        description="The getter allows DB_PASSWORD and JWT_SECRET to be read when they are on the allowlist",
        file="db/migrations/099_platform_secret_accessors.sql",
        old="p_key IN ('DB_PASSWORD', 'JWT_SECRET') THEN\n        RAISE EXCEPTION 'secret key is not readable",
        new="p_key IN ('DB_PASSWORD') THEN\n        RAISE EXCEPTION 'secret key is not readable",
        command=SEC, surface="backend",
        expect="test_an_environment_only_key_is_refused_even_when_it_is_on_the_allowlist",
    ),
    Mutant(
        key="retry-null-digest-bypass",
        description="A rejected row with no stored digest is claimed for retry (the pre-fix claim)",
        file="routers/jobs.py",
        old=("      AND (criteria_extraction_error IS DISTINCT FROM :rejected_message\n"
             "           OR (length(criteria_last_failed_description_hash) = 64\n"
             "               AND criteria_last_failed_description_hash ~ '^[0-9a-f]+$'\n"
             "               AND criteria_last_failed_description_hash IS DISTINCT FROM :description_hash))"),
        new=("      AND (criteria_extraction_error IS DISTINCT FROM :rejected_message\n"
             "           OR criteria_last_failed_description_hash IS DISTINCT FROM :description_hash)"),
        command=SEC, surface="backend",
        expect="test_a_rejected_row_is_claimed_only_when_the_stored_digest_proves_a_change",
    ),
    Mutant(
        key="retry-malformed-digest-bypass",
        description="A malformed stored digest is accepted as proof of a change",
        file="routers/jobs.py",
        old="    return bool(stored_hash and _SHA256_HEX.match(stored_hash) and stored_hash != current_hash)",
        new="    return bool(stored_hash and stored_hash != current_hash)",
        command=SEC, surface="backend",
        expect="test_the_rule_for_each_failure_and_stored_value",
    ),
    Mutant(
        key="transient-failure-permanent",
        description="Every other failure is treated as a refused retry (transient failures become permanent)",
        file="routers/jobs.py",
        old="    if error == INSUFFICIENT_MESSAGE:\n        return stored_hash == current_hash\n    return False",
        new="    if error == INSUFFICIENT_MESSAGE:\n        return stored_hash == current_hash\n    return True",
        command=SEC, surface="backend",
        expect="test_the_rule_for_each_failure_and_stored_value",
    ),
    Mutant(
        key="normaliser-case-sensitive",
        description="The description digest no longer ignores case",
        file="backend/workers/criteria_worker.py",
        old='    normalized = " ".join((description or "").lower().split())',
        new='    normalized = " ".join((description or "").split())',
        command=SEC, surface="backend",
        expect="test_the_normaliser_ignores_case_and_whitespace_and_nothing_else",
    ),
    Mutant(
        key="rejected-digest-not-recorded",
        description="A failed extraction no longer stores the normalised description digest",
        file="backend/workers/requirements_v2_worker.py",
        old="        criteria_last_failed_description_hash = :desc_hash, criteria_last_failed_at = now()",
        new="        criteria_last_failed_at = now()",
        command=SEC, surface="backend",
        expect="test_a_rejected_extraction_stores_the_normalized_hash_with_the_failed_status",
    ),
    Mutant(
        key="stale-claim-overwrites-failure",
        description="A stale worker's failure write is no longer guarded by the claim token",
        file="backend/workers/requirements_v2_worker.py",
        old=("criteria_last_failed_at = now()\n    WHERE job_id = :jid AND criteria_extraction_status = 'processing' "
             "AND updated_at = :token"),
        new="criteria_last_failed_at = now()\n    WHERE job_id = :jid AND criteria_extraction_status = 'processing'",
        command=SEC, surface="backend",
        expect="test_a_stale_claim_cannot_overwrite_the_status_or_the_hash",
    ),
    Mutant(
        key="job-code-from-tenant-count",
        description="Job codes are numbered from a count of the tenant's own jobs (the pre-101 behaviour)",
        file="routers/jobs.py",
        old="text(\"SELECT nextval('cv_analyzer.job_code_seq')\")",
        new="text(\"SELECT count(*) + 1 FROM cv_analyzer.jobs\")",
        command=SEC, surface="backend",
        expect="test_concurrent_tenants_under_row_level_security_never_receive_the_same_code",
    ),
    Mutant(
        key="job-code-sequence-granted-to-worker",
        description="The job-code sequence is granted to the worker login as well",
        file="db/runtime_grants.py",
        old='f"GRANT USAGE ON SEQUENCE cv_analyzer.job_code_seq TO {APP};"',
        new='f"GRANT USAGE ON SEQUENCE cv_analyzer.job_code_seq TO {APP}, {WORKER};"',
        command=SEC, surface="backend",
        expect="test_the_sequence_is_a_bigint_starting_at_one_and_granted_to_the_job_path_only",
        edits=(("db/ops/0005_runtime_privileges.sql.template",
                "GRANT USAGE ON SEQUENCE cv_analyzer.job_code_seq TO cv_app;",
                "GRANT USAGE ON SEQUENCE cv_analyzer.job_code_seq TO cv_app, cv_worker;"),),
    ),
    Mutant(
        key="application-files-row-lock",
        description="The source files are row-locked FOR SHARE, which the runtime role cannot do",
        file="backend/services/application_reuse_service.py",
        old='    ORDER BY af.created_at, af.file_id\n""")',
        new='    ORDER BY af.created_at, af.file_id\n    FOR SHARE\n""")',
        command=SEC, surface="backend",
        expect="test_a_one_file_reuse_creates_an_application_with_its_own_copy_and_scores_nothing",
    ),
    Mutant(
        key="search-path-role-default-removed",
        description="The tenant login loses its role-level search path",
        file="db/ops/0004_runtime_roles.sql.template",
        old="ALTER ROLE cv_app     SET search_path = cv_analyzer, public;",
        new="-- ALTER ROLE cv_app     SET search_path = cv_analyzer, public;",
        command=SEC, surface="backend",
        expect="test_the_role_default_survives_an_error_and_a_rollback",
    ),
    Mutant(
        key="runtime-role-can-create-databases",
        description="The tenant worker login is granted CREATEDB by the operator script",
        file="db/ops/0004_runtime_roles.sql.template",
        old="ALTER ROLE cv_worker  LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE",
        new="ALTER ROLE cv_worker  LOGIN NOSUPERUSER NOBYPASSRLS CREATEDB NOCREATEROLE",
        command=SEC, surface="backend",
        expect="test_every_runtime_role_is_a_plain_login_with_no_elevated_attribute",
    ),
    Mutant(
        key="runtime-role-can-create-roles",
        description="The platform administration login is granted CREATEROLE by the operator script",
        file="db/ops/0004_runtime_roles.sql.template",
        old="ALTER ROLE cv_admin   LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE",
        new="ALTER ROLE cv_admin   LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB CREATEROLE",
        command=SEC, surface="backend",
        expect="test_every_runtime_role_is_a_plain_login_with_no_elevated_attribute",
    ),
    Mutant(
        key="tenant-predicate-removed-from-policy",
        description="The usage-log policy no longer restricts rows to the tenant",
        file="db/migrations/100_runtime_rls_identity.sql",
        old=("CREATE POLICY tenant_scope_ai_usage_log ON cv_analyzer.ai_usage_log FOR ALL TO PUBLIC\n"
             "    USING (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true))\n"
             "    WITH CHECK (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true));"),
        new=("CREATE POLICY tenant_scope_ai_usage_log ON cv_analyzer.ai_usage_log FOR ALL TO PUBLIC\n"
             "    USING (true)\n"
             "    WITH CHECK (true);"),
        command=SEC, surface="backend",
        expect="test_a_forged_role_variable_cannot_write_into_another_tenants_usage_log",
    ),
    Mutant(
        key="application-files-update-widened",
        description="The tenant login is granted UPDATE on application_files (matrix and operator script)",
        file="db/runtime_grants.py",
        old='    "application_files":                         {APP: "DIS", ADMIN: "I", WORKER: "ISU"},',
        new='    "application_files":                         {APP: "DISU", ADMIN: "I", WORKER: "ISU"},',
        command=SEC, surface="backend",
        expect="test_the_manifest_declares_exactly_the_matrix",
        edits=(("db/ops/0005_runtime_privileges.sql.template",
                "GRANT SELECT, INSERT, DELETE ON cv_analyzer.application_files TO cv_app;",
                "GRANT SELECT, INSERT, UPDATE, DELETE ON cv_analyzer.application_files TO cv_app;"),),
    ),
    Mutant(
        key="super-admin-route-on-tenant-session",
        description="The AI usage routes take the tenant session under the administration name",
        file="routers/ai_usage.py",
        old="from auth.dependencies import require_role, get_platform_db",
        new="from auth.dependencies import require_role\nfrom database import get_db as get_platform_db",
        command=SEC, surface="backend",
        expect="test_every_super_admin_route_uses_the_administration_session",
    ),
    Mutant(
        key="user-invites-excess-grants",
        description="The administration login is granted SELECT, UPDATE and DELETE on user_invites (matrix and script)",
        file="db/runtime_grants.py",
        old='    "user_invites":                              {ADMIN: "I"},',
        new='    "user_invites":                              {ADMIN: "ISU"},',
        command=SEC, surface="backend",
        expect="test_the_manifest_declares_exactly_the_matrix",
        edits=(("db/ops/0005_runtime_privileges.sql.template",
                "GRANT INSERT ON cv_analyzer.user_invites TO cv_admin;",
                "GRANT SELECT, INSERT, UPDATE, DELETE ON cv_analyzer.user_invites TO cv_admin;"),),
    ),
    Mutant(
        key="security-test-removed-security",
        description="A forged-claim test is deleted from the security suite",
        file="backend/tests/test_security_revalidation_postgres.py",
        old="def test_an_unsigned_token_is_refused(built, ids):",
        new="def _removed_unsigned_token_check(built, ids):",
        command=SEC, surface="backend",
        expect="test_security_suite_keeps_its_required_tests",
    ),
    Mutant(
        key="explicit-or-merge-disabled",
        description="Staging follow-up: the parser no longer merges explicit OR alternatives into one requirement",
        file="services/requirements_v2/extraction/parser.py",
        old="            value = merge_explicit_alternatives(value, jd)",
        new="            pass",
        command=FOLLOWUP_COMMAND, surface="backend",
        expect="test_the_stored_python_powershell_shapes_become_one_required_alternatives_item",
    ),
    Mutant(
        key="job-context-dropped",
        description="Staging follow-up: a verified job context is no longer put into the document",
        file="services/requirements_v2/extraction/parser.py",
        old='        doc["context"] = context',
        new="        pass",
        command=FOLLOWUP_COMMAND, surface="backend",
        expect="test_the_acceptance_sentence_yields_the_three_context_values_and_nothing_scored",
    ),
    Mutant(
        key="explicit-preferred-omission-undetected",
        description="Staging follow-up: a stated preferred sentence with no item is no longer detected",
        file="services/requirements_v2/extraction/completeness.py",
        old="        if not (cue or under_heading):\n            continue",
        new="        if True:\n            continue",
        command=FOLLOWUP_COMMAND, surface="backend",
        expect="test_a_preferred_sentence_without_an_item_is_reported_and_a_covered_one_is_not",
    ),
    Mutant(
        key="explicit-preferred-omission-reports-success",
        description="Staging follow-up: an extraction that still lacks a stated preferred sentence is stored as a success",
        file="workers/requirements_v2_worker.py",
        old="    if result.ok and result.missed_preferred:\n        logger.error",
        new="    if False and result.missed_preferred:\n        logger.error",
        command=FOLLOWUP_COMMAND, surface="backend",
        expect="test_an_extraction_that_keeps_dropping_a_stated_preferred_sentence_fails_instead_of_succeeding",
    ),
    Mutant(
        key="preferred-sentence-still-warns",
        description="UAT closure: an explicit 'is preferred' sentence no longer establishes the classification (the false ITIL warning)",
        file="services/requirements_v2/extraction/parser.py",
        old="    if jd_marks_preferred(jd, span):\n        return None",
        new="    if False and jd_marks_preferred(jd, span):\n        return None",
        command=PREFERRED_COMMAND, surface="backend",
        expect="test_itil_certification_is_preferred_needs_no_acknowledgment",
    ),
    Mutant(
        key="unsupported-preferred-always-accepted",
        description="UAT closure: any sentence is accepted as marking a requirement Preferred",
        file="services/requirements_v2/extraction/cues.py",
        old="    return heading is not None and bool(PREFERRED_RE.search(normalize(heading)))",
        new="    return True",
        command=PREFERRED_COMMAND, surface="backend",
        expect="test_an_unclear_or_unsupported_preferred_classification_still_requires_acknowledgment",
    ),
    Mutant(
        key="cue-leaks-across-the-sentence",
        description="UAT closure: any optional cue in the sentence establishes every requirement in it (the sentence-wide behaviour)",
        file="services/requirements_v2/extraction/cues.py",
        old="    owners = [(a, b) for a, b in _clauses(folded) if a < n_hi and b > n_lo]",
        new="    return bool(PREFERRED_RE.search(folded)) and not states_required(folded)\n    owners = []",
        command=BINDING_COMMAND, surface="backend",
        expect="test_an_optional_cue_binds_only_to_its_own_requirement",
    ),
    Mutant(
        key="trailing-cue-binds-through-a-list",
        description="UAT closure: a cue at the end of a comma list establishes every item in the list",
        file="services/requirements_v2/extraction/cues.py",
        old="        elif not _LIST_COMMA_RE.search(folded[start:a] + folded[b:end]):",
        new="        elif True:",
        command=BINDING_COMMAND, surface="backend",
        expect="test_an_optional_cue_binds_only_to_its_own_requirement",
    ),
    Mutant(
        key="clauses-never-cut",
        description="UAT closure: required and optional wording in one sentence are no longer separated into clauses",
        file="services/requirements_v2/extraction/cues.py",
        old="        if _has_marker(folded[cut:match.start()]) and _has_marker(folded[match.end():]):",
        new="        if False:",
        command=BINDING_COMMAND, surface="backend",
        expect="test_an_optional_cue_binds_only_to_its_own_requirement",
    ),
    Mutant(
        key="required-clause-accepts-an-optional-heading",
        description="UAT closure: a clause that says required still counts as Preferred under a Preferred heading",
        file="services/requirements_v2/extraction/cues.py",
        old="    if states_required(folded[start:end]):\n        return False",
        new="    if False:\n        return False",
        command=BINDING_COMMAND, surface="backend",
        expect="test_an_optional_cue_binds_only_to_its_own_requirement",
    ),
    Mutant(
        key="model-chosen-cue-establishes",
        description="UAT closure: any phrase the model names as its cue and that appears in the description establishes Preferred",
        file="services/requirements_v2/extraction/parser.py",
        old="    if jd_marks_preferred(jd, span):\n        return None",
        new="    if jd_marks_preferred(jd, span) or (isinstance(cue, str) and cue.strip() and contains_phrase(jd, cue) and span is not None):\n        return None",
        command=BINDING_COMMAND, surface="backend",
        expect="test_a_cue_the_model_names_establishes_nothing",
    ),
    Mutant(
        key="refused-poll-keeps-job-data",
        description="UAT closure: a 403/404 on the extraction poll leaves the previously loaded job on screen",
        file="pages/JobDetails.tsx",
        old="        if (refuseAccess(err)) return;                  // access is gone: nothing is shown and polling ends with the data",
        new="        if (false && refuseAccess(err)) return;",
        command=JOB_ACCESS_COMMAND, surface="frontend",
    ),
    Mutant(
        key="refused-job-tells-403-from-404",
        description="UAT closure: the 404 state names the missing job, so it differs from the refused state",
        file="pages/JobDetails.tsx",
        old='          <p className="font-bold mb-2">Job not available</p>',
        new='          <p className="font-bold mb-2">{accessError === 404 ? \'Job not found\' : \'Job not available\'}</p>',
        command=JOB_ACCESS_COMMAND, surface="frontend",
    ),
    Mutant(
        key="viewer-may-export",
        description="Role matrix: a Viewer is allowed to download the applications export",
        file="auth/roles.py",
        old="EXPORT_ROLES = frozenset({ADMIN, HR_MANAGER, RECRUITER, SUPER_ADMIN})",
        new="EXPORT_ROLES = frozenset({ADMIN, HR_MANAGER, RECRUITER, VIEWER, SUPER_ADMIN})",
        command=MATRIX_COMMAND, surface="backend",
        expect="test_each_role_gets_exactly_the_access_the_matrix_gives_it",
    ),
    Mutant(
        key="recruiter-may-edit-requirements",
        description="Role matrix: a Recruiter is allowed to edit Requirements",
        file="auth/roles.py",
        old="REQUIREMENTS_EDITOR_ROLES = frozenset({ADMIN, HR_MANAGER})",
        new="REQUIREMENTS_EDITOR_ROLES = frozenset({ADMIN, HR_MANAGER, RECRUITER})",
        command=MATRIX_COMMAND, surface="backend",
        expect="test_each_role_gets_exactly_the_access_the_matrix_gives_it",
    ),
    Mutant(
        key="viewer-cannot-read-requirements",
        description="Role matrix: a Viewer is refused the Requirements read view",
        file="auth/roles.py",
        old="REQUIREMENTS_READER_ROLES = TENANT_ROLES",
        new="REQUIREMENTS_READER_ROLES = frozenset({ADMIN, HR_MANAGER, RECRUITER})",
        command=MATRIX_COMMAND, surface="backend",
        expect="test_each_role_gets_exactly_the_access_the_matrix_gives_it",
    ),
    Mutant(
        key="workflow-status-guard-removed",
        description="Role matrix: the workflow-status route loses its role guard, so a Viewer reaches the handler",
        file="routers/applications.py",
        old='@router.patch("/{application_id}/workflow-status", dependencies=[RequireManager])',
        new='@router.patch("/{application_id}/workflow-status")',
        command=MATRIX_COMMAND, surface="backend",
        expect="test_each_role_gets_exactly_the_access_the_matrix_gives_it",
    ),
    Mutant(
        key="audit-log-scope-dropped",
        description="Audit logs: a tenant admin is no longer forced onto their own tenant",
        file="routers/audit_logs.py",
        old="        tenant_id = str(current_user.tenant_id)\n",
        new="        pass\n",
        command=AUDIT_SCOPE_COMMAND, surface="backend",
        expect="test_a_tenant_admin_is_always_scoped_to_their_own_tenant",
    ),
    Mutant(
        key="audit-log-foreign-tenant-honoured",
        description="Audit logs: a tenant admin may name another tenant",
        file="routers/audit_logs.py",
        old="        if tenant_id and str(tenant_id).strip().lower() != str(current_user.tenant_id).lower():",
        new="        if False:",
        command=AUDIT_SCOPE_COMMAND, surface="backend",
        expect="test_a_tenant_admin_naming_their_own_tenant_is_allowed_and_naming_another_is_refused",
    ),
    Mutant(
        key="ui-viewer-may-export",
        description="Role UI: the export permission includes a Viewer",
        file="utils/permissions.ts",
        old="const EXPORT_ROLES = ['super_admin', 'admin', 'hr_manager', 'recruiter'] as const;",
        new="const EXPORT_ROLES = ['super_admin', 'admin', 'hr_manager', 'recruiter', 'viewer'] as const;",
        command=ROLE_UI_COMMAND, surface="frontend",
    ),
    Mutant(
        key="ui-export-menu-always-shown",
        description="Role UI: the Export menu is shown to every role",
        file="pages/CandidatesWorkspace.tsx",
        old='{canExportApplications(auth.user?.role) && (',
        new='{true && (',
        command=ROLE_UI_COMMAND, surface="frontend",
    ),
    Mutant(
        key="ui-drawer-always-editable",
        description="Role UI: the candidate drawer shows editing controls to every role",
        file="pages/CandidatesWorkspace.tsx",
        old='const canMutate = canMutateApplications(userRole);',
        new='const canMutate = true;',
        command=ROLE_UI_COMMAND, surface="frontend",
    ),
    Mutant(
        key="ui-recruiter-edits-requirements",
        description="Role UI: the Requirements editor is offered to a Recruiter",
        file="utils/permissions.ts",
        old="export const REQUIREMENTS_EDIT_ROLES: readonly string[] = ['admin', 'hr_manager'];",
        new="export const REQUIREMENTS_EDIT_ROLES: readonly string[] = ['admin', 'hr_manager', 'recruiter'];",
        command=ROLE_UI_COMMAND, surface="frontend",
    ),
    Mutant(
        key="ui-application-notes-always-editable",
        description="Role UI: application notes and workflow are editable by every role",
        file="pages/ApplicationDetails.tsx",
        old='const canMutate = canMutateApplications(userRole);',
        new='const canMutate = true;',
        command=ROLE_UI_COMMAND, surface="frontend",
    ),
    Mutant(
        key="ui-tenant-audit-shows-tenants",
        description="Role UI: a tenant admin's audit page shows the tenant column",
        file="pages/AuditLogs.tsx",
        old="const platform = scope === 'platform';",
        new='const platform = true;',
        command=ROLE_UI_COMMAND, surface="frontend",
    ),
    Mutant(
        key="ui-add-job-always-shown",
        description="Role UI: Add New Job is shown to every role",
        file="pages/JobsDashboard.tsx",
        old='{canManageJobs(auth.user?.role) && (',
        new='{true && (',
        command=ROLE_UI_COMMAND, surface="frontend",
    ),
    Mutant(
        key="ui-manual-upload-always-shown",
        description="Role UI: manual CV upload is offered to every role",
        file="pages/JobDetails.tsx",
        old='const canManageApplications = canMutateApplications(role);',
        new='const canManageApplications = true;',
        command=ROLE_UI_COMMAND, surface="frontend",
    ),
    Mutant(
        key="gates-score-wins-over-failed-gate",
        description="Gates: a good score decision wins over a failed gate in the effective decision",
        file="services/eligibility_service.py",
        old='    if eligibility_status == NOT_ELIGIBLE:\n        return NOT_ELIGIBLE\n    if eligibility_status == REVIEW_REQUIRED or scoring_status == \"review_required\":',
        new='    if False:\n        return NOT_ELIGIBLE\n    if eligibility_status == REVIEW_REQUIRED or scoring_status == \"review_required\":',
        command=GATES_OFFLINE_COMMAND, surface="backend",
        expect="test_effective_decision_lets_a_failed_gate_win_over_a_high_score_decision",
    ),
    Mutant(
        key="gates-cv-unquoted-passes",
        description="Gates: a CV verdict passes the gate without a quote found in the CV",
        file="services/eligibility_service.py",
        old='if verdict == "met" and quoted and confidence >= PASS_CONFIDENCE:',
        new='if verdict == "met" and confidence >= PASS_CONFIDENCE:',
        command=GATES_OFFLINE_COMMAND, surface="backend",
        expect="test_a_quote_that_is_not_in_the_cv_never_passes",
    ),
    Mutant(
        key="gates-cv-fails-without-contradiction",
        description="Gates: a CV 'not met' fails the gate without an explicit contradiction",
        file="services/eligibility_service.py",
        old=' and item.get("explicit_contradiction") is True:',
        new=':',
        command=GATES_OFFLINE_COMMAND, surface="backend",
        expect="test_not_met_without_an_explicit_contradiction_does_not_fail",
    ),
    Mutant(
        key="gates-cv-fail-threshold-zero",
        description="Gates: a low-confidence 'not met' fails the gate",
        file="services/eligibility_service.py",
        old='FAIL_CONFIDENCE = 0.90',
        new='FAIL_CONFIDENCE = 0.0',
        command=GATES_OFFLINE_COMMAND, surface="backend",
        expect="test_not_met_below_the_fail_confidence_does_not_fail",
    ),
    Mutant(
        key="gates-unreadable-answer-fails",
        description="Gates: an answer that cannot be read counts as failed",
        file="services/eligibility_service.py",
        old='        if low not in ("yes", "no"):\n            return NEEDS_VERIFICATION',
        new='        if low not in ("yes", "no"):\n            return FAILED',
        command=GATES_OFFLINE_COMMAND, surface="backend",
        expect="test_an_answer_that_cannot_be_read_is_needs_verification_never_failed",
    ),
    Mutant(
        key="gates-garbled-confidence-trusted",
        description="Gates: a confidence outside 0..1 is clamped and trusted",
        file="services/eligibility_service.py",
        old='    if not 0.0 <= confidence <= 1.0:                       # NaN, negative or above 1: garbled, so never decisive\n        confidence = 0.0',
        new='    confidence = min(max(confidence, 0.0), 1.0)',
        command=GATES_OFFLINE_COMMAND, surface="backend",
        expect="test_a_garbled_confidence_is_never_a_decisive_one",
    ),
    Mutant(
        key="gates-preferred-can-be-gate",
        description="Gates: a Preferred item can be made a Mandatory Gate",
        file="services/requirements_v2/gates.py",
        old='            if item["importance"] != IMPORTANCE_REQUIRED:\n                raise ValueError("only a Required item can be a Mandatory Gate")\n',
        new='',
        command=GATES_OFFLINE_COMMAND, surface="backend",
        expect="test_a_preferred_item_cannot_be_made_a_gate",
    ),
    Mutant(
        key="gates-preferred-gate-enforced",
        description="Gates: a gate stored on a Preferred item is enforced",
        file="services/requirements_v2/gates.py",
        old='if gate is None or item["importance"] != IMPORTANCE_REQUIRED or gate.get("verification_method") is None:',
        new='if gate is None or gate.get("verification_method") is None:',
        command=GATES_OFFLINE_COMMAND, surface="backend",
        expect="test_a_gate_stored_on_a_preferred_item_is_refused_by_the_rules",
    ),
    Mutant(
        key="gates-no-method-accepted",
        description="Gates: a gate without a verification method passes the document rules",
        file="services/requirements_v2/gates.py",
        old='            if method is None:\n                bad("gate_without_verification_method"',
        new='            if False:\n                bad("gate_without_verification_method"',
        command=GATES_OFFLINE_COMMAND, surface="backend",
        expect="test_a_gate_without_a_verification_method_is_refused",
    ),
    Mutant(
        key="gates-record-for-cv-gate",
        description="Gates: a result can be recorded for a CV-evidence gate",
        file="services/eligibility_service.py",
        old='    if gate.method not in ("assessment", "manual"):',
        new='    if False:',
        command=GATES_PG_COMMAND, surface="backend",
        expect="test_a_result_cannot_be_recorded_for_a_cv_or_question_gate_or_with_an_invalid_value",
    ),
    Mutant(
        key="gates-override-without-reason",
        description="Gates: an override is accepted without a reason",
        file="services/eligibility_service.py",
        old='    if not reason:\n        raise EligibilityError("An override needs a reason."',
        new='    if False:\n        raise EligibilityError("An override needs a reason."',
        command=GATES_PG_COMMAND, surface="backend",
        expect="test_an_override_needs_a_reason_and_stores_original_new_user_and_time",
    ),
    Mutant(
        key="gates-link-other-job-accepted",
        description="Gates: a requirement gate may link a question of another job",
        file="services/requirements_edit.py",
        old='elif row[1].lower() != str(job_id).lower():',
        new='elif False:',
        command=GATES_PG_COMMAND, surface="backend",
        expect="test_a_question_of_another_job_cannot_be_linked",
    ),
    Mutant(
        key="gates-recruiter-may-override",
        description="Gates: a Recruiter may override a gate result",
        file="routers/gates.py",
        old='@router.post("/{application_id}/gates/{gate_key}/override", dependencies=[RequireRequirementsEditor])',
        new='@router.post("/{application_id}/gates/{gate_key}/override", dependencies=[RequireRequirementsReader])',
        command=MATRIX_COMMAND, surface="backend",
        expect="test_each_role_gets_exactly_the_access_the_matrix_gives_it",
    ),
    Mutant(
        key="gates-viewer-may-record-result",
        description="Gates: a Viewer may record a gate result",
        file="routers/gates.py",
        old='@router.post("/{application_id}/gates/{gate_key}/result", dependencies=[RequireRequirementsEditor])',
        new='@router.post("/{application_id}/gates/{gate_key}/result", dependencies=[RequireRequirementsReader])',
        command=GATES_PG_COMMAND, surface="backend",
        expect="test_a_recruiter_and_a_viewer_cannot_record_override_clear_or_evaluate",
    ),
    Mutant(
        key="gates-recruiter-may-confirm-gate",
        description="Gates: the editor role check is skipped when confirming a gate in the requirements editor",
        file="services/requirements_edit.py",
        old='    check_editor(user.role)\n    if not is_int(client_revision)',
        new='    if not is_int(client_revision)',
        command=GATES_PG_COMMAND, surface="backend",
        expect="test_a_recruiter_and_a_viewer_cannot_confirm_a_gate",
    ),
    Mutant(
        key="gates-existing-questions-become-gates",
        description="Gates: the migration turns every existing question into an eligibility gate",
        file="db/migrations/102_gates_knockout_criteria.sql",
        old='ADD COLUMN IF NOT EXISTS is_eligibility_gate  boolean     NOT NULL DEFAULT false',
        new='ADD COLUMN IF NOT EXISTS is_eligibility_gate  boolean     NOT NULL DEFAULT true',
        command=GATES_PG_COMMAND, surface="backend",
        expect="test_an_existing_style_question_is_advisory_by_default",
    ),
    Mutant(
        key="gates-rls-open",
        description="Gates: the gate results policy lets every tenant see every row",
        file="db/migrations/102_gates_knockout_criteria.sql",
        old="CREATE POLICY tenant_scope_application_gate_results ON cv_analyzer.application_gate_results FOR ALL TO PUBLIC\n    USING (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true))",
        new='CREATE POLICY tenant_scope_application_gate_results ON cv_analyzer.application_gate_results FOR ALL TO PUBLIC\n    USING (true)',
        command=GATES_PG_COMMAND, surface="backend",
        expect="test_another_tenant_cannot_see_or_change_gate_results",
    ),
    Mutant(
        key="gates-failed-gate-lets-score-win",
        description="Gates: with a score of 99, a failed not-eligible gate no longer changes the effective decision",
        file="services/eligibility_service.py",
        old="    if eligibility_status == NOT_ELIGIBLE:\n        return NOT_ELIGIBLE\n    if eligibility_status == REVIEW_REQUIRED or scoring_status == \"review_required\":",
        new="    if False:\n        return NOT_ELIGIBLE\n    if eligibility_status == REVIEW_REQUIRED or scoring_status == \"review_required\":",
        command=GATES_PG_COMMAND, surface="backend",
        expect="test_a_failed_not_eligible_gate_overrides_a_score_of_99_and_leaves_the_stored_score_and_decision_unchanged",
    ),
    Mutant(
        key="gates-missing-evidence-rejects",
        description="Gates: missing or unclear evidence makes the candidate Not Eligible",
        file="services/eligibility_service.py",
        old='    if any(g.result == FAILED for g in states):\n        return NOT_ELIGIBLE',
        new='    if any(g.result in (FAILED, NEEDS_VERIFICATION) for g in states):\n        return NOT_ELIGIBLE',
        command=GATES_OFFLINE_COMMAND, surface="backend",
        expect="test_missing_unclear_or_unassessed_evidence_gives_review_required_never_not_eligible",
    ),
    Mutant(
        key="gates-missing-evidence-rejects-at-high-score",
        description="Gates: with a score of 99, missing CV evidence makes the candidate Not Eligible",
        file="services/eligibility_service.py",
        old='    if any(g.result == FAILED for g in states):\n        return NOT_ELIGIBLE',
        new='    if any(g.result in (FAILED, NEEDS_VERIFICATION) for g in states):\n        return NOT_ELIGIBLE',
        command=GATES_PG_COMMAND, surface="backend",
        expect="test_missing_evidence_gives_review_required_not_not_eligible_even_at_a_score_of_99",
    ),
    Mutant(
        key="gates-confirmed-failure-only-reviews",
        description="Gates: clear evidence of non-compliance only sends the candidate to review",
        file="services/eligibility_service.py",
        old='    if any(g.result == FAILED for g in states):\n        return NOT_ELIGIBLE',
        new='    if False:\n        return NOT_ELIGIBLE',
        command=GATES_OFFLINE_COMMAND, surface="backend",
        expect="test_clear_evidence_of_non_compliance_makes_the_candidate_not_eligible_whatever_else_passed",
    ),
    Mutant(
        key="gates-unresolved-passes",
        description="Gates: a gate that needs verification or is not assessed lets the candidate through",
        file="services/eligibility_service.py",
        old='    if any(g.result in (NEEDS_VERIFICATION, NOT_ASSESSED) for g in states):',
        new='    if False:',
        command=GATES_OFFLINE_COMMAND, surface="backend",
        expect="test_missing_unclear_or_unassessed_evidence_gives_review_required_never_not_eligible",
    ),
    Mutant(
        key="gates-override-still-blocks",
        description="Gates: an overridden gate still blocks eligibility",
        file="services/eligibility_service.py",
        old='    if any(g.result in (NEEDS_VERIFICATION, NOT_ASSESSED) for g in states):',
        new='    if any(g.result in (NEEDS_VERIFICATION, NOT_ASSESSED, OVERRIDDEN) for g in states):',
        command=GATES_OFFLINE_COMMAND, surface="backend",
        expect="test_every_gate_passed_or_overridden_is_eligible",
    ),
    Mutant(
        key="gates-processing-error-rejects",
        description="Gates: a model or parsing error is recorded as a failed gate",
        file="services/eligibility_service.py",
        old='{"result": PROCESSING_ERROR, "confidence": None, "summary": f"{PROCESSING_ERROR_PREFIX} {reason}"}',
        new='{"result": FAILED, "confidence": None, "summary": f"{PROCESSING_ERROR_PREFIX} {reason}"}',
        command=GATES_PG_COMMAND, surface="backend",
        expect="test_a_model_or_parsing_error_is_a_processing_error_and_never_rejects_the_candidate",
    ),
    Mutant(
        key="gates-processing-error-erases-result",
        description="Gates: a later processing error replaces an earlier decisive result",
        file="services/eligibility_service.py",
        old='if prior is not None and prior["computed_result"] in (PASSED, FAILED):',
        new='if False:',
        command=GATES_PG_COMMAND, surface="backend",
        expect="test_a_later_processing_error_does_not_change_an_earlier_decisive_result",
    ),
    Mutant(
        key="gates-conflicting-evidence-fails",
        description="Gates: a conflicting CV verdict with a quote fails the gate",
        file="services/eligibility_service.py",
        old='if verdict == "not_met" and quoted and confidence >= FAIL_CONFIDENCE and item.get("explicit_contradiction") is True:',
        new='if verdict != "met" and quoted and confidence >= FAIL_CONFIDENCE and item.get("explicit_contradiction") is True:',
        command=GATES_OFFLINE_COMMAND, surface="backend",
        expect="test_ambiguous_conflicting_or_incomplete_evidence_is_review_required_not_not_eligible",
    ),
    Mutant(
        key="gates-failure-action-exposed",
        description="Gates: the requirement projection returns the legacy failure action again",
        file="services/requirements_edit.py",
        old='"mandatory": True, "verification_method": gate["verification_method"],\n                "linked_question_id"',
        new='"mandatory": True, "verification_method": gate["verification_method"], "failure_action": gate.get("failure_action"),\n                "linked_question_id"',
        command=GATES_OFFLINE_COMMAND, surface="backend",
        expect="test_a_legacy_value_is_neither_used_nor_exposed_by_the_decision_or_the_projection",
    ),
    Mutant(
        key="gates-ui-processing-error-hidden",
        description="Gates UI: a processing error is shown as an ordinary result",
        file="components/GateSummary.tsx",
        old='{gate.processing_error ? copy.processingError : copy.result[gate.result]}',
        new='{copy.result[gate.result]}',
        command=GATES_UI_COMMAND, surface="frontend",
    ),
    Mutant(
        key="gates-ui-everyone-overrides",
        description="Gates UI: every role is offered the override control",
        file="components/GateSummary.tsx",
        old='const canAct = canEditRequirements(userRole);',
        new='const canAct = true;',
        command=GATES_UI_COMMAND, surface="frontend",
    ),
    Mutant(
        key="gates-ui-questions-become-gates",
        description="Gates UI: a saved question is a gate unless it says otherwise",
        file="utils/gates.ts",
        old='is_eligibility_gate: q.is_eligibility_gate === true };',
        new='is_eligibility_gate: q.is_eligibility_gate !== false };',
        command=GATES_UI_COMMAND, surface="frontend",
    ),
    Mutant(
        key="gates-ui-suggestion-auto-confirmed",
        description="Gates UI: an AI suggestion is shown as a confirmed Mandatory Gate",
        file="utils/gates.ts",
        old="  if (item.gate && item.importance === 'required') {\n    out.push({ key: 'gate'",
        new="  if ((item.gate || item.gate_suggestion) && item.importance === 'required') {\n    out.push({ key: 'gate'",
        command=GATES_UI_COMMAND, surface="frontend",
    ),
    Mutant(
        key="items-partial-factor",
        description='Item scoring: Partially Met earns more than half',
        file='services/item_scoring/calc.py',
        old='PARTIAL_FACTOR = Fraction(1, 2)',
        new='PARTIAL_FACTOR = Fraction(3, 5)',
        command=ITEM_OFFLINE_COMMAND, surface="backend",
        expect="test_partially_met_earns_exactly_half_the_item_weight",
    ),
    Mutant(
        key="items-not-met-credit",
        description='Item scoring: Not Met earns partial credit',
        file='services/item_scoring/calc.py',
        old='MET: Fraction(1), PARTIALLY_MET: PARTIAL_FACTOR, NOT_MET: Fraction(0)}',
        new='MET: Fraction(1), PARTIALLY_MET: PARTIAL_FACTOR, NOT_MET: Fraction(1, 10)}',
        command=ITEM_OFFLINE_COMMAND, surface="backend",
        expect="test_not_met_earns_zero",
    ),
    Mutant(
        key="items-unclear-final-score",
        description='Item scoring: an unclear item is scored as zero and the category reports a final score',
        file='services/item_scoring/calc.py',
        old='score=score_min if resolved else None,',
        new='score=score_min,',
        command=ITEM_OFFLINE_COMMAND, surface="backend",
        expect="test_a_category_with_an_unclear_item_has_no_score_or_contribution_only_a_range",
    ),
    Mutant(
        key="items-unclear-max-range",
        description="Item scoring: the provisional maximum does not count an unclear item's full weight",
        file='services/item_scoring/calc.py',
        old='score_min, score_max = decided, decided + open_weight',
        new='score_min, score_max = decided, decided',
        command=ITEM_OFFLINE_COMMAND, surface="backend",
        expect="test_every_unclear_item_counts_zero_in_the_minimum_and_its_full_weight_in_the_maximum",
    ),
    Mutant(
        key="items-rounding-floor",
        description='Item scoring: the final score is floored instead of rounded half up',
        file='services/item_scoring/calc.py',
        old='return int((value + Fraction(1, 2)) // 1)',
        new='return int(value // 1)',
        command=ITEM_OFFLINE_COMMAND, surface="backend",
        expect="test_the_single_rounding_rule_is_half_up_on_the_final_score",
    ),
    Mutant(
        key="items-preferred-in-score",
        description='Item scoring: Preferred items enter the calculation',
        file='services/item_scoring/service.py',
        old='for r in rows if r["importance"] == "required"]\n    return calc.compute',
        new='for r in rows if r["importance"] in ("required", "preferred")]\n    return calc.compute',
        command=ITEM_PG_COMMAND, surface="backend",
        expect="test_preferred_results_never_change_the_score_or_the_status",
    ),
    Mutant(
        key="items-partial-gate-passes",
        description='Item scoring: a partially met Mandatory Gate item passes the gate',
        file='services/eligibility_service.py',
        old='"partially_met": FAILED,',
        new='"partially_met": PASSED,',
        command=ITEM_OFFLINE_COMMAND, surface="backend",
        expect="test_the_gate_outcome_for_each_item_result",
    ),
    Mutant(
        key="items-unclear-gate-fails",
        description='Item scoring: an unclear Mandatory Gate item makes the candidate Not Eligible',
        file='services/eligibility_service.py',
        old='"unclear": NEEDS_VERIFICATION}',
        new='"unclear": FAILED}',
        command=ITEM_OFFLINE_COMMAND, surface="backend",
        expect="test_the_gate_outcome_for_each_item_result",
    ),
    Mutant(
        key="items-stale-never",
        description='Item scoring: a score from an earlier revision is never stale',
        file='services/item_scoring/service.py',
        old='stale = current is not None and run["requirements_revision"] != current',
        new='stale = False',
        command=ITEM_PG_COMMAND, surface="backend",
        expect="test_a_requirements_edit_makes_the_prior_score_stale_and_the_view_says_which_revisions",
    ),
    Mutant(
        key="items-effective-ignores-review",
        description='Item scoring: the effective decision ignores an unfinished item-level score',
        file='services/eligibility_service.py',
        old='if eligibility_status == REVIEW_REQUIRED or scoring_status == "review_required":',
        new='if eligibility_status == REVIEW_REQUIRED:',
        command=ITEM_OFFLINE_COMMAND, surface="backend",
        expect="test_the_effective_decision_rule",
    ),
    Mutant(
        key="items-effective-sql-ignores-review",
        description='Item scoring: the SQL effective decision ignores an unfinished item-level score',
        file='services/effective_decision.py',
        old=" OR {alias}.scoring_status = 'review_required' THEN",
        new=' THEN',
        command=ITEM_PG_COMMAND, surface="backend",
        expect="test_the_sql_effective_decision_matches_the_python_rule_for_every_state",
    ),
    Mutant(
        key="items-ui-stale-hidden",
        description='Item scoring UI: the stale warning is never shown',
        file='components/ItemScoring.tsx',
        old='{view.stale && (',
        new='{false && (',
        command=ITEM_UI_COMMAND, surface="frontend",
    ),
    Mutant(
        key="items-ui-everyone-resolves",
        description='Item scoring UI: every role is offered the resolve control',
        file='components/ItemScoring.tsx',
        old='const canAct = canEditRequirements(userRole);',
        new='const canAct = true;',
        command=ITEM_UI_COMMAND, surface="frontend",
    ),
    Mutant(
        key="items-ui-stored-decision-shown",
        description='Item scoring UI: the stored decision is shown as the overall outcome',
        file='components/ItemScoring.tsx',
        old='outcomeLabel(view.effective_decision, lang)',
        new='outcomeLabel(view.decision, lang)',
        command=ITEM_UI_COMMAND, surface="frontend",
    ),
    Mutant(
        key="items-ui-missing-score-zero",
        description='Item scoring UI: a missing score is shown as zero',
        file='utils/itemScoring.ts',
        old="return score == null ? '—' : String(score);",
        new='return String(score ?? 0);',
        command=ITEM_UI_COMMAND, surface="frontend",
    ),
    Mutant(
        key="matrix-failed-gate-ignored",
        description='Matrix: a confirmed failed gate no longer makes the candidate Not Eligible',
        file='services/matrix/query.py',
        old="WHEN b.eligibility_status = 'not_eligible' THEN 'not_eligible'",
        new="WHEN b.eligibility_status = 'not_eligible' THEN 'eligible'",
        command=MATRIX_PG_COMMAND, surface="backend",
        expect="test_a_failed_gate_makes_a_high_score_candidate_not_eligible_without_touching_the_score",
    ),
    Mutant(
        key="matrix-score-replaces-eligibility",
        description='Matrix: a high score decides eligibility instead of the gates',
        file='services/matrix/query.py',
        old='_ELIGIBILITY = """CASE WHEN b.eligibility_status = \'not_eligible\'',
        new='_ELIGIBILITY = """CASE WHEN b.score >= 85 THEN \'eligible\' WHEN b.eligibility_status = \'not_eligible\'',
        command=MATRIX_OFFLINE_COMMAND, surface="backend",
        expect="test_the_eligibility_rule_puts_a_failed_gate_before_everything_else",
    ),
    Mutant(
        key="matrix-unclear-becomes-not-met",
        description='Matrix: an unclear Required result is shown as Not Met',
        file='services/matrix/summaries.py',
        old='"not_met": "not_met", "unclear": "unclear"}',
        new='"not_met": "not_met", "unclear": "not_met"}',
        command=MATRIX_OFFLINE_COMMAND, surface="backend",
        expect="test_a_mandatory_result_keeps_its_own_word_and_unclear_is_not_converted",
    ),
    Mutant(
        key="matrix-unclear-not-review-required",
        description='Matrix: an unresolved Required item no longer makes the candidate Review Required',
        file='services/matrix/query.py',
        old="WHEN b.eligibility_status = 'review_required' OR b.scoring_status = 'review_required' THEN 'review_required'",
        new="WHEN b.eligibility_status = 'review_required' THEN 'review_required'",
        command=MATRIX_PG_COMMAND, surface="backend",
        expect="test_unclear_or_unverifiable_evidence_is_review_required_not_not_eligible",
    ),
    Mutant(
        key="matrix-preferred-exposes-partial",
        description='Matrix: an unexpected Preferred result is shown as it is (for example partially met)',
        file='services/matrix/summaries.py',
        old='return _PREFERRED.get(stored, "unclear")',
        new='return _PREFERRED.get(stored, stored)',
        command=MATRIX_OFFLINE_COMMAND, surface="backend",
        expect="test_a_preferred_item_never_exposes_partial_compliance",
    ),
    Mutant(
        key="matrix-preferred-filter-partial",
        description='Matrix: a Preferred filter may name partial compliance',
        file='services/matrix/config.py',
        old='results: list[Literal["yes", "no", "unclear", "not_assessed"]] = Field(min_length=1, max_length=4)\n\n\nclass GateFilter',
        new='results: list[Literal["yes", "no", "unclear", "not_assessed", "partially_met"]] = Field(min_length=1, max_length=5)\n\n\nclass GateFilter',
        command=MATRIX_OFFLINE_COMMAND, surface="backend",
        expect="test_a_preferred_filter_cannot_name_partial_compliance",
    ),
    Mutant(
        key="matrix-tenant-predicate-layouts",
        description='Matrix: layouts are read without the tenant predicate',
        file='services/matrix/config.py',
        old="    WHERE c.tenant_id = CAST(:tid AS uuid)\n      AND ((c.scope = 'tenant')",
        new="    WHERE ((c.scope = 'tenant')",
        command=MATRIX_PG_COMMAND, surface="backend",
        expect="test_the_explicit_tenant_predicates_hold_even_when_row_level_security_is_not_the_one_deciding",
    ),
    Mutant(
        key="matrix-tenant-predicate-reset",
        description='Matrix: a layout is reset without the tenant predicate',
        file='services/matrix/config.py',
        old='text(f"DELETE FROM candidate_matrix_configs WHERE tenant_id = CAST(:tid AS uuid) AND {where}")',
        new='text(f"DELETE FROM candidate_matrix_configs WHERE {where}")',
        command=MATRIX_PG_COMMAND, surface="backend",
        expect="test_the_explicit_tenant_predicates_hold_even_when_row_level_security_is_not_the_one_deciding",
    ),
    Mutant(
        key="matrix-export-leaks-hidden-columns",
        description='Matrix: the export writes every available column, not the visible ones',
        file='routers/candidate_matrix.py',
        old='        columns = [registry[c] for c in visible]',
        new='        columns = [c for c in registry.values() if c.available]',
        command=MATRIX_PG_COMMAND, surface="backend",
        expect="test_the_export_follows_the_visible_columns_filters_sort_and_hides_everything_else",
    ),
    Mutant(
        key="matrix-precedence-reversed",
        description='Matrix: the tenant default beats a personal view',
        file='services/matrix/config.py',
        old='    for scope in ("personal", "job", "tenant"):\n        layer = layers.get(scope)',
        new='    for scope in ("tenant", "job", "personal"):\n        layer = layers.get(scope)',
        command=MATRIX_OFFLINE_COMMAND, surface="backend",
        expect="test_precedence_is_personal_then_job_then_tenant_then_system",
    ),
    Mutant(
        key="matrix-legacy-breaks-the-matrix",
        description='Matrix: an application without item-level results drops out of the matrix',
        file='services/matrix/query.py',
        old='        LEFT JOIN ev ON ev.run_id = b.run_id',
        new='        JOIN ev ON ev.run_id = b.run_id',
        command=MATRIX_PG_COMMAND, surface="backend",
        expect="test_legacy_and_unscored_applications_do_not_break_the_matrix",
    ),
    Mutant(
        key="matrix-unstable-paging",
        description='Matrix: sorting loses its deterministic tie-break',
        file='services/matrix/query.py',
        old='return f"{expr} {direction} NULLS LAST, m.applied_at DESC, m.application_id", params',
        new='return f"{expr} {direction} NULLS LAST", params',
        command=MATRIX_OFFLINE_COMMAND, surface="backend",
        expect="test_every_ordering_ends_with_the_deterministic_tie_break",
    ),
    Mutant(
        key="matrix-viewer-saves-views",
        description='Matrix: a viewer may save a personal view',
        file='auth/roles.py',
        old='MATRIX_PERSONAL_ROLES = frozenset({ADMIN, HR_MANAGER, RECRUITER})',
        new='MATRIX_PERSONAL_ROLES = frozenset({ADMIN, HR_MANAGER, RECRUITER, VIEWER})',
        command=MATRIX_PG_COMMAND, surface="backend",
        expect="test_roles_for_reading_saving_and_exporting",
    ),
    Mutant(
        key="matrix-ui-viewer-controls",
        description='Matrix UI: every role is offered the personal save',
        file='pages/CandidateMatrix.tsx',
        old='const canPersonal = !!config?.permissions.edit_personal;',
        new='const canPersonal = true;',
        command=MATRIX_UI_COMMAND, surface="frontend",
    ),
    Mutant(
        key="matrix-ui-preferred-partial",
        description='Matrix UI: the Preferred criteria filter offers partial compliance',
        file='components/MatrixFilters.tsx',
        old="preferred: ['yes', 'no', 'unclear', 'not_assessed'],",
        new="preferred: ['yes', 'partially_met', 'no', 'unclear', 'not_assessed'],",
        command=MATRIX_UI_COMMAND, surface="frontend",
    ),
    Mutant(
        key="matrix-ui-unclear-unflagged",
        description='Matrix UI: an unclear Required summary is no longer flagged',
        file='pages/CandidateMatrix.tsx',
        old='{m.assessed && (m.unclear ?? 0) > 0 && <span',
        new='{false && <span',
        command=MATRIX_UI_COMMAND, surface="frontend",
    ),
    Mutant(
        key="matrix-ui-candidate-column-not-frozen",
        description='Matrix UI: the candidate column scrolls away',
        file='pages/CandidateMatrix.tsx',
        old='layout.frozen_candidate && id === NAME_COLUMN ?',
        new='false && id === NAME_COLUMN ?',
        command=MATRIX_UI_COMMAND, surface="frontend",
    ),
    Mutant(
        key="matrix-ui-export-unfiltered",
        description='Matrix UI: the export ignores the current filters',
        file='pages/CandidateMatrix.tsx',
        old='exportMatrix(jobId, { columns: layout.columns, filters: layout.filters, sort, lang })',
        new='exportMatrix(jobId, { columns: layout.columns, filters: {}, sort, lang })',
        command=MATRIX_UI_COMMAND, surface="frontend",
    ),
)


def _copy_repo(dest: pathlib.Path) -> None:
    def ignore(_dir: str, names: list[str]) -> set[str]:
        return {n for n in names if n in {"node_modules", ".git", "__pycache__", "dist", "artifacts"}}
    shutil.copytree(ROOT, dest, ignore=ignore, symlinks=False, dirs_exist_ok=False)
    nm = ROOT / "node_modules"
    if nm.exists():
        (dest / "node_modules").symlink_to(nm)


INVALID_OUTPUT = re.compile(
    r"ERROR collecting|SyntaxError|IndentationError|ImportError|ModuleNotFoundError|INTERNALERROR|"
    r"^ERROR |ChainValidationError|ManifestError|no tests ran", re.M)


def _repo_path(mutant: Mutant, file: str) -> str:
    """Backend mutants name files relative to backend/; the repository path is backend/ followed by that name."""
    if mutant.surface == "backend" and not file.startswith("backend/"):
        return f"backend/{file}"
    return file


def _apply(mutant: Mutant, work: pathlib.Path) -> str | None:
    """Apply the primary edit and every extra edit. Returns an error string when an anchor does not match exactly once."""
    edits = [(mutant.file, mutant.old, mutant.new), *mutant.edits] if mutant.surface == "backend" else []
    if mutant.surface != "backend":
        edits = [(mutant.file, mutant.old, mutant.new)]
    for file, old, new in edits:
        target = work / _repo_path(mutant, file)
        text = target.read_text(encoding="utf-8")
        count = text.count(old)
        if count != 1:
            return f"anchor in {file} found {count} times; mutant not applied"
        target.write_text(text.replace(old, new), encoding="utf-8")
    return None


def _run_command(mutant: Mutant, work: pathlib.Path) -> tuple[int, str]:
    cwd = work / ("backend" if mutant.surface == "backend" else "")
    command = _command_for(mutant)
    if mutant.surface == "backend" and "-rfE" not in command:
        command.insert(command.index("pytest") + 1, "-rfE")    # the verdict reads the FAILED lines
    proc = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=1500)
    return proc.returncode, proc.stdout + proc.stderr


def _verdict(mutant: Mutant, returncode: int, output: str) -> tuple[bool, str]:
    """(caught, detail). Backend mutants are caught only by the named test failing; other surfaces by a non-zero exit."""
    if mutant.surface != "backend":
        tail = (output.strip().splitlines() or [""])[-1]
        return returncode != 0, tail
    if returncode == 0:
        return False, "every test passed with the mutant in place"
    if not mutant.expect:
        return False, "backend mutant has no expected test"
    # A FAILED line for the named test is the evidence: a setup or collection error reports ERROR, never FAILED for that test.
    if not re.search(rf"^FAILED \S*::(?:\S*::)?{re.escape(mutant.expect)}", output, re.M):
        if INVALID_OUTPUT.search(output):
            return False, "collection, import or setup error: not an assertion"
        return False, f"the expected test {mutant.expect} did not fail"
    return True, f"{mutant.expect} failed as expected"


def _check(mutant: Mutant, baselines: dict) -> tuple[bool, str]:
    """Run one mutant in a throw-away copy. The unmutated copy must pass the same command first (cached per command)."""
    with tempfile.TemporaryDirectory(prefix="mutant-") as tmp:
        work = pathlib.Path(tmp) / "repo"
        _copy_repo(work)
        command_key = (mutant.surface, tuple(_command_for(mutant)))
        if mutant.surface == "backend" and command_key not in baselines:
            code, output = _run_command(mutant, work)
            baselines[command_key] = (code == 0, output.strip().splitlines()[-1:] or [""])
        if mutant.surface == "backend" and not baselines[command_key][0]:
            return False, f"baseline failed on the unmutated copy: {baselines[command_key][1][0]}"
        error = _apply(mutant, work)
        if error:
            return False, error
        code, output = _run_command(mutant, work)
        return _verdict(mutant, code, output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="run mutants whose key contains this text")
    args = parser.parse_args(argv)

    selected = [m for m in MUTANTS if not args.only or args.only in m.key]
    if not selected:
        print("no mutants selected", file=sys.stderr)
        return 2

    failures = 0
    baselines: dict = {}
    for mutant in selected:
        caught, detail = _check(mutant, baselines)
        verdict = "caught" if caught else "SURVIVED"
        failures += 0 if caught else 1
        print(f"{verdict:9} {mutant.key:<36} {mutant.description}")
        print(f"          {detail}")

    print(f"\n{len(selected) - failures}/{len(selected)} selected mutants caught")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
