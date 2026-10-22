"""Every difference between the restored production schema and the committed legacy reference, individually.

tests/fixtures/restored_production/schema.sql is the schema-only dump of the restored production backup. db/legacy/production_lineage.json
classifies each of the 86 identities that differ from db/legacy/reference.json. These tests prove:

  * the file lists exactly the identities the dump differs in, with the exact text found there (no more, no fewer);
  * each classification means what it says: an `equivalent` constraint allows the same values, an `extended` one allows a superset, a
    `wider` column is wider and otherwise equal, a `production_only` object cannot make a statement of the current release fail;
  * the classifier accepts the recorded text and nothing else, and refuses a half-applied step.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import legacy_production_helper as LP  # noqa: E402
import realdb_helper as H  # noqa: E402
from db.legacy import fingerprint as F  # noqa: E402
from db.legacy import lineage as L  # noqa: E402
from db.legacy import production as PR  # noqa: E402

pytestmark = pytest.mark.skipif(H.unavailable_reason() is not None, reason=H.unavailable_reason() or "")

COUNTS = {"missing": 13, "different": 50, "unknown": 23}


@pytest.fixture(scope="module")
def live():
    cluster = H.Cluster()
    database = None
    try:
        database = LP.restored_production(cluster)
        conn = database.bootstrap()
        snapshot = F.snapshot(conn.cursor())
        conn.close()
        yield snapshot
    finally:
        if database is not None:
            database.close()
        cluster.stop()


@pytest.fixture(scope="module")
def diff(live):
    missing, different, extra = F.diff(L.EXPECTED_LEGACY, live)
    drop = lambda keys: sorted(k for k in keys if not k.startswith("seed|"))        # a schema-only dump holds no seeded rows
    return {"missing": drop(missing), "different": drop(different), "unknown": drop(extra)}


def literals(definition: str) -> list[str]:
    return re.findall(r"'((?:[^']|'')*)'", definition)


def canonical(definition: str) -> str:
    """The definition without the casts and parentheses PostgreSQL adds when a column has been retyped: the same CHECK or predicate
    prints as ANY ((ARRAY['a'::character varying, ...])::text[]) on a clean build and as ANY (ARRAY[('a'::character varying)::text, ...])
    after ALTER COLUMN TYPE. Literals, column names, operators and their order are kept."""
    text = definition.replace("::character varying", "").replace("::text[]", "").replace("::text", "")
    return re.sub(r"[()\s]", "", text)


# --- the file equals the dump's difference ------------------------------------------------------------------------------------------
def test_the_dump_differs_from_the_reference_in_exactly_the_classified_identities(live, diff):
    assert {k: len(v) for k, v in diff.items()} == COUNTS
    classified = PR.IDENTITIES
    assert sorted(classified) == sorted(diff["missing"] + diff["different"] + diff["unknown"])
    for key in diff["missing"]:
        assert classified[key]["live"] is None and key not in live
    for key in diff["different"] + diff["unknown"]:
        assert classified[key]["live"] == live[key], key
    for key in diff["unknown"]:
        assert key not in L.EXPECTED_LEGACY and classified[key]["disposition"] == "production_only"
    for key in diff["missing"] + diff["different"]:
        assert key in L.EXPECTED_LEGACY and classified[key]["live"] != L.EXPECTED_LEGACY[key]


def test_every_identity_has_a_disposition_a_cause_and_a_root_cause_text():
    for key, entry in PR.IDENTITIES.items():
        assert entry["disposition"] in PR.DISPOSITIONS, key
        assert entry["cause"] in PR.CAUSES, key
        assert (entry["disposition"] == "reconcile") == (entry.get("step") is not None), key
        assert (entry["disposition"] == "substituted") == ("substitute" in entry), key
    for name, cause in PR.CAUSES.items():
        assert len(cause["title"]) > 20 and len(cause["root_cause"]) > 80, name
        assert any(e["cause"] == name for e in PR.IDENTITIES.values()), f"cause {name} is used by no identity"
    for step in PR.STEPS:
        assert step.keys, step.name
        assert {PR.IDENTITIES[k]["disposition"] for k in step.keys} == {"reconcile"}
    assert {e["step"] for e in PR.IDENTITIES.values() if e.get("step")} == {s.name for s in PR.STEPS}
    assert sorted(PR.CORRECTIONS) == sorted(PR.CORRECTION_ORDER)
    for file in PR.CORRECTIONS:
        assert file in L.DATA_CHECK_FILES


def test_the_dispositions_are_reported_per_identity_in_the_complete_report(live):
    out = L.Assessment()
    L.assess_structure(live, out, accepted_seeds=frozenset(k for k in F.diff(L.EXPECTED_LEGACY, live)[0] if k.startswith("seed|")))
    assert not out.blocking, [f for f in out.findings if f[0] == "FAIL"]
    listed = [e["key"] for part in ("missing", "different", "unknown") for e in out.structure[part]]
    assert sorted(listed) == sorted(PR.IDENTITIES)
    for part in ("missing", "different", "unknown"):
        for entry in out.structure[part]:
            assert entry["cause"] in PR.CAUSES and entry["classified_as"] == PR.IDENTITIES[entry["key"]]["disposition"]


# --- each disposition means what it says --------------------------------------------------------------------------------------------
EQUIVALENT = sorted(k for k, e in PR.IDENTITIES.items() if e["disposition"] == "equivalent"
                    and k.split("|")[0] in ("constraint", "index"))


@pytest.mark.parametrize("key", EQUIVALENT)
def test_an_equivalent_constraint_or_index_allows_exactly_the_reference_values(key):
    expected, recorded = L.EXPECTED_LEGACY[key], PR.IDENTITIES[key]["live"]
    assert expected != recorded                       # otherwise it would not be a difference at all
    assert literals(expected) == literals(recorded)    # the same values, in the same order
    assert canonical(expected) == canonical(recorded)  # the same expression, apart from casts and parentheses


def test_the_verifier_rejects_a_constraint_that_is_not_equivalent():
    expected = L.EXPECTED_LEGACY["constraint|tenants.tenants_status_check"]
    for changed in (PR.IDENTITIES["constraint|tenants.tenants_status_check"]["live"].replace("'suspended'", "'banned'"),
                    PR.IDENTITIES["constraint|tenants.tenants_status_check"]["live"].replace("'active'", "'active'), ('x')"),
                    PR.IDENTITIES["constraint|tenants.tenants_status_check"]["live"].replace("ANY", "ALL")):
        assert canonical(changed) != canonical(expected) or literals(changed) != literals(expected)


def test_the_one_extended_constraint_allows_a_strict_superset_and_is_otherwise_the_same():
    key = "constraint|job_criteria.job_criteria_criteria_extraction_status_check"
    assert PR.IDENTITIES[key]["disposition"] == "extended"
    reference, recorded = set(literals(L.EXPECTED_LEGACY[key])), set(literals(PR.IDENTITIES[key]["live"]))
    assert reference < recorded and recorded - reference == {"insufficient", "blocked"}
    strip = lambda text: re.sub(r"'[^']*'", "", canonical(text))
    assert strip(L.EXPECTED_LEGACY[key]).replace(",", "") == strip(PR.IDENTITIES[key]["live"]).replace(",", "")


def test_the_wider_column_is_only_wider():
    key = "column|email_ingest_log.job_code_source"
    assert PR.IDENTITIES[key]["disposition"] == "wider"
    pattern = re.compile(r"^character varying\((\d+)\) (notnull=\w+ default=.*)$")
    reference, recorded = pattern.match(L.EXPECTED_LEGACY[key]), pattern.match(PR.IDENTITIES[key]["live"])
    assert int(recorded.group(1)) > int(reference.group(1)) and recorded.group(2) == reference.group(2)


def test_the_set_updated_at_function_and_trigger_differ_only_in_formatting_and_schema(live):
    function = "function|set_updated_at()"
    trigger = "trigger|application_intake_log.trg_ail_updated_at"
    assert {PR.IDENTITIES[function]["disposition"], PR.IDENTITIES[trigger]["disposition"]} == {"equivalent"}
    body = lambda text: PR.normalise_body(text)
    # the reference's body is the one-line form of migration 032; production keeps schema.sql's multi-line form of the same statements
    reference = body("BEGIN NEW.updated_at = now(); RETURN NEW; END;")
    fixture = (LP.FIXTURE_DIR / "schema.sql").read_text(encoding="utf-8")
    section = fixture[fixture.index("CREATE FUNCTION cv_analyzer.set_updated_at()"):]
    section = section[section.index("$$") + 2:section.index("$$", section.index("$$") + 2)]
    assert body(section) == reference
    assert PR.IDENTITIES[trigger]["live"] == L.EXPECTED_LEGACY[trigger].replace("FUNCTION set_updated_at()",
                                                                                    "FUNCTION public.set_updated_at()")


def test_production_only_columns_and_constraints_cannot_make_a_current_statement_fail(live):
    """An extra column breaks an INSERT only if it is NOT NULL without a default. An extra constraint breaks one only if it constrains
    a column the release writes. Neither holds for any preserved object."""
    production_only = {k: e for k, e in PR.IDENTITIES.items() if e["disposition"] == "production_only"}
    columns = {k.split("|")[1]: e["live"] for k, e in production_only.items() if k.startswith("column|")}
    for name, definition in columns.items():
        assert "notnull=False" in definition or re.search(r"default=\S", definition), f"{name} would break an INSERT that omits it"
    names = {name.split(".")[1] for name in columns}
    for key, entry in production_only.items():
        if key.startswith("constraint|"):
            table = key.split("|")[1].split(".")[0]
            referenced = set(re.findall(r"\(\(?\(?(\w+)\)?::", entry["live"])) | set(re.findall(r"\((\w+)\)::", entry["live"]))
            assert referenced and referenced <= {c.split(".")[1] for c in columns if c.startswith(table + ".")}, (key, referenced)
    assert names                                                                         # the check above ran over something


def test_the_substituted_index_is_the_same_index_under_another_name():
    key = "index|idx_knockout_answers_application"
    entry = PR.IDENTITIES[key]
    other = PR.IDENTITIES[entry["substitute"]]["live"]
    assert other.replace("idx_application_knockout_answers_app", "idx_knockout_answers_application") == L.EXPECTED_LEGACY[key]


def test_a_reconciled_identity_ends_in_the_reference_definition_except_the_one_recorded_end_state():
    ends = {k: e["after"] for k, e in PR.IDENTITIES.items() if e.get("after")}
    assert list(ends) == ["column|job_knockout_questions.question_type"]
    # width 50 is production's; the default is what migration 044 defines; the column stays NOT NULL
    assert ends["column|job_knockout_questions.question_type"] == (
        L.EXPECTED_LEGACY["column|job_knockout_questions.question_type"].replace("character varying(20)", "character varying(50)"))


def test_every_reconcile_step_names_its_prechecks_and_each_precheck_is_a_count():
    for step in PR.STEPS:
        for check in step.prechecks:
            assert check.sql.lstrip().upper().startswith("SELECT") and "count" in check.sql.lower() and check.advice
    assert [s.name for s in PR.STEPS if not s.prechecks] == ["email_ingest_log_operational_columns"]    # additive only


# --- the classifier -----------------------------------------------------------------------------------------------------------------
def _all(live):
    return [k for k in sorted(set(PR.IDENTITIES))]


def test_the_classifier_accepts_the_recorded_state_and_nothing_else(live):
    result = PR.classify(live, _all(live))
    assert result.unexplained == []
    assert len(result.accepted) + sum(len(v) for v in result.needed.values()) + len(result.repairs) == 86
    assert sorted(result.needed) == sorted(s.name for s in PR.STEPS)
    assert result.repairs == ["index|idx_workflow_policies_sla"]


@pytest.mark.parametrize("key", sorted(k for k, e in PR.IDENTITIES.items() if e["live"] is not None and e["disposition"] != "repair"))
def test_any_other_text_for_a_recorded_identity_is_unexplained(live, key):
    altered = dict(live)
    altered[key] = live[key] + " "
    result = PR.classify(altered, _all(altered))
    assert any(key in message and "neither" in message for message in result.unexplained)


def test_an_identity_that_is_not_in_the_file_is_unexplained(live):
    result = PR.classify(live, _all(live) + ["column|jobs.surprise"])
    assert any("column|jobs.surprise" in m for m in result.unexplained)


def test_a_half_applied_step_is_a_state_the_procedure_does_not_explain(live):
    # one identity of the 031 step already carries the reference definition: it is not among the differing keys any more
    keys = [k for k in _all(live) if k != "column|email_ingest_log.notification_queued_at"]
    result = PR.classify(live, keys)
    assert any(m.startswith("step email_ingest_log_operational_columns") for m in result.unexplained)


def test_a_substituted_index_needs_its_substitute_in_the_recorded_state(live):
    altered = {k: v for k, v in live.items() if k != "index|idx_application_knockout_answers_app"}
    keys = [k for k in _all(live) if k != "index|idx_application_knockout_answers_app"]
    result = PR.classify(altered, keys)
    assert any("substitute" in m for m in result.unexplained)


def test_the_final_state_check_accepts_the_reference_the_recorded_end_state_and_nothing_else():
    assert PR.final_ok("column|x.y", "a", "a")
    assert not PR.final_ok("column|x.y", "a", "b")                                        # an identity the file does not know
    key = "column|job_knockout_questions.question_type"
    assert PR.final_ok(key, L.EXPECTED_LEGACY[key], PR.IDENTITIES[key]["after"])
    assert not PR.final_ok(key, L.EXPECTED_LEGACY[key], PR.IDENTITIES[key]["live"])        # the pre-conversion text is not an end state
    equivalent = "constraint|tenants.tenants_status_check"
    assert PR.final_ok(equivalent, L.EXPECTED_LEGACY[equivalent], PR.IDENTITIES[equivalent]["live"])
    assert not PR.final_ok(equivalent, L.EXPECTED_LEGACY[equivalent], PR.IDENTITIES[equivalent]["live"] + " ")
    assert PR.final_ok("index|idx_knockout_answers_application", L.EXPECTED_LEGACY["index|idx_knockout_answers_application"], None)
    reconciled = "constraint|applications.applications_workflow_status_check"
    assert not PR.final_ok(reconciled, L.EXPECTED_LEGACY[reconciled], None)               # a reconciled constraint must exist afterwards


def test_the_lineage_file_is_valid_json_with_the_expected_top_level_shape():
    document = json.loads((PR.HERE / "production_lineage.json").read_text(encoding="utf-8"))
    assert set(document) == {"description", "causes", "identities"}
    assert len(document["identities"]) == 86
