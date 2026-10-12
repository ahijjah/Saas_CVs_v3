"""Offline tests of the versioned human-review package, v3 (scripts/requirements_v2_review_package.py).

v3 supersedes v2 for two mechanical reasons: the OR-silent probe now inspects conditions as well as category items, and the checker
recognises joint quotation coverage across separate items. The tests check: byte-for-byte regeneration; blank reviewer cells; the two
corrections on the cases that exposed them (S010 probe, S003 joint coverage); a genuinely missing clause that stays flagged; and that
the historical v2 and v1 packages still match their own manifests. No network, no model call, no judgment is filled.
"""
import collections
import csv
import hashlib
import json
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
PKG = BACKEND / "tests" / "fixtures" / "requirements_v2_review_package_v3"
V2 = BACKEND / "tests" / "fixtures" / "requirements_v2_review_package_v2"
V1 = BACKEND / "tests" / "fixtures" / "requirements_v2_review_package"
sys.path.insert(0, str(BACKEND / "scripts"))
import requirements_v2_review_package as RP  # noqa: E402
import requirements_v2_staged_check as P  # noqa: E402

B03 = "B03_en_warehouse_supervisor"
B07 = "B07_ar_accountant"
S003_STATEMENT = "الشروط: الراتب بالريال السعودي، والعمل في مدينة الرياض بدوام كامل."


def _rows(name):
    return list(csv.DictReader((PKG / name).read_text(encoding="utf-8").splitlines()))


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stored(case):
    rows = [json.loads(x) for x in (PKG / "sources" / "benchmark_v2-2_run1_calls.jsonl").read_text(encoding="utf-8").splitlines()]
    return [r["raw"] for r in rows if r["case"] == case][0]


def _bench_jd(case):
    return json.loads((BACKEND / "tests" / "fixtures" / "requirements_v2_benchmark" / "cases" / f"{case}.json").read_text(encoding="utf-8"))["jd"]


@pytest.fixture(scope="module")
def sheet():
    return _rows("review_sheet_v2.csv")


@pytest.fixture(scope="module")
def starter():
    return _rows("starter_review_sheet_v2.csv")


# ── regeneration and provenance ──────────────────────────────────────────────────────────────────────────────────────────

def test_the_v3_package_regenerates_byte_for_byte_from_its_sources():
    files, summary = RP.build()
    for name, data in files.items():
        assert (PKG / name).read_bytes() == data, name
    recorded = json.loads((PKG / "MANIFEST.json").read_text(encoding="utf-8"))
    regenerated = RP.manifest_for(files, summary)
    for key in ("sources_sha256", "outputs_sha256", "summary", "selection"):
        assert recorded[key] == regenerated[key], key


def test_the_v3_manifest_states_that_it_supersedes_v2():
    recorded = json.loads((PKG / "MANIFEST.json").read_text(encoding="utf-8"))
    assert "version 3" in recorded["package"]
    assert "27eee4d" in json.dumps(recorded.get("supersedes", {}))


# ── reviewer cells stay blank ────────────────────────────────────────────────────────────────────────────────────────────

def test_every_reviewer_cell_in_the_v3_sheets_is_blank(sheet, starter):
    for rows in (sheet, starter):
        assert rows
        assert all(r["reviewer_choice"] == "" and r["reviewer_note"] == "" for r in rows)


def test_the_v3_starter_has_four_rows_in_each_of_the_three_strata(starter):
    assert len(starter) == 12
    assert collections.Counter(r["selection_stratum"] for r in starter) == {
        "real_warning": 4, "candidate_warning": 4, "unflagged_probe": 4}


# ── correction 1: the OR-silent probe inspects conditions as well as items ───────────────────────────────────────────────

def test_the_stored_b03_answer_quotes_each_clause_of_the_terms_statement_in_its_conditions():
    """Checked on the stored answer itself, so the probe fix does not rest on the probe text."""
    answer = json.loads(_stored(B03))
    conditions = " ".join(c["source_text"] for k in ("non_scoreable_requirements", "post_hiring_conditions") for c in answer.get(k, []))
    for clause in ("rotating night shifts", "Abu Dhabi depot", "residence visa or be willing to relocate"):
        assert clause in conditions, clause


def test_the_b03_terms_statement_is_not_an_or_silent_probe_in_v3(sheet):
    assert [r for r in sheet if r["case_id"] == B03 and r["row_kind"] == "checker_silent_probe"] == []
    assert [r for r in sheet if r["case_id"] == B03 and r["warning_type"] == "or_silent_probe"] == []


def test_the_v3_checker_quotes_the_terms_statement_through_the_conditions_so_no_or_silent_fires():
    rep = P.validate(_bench_jd(B03), _stored(B03))
    stmt = [s for s in rep["statements"] if s["text"].startswith("Terms:")][0]["id"]
    assert not [x for x in rep["review_queue"] if x["type"] in ("or_silent_probe", "partial_quote") and x.get("statement") == stmt]


# ── correction 2: joint quotation coverage across separate items ─────────────────────────────────────────────────────────

def test_the_s003_statement_has_no_partial_quote_row_in_v3(sheet):
    rows = [r for r in sheet if r["case_id"] == B07 and r["jd_statement_exact"] == S003_STATEMENT]
    assert rows and all(r["warning_type"] != "partial_quote" for r in rows)


def test_the_s003_statement_is_now_a_location_probe_in_v3(sheet):
    rows = [r for r in sheet if r["case_id"] == B07 and r["jd_statement_exact"] == S003_STATEMENT]
    assert rows and {r["row_kind"] for r in rows} == {"location_probe"}


def test_the_checker_still_flags_a_genuinely_missing_final_clause_of_the_terms_statement():
    """Removing the visa/relocation condition leaves its clause uncovered, so the partial quotation is still raised."""
    answer = json.loads(_stored(B03))
    answer["non_scoreable_requirements"] = [c for c in answer["non_scoreable_requirements"] if "residence visa" not in c["source_text"]]
    rep = P.validate(_bench_jd(B03), json.dumps(answer, ensure_ascii=False))
    stmt = [s for s in rep["statements"] if s["text"].startswith("Terms:")][0]["id"]
    partial = [x for x in rep["review_queue"] if x["type"] == "partial_quote" and x.get("statement") == stmt]
    assert len(partial) == 1 and "candidates must hold" in partial[0]["detail"]


# ── historical packages are untouched ────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [V2, V1], ids=["v2", "v1"])
def test_the_historical_package_outputs_match_their_own_manifest(path):
    recorded = json.loads((path / "MANIFEST.json").read_text(encoding="utf-8"))
    for name, digest in recorded["outputs_sha256"].items():
        assert _sha(path / name) == digest, f"{path.name}/{name}"
