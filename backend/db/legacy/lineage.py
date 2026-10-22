"""The legacy production lineage, and the checks that prove a database is exactly that.

Production source commit 63f3bd7 followed migrations 002 to 092 and then its own 093 to 108 (db/legacy/lineage_63f3bd7). Main numbers
its migrations differently from 093 on, and has no ledger-less path. This module holds what a safe conversion needs to know:

  * what the base chain (002 to 092) and each legacy file produce, from db/legacy/reference.json (generated from clean builds, see
    scripts/generate_legacy_reference.py);
  * the four base files that did not apply on a clean chain and are replaced or repaired (REPAIR_FILES);
  * data checks for migrations whose effect is rows, not objects;
  * the explicit reconciliation of the objects where legacy and main disagree.

Nothing here writes. db/legacy/upgrade.py applies the result."""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field

from . import fingerprint as F

HERE = pathlib.Path(__file__).resolve().parent
LEGACY_DIR = HERE / "lineage_63f3bd7"
REFERENCE = json.loads((HERE / "reference.json").read_text(encoding="utf-8"))
LINEAGE_NAME = "legacy-63f3bd7"
LEGACY_COMMIT = REFERENCE["legacy_source_commit"]

# Four base files did not apply on a clean chain: 010 (replaced by 095), 024, 070 (replaced by 096) and 087 (replaced by 097). A working
# production database has the effects of 010, 024 and 087, because the application depends on them (plans, configuration keys, and
# application_knockout_answers.tenant_id); those were applied by hand at the time. The effect of 070 cannot have applied anywhere, because
# the file has a syntax error, so production lacks the SLA index and defaults.
#
# AUTO_REPAIRS are run by the conversion when the live catalogue lacks what they produce. Only 096 qualifies: it is order-independent,
# idempotent and validates what it finds. 095 and 097 validate against the state at their own position in the chain and raise on a
# database that later migrations have changed, and 024 depends on 095, so they are never run out of order. If their effects are absent,
# the conversion stops and says which objects are missing.
AUTO_REPAIRS = ("096_repair_070_sla_policy_index.sql",)
MUST_BE_PRESENT = ("095_repair_010_platform_control.sql", "024_plan_features_seed.sql", "097_repair_087_knockout_answer_tenant.sql")
REPAIR_FILES = AUTO_REPAIRS + MUST_BE_PRESENT
# The originals that the repairs replace. They are never executed; the ledger records them as superseded.
SUPERSEDED_ORIGINALS = {
    "010_platform_control.sql": "095_repair_010_platform_control.sql",
    "070_sla_thresholds_config.sql": "096_repair_070_sla_policy_index.sql",
    "087_autosave_deterministic_answers.sql": "097_repair_087_knockout_answer_tenant.sql",
}
# Main files that the conversion applies itself, because production has the legacy form of their effect.
BRIDGE_RUN = "093_requirements_v2_storage_schema.sql"           # idempotent; legacy 106 and 107 added the same columns
BRIDGE_RECONCILED = "094_candidate_reuse_submission_source.sql"  # narrows a check that legacy 099 widened; see RECONCILIATIONS


def _fold(files: list[dict]) -> tuple[dict[str, str], dict[str, str]]:
    """(state, setter) after applying each file's delta in order. `setter` names the last file that set each identity."""
    state: dict[str, str] = {}
    setter: dict[str, str] = {}
    for item in files:
        for key in item["removed"]:
            state.pop(key, None)
            setter.pop(key, None)
        for key, value in item["set"].items():
            state[key] = value
            setter[key] = item["file"]
    return state, setter


def base_files() -> list[dict]:
    return REFERENCE["base"]


def legacy_files() -> list[dict]:
    return REFERENCE["legacy"]


EXPECTED_BASE, SETTER_BASE = _fold(base_files())
EXPECTED_LEGACY, SETTER_LEGACY = _fold(base_files() + legacy_files())


# --- data checks ----------------------------------------------------------------------------------------------------------------
# For a migration whose effect is a data correction, the check counts the rows that the correction would still change. It must be
# zero. These are the only checks of this kind; a migration without a structural or data check is listed in ATTESTED with the reason.
@dataclass(frozen=True)
class DataCheck:
    file: str
    description: str
    sql: str            # returns one integer: the number of rows that still show the problem the migration corrected


