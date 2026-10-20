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
