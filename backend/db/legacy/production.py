"""The real production database, as restored from the backup, against the legacy reference.

The reference (lineage.py) is what a clean run of the legacy migrations produces. The production database differs from it in 86
identities (13 missing, 50 different, 23 not in the reference). production_lineage.json classifies every one of them, individually,
with the exact catalogue text found on production and the root cause. Each has one disposition:

  equivalent       the same behaviour printed differently (constraints rebuilt by ALTER COLUMN TYPE, a function body's whitespace);
                   accepted only when the live text equals the recorded text exactly
  extended         the production constraint allows a superset of the reference's values; accepted as recorded
  wider            a column wider than the reference's; accepted as recorded
  production_only  an object the reference does not have (early-schema columns, duplicate indexes); preserved, never dropped
  substituted      an expected index that production has under another name; accepted while that index exists as recorded
  reconcile        a missing or weaker object that main needs; created or tightened by the named step in one transaction, after its
                   prechecks (orphans, duplicates, nulls) are zero. No row is changed or deleted to make a precheck pass.
  repair           created by the repair migration 096 (lineage.AUTO_REPAIRS)

Nothing is matched by pattern at run time: a live definition that is neither the reference's nor the recorded one is unexplained and
stops the conversion. Tests (tests/test_legacy_production_variations.py) prove the file equals the diff of the restored schema and
that every `equivalent` / `extended` entry means what it claims."""
from __future__ import annotations

import json
import pathlib
import re
from dataclasses import dataclass

HERE = pathlib.Path(__file__).resolve().parent
LINEAGE = json.loads((HERE / "production_lineage.json").read_text(encoding="utf-8"))
CAUSES: dict[str, dict] = LINEAGE["causes"]
IDENTITIES: dict[str, dict] = LINEAGE["identities"]

PRESERVED = ("equivalent", "extended", "wider", "production_only")
DISPOSITIONS = PRESERVED + ("substituted", "reconcile", "repair")

# Functions outside cv_analyzer that a cv_analyzer trigger may call, and the function in cv_analyzer whose body they must equal.
EXTERNAL_FUNCTIONS = {"public.set_updated_at()": "cv_analyzer.set_updated_at()"}


@dataclass(frozen=True)
class Precheck:
    name: str
    sql: str                  # one integer: rows that would make the step fail. Must be zero; the tool changes none of them.
    advice: str
    after_corrections: bool = False     # the declared data corrections may bring this to zero; only prepare can decide


@dataclass(frozen=True)
class Step:
    name: str
    sql: str
    prechecks: tuple[Precheck, ...] = ()

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(k for k, e in IDENTITIES.items() if e.get("step") == self.name)


VALID_WORKFLOW_STATUSES = ("awaiting_review", "under_review", "shortlisted", "interviewing", "offer_made", "hired", "rejected",
                           "withdrawn", "on_hold")
_WORKFLOW_LIST = ", ".join(f"'{v}'" for v in VALID_WORKFLOW_STATUSES)