DATA_CHECKS: tuple[DataCheck, ...] = (
    DataCheck("059_rename_scored_to_ai_scored.sql", "no application is still in the retired 'scored' state",
              "SELECT count(*) FROM cv_analyzer.applications WHERE processing_status = 'scored'"),
    DataCheck("077_clean_stopped_workflow_pollution.sql", "no failed, stopped application carries a workflow status",
              "SELECT count(*) FROM cv_analyzer.applications WHERE processing_status = 'failed' AND stopped_reason IS NOT NULL "
              "AND workflow_status IS NOT NULL"),
    DataCheck("081_fix_knockout_answer_value_encoding.sql", "no knockout answer is a JSON-quoted string or empty",
              "SELECT count(*) FROM cv_analyzer.application_knockout_answers "
              "WHERE (answer_value ~ '^\"[^\"\\\\]*\"$' AND char_length(answer_value) > 2) OR trim(answer_value) = '' "
              "OR answer_value = '\"\"'"),
    DataCheck("084_fix_knockout_provenance_not_found.sql", "no 'not_found' suggestion carries an answer, evidence or confidence",
              "SELECT count(*) FROM cv_analyzer.application_knockout_answer_suggestions WHERE suggested_source = 'not_found' "
              "AND (suggested_answer IS NOT NULL OR evidence_text IS NOT NULL OR confidence > 0 "
              "OR verification_status != 'not_found')"),
    DataCheck("085_fix_knockout_suggestions_invalid_answers.sql", "no pending 'not_found' suggestion keeps an answer",
              "SELECT count(*) FROM cv_analyzer.application_knockout_answer_suggestions WHERE status = 'pending' AND "
              "((verification_status = 'not_found' AND suggested_answer IS NOT NULL) OR "
              "(confidence = 0 AND (evidence_text IS NULL OR trim(evidence_text) = '') AND suggested_answer IS NOT NULL))"),
    DataCheck("086_fix_knockout_answer_method_not_found.sql", "every 'not_found' suggestion has the 'not_found' answer method",
              "SELECT count(*) FROM cv_analyzer.application_knockout_answer_suggestions WHERE suggested_source = 'not_found' "
              "AND verification_status = 'not_found' AND answer_method IS DISTINCT FROM 'not_found'"),
    DataCheck("089_recruiter_approved_method.sql", "no accepted AI suggestion is still recorded as an unreviewed inference",
              "SELECT count(*) FROM cv_analyzer.application_knockout_answers a JOIN "
              "cv_analyzer.application_knockout_answer_suggestions s ON s.application_id = a.application_id "
              "AND s.question_id = a.question_id WHERE s.status = 'accepted' AND a.answer_source IN ('ai_cv', 'ai_email', 'ai_cv_email') "
              "AND a.answer_method = 'ai_inference' AND a.answer_value = s.suggested_answer"),
    DataCheck("091_fix_null_workflow_status_ai_scored.sql", "no scored application lacks a workflow status",
              "SELECT count(*) FROM cv_analyzer.applications WHERE processing_status = 'ai_scored' AND workflow_status IS NULL"),
    DataCheck("092_repair_legacy_new_workflow_status.sql", "no scored application is still in the retired 'new' workflow status",
              "SELECT count(*) FROM cv_analyzer.applications WHERE processing_status = 'ai_scored' AND workflow_status = 'new'"),
)
DATA_CHECK_FILES = {c.file: c for c in DATA_CHECKS}

# Files whose only effect cannot be proven from the catalogue or from a stable row predicate. They are recorded as 'attested' in the
# evidence table with this reason, never as proven, and the runbook lists them for the operator's sign-off.
ATTESTED = {
    "005_job_code_alias.sql": "back-fills jobs.platform_email once; later rows are written by the application, so no row predicate is stable",
}
# Files that change nothing (diagnostic queries only, or objects schema.sql already creates).
NO_EFFECT = {
    "058_fix_workflow_pollution.sql": "read-only diagnostic queries",
    "004_job_fields.sql": "ADD COLUMN IF NOT EXISTS on columns schema.sql already creates; the columns are in the structural proof",
    "006_async_criteria.sql": "ADD COLUMN IF NOT EXISTS on columns schema.sql already creates; the columns are in the structural proof",
    "010_platform_control.sql": "superseded: never executed",
    "070_sla_thresholds_config.sql": "superseded: never executed",
    "087_autosave_deterministic_answers.sql": "superseded: never executed",
}


