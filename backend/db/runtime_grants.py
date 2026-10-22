"""Runtime privilege matrix for the cv_analyzer schema, and the generator for ops/0005_runtime_privileges.sql.template.

Every grant the runtime roles hold is listed here, derived from the SQL each code path issues (request paths for cv_app, worker paths
for cv_worker, platform-administration paths for cv_admin). Nothing else is granted. A test compares the live database with this
matrix and fails on any extra or missing privilege, so a grant can change only in this file, through review.

Verbs: S = SELECT, I = INSERT, U = UPDATE, D = DELETE.

    python -m db.runtime_grants > db/ops/0005_runtime_privileges.sql.template
"""
from __future__ import annotations

import sys

APP = "cv_app"
WORKER = "cv_worker"
ADMIN = "cv_admin"
SECRETS = "cv_secrets"
RUNTIME_ROLES = (APP, WORKER, ADMIN, SECRETS)

# table -> role -> verbs. Append-only logs grant INSERT and SELECT only; the runtime never updates or deletes an audit or usage row.
MATRIX: dict[str, dict[str, str]] = {
    "agency_user_clients":                       {APP: "DIS"},
    "ai_model_pricing":                          {APP: "S", ADMIN: "SU", WORKER: "S"},
    "ai_model_registry":                         {APP: "S", ADMIN: "ISU", WORKER: "S"},
    "ai_prompts":                                {APP: "S", ADMIN: "ISU", WORKER: "S"},
    "ai_stage_defaults":                         {APP: "S", ADMIN: "ISU", WORKER: "S"},
    "ai_usage_log":                              {APP: "I", ADMIN: "S", WORKER: "I"},
    "application_candidate_tags":                {APP: "DIS"},
    "application_files":                         {APP: "DIS", ADMIN: "I", WORKER: "ISU"},
    "application_intake_log":                    {APP: "ISU", ADMIN: "ISU", WORKER: "ISU"},
    "application_knockout_answer_suggestions":   {APP: "ISU", WORKER: "ISU"},
    "application_knockout_answer_validations":   {APP: "ISU"},
    "application_knockout_answers":              {APP: "ISU", ADMIN: "IU", WORKER: "ISU"},
    "application_knockout_validation_runs":      {APP: "IS"},
    "application_score_comparisons":             {APP: "S", WORKER: "I"},
    "application_scores":                        {APP: "DS", WORKER: "IS"},
    "application_workflow_history":              {APP: "IS"},
    "applications":                              {APP: "DISU", ADMIN: "IS", WORKER: "ISU"},
    "audit_logs":                                {APP: "I", ADMIN: "S", WORKER: "I"},
    "candidate_approvals":                       {APP: "DISU"},
    "candidate_comments":                        {APP: "DISU"},
    "candidate_communications":                  {APP: "DISU"},
    "candidate_interview_feedback":              {APP: "ISU"},
    "candidate_interviews":                      {APP: "DISU"},
    "candidate_message_templates":               {APP: "DISU"},
    "candidate_saved_views":                     {APP: "DISU"},
    "candidate_tags":                            {APP: "DIS"},
    "client_organizations":                      {APP: "ISU"},
    "communication_automation_rules":            {APP: "DISU"},
    "duplicate_application_logs":                {APP: "S", WORKER: "I"},
    "email_ingest_log":                          {ADMIN: "S", WORKER: "ISU"},
    "job_campaigns":                             {APP: "DISU", ADMIN: "S", WORKER: "S"},
    "job_criteria":                              {APP: "ISU", ADMIN: "S", WORKER: "SU"},
    "job_knockout_questions":                    {APP: "DIS", ADMIN: "S", WORKER: "S"},
    "jobs":                                      {APP: "ISU", ADMIN: "SU", WORKER: "S"},
    "notification_log":                          {WORKER: "ISU"},
    "plan_features":                             {APP: "S", ADMIN: "SU"},
    "subscription_plans":                        {APP: "S", ADMIN: "DISU", WORKER: "S"},
    "system_config":                             {APP: "S", ADMIN: "ISU", WORKER: "S"},
    "tenant_usage_monthly":                      {APP: "ISU", ADMIN: "ISU", WORKER: "ISU"},
    "tenant_workflow_policies":                  {APP: "ISU"},
    "tenants":                                   {APP: "SU", ADMIN: "ISU", WORKER: "S"},
    "user_invites":                              {ADMIN: "I"},
    "users":                                     {APP: "ISU", ADMIN: "ISU", WORKER: "S"},
}