STEPS: tuple[Step, ...] = (
    Step(
        name="email_ingest_log_operational_columns",
        sql="""
            ALTER TABLE cv_analyzer.email_ingest_log
                ADD COLUMN processing_duration_ms integer,
                ADD COLUMN notification_queued_at timestamptz;
            CREATE INDEX idx_email_ingest_log_stuck ON cv_analyzer.email_ingest_log (processing_started_at)
                WHERE processed_at IS NULL AND processing_started_at IS NOT NULL;
            COMMENT ON COLUMN cv_analyzer.email_ingest_log.processing_duration_ms IS 'Total intake duration in milliseconds (processed_at - processing_started_at)';
            COMMENT ON COLUMN cv_analyzer.email_ingest_log.notification_queued_at IS 'Timestamp when queue_candidate_notification completed';
        """),
    Step(
        name="knockout_answers_integrity",
        prechecks=(
            Precheck("answers without created_at", "SELECT count(*) FROM cv_analyzer.application_knockout_answers WHERE created_at IS NULL",
                     "set created_at on these rows, then repeat; the tool does not invent timestamps"),
            Precheck("answers without is_disqualifying",
                     "SELECT count(*) FROM cv_analyzer.application_knockout_answers WHERE is_disqualifying IS NULL",
                     "decide the value of the flag for these rows, then repeat"),
            Precheck("answers whose application does not exist",
                     "SELECT count(*) FROM cv_analyzer.application_knockout_answers a WHERE NOT EXISTS "
                     "(SELECT 1 FROM cv_analyzer.applications p WHERE p.application_id = a.application_id)",
                     "orphaned answers: decide whether to delete them, then repeat"),
            Precheck("answers whose question does not exist",
                     "SELECT count(*) FROM cv_analyzer.application_knockout_answers a WHERE NOT EXISTS "
                     "(SELECT 1 FROM cv_analyzer.job_knockout_questions q WHERE q.question_id = a.question_id)",
                     "orphaned answers: decide whether to delete them, then repeat"),
            Precheck("answers whose tenant does not exist",
                     "SELECT count(*) FROM cv_analyzer.application_knockout_answers a WHERE a.tenant_id IS NOT NULL AND NOT EXISTS "
                     "(SELECT 1 FROM cv_analyzer.tenants t WHERE t.tenant_id = a.tenant_id)",
                     "answers pointing at a missing tenant: decide, then repeat"),
            Precheck("duplicate answers for one application and question",
                     "SELECT coalesce(sum(n - 1), 0)::bigint FROM (SELECT count(*) AS n FROM cv_analyzer.application_knockout_answers "
                     "GROUP BY application_id, question_id HAVING count(*) > 1) d",
                     "choose which duplicate to keep, then repeat; the tool does not pick one"),
        ),
        sql="""
            ALTER TABLE cv_analyzer.application_knockout_answers
                ALTER COLUMN created_at SET NOT NULL,
                ALTER COLUMN is_disqualifying SET NOT NULL,
                ADD CONSTRAINT application_knockout_answers_application_id_fkey FOREIGN KEY (application_id)
                    REFERENCES cv_analyzer.applications(application_id) ON DELETE CASCADE,
                ADD CONSTRAINT application_knockout_answers_question_id_fkey FOREIGN KEY (question_id)
                    REFERENCES cv_analyzer.job_knockout_questions(question_id) ON DELETE CASCADE,
                ADD CONSTRAINT application_knockout_answers_tenant_id_fkey FOREIGN KEY (tenant_id)
                    REFERENCES cv_analyzer.tenants(tenant_id),
                ADD CONSTRAINT application_knockout_answers_application_id_question_id_key UNIQUE (application_id, question_id);
        """),
    Step(
        name="knockout_questions_integrity",
        prechecks=(
            Precheck("questions without created_at", "SELECT count(*) FROM cv_analyzer.job_knockout_questions WHERE created_at IS NULL",
                     "set created_at on these rows, then repeat"),
            Precheck("questions without is_required", "SELECT count(*) FROM cv_analyzer.job_knockout_questions WHERE is_required IS NULL",
                     "decide the value of the flag for these rows, then repeat"),
            Precheck("questions whose job does not exist",
                     "SELECT count(*) FROM cv_analyzer.job_knockout_questions q WHERE NOT EXISTS "
                     "(SELECT 1 FROM cv_analyzer.jobs j WHERE j.job_id = q.job_id)",
                     "orphaned questions: decide whether to delete them, then repeat"),
        ),
        sql="""
            ALTER TABLE cv_analyzer.job_knockout_questions
                ALTER COLUMN created_at SET NOT NULL,
                ALTER COLUMN is_required SET NOT NULL,
                ALTER COLUMN question_type SET DEFAULT 'yes_no',
                ADD CONSTRAINT job_knockout_questions_job_id_fkey FOREIGN KEY (job_id)
                    REFERENCES cv_analyzer.jobs(job_id) ON DELETE CASCADE;
            CREATE INDEX idx_knockout_questions_job ON cv_analyzer.job_knockout_questions (job_id, display_order);
        """),
    Step(
        name="workflow_status_model",
        prechecks=(
            Precheck("applications with a workflow status outside the valid set",
                     f"SELECT count(*) FROM cv_analyzer.applications WHERE workflow_status IS NOT NULL "
                     f"AND workflow_status NOT IN ({_WORKFLOW_LIST})",
                     "these rows hold the retired 'new' (or another stray) status; the declared data corrections 077, 092 and 057 "
                     "address 'new'; repeat after they are applied. Anything else is an operator decision.",
                     after_corrections=True),
        ),
        sql=f"""
            ALTER TABLE cv_analyzer.applications ALTER COLUMN workflow_status SET DEFAULT 'awaiting_review';
            ALTER TABLE cv_analyzer.applications ADD CONSTRAINT applications_workflow_status_check
                CHECK (workflow_status IN ({_WORKFLOW_LIST}));
            DROP INDEX cv_analyzer.idx_applications_workflow_status;
            CREATE INDEX idx_applications_workflow_status ON cv_analyzer.applications (job_id, workflow_status);
        """),
)
STEP_BY_NAME = {s.name: s for s in STEPS}