# --- reconciliation ---------------------------------------------------------------------------------------------------------------
# Where legacy and main disagree. Each step is a statement set, the identities it may change, and why. The conversion applies them in
# one transaction and then requires that the catalogue changed in exactly these identities and no others.
@dataclass(frozen=True)
class Reconciliation:
    name: str
    reason: str
    sql: str
    changes: tuple[str, ...]            # identities (fingerprint keys) this step may add, change or remove


RECONCILIATIONS: tuple[Reconciliation, ...] = (
    Reconciliation(
        name="submission_source_union",
        reason=("Main migration 094 replaces applications_submission_source_check with a set that has 'candidate_reuse' and no "
                "'bulk_excel_upload', and removes the column default. Legacy 099 allowed 'bulk_excel_upload'. Running 094 as written "
                "fails when bulk rows exist, and would stop the database from holding them. The conversion instead installs the "
                "union of both sets, so existing bulk applications stay valid and copy-to-job works, and drops the default as 094 "
                "does. The effect 094 requires is then proved, not assumed."),
        sql="""
            ALTER TABLE cv_analyzer.applications DROP CONSTRAINT IF EXISTS applications_submission_source_check;
            ALTER TABLE cv_analyzer.applications ADD CONSTRAINT applications_submission_source_check
                CHECK (submission_source IN ('manual_upload', 'email_forwarding', 'platform_email', 'public_apply',
                                             'candidate_reuse', 'bulk_excel_upload'));
            ALTER TABLE cv_analyzer.applications ALTER COLUMN submission_source DROP DEFAULT;
        """,
        changes=("constraint|applications.applications_submission_source_check", "column|applications.submission_source"),
    ),
    Reconciliation(
        name="tenant_features_admin_policy",
        reason=("Legacy 102 gave tenant_features a policy that reads the forgeable app.current_role in the form `= ANY (ARRAY[...])`. "
                "Main migration 100 rewrites only the `= 'super_admin'` form and stops with an error on any other. The conversion "
                "replaces this policy with the identity-based form main uses everywhere: access for the cv_admin session role."),
        sql="""
            DROP POLICY IF EXISTS tenant_features_admin_bypass ON cv_analyzer.tenant_features;
            CREATE POLICY tenant_features_admin_bypass ON cv_analyzer.tenant_features FOR ALL TO PUBLIC
                USING (current_user = 'cv_admin') WITH CHECK (current_user = 'cv_admin');
        """,
        changes=("policy|tenant_features.tenant_features_admin_bypass",),
    ),
    Reconciliation(
        name="tenant_feature_audit_rls",
        reason=("Legacy 103 created tenant_feature_audit with a tenant_id column and no row-level security. Every other tenant table in "
                "this release has it, and the preflight requires it on tables that hold tenant rows. It is enabled with the same "
                "policy shape as main migration 100 uses. No runtime role has a privilege on the table, so this changes no access."),
        sql="""
            ALTER TABLE cv_analyzer.tenant_feature_audit ENABLE ROW LEVEL SECURITY;
            CREATE POLICY tenant_scope_tenant_feature_audit ON cv_analyzer.tenant_feature_audit FOR ALL TO PUBLIC
                USING (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true))
                WITH CHECK (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true));
        """,
        changes=("rls|tenant_feature_audit", "policy|tenant_feature_audit.tenant_scope_tenant_feature_audit"),
    ),
)

# Legacy-only tables: they exist only in databases upgraded from this lineage. Main's code does not reference them, so no runtime role
# is granted anything on them; the data is kept, sealed, and owned by the migrator. The list lives in db/runtime_grants.py, next to the
# privilege matrix, because the preflight and the grant generator read it from there.
from db.runtime_grants import LEGACY_PRESERVED_TABLES  # noqa: E402,F401