# Tables that hold data but are granted to no runtime role.
#   LEGACY_PRESERVED_TABLES  exist only in a database converted from the legacy production lineage (db/legacy). The current release's code
#                            does not use them, so nothing is granted; the data is kept, owned by the migrator and sealed by 0005.
#   OPERATOR_TABLES          the migration ledger and the conversion's evidence table; runtime roles never touch them.
# The preflight accepts a table without a matrix entry only if it is named here, and only the first group when the evidence table shows
# the conversion ran.
LEGACY_PRESERVED_TABLES = ("bulk_upload_batches", "bulk_upload_rows", "tenant_features", "tenant_feature_audit")
OPERATOR_TABLES = ("schema_migrations", "schema_migration_legacy_evidence")

# platform_secrets: no table privilege for any runtime role. Metadata columns only, never value. Writes go through the setter, reads
# of values through the getter.
PLATFORM_SECRETS_METADATA = ("key", "masked_value", "description", "category", "is_critical", "has_value", "updated_at", "updated_by_email")
PLATFORM_SECRETS_COLUMN_GRANTS: dict[str, tuple[str, ...]] = {
    APP: ("key", "has_value"),
    ADMIN: PLATFORM_SECRETS_METADATA,
}
PLATFORM_SECRETS_COLUMNS = (
    "key", "value", "masked_value", "description", "category", "is_critical", "has_value", "updated_at", "updated_by",
    "updated_by_email",
)

# Functions: the only way a runtime role reaches a secret value or changes one.
FUNCTION_EXECUTE: dict[str, str] = {
    "get_platform_secret(text)": SECRETS,
    "set_platform_secret(text, text, text, text, text)": ADMIN,
}

VERB_PRIVILEGES = {"S": "SELECT", "I": "INSERT", "U": "UPDATE", "D": "DELETE"}


def expected(table: str, role: str) -> set[str]:
    """The table privileges the matrix grants role on table, as SQL privilege names."""
    return {VERB_PRIVILEGES[v] for v in MATRIX.get(table, {}).get(role, "")}


def generate_sql() -> str:
    """The operator script. Revokes every runtime privilege on the schema first, then grants exactly the matrix."""
    roles = ", ".join(RUNTIME_ROLES)
    out = [
        "-- ops/0005_runtime_privileges.sql.template",
        "--",
        "-- GENERATED from backend/db/runtime_grants.py. Do not edit by hand. Regenerate with:",
        "--   python -m db.runtime_grants > db/ops/0005_runtime_privileges.sql.template",
        "-- A test fails if this file differs from the generator output.",
        "--",
        "-- OPERATOR TEMPLATE, not a migration. Run after 0004 and after ownership has moved to the migrator role:",
        "--   psql -X -v ON_ERROR_STOP=1 -d \"$DATABASE\" -f 0005_runtime_privileges.sql.template",
        "-- It revokes every privilege the runtime roles hold on the application schema, then grants exactly the matrix. Nothing is",
        "-- granted to PUBLIC.",
        "",
        "\\set ON_ERROR_STOP on",
        "",
        "BEGIN;",
        "",
        "-- 1. Start from nothing.",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA cv_analyzer FROM {roles}, PUBLIC;",
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA cv_analyzer FROM {roles}, PUBLIC;",
        f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA cv_analyzer FROM {roles}, PUBLIC;",
        f"REVOKE ALL ON SCHEMA cv_analyzer FROM {roles}, PUBLIC;",
        f"REVOKE ALL (" + ", ".join(PLATFORM_SECRETS_COLUMNS) + f") ON cv_analyzer.platform_secrets FROM {roles}, PUBLIC;",
        "",
        "-- 2. Schema access and the table grants of the matrix.",
        f"GRANT USAGE ON SCHEMA cv_analyzer TO {', '.join(RUNTIME_ROLES)};",
    ]
    for table in sorted(MATRIX):
        for role in (APP, WORKER, ADMIN, SECRETS):
            verbs = MATRIX[table].get(role)
            if not verbs:
                continue
            privileges = ", ".join(VERB_PRIVILEGES[v] for v in ("S", "I", "U", "D") if v in verbs)
            out.append(f"GRANT {privileges} ON cv_analyzer.{table} TO {role};")
    out += ["", "-- 3. Column grants: platform_secrets metadata only. No value column for any runtime role."]
    for role, columns in PLATFORM_SECRETS_COLUMN_GRANTS.items():
        out.append(f"GRANT SELECT ({', '.join(columns)}) ON cv_analyzer.platform_secrets TO {role};")
    # job_code_seq is the only sequence. Only the job-creation path (routers/jobs.py) draws from it, as cv_app.
    out += ["", "-- 4. Sequences: job_code_seq only.",
            f"GRANT USAGE ON SEQUENCE cv_analyzer.job_code_seq TO {APP};", "",
            "-- 5. Functions: each executable by one role only.",
            ]
    for signature, role in FUNCTION_EXECUTE.items():
        out.append(f"GRANT EXECUTE ON FUNCTION cv_analyzer.{signature} TO {role};")
    out += ["", "COMMIT;", ""]
    return "\n".join(out)


if __name__ == "__main__":
    sys.stdout.write(generate_sql())