# --- data corrections -------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Correction:
    file: str
    sql: str | None          # None: the body of the migration file itself is executed (migration_body)
    note: str


CORRECTIONS: dict[str, Correction] = {c.file: c for c in (
    Correction("077_clean_stopped_workflow_pollution.sql", None,
               "sets workflow_status to NULL for failed applications that carry a stopped_reason"),
    Correction("081_fix_knockout_answer_value_encoding.sql", None,
               "strips JSON quoting from knockout answers and DELETES answers that are empty strings"),
    Correction("092_repair_legacy_new_workflow_status.sql", None,
               "sets workflow_status to 'awaiting_review' for scored applications that hold 'new' or NULL"),
    # Migration 057 ends with `UPDATE ... SET workflow_status = 'awaiting_review' WHERE workflow_status = 'new'`. On production 057's
    # early revision left that statement unrun. It is restricted here to the rows 077 and 092 do not own, so it cannot undo 077.
    Correction("057_application_workflow.sql",
               "UPDATE cv_analyzer.applications SET workflow_status = 'awaiting_review' WHERE workflow_status = 'new' "
               "AND processing_status IS DISTINCT FROM 'ai_scored' "
               "AND NOT (processing_status = 'failed' AND stopped_reason IS NOT NULL)",
               "sets workflow_status to 'awaiting_review' for any other application still holding the retired 'new'"),
)}
# Corrections run in this order (077 before 057, because the 057 statement leaves 077's rows alone only when they are already NULL).
CORRECTION_ORDER = ("077_clean_stopped_workflow_pollution.sql", "081_fix_knockout_answer_value_encoding.sql",
                    "092_repair_legacy_new_workflow_status.sql", "057_application_workflow.sql")


# --- classification ---------------------------------------------------------------------------------------------------------------
@dataclass
class Classified:
    accepted: list[dict]
    needed: dict[str, list[str]]           # step -> keys in the production state
    repairs: list[str]                     # keys left to the repair files
    unexplained: list[str]


def classify(live: dict[str, str], keys: list[str]) -> Classified:
    """`keys`: the non-seed identities that differ from the reference (missing, different or unknown). Each must be in the recorded
    production state, exactly; a reconcile step must have all of its identities in that state or none."""
    out = Classified([], {}, [], [])
    present = set(keys)
    for key in keys:
        entry = IDENTITIES.get(key)
        if entry is None:
            out.unexplained.append(f"{key} is not in the legacy reference or the recorded production state")
            continue
        recorded = entry["live"]
        if live.get(key) != recorded:
            out.unexplained.append(f"{key} is neither as the legacy reference defines it nor as recorded for production")
            continue
        kind = entry["disposition"]
        if kind in PRESERVED:
            out.accepted.append({"key": key, "disposition": kind, "cause": entry["cause"]})
        elif kind == "substituted":
            other = entry["substitute"]
            if live.get(other) != IDENTITIES[other]["live"]:
                out.unexplained.append(f"{key} is absent and its substitute {other} is not as recorded")
            else:
                out.accepted.append({"key": key, "disposition": kind, "cause": entry["cause"], "substitute": other})
        elif kind == "reconcile":
            out.needed.setdefault(entry["step"], []).append(key)
        elif kind == "repair":
            out.repairs.append(key)
    for step, members in out.needed.items():
        others = [k for k in STEP_BY_NAME[step].keys if k not in present]
        if others:
            out.unexplained.append(f"step {step}: {len(members)} identities are in the production state but {others} already match "
                                   f"the reference - a mixed state this procedure does not explain")
    return out