@dataclass
class Assessment:
    findings: list[tuple[str, str, str]] = field(default_factory=list)       # (level, name, detail)
    repairs: list[str] = field(default_factory=list)                           # repair files the live catalogue needs
    data: dict[str, int] = field(default_factory=dict)                         # data-check file -> violating rows
    verified_keys: dict[str, int] = field(default_factory=dict)               # file -> identities proved present
    accepted: list[str] = field(default_factory=list)                          # seed rows the operator accepted as absent
    live: dict[str, str] = field(default_factory=dict)

    def add(self, level: str, name: str, detail: str = "") -> None:
        self.findings.append((level, name, detail))

    @property
    def blocking(self) -> bool:
        return any(level == "FAIL" for level, _, _ in self.findings)


def assess_structure(live: dict[str, str], out: Assessment, *, accepted_seeds: frozenset[str] = frozenset()) -> None:
    """Compare the live catalogue with the base chain plus the legacy files. Missing or different objects whose last setter is a repair
    file are repairable; anything else, and every unknown object, is blocking. Seeded rows added by an operator are not unknown."""
    missing, different, extra = F.diff(EXPECTED_LEGACY, live)
    repairable: dict[str, list[str]] = {}
    blocking: list[str] = []
    for key in missing + different:
        setter = SETTER_LEGACY[key]
        if key in accepted_seeds and key.startswith("seed|"):
            out.add("ACK", "seed row", f"{key} is absent; accepted by the operator (it is configuration an operator may delete)")
            out.accepted.append(key)
        elif setter in AUTO_REPAIRS:
            repairable.setdefault(setter, []).append(key)
        else:
            state = "missing" if key in missing else "different"
            note = " - its effect was never applied here and is not repaired automatically" if setter in MUST_BE_PRESENT else ""
            blocking.append(f"{state} {key} (set by {setter}){note}")
    unknown = [k for k in extra if not k.startswith("seed|")]
    for key in blocking[:40]:
        out.add("FAIL", "expected object", key)
    if len(blocking) > 40:
        out.add("FAIL", "expected object", f"... and {len(blocking) - 40} more")
    for key in unknown[:40]:
        out.add("FAIL", "unknown object", f"{key} is not part of the legacy lineage")
    if len(unknown) > 40:
        out.add("FAIL", "unknown object", f"... and {len(unknown) - 40} more")
    for file, keys in repairable.items():
        out.repairs.append(file)
        out.add("REPAIR", file, f"{len(keys)} object(s) absent or different; this file will be run to create or validate them")
    out.repairs.sort(key=[e["file"] for e in base_files()].index)
    verified: dict[str, int] = {}
    for item in base_files() + legacy_files():
        keys = [k for k in item["set"] if k in EXPECTED_LEGACY and SETTER_LEGACY.get(k) == item["file"] and k in live
                and live[k] == EXPECTED_LEGACY[k]]
        verified[item["file"]] = len(keys)
    out.verified_keys = verified
    seeds_extra = sum(1 for k in extra if k.startswith("seed|"))
    out.add("PASS" if not blocking and not unknown else "FAIL", "structure",
            f"{len(EXPECTED_LEGACY)} identities expected; {len(missing)} missing, {len(different)} different, "
            f"{len(unknown)} unknown; {seeds_extra} operator-added seed row(s) ignored")


def assess_data(cur, out: Assessment, *, acknowledged: frozenset[str] = frozenset()) -> None:
    for check in DATA_CHECKS:
        try:
            cur.execute(check.sql)
            n = cur.fetchone()[0]
        except Exception as exc:        # noqa: BLE001 - a column or table the structure check also reports
            out.add("FAIL", f"data check {check.file}", f"could not run: {str(exc).splitlines()[0]}")
            cur.connection.rollback() if not cur.connection.autocommit else None
            continue
        out.data[check.file] = n
        if n == 0:
            out.add("PASS", f"data check {check.file}", check.description)
        elif check.file in acknowledged:
            out.add("ACK", f"data check {check.file}", f"{n} row(s) still show the problem; acknowledged by the operator")
        else:
            out.add("FAIL", f"data check {check.file}", f"{n} row(s) still show the problem: {check.description}")
    for file, reason in ATTESTED.items():
        out.add("ATTEST", file, reason)


def known_relations() -> set[str]:
    return {k.split("|", 1)[1] for k in EXPECTED_LEGACY if k.startswith("relation|")}
