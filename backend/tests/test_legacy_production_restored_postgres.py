"""The restored production database (tests/fixtures/restored_production/schema.sql), converted for the current release, end to end.

The schema is the real one from the restored backup; the rows are generated with the counts the real report showed (86 stopped
applications that carry a workflow status, 524 scored applications on the retired 'new', two knockout answers needing re-encoding) plus
ordinary rows. Seeded configuration rows, which a schema-only dump does not hold, are copied from a database built from the legacy files.

Every refusal below is proved to change nothing: the catalogue and every row are identical before and after.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import time
import urllib.error
import uuid

import psycopg2
import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import legacy_production_helper as LP  # noqa: E402
import realdb_helper as H  # noqa: E402
from auth.password import hash_password  # noqa: E402
from db.legacy import fingerprint as F  # noqa: E402
from db.legacy import lineage as L  # noqa: E402
from db.legacy import production as PR  # noqa: E402
from db.legacy import upgrade as U  # noqa: E402
from db.runner import runner  # noqa: E402
from scripts import db_preflight as P  # noqa: E402
from scripts import legacy_upgrade as CLI  # noqa: E402
from test_legacy_production_upgrade_postgres import _connect, _free_port, _http, _runtime_env, snapshot_rows  # noqa: E402

pytestmark = pytest.mark.skipif(H.unavailable_reason() is not None, reason=H.unavailable_reason() or "")

PASSWORD = "Pw-" + uuid.uuid4().hex
F077, F081, F092, F057 = ("077_clean_stopped_workflow_pollution.sql", "081_fix_knockout_answer_value_encoding.sql",
                          "092_repair_legacy_new_workflow_status.sql", "057_application_workflow.sql")
HASH = hash_password(PASSWORD)


@pytest.fixture(scope="module")
def cluster():
    c = H.Cluster()
    try:
        yield c
    finally:
        c.stop()


@pytest.fixture(scope="module")
def seed_source(cluster):
    database = LP.build_legacy(cluster, mode="production")
    try:
        yield database
    finally:
        database.close()


@pytest.fixture()
def prod(cluster, seed_source):
    database = LP.restored_production(cluster)
    LP.add_seed_rows(database, seed_source)
    database.ids = LP.seed_restored(database, password_hash=HASH)
    try:
        yield database
    finally:
        database.close()


def catalogue(database):
    conn = database.bootstrap()
    try:
        return F.snapshot(conn.cursor())
    finally:
        conn.close()


def rows(database):
    return snapshot_rows(database)


def prepare(database, **kwargs):
    if not getattr(database, "migration_roles", False):
        H.provision_migration_roles(database)
        database.migration_roles = True
    conn = database.bootstrap()
    try:
        return U.prepare(conn, **kwargs)
    finally:
        conn.close()


def assert_refused_and_unchanged(database, expect: str, **kwargs):
    """prepare stops with a message containing `expect`, and the catalogue and every row are exactly what they were."""
    before_catalogue, before_rows = catalogue(database), rows(database)
    with pytest.raises(U.ConversionError) as raised:
        prepare(database, **kwargs)
    assert expect in str(raised.value), str(raised.value)[:800]
    assert catalogue(database) == before_catalogue
    assert rows(database) == before_rows


def corrections(*files):
    return {f: LP.REAL_COUNTS[f] for f in files}


ALL = corrections(F077, F081, F092)


# ---------------------------------------------------------------------------------------------------------------------------------
# 1. The report
# ---------------------------------------------------------------------------------------------------------------------------------

def test_the_report_gives_the_counts_before_anything_is_changed_and_blocks_until_they_are_confirmed(prod):
    before_catalogue, before_rows = catalogue(prod), rows(prod)
    conn = prod.bootstrap()
    a = U.assess(conn)
    assert a.blocking
    assert a.data[F077] == 86 and a.data[F081] == 2 and a.data[F092] == 524 and a.data[F057] == 0
    assert a.breakdown[F092] == [["manual_upload", 524]]
    assert sorted(x[0] for x in a.breakdown[F077]) == ["duplicate_blocked / new", "extraction_failed / new", "processing_error / new",
                                                      "security_blocked / new"]
    assert sum(x[1] for x in a.breakdown[F077]) == 86
    failures = {name: detail for level, name, detail in a.findings if level == "FAIL"}
    assert set(failures) == {f"data check {F077}", f"data check {F081}", f"data check {F092}"}
    assert f"--apply-data-correction {F092}=524" in failures[f"data check {F092}"]
    # structure: nothing unexplained, every difference is a recorded production variation
    assert not [f for f in a.findings if f[1] in ("expected object", "unknown object", "production state")]
    assert len(a.production) == 86 and a.steps == [s.name for s in PR.STEPS]
    conn.close()
    assert catalogue(prod) == before_catalogue and rows(prod) == before_rows


def test_the_complete_report_classifies_every_identity_and_holds_no_row_content(prod, capsys, monkeypatch):
    monkeypatch.setenv("LEGACY_UPGRADE_DATABASE_URL", f"postgresql://postgres@127.0.0.1:{prod.cluster.port}/{prod.name}")
    assert CLI.main(["check", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    structure = report["structure"]
    assert (len(structure["missing"]) - len(structure["missing_seed_rows"]), len(structure["different"]), len(structure["unknown"])) == \
        (13, 50, 23)
    for part in ("missing", "different", "unknown"):
        for entry in structure[part]:
            assert entry["classified_as"] in PR.DISPOSITIONS and entry["cause"] in PR.CAUSES
    assert report["reconcile_steps"] == [s.name for s in PR.STEPS]
    assert len(report["production_variations"]) == 86
    assert report["prechecks"]["knockout_answers_integrity: duplicate answers for one application and question"] == 0
    text = json.dumps(report)
    for personal in ("admin@acme.example", "Do you hold a licence", "Can you relocate", "scored-new-", "stopped-"):
        assert personal not in text
    assert CLI.main(["check", "--apply-data-correction", f"{F092}=524"]) == 1       # the other two are still unconfirmed
    out = capsys.readouterr().out
    assert "CORRECT" in out and f"data check {F092}" in out


# ---------------------------------------------------------------------------------------------------------------------------------
# 2. The conversion
# ---------------------------------------------------------------------------------------------------------------------------------

def _app_rows(database):
    conn = database.bootstrap()
    cur = conn.cursor()
    cur.execute("SELECT application_id::text, to_jsonb(a) FROM cv_analyzer.applications a")
    out = {k: v for k, v in cur.fetchall()}
    conn.close()
    return out


def test_the_declared_corrections_change_only_the_rows_they_report_and_the_state_is_proved(prod):
    before = _app_rows(prod)
    answers_before = {r["answer_id"]: r for r in rows(prod)["application_knockout_answers"]}
    result = prepare(prod, corrections=ALL)
    assert result.pending == U.EXPECTED_PENDING_AFTER_PREPARE
    after = _app_rows(prod)
    assert set(after) == set(before)                                           # no application was deleted or added
    changed = {k for k in before if before[k] != after[k]}
    assert len(changed) == 86 + 524
    for key in changed:
        old, new = before[key], after[key]
        differing = {c for c in old if old[c] != new.get(c)}
        if old["processing_status"] == "failed":
            assert new["workflow_status"] is None and old["workflow_status"] == "new" and differing <= {"workflow_status", "updated_at"}
        else:
            assert new["workflow_status"] == "awaiting_review" and old["workflow_status"] == "new"
            assert differing <= {"workflow_status", "updated_at"}
    untouched = [k for k in before if k not in changed]
    assert len(untouched) == 12 + 5 + 4 and all(before[k] == after[k] for k in untouched)
    # 081: one answer rewritten ('"no"' -> 'no'), one empty answer deleted, the others identical
    answers_after = {r["answer_id"]: r for r in rows(prod)["application_knockout_answers"]}
    assert len(answers_before) - len(answers_after) == 1
    assert sorted(r["answer_value"] for r in answers_after.values()) == ["no", "no", "yes"]
    assert {k: v for k, v in answers_before.items() if v["answer_value"] in ("yes", "no")}.keys() <= answers_after.keys()
    # the evidence records what was reported, what was done and the state afterwards
    conn = prod.bootstrap()
    cur = conn.cursor()
    cur.execute("SELECT item, detail FROM cv_analyzer.schema_migration_legacy_evidence WHERE item_kind = 'data_correction' ORDER BY item")
    evidence = {item: detail for item, detail in cur.fetchall()}
    assert set(evidence) == set(ALL)
    assert [evidence[f]["rows_reported"] for f in sorted(ALL)] == [86, 2, 524]
    assert all(evidence[f]["rows_after"] == 0 for f in ALL)
    assert evidence[F081]["rows_deleted"]["application_knockout_answers"] == 1 and evidence[F077]["rows_deleted"] == \
        {"applications": 0, "application_knockout_answers": 0}
    cur.execute("SELECT item, detail->'identities' FROM cv_analyzer.schema_migration_legacy_evidence WHERE item_kind = 'production_variation'")
    recorded = {item: ids for item, ids in cur.fetchall()}
    assert set(recorded) == set(PR.CAUSES)
    assert sum(len(v) for v in recorded.values()) == 86
    cur.execute("SELECT proof FROM cv_analyzer.schema_migration_legacy_evidence WHERE item = %s AND item_kind = 'baselined_migration'", (F092,))
    assert cur.fetchone()[0] in ("data", "catalog+data")
    conn.close()


def test_the_converted_catalogue_equals_the_reference_apart_from_the_recorded_production_state(prod):
    prepare(prod, corrections=ALL)
    live = catalogue(prod)
    missing, different, extra = F.diff(L.EXPECTED_LEGACY, live)
    declared = {"constraint|job_criteria.weights_sum_100"}
    for step in L.RECONCILIATIONS:
        declared |= set(step.changes)
    kinds = {k: PR.IDENTITIES[k]["disposition"] for k in PR.IDENTITIES}
    # whatever still differs is exactly a preserved production variation (or main's own change); nothing reconcile-able remains
    for key in different:
        assert key in declared or kinds.get(key) in ("equivalent", "extended", "wider", "production_only") or \
            (kinds.get(key) == "reconcile" and live[key] == PR.IDENTITIES[key]["after"]), key
    assert [k for k in different if kinds.get(k) in ("reconcile", "repair")] == ["column|job_knockout_questions.question_type"]
    assert [k for k in missing if not k.startswith("seed|")] == ["index|idx_knockout_answers_application"]
    assert {k for k in extra if not k.startswith("seed|")} - declared == {k for k, d in kinds.items() if d == "production_only"
                                                                          and k not in L.EXPECTED_LEGACY}
    for key, entry in PR.IDENTITIES.items():
        if entry["disposition"] == "reconcile":
            assert live[key] == (entry.get("after") or L.EXPECTED_LEGACY[key]), key


def test_the_whole_procedure_passes_the_preflight_and_the_database_enforces_what_it_now_has(prod):
    ids = prod.ids
    prepare(prod, corrections=ALL)
    operator = _connect(prod, "cv_migration_operator", prod.runtime_passwords["cv_migration_operator"])
    try:
        report = runner.run(operator, owner_role="cv_migrator")
    finally:
        operator.close()
    assert report.applied == U.EXPECTED_PENDING_AFTER_PREPARE
    H.provision_runtime_logins(prod)
    conn = _connect(prod)
    conn.autocommit = False
    conn.set_session(readonly=True)
    checks = P.run(conn, phase="after")
    conn.rollback()
    conn.close()
    assert [c for c in checks if c.status != P.PASS] == []
    names = {c.name for c in checks}
    assert "legacy lineage: trigger function outside cv_analyzer" in names
    assert "legacy lineage: every recorded data correction ended at zero rows" in names

    boot = prod.bootstrap()
    boot.autocommit = False
    cur = boot.cursor()

    def refuses(sql, params, error):
        cur.execute("SAVEPOINT s")
        with pytest.raises(error):
            cur.execute(sql, params)
        cur.execute("ROLLBACK TO s")

    # the retired status is refused, valid ones are not
    refuses("UPDATE cv_analyzer.applications SET workflow_status = 'new' WHERE application_id = %s", (ids["app_ok"],),
            psycopg2.errors.CheckViolation)
    cur.execute("UPDATE cv_analyzer.applications SET workflow_status = 'on_hold' WHERE application_id = %s", (ids["app_ok"],))
    cur.execute("INSERT INTO cv_analyzer.applications (job_id, tenant_id, candidate_name, processing_status, submission_source) "
                "VALUES (%s, %s, 'new default', 'pending', 'manual_upload') RETURNING workflow_status", (ids["job_a"], ids["tenant_a"]))
    assert cur.fetchone()[0] == "awaiting_review"                                   # the default is the model's, not the retired one
    # integrity the production tables lacked
    refuses("INSERT INTO cv_analyzer.application_knockout_answers (application_id, question_id, answer_value, tenant_id) "
            "VALUES (gen_random_uuid(), %s, 'x', %s)", (ids["question"], ids["tenant_a"]), psycopg2.errors.ForeignKeyViolation)
    refuses("INSERT INTO cv_analyzer.application_knockout_answers (application_id, question_id, answer_value, tenant_id) "
            "VALUES (%s, gen_random_uuid(), 'x', %s)", (ids["app_ok"], ids["tenant_a"]), psycopg2.errors.ForeignKeyViolation)
    refuses("INSERT INTO cv_analyzer.application_knockout_answers (application_id, question_id, answer_value, tenant_id) "
            "VALUES (%s, %s, 'x', gen_random_uuid())", (ids["app_ok2"], ids["question"]), psycopg2.errors.ForeignKeyViolation)
    refuses("INSERT INTO cv_analyzer.application_knockout_answers (application_id, question_id, answer_value, tenant_id) "
            "VALUES (%s, %s, 'again', %s)", (ids["app_ok"], ids["question"], ids["tenant_a"]), psycopg2.errors.UniqueViolation)
    refuses("INSERT INTO cv_analyzer.job_knockout_questions (job_id, tenant_id, question_text, question_type) "
            "VALUES (gen_random_uuid(), %s, 'q', 'yes_no')", (ids["tenant_a"],), psycopg2.errors.ForeignKeyViolation)
    cur.execute("INSERT INTO cv_analyzer.job_knockout_questions (job_id, tenant_id, question_text) VALUES (%s, %s, 'default type') "
                "RETURNING question_type", (ids["job_a"], ids["tenant_a"]))
    assert cur.fetchone()[0] == "yes_no"
    refuses("UPDATE cv_analyzer.job_knockout_questions SET is_required = NULL", (), psycopg2.errors.NotNullViolation)
    # the columns migration 031 gives the intake worker exist and can be written
    cur.execute("INSERT INTO cv_analyzer.email_ingest_log (tenant_id, message_id, status, processing_duration_ms, notification_queued_at) "
                "VALUES (%s, 'm-1', 'received', 12, now())", (ids["tenant_a"],))
    # production-only columns keep their data and defaults
    cur.execute("SELECT intake_status, source_channel FROM cv_analyzer.applications WHERE application_id = %s", (ids["app_ok"],))
    assert cur.fetchone() == ("received", "manual_upload")
    boot.close()

    # the trigger that calls public.set_updated_at() still fires, as the worker login
    worker = _connect(prod, "cv_worker", prod.runtime_passwords["cv_worker"])
    wc = worker.cursor()
    wc.execute("SELECT set_config('app.current_tenant_id', %s, false)", (ids["tenant_a"],))
    wc.execute("UPDATE cv_analyzer.application_intake_log SET status = 'queued', updated_at = now() - interval '1 day' "
               "WHERE intake_log_id = %s RETURNING updated_at > now() - interval '1 hour'", (ids["intake_log"],))
    assert wc.fetchone() == (True,)
    worker.close()


def test_the_new_api_and_a_worker_path_run_on_the_converted_production_schema(prod, tmp_path):
    prepare(prod, corrections=ALL)
    operator = _connect(prod, "cv_migration_operator", prod.runtime_passwords["cv_migration_operator"])
    runner.run(operator, owner_role="cv_migrator")
    operator.close()
    H.provision_runtime_logins(prod)
    port = _free_port()
    log = (tmp_path / "api.log").open("w")
    process = subprocess.Popen(
        [sys.executable, str(REPO / "e2e" / "support" / "backend_app.py"), "--port", str(port), "--origin", "http://127.0.0.1:1",
         "--files-root", str(tmp_path)], env=_runtime_env(prod), cwd=REPO, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 120
        while True:
            assert process.poll() is None, (tmp_path / "api.log").read_text()[-2000:]
            try:
                if _http("GET", base + "/health")[0] == 200:
                    break
            except (urllib.error.URLError, ConnectionError):
                pass
            assert time.time() < deadline, "the API did not start"
            time.sleep(0.5)
        status, login = _http("POST", base + "/auth/login", body={"email": "admin@acme.example", "password": PASSWORD})
        assert status == 200, login
        token = login["token"]
        status, jobs = _http("GET", base + "/jobs", token)
        assert status == 200
        listing = jobs if isinstance(jobs, list) else jobs.get("jobs") or jobs.get("data") or []
        assert {j.get("job_code") for j in listing} == {"JOB-2026-0007"}                 # tenant scoping holds on production's schema
        status, created = _http("POST", base + "/jobs", token, {"title": "Created after the upgrade", "description": (
            "Senior Python developer. Responsibilities: build reporting APIs and PostgreSQL schemas for finance teams. "
            "Requirements: five years of backend experience, strong SQL, Python and Docker skills, a bachelor's degree.")})
        assert status == 201, created
        status, after = _http("GET", base + "/jobs", token)
        codes = sorted(j["job_code"] for j in (after if isinstance(after, list) else after.get("jobs") or after.get("data") or []))
        assert len(codes) == 2 and int(codes[-1].rsplit("-", 1)[1]) > 7
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()
    script = (
        "import asyncio\n"
        "from workers.watchdog import _reset_stuck_async\n"
        "from database import worker_engine, assert_runtime_role\n"
        "from sqlalchemy.ext.asyncio import AsyncSession\n"
        "async def go():\n"
        "    async with AsyncSession(worker_engine) as s:\n"
        "        await assert_runtime_role(s)\n"
        "    return await _reset_stuck_async()\n"
        "print('stuck=%d' % asyncio.run(go()))\n")
    done = subprocess.run([sys.executable, "-c", script], env=_runtime_env(prod), cwd=BACKEND, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stderr[-2000:]
    assert "stuck=0" in done.stdout


def test_the_intake_worker_statements_of_this_release_run_on_the_converted_email_ingest_log(prod):
    """The reason migration 031's two missing columns are created and not accepted: main's intake worker writes them."""
    import re
    source = (BACKEND / "workers" / "email_ingest.py").read_text(encoding="utf-8") if (BACKEND / "workers" / "email_ingest.py").exists() else ""
    sources = source + "".join(p.read_text(encoding="utf-8") for p in (BACKEND / "workers").glob("*.py"))
    assert re.search(r"processing_duration_ms", sources) and re.search(r"notification_queued_at", sources)
    live = catalogue(prod)
    assert "column|email_ingest_log.processing_duration_ms" not in live
    prepare(prod, corrections=ALL)
    live = catalogue(prod)
    assert live["column|email_ingest_log.processing_duration_ms"].startswith("integer")
    assert live["column|email_ingest_log.notification_queued_at"].startswith("timestamp with time zone")


def test_a_dry_run_changes_nothing_and_a_second_conversion_is_refused(prod):
    before_catalogue, before_rows = catalogue(prod), rows(prod)
    result = prepare(prod, corrections=ALL, dry_run=True)
    assert result.pending == U.EXPECTED_PENDING_AFTER_PREPARE and any(s.startswith("corrected") for s in result.steps)
    assert catalogue(prod) == before_catalogue and rows(prod) == before_rows
    boot = prod.bootstrap()
    cur = boot.cursor()
    cur.execute("SELECT to_regclass('cv_analyzer.schema_migrations') IS NULL, current_setting('search_path')")
    assert cur.fetchone()[0] is True
    boot.close()
    prepare(prod, corrections=ALL)
    with pytest.raises(U.ConversionError, match="already exists"):
        prepare(prod, corrections=ALL)


# ---------------------------------------------------------------------------------------------------------------------------------
# 3. Data corrections are explicit
# ---------------------------------------------------------------------------------------------------------------------------------

def test_without_a_confirmed_count_the_corrections_are_not_applied(prod):
    assert_refused_and_unchanged(prod, "the database is not in the exact legacy state")
    assert_refused_and_unchanged(prod, F092, corrections=corrections(F077, F081))              # 092 left unconfirmed: still blocks


def test_a_confirmed_count_that_is_not_the_live_count_refuses_and_changes_nothing(prod):
    wrong = dict(ALL)
    wrong[F092] = 523
    assert_refused_and_unchanged(prod, "the operator confirmed 523 row(s) but 524", corrections=wrong)
    wrong = dict(ALL)
    wrong[F077] = 0
    assert_refused_and_unchanged(prod, "the operator confirmed 0 row(s) but 86", corrections=wrong)


def test_only_the_declared_corrections_can_be_requested(prod):
    assert_refused_and_unchanged(prod, "no declared correction exists", corrections={**ALL, "059_rename_scored_to_ai_scored.sql": 0})


def test_a_correction_that_leaves_rows_behind_rolls_everything_back(prod, monkeypatch):
    monkeypatch.setitem(PR.CORRECTIONS, F092, PR.Correction(F092, "UPDATE cv_analyzer.applications SET updated_at = now() WHERE false", "no-op"))
    assert_refused_and_unchanged(prod, "still show the problem afterwards", corrections=ALL)


def test_a_correction_that_deletes_applications_is_refused(prod, monkeypatch):
    monkeypatch.setitem(PR.CORRECTIONS, F077, PR.Correction(
        F077, "DELETE FROM cv_analyzer.applications WHERE processing_status = 'failed' AND stopped_reason IS NOT NULL", "deletes"))
    assert_refused_and_unchanged(prod, "removed rows it is not declared to remove", corrections=ALL)


def test_the_057_backfill_is_a_separate_confirmed_correction_that_leaves_077s_rows_alone(prod):
    conn = prod.bootstrap()
    conn.cursor().execute("INSERT INTO cv_analyzer.applications (job_id, tenant_id, candidate_name, processing_status, workflow_status, "
                          "submission_source) SELECT job_id, tenant_id, 'pending-new-' || g, 'pending', 'new', 'manual_upload' "
                          "FROM cv_analyzer.applications, generate_series(1, 3) g WHERE candidate_name = 'scored-ok-1'")
    conn.close()
    a = U.assess(prod.bootstrap())
    assert a.data[F057] == 3 and a.breakdown[F057] == [["pending / stopped_reason -", 3]]
    assert_refused_and_unchanged(prod, f"data check {F057}: 3 row(s)", corrections=ALL)
    # acknowledging the rows instead of correcting them leaves them 'new': the workflow check cannot be added, the step stops with the
    # count, and nothing changes
    assert_refused_and_unchanged(prod, "applications with a workflow status outside the valid set: 3", corrections=ALL,
                                 acknowledged=frozenset({F057}))
    result = prepare(prod, corrections={**ALL, F057: 3})
    assert any(s.startswith(f"corrected {F057}") for s in result.steps)
    live = _app_rows(prod)
    assert {v["workflow_status"] for v in live.values() if v["candidate_name"].startswith("pending-new-")} == {"awaiting_review"}
    assert {v["workflow_status"] for v in live.values() if v["candidate_name"].startswith("stopped-")} <= {None}


# ---------------------------------------------------------------------------------------------------------------------------------
# 4. Prechecks stop, and change nothing
# ---------------------------------------------------------------------------------------------------------------------------------

def _boot_exec(database, *statements):
    conn = database.bootstrap()
    cur = conn.cursor()
    cur.execute("SET search_path = cv_analyzer, public")
    for statement in statements:
        cur.execute(statement)
    conn.close()


def test_an_answer_whose_application_is_gone_stops_the_conversion(cluster, seed_source):
    database = LP.restored_production(cluster)
    LP.add_seed_rows(database, seed_source)
    database.ids = LP.seed_restored(database, password_hash=HASH, orphan_answer=True)
    try:
        a = U.assess(database.bootstrap())
        assert a.prechecks["knockout_answers_integrity: answers whose application does not exist"] == 1
        assert any(l == "FAIL" and "answers whose application does not exist: 1" in d for l, _, d in a.findings)
        assert_refused_and_unchanged(database, "answers whose application does not exist: 1", corrections=ALL)
    finally:
        database.close()


@pytest.mark.parametrize("statement,expect", [
    ("UPDATE application_knockout_answers SET created_at = NULL WHERE answer_value = 'yes'", "answers without created_at: 1"),
    ("UPDATE application_knockout_answers SET is_disqualifying = NULL WHERE answer_value = 'yes'", "answers without is_disqualifying: 1"),
    ("INSERT INTO application_knockout_answers (application_id, question_id, answer_value, tenant_id) "
     "SELECT application_id, question2_id, 'x', gen_random_uuid() FROM (SELECT a.application_id, "
     "(SELECT question_id FROM job_knockout_questions LIMIT 1 OFFSET 1) AS question2_id FROM applications a "
     "WHERE candidate_name = 'scored-ok-3') s", "answers whose tenant does not exist: 1"),
    ("UPDATE job_knockout_questions SET created_at = NULL", "questions without created_at: 2"),
    ("UPDATE job_knockout_questions SET is_required = NULL WHERE question_text = 'Can you relocate?'", "questions without is_required: 1"),
])
def test_rows_that_would_break_a_new_constraint_stop_the_conversion_and_are_not_touched(prod, statement, expect):
    _boot_exec(prod, statement)
    assert_refused_and_unchanged(prod, expect, corrections=ALL)


def test_the_duplicate_precheck_counts_surplus_answers(prod):
    """Production already has a unique index on the pair, so duplicates cannot exist there; the precheck is still what protects the
    unique constraint, and is proved on a database where the index was removed."""
    _boot_exec(prod, "DROP INDEX idx_knockout_answers_app_question_unique",
               "INSERT INTO application_knockout_answers (application_id, question_id, answer_value, tenant_id) "
               "SELECT application_id, question_id, 'dup', tenant_id FROM application_knockout_answers WHERE answer_value = 'yes'")
    check = [c for c in PR.STEP_BY_NAME["knockout_answers_integrity"].prechecks if c.name.startswith("duplicate")][0]
    conn = prod.bootstrap()
    cur = conn.cursor()
    cur.execute(check.sql)
    assert cur.fetchone()[0] == 1
    conn.close()
    a = U.assess(prod.bootstrap())
    assert a.prechecks["knockout_answers_integrity: duplicate answers for one application and question"] == 1


def test_a_question_whose_job_is_gone_stops_the_conversion(prod):
    _boot_exec(prod,
               "INSERT INTO jobs (tenant_id, created_by, title, description, job_code) SELECT tenant_id, created_by, 't', 'd', 'JOB-2026-0999' "
               "FROM jobs WHERE job_code = 'JOB-2026-0007'",
               "INSERT INTO job_knockout_questions (job_id, tenant_id, question_text, question_type) SELECT job_id, tenant_id, 'q', 'yes_no' "
               "FROM jobs WHERE job_code = 'JOB-2026-0999'",
               "DELETE FROM job_criteria WHERE job_id IN (SELECT job_id FROM jobs WHERE job_code = 'JOB-2026-0999')",
               "DELETE FROM jobs WHERE job_code = 'JOB-2026-0999'")
    assert_refused_and_unchanged(prod, "questions whose job does not exist: 1", corrections=ALL)


# ---------------------------------------------------------------------------------------------------------------------------------
# 5. Anything not recorded is refused
# ---------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("statement,expect", [
    ("ALTER TABLE tenants ADD COLUMN surprise text", "column|tenants.surprise"),
    ("CREATE TABLE surprise_table (id int)", "relation|surprise_table"),
    ("ALTER TABLE applications ALTER COLUMN source_email TYPE varchar(300)", "column|applications.source_email"),
    ("ALTER TABLE applications DROP CONSTRAINT applications_source_channel_check, ADD CONSTRAINT applications_source_channel_check "
     "CHECK (source_channel IN ('manual_upload'))", "constraint|applications.applications_source_channel_check"),
    ("ALTER TABLE users DROP CONSTRAINT users_role_check, ADD CONSTRAINT users_role_check CHECK (role IN ('admin', 'viewer'))",
     "constraint|users.users_role_check"),
    ("ALTER TABLE job_criteria DROP CONSTRAINT job_criteria_criteria_extraction_status_check, ADD CONSTRAINT "
     "job_criteria_criteria_extraction_status_check CHECK (criteria_extraction_status IN ('pending', 'processing', 'completed', 'failed', 'insufficient', 'blocked', 'other'))",
     "constraint|job_criteria.job_criteria_criteria_extraction_status_check"),
    ("DROP INDEX idx_application_knockout_answers_app", "index|idx_knockout_answers_application"),
    ("ALTER TABLE email_ingest_log ADD COLUMN processing_duration_ms integer", "step email_ingest_log_operational_columns"),
    ("ALTER TABLE application_knockout_answers ADD CONSTRAINT application_knockout_answers_application_id_question_id_key "
     "UNIQUE (application_id, question_id)", "step knockout_answers_integrity"),
    ("CREATE INDEX idx_extra_on_apps ON applications (candidate_name)", "index|idx_extra_on_apps"),
    ("ALTER TABLE job_knockout_questions ALTER COLUMN question_type SET DEFAULT 'single_choice'",
     "column|job_knockout_questions.question_type"),
])
def test_a_difference_that_is_not_the_recorded_production_state_stops_the_conversion(prod, statement, expect):
    _boot_exec(prod, statement)
    a = U.assess(prod.bootstrap())
    assert a.blocking and any(expect in d or expect in n for _, n, d in a.findings), [f for f in a.findings if f[0] == "FAIL"][:3]
    assert_refused_and_unchanged(prod, expect, corrections=ALL)


@pytest.mark.parametrize("statements,expect", [
    (("CREATE OR REPLACE FUNCTION public.set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
      "NEW.updated_at = now() + interval '1 day'; RETURN NEW; END; $$",), "has a body that differs"),
    (("CREATE OR REPLACE FUNCTION public.set_updated_at() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$ "
      "BEGIN NEW.updated_at = now(); RETURN NEW; END; $$",), "not a plain plpgsql trigger function"),
    (("CREATE ROLE cv_worker NOLOGIN", "ALTER FUNCTION public.set_updated_at() OWNER TO cv_worker"), "is owned by runtime role cv_worker"),
])
def test_the_external_trigger_function_must_be_the_plain_function_the_fixture_documents(prod, statements, expect):
    _boot_exec(prod, *statements)
    a = U.assess(prod.bootstrap())
    assert a.blocking and any(expect in d for _, n, d in a.findings if n == "external dependency"), a.findings[:5]
    with pytest.raises(U.ConversionError, match="external dependency"):
        prepare(prod, corrections=ALL)


# ---------------------------------------------------------------------------------------------------------------------------------
# 6. The guards inside the transaction, each exercised directly
# ---------------------------------------------------------------------------------------------------------------------------------

def _transaction(database):
    conn = database.bootstrap()
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute("SET search_path = cv_analyzer, public")
    return conn, cur


def test_a_correction_applied_with_a_count_other_than_the_live_one_is_refused_inside_the_transaction(prod):
    conn, cur = _transaction(prod)
    with pytest.raises(U.ConversionError, match="92 confirmed|confirmed"):
        U._apply_correction(cur, F092, 92, U.Result(assessment=L.Assessment()))
    conn.rollback()
    conn.close()


def test_only_migration_081_may_delete_knockout_answers(prod, monkeypatch):
    monkeypatch.setitem(PR.CORRECTIONS, F092, PR.Correction(
        F092, "UPDATE cv_analyzer.applications SET workflow_status = 'awaiting_review', updated_at = now() WHERE processing_status = "
              "'ai_scored' AND workflow_status = 'new'; DELETE FROM cv_analyzer.application_knockout_answers WHERE answer_value = 'yes'",
        "also deletes an answer"))
    assert_refused_and_unchanged(prod, "removed rows it is not declared to remove", corrections=ALL)


def test_the_057_backfill_leaves_stopped_applications_alone_even_when_077_has_not_run(prod):
    conn, cur = _transaction(prod)
    cur.execute("INSERT INTO applications (job_id, tenant_id, candidate_name, processing_status, workflow_status, submission_source) "
                "SELECT job_id, tenant_id, 'pending-new-' || g, 'pending', 'new', 'manual_upload' FROM applications, generate_series(1, 3) g "
                "WHERE candidate_name = 'scored-ok-1'")
    U._apply_correction(cur, F057, 3, U.Result(assessment=L.Assessment()))
    cur.execute("SELECT processing_status, workflow_status, count(*) FROM applications WHERE workflow_status IS NOT DISTINCT FROM 'new' "
                "OR candidate_name LIKE 'pending-new-%' GROUP BY 1, 2 ORDER BY 1")
    remaining = cur.fetchall()
    conn.rollback()
    conn.close()
    assert ("pending", "awaiting_review", 3) in remaining                    # the backfill ran on the rows it owns
    assert ("failed", "new", 86) in remaining                                # 077's rows (still 'new' here) and 092's are untouched
    assert ("ai_scored", "new", 524) in remaining


def test_a_step_that_changes_an_identity_it_does_not_declare_is_rolled_back(prod, monkeypatch):
    import dataclasses
    step = PR.STEP_BY_NAME["knockout_questions_integrity"]
    monkeypatch.setitem(PR.STEP_BY_NAME, step.name,
                        dataclasses.replace(step, sql=step.sql + "CREATE INDEX idx_undeclared ON cv_analyzer.jobs (title);"))
    assert_refused_and_unchanged(prod, "changed objects outside its declared set", corrections=ALL)


def test_a_step_that_ends_in_the_wrong_definition_is_rolled_back(prod, monkeypatch):
    import dataclasses
    step = PR.STEP_BY_NAME["workflow_status_model"]
    monkeypatch.setitem(PR.STEP_BY_NAME, step.name, dataclasses.replace(
        step, sql=step.sql.replace("'withdrawn', 'on_hold'", "'withdrawn'")))
    assert_refused_and_unchanged(prod, "did not end in the expected definition", corrections=ALL)


# ---------------------------------------------------------------------------------------------------------------------------------
# 7. After the conversion
# ---------------------------------------------------------------------------------------------------------------------------------

def _preflight(database):
    conn = _connect(database)
    conn.autocommit = False
    conn.set_session(readonly=True)
    try:
        return P.run(conn, phase="after")
    finally:
        conn.rollback()
        conn.close()


def test_the_preflight_fails_when_the_production_evidence_or_the_external_function_is_tampered_with(prod):
    prepare(prod, corrections=ALL)
    operator = _connect(prod, "cv_migration_operator", prod.runtime_passwords["cv_migration_operator"])
    runner.run(operator, owner_role="cv_migrator")
    operator.close()
    H.provision_runtime_logins(prod)
    assert [c for c in _preflight(prod) if c.status != P.PASS] == []
    boot = prod.bootstrap()
    cur = boot.cursor()
    cur.execute("UPDATE cv_analyzer.schema_migration_legacy_evidence SET detail = jsonb_set(detail, '{rows_after}', '3') "
                "WHERE item_kind = 'data_correction' AND item = %s", (F092,))
    cur.execute("UPDATE cv_analyzer.schema_migration_legacy_evidence SET item = 'invented_cause' WHERE item_kind = 'production_variation' "
                "AND item = 'early_schema_columns'")
    cur.execute("CREATE OR REPLACE FUNCTION public.set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                "NEW.updated_at = now() + interval '1 year'; RETURN NEW; END; $$")
    boot.close()
    failed = {c.name for c in _preflight(prod) if c.status == P.FAIL}
    assert {"legacy lineage: every recorded data correction ended at zero rows",
            "legacy lineage: every recorded production variation has a known root cause",
            "legacy lineage: trigger function outside cv_analyzer"} <= failed


def test_the_command_line_takes_a_confirmed_count_per_file_and_never_prints_the_password(prod, capsys, monkeypatch):
    H.provision_migration_roles(prod)
    monkeypatch.setenv("LEGACY_UPGRADE_DATABASE_URL", f"postgresql://postgres:s3cret-pw@127.0.0.1:{prod.cluster.port}/{prod.name}")
    for bad in (["--apply-data-correction", F092], ["--apply-data-correction", f"{F092}=abc"],
                ["--apply-data-correction", f"{F092}=1", "--apply-data-correction", f"{F092}=2"], ["--apply-data-correction", "=5"]):
        assert CLI.main(["prepare", "--dry-run", *bad]) == 2
    capsys.readouterr()
    flags = [x for f, n in ALL.items() for x in ("--apply-data-correction", f"{f}={n}")]
    assert CLI.main(["prepare", "--dry-run", *flags]) == 0
    out = capsys.readouterr().out
    assert "done: corrected " + F092 + ": 524 -> 0" in out and "dry run: rolled back" in out and "s3cret-pw" not in out
    assert CLI.main(["prepare", "--dry-run", "--apply-data-correction", f"{F092}=1"]) == 1
    captured = capsys.readouterr()
    assert "confirmed 1 row(s) but 524" in captured.err and "Nothing was changed" in captured.err and "s3cret-pw" not in captured.err


def test_a_step_refuses_inside_the_transaction_when_a_precheck_is_not_zero(cluster, seed_source):
    """The report already blocks on these rows; this proves the step itself refuses too, so it cannot be driven past the report."""
    database = LP.restored_production(cluster)
    LP.add_seed_rows(database, seed_source)
    database.ids = LP.seed_restored(database, password_hash=HASH, orphan_answer=True)
    try:
        conn, cur = _transaction(database)
        with pytest.raises(U.ConversionError, match="step knockout_answers_integrity cannot be applied.*answers whose application does not exist: 1"):
            U._apply_step(cur, PR.STEP_BY_NAME["knockout_answers_integrity"], U.Result(assessment=L.Assessment()))
        conn.rollback()
        conn.close()
    finally:
        database.close()