def allowed_final(key: str) -> set[str | None]:
    """The live text each identity may have after the conversion. None means absent."""
    entry = IDENTITIES.get(key)
    if entry is None:
        return set()
    kind = entry["disposition"]
    if kind in PRESERVED:
        return {entry["live"]}
    if kind == "substituted":
        return {None}
    return set()          # reconcile and repair end in the reference's definition, or `after` below


def after_state(key: str) -> str | None:
    """For a reconciled identity whose end state is not the reference's, the recorded end state."""
    return (IDENTITIES.get(key) or {}).get("after")


def final_ok(key: str, expected: str | None, live: str | None) -> bool:
    entry = IDENTITIES.get(key)
    if live == expected:
        return True
    if entry is None:
        return False
    if entry["disposition"] in PRESERVED + ("substituted",):
        return live in allowed_final(key)
    if entry["disposition"] == "reconcile" and entry.get("after") is not None:
        return live == entry["after"]
    return False


def normalise_body(source: str) -> str:
    return re.sub(r"\s+", " ", source.strip().lower())


def external_dependencies(cur, runtime_roles: tuple[str, ...]) -> list[tuple[str, str]]:
    """Triggers in cv_analyzer that call a function outside cv_analyzer, as (level, message). Each must be a declared external
    function whose body equals the cv_analyzer function it stands for, owned by a role that is not a runtime role."""
    cur.execute("""
        SELECT t.tgname, c.relname, pn.nspname, p.proname, pg_get_function_identity_arguments(p.oid), pg_get_userbyid(p.proowner),
               p.prosrc, p.lanname, p.prosecdef, p.proconfig IS NOT NULL, p.prorettype::regtype::text
        FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN (SELECT p.*, l.lanname FROM pg_proc p JOIN pg_language l ON l.oid = p.prolang) p ON p.oid = t.tgfoid
        JOIN pg_namespace pn ON pn.oid = p.pronamespace
        WHERE n.nspname = 'cv_analyzer' AND NOT t.tgisinternal AND pn.nspname <> 'cv_analyzer'""")
    found = cur.fetchall()
    results: list[tuple[str, str]] = []
    for trigger, table, schema, name, args, owner, source, language, secdef, has_config, returns in found:
        signature = f"{schema}.{name}({args})"
        target = EXTERNAL_FUNCTIONS.get(signature)
        if target is None:
            results.append(("FAIL", f"trigger {table}.{trigger} calls {signature}, which is not a declared external dependency"))
            continue
        tschema, tname = target.split("(")[0].split(".")
        cur.execute("SELECT p.prosrc FROM pg_proc p WHERE p.pronamespace = %s::regnamespace AND p.proname = %s", (tschema, tname))
        row = cur.fetchone()
        if row is None:
            results.append(("FAIL", f"{target}, which {signature} must equal, does not exist"))
        elif normalise_body(row[0]) != normalise_body(source):
            results.append(("FAIL", f"{signature} has a body that differs from {target}"))
        elif language != "plpgsql" or secdef or has_config or returns != "trigger":
            results.append(("FAIL", f"{signature} is not a plain plpgsql trigger function (security definer, settings or other language)"))
        elif owner in runtime_roles:
            results.append(("FAIL", f"{signature} is owned by runtime role {owner}; transfer it before converting"))
        else:
            results.append(("PASS", f"trigger {table}.{trigger} calls {signature}: body equals {target}, owner {owner}, "
                                    "not security definer"))
    return results
