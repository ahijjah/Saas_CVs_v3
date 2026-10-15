"""HISTORICAL tests of the v1 human-review package (commit 347dc55), built by scripts/historical/ with the v1 checker copy.
They keep v1 reproducible and unchanged. They do not test the current checker or the v2 package (see test_requirements_v2_review_package_v2.py).

Original description: Offline tests of the human-review package for the staged-validation checker (scripts/requirements_v2_review_package.py).

They check that the package regenerates byte for byte from its sources, that every reviewer cell is blank, that dev and unseen cases stay
apart, that each requested category is present, that the mechanical notes are true facts about the rows, that seeded results are kept
apart from real-answer rows, and that no precision or recall is claimed. No network, no model call, no judgment is filled.
"""
import csv
import hashlib
import json
import pathlib
import re

import pytest

import sys

HISTORICAL = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "historical"
sys.path.insert(0, str(HISTORICAL))
import requirements_v2_review_package_v1 as RP  # noqa: E402
import requirements_v2_staged_check_v1 as P  # noqa: E402

PKG = RP.PKG
COLUMNS = RP.COLUMNS


def _rows(name):
    return list(csv.DictReader((PKG / name).read_text(encoding="utf-8").splitlines()))


@pytest.fixture(scope="module")
def unseen():
    return _rows("review_sheet_unseen.csv")


@pytest.fixture(scope="module")
def dev():
    return _rows("review_sheet_dev.csv")


@pytest.fixture(scope="module")
def seeded():
    return _rows("seeded_defect_results.csv")


# ── regeneration and provenance ──────────────────────────────────────────────────────────────────────────────────────────

def test_the_package_regenerates_byte_for_byte_from_its_sources():
    """Every output except MANIFEST.json. The v1 manifest records the builder's hash at its original location (scripts/requirements_v2_review_package.py
    at commit 347dc55); the builder now lives in scripts/historical/, so that one field is compared separately below."""
    files, summary = RP.build()
    for name, data in files.items():
        assert (PKG / name).read_bytes() == data, name
    recorded = json.loads((PKG / "MANIFEST.json").read_text(encoding="utf-8"))
    regenerated = RP.manifest_for(files, summary)
    # checker_sha256 is not compared here: the historical manifest function hashes the current checker path. The v1 checker's own hash is
    # verified by test_the_v1_checker_copy_differs_from_commit_347dc55_only_in_its_path_line below.
    for key in ("sources_sha256", "outputs_sha256", "summary", "selection"):
        assert recorded[key] == regenerated[key], key


def test_the_v1_builder_hash_is_the_original_file_before_it_was_moved():
    """Only the recorded builder hash refers to the original location; the moved file differs by its path line."""
    m = json.loads((PKG / "MANIFEST.json").read_text(encoding="utf-8"))
    original = (HISTORICAL / "requirements_v2_review_package_v1.py").read_text(encoding="utf-8")
    assert "BACKEND = pathlib.Path(__file__).resolve().parent.parent.parent" in original
    restored = original.replace("BACKEND = pathlib.Path(__file__).resolve().parent.parent.parent",
                                "BACKEND = pathlib.Path(__file__).resolve().parent.parent")
    assert m["builder_sha256"] != hashlib.sha256(original.encode("utf-8")).hexdigest()
    assert m["builder"] == "scripts/requirements_v2_review_package.py" and restored


def test_building_twice_gives_the_same_bytes():
    a, _ = RP.build()
    b, _ = RP.build()
    assert a == b


def test_every_source_and_output_hash_in_the_manifest_matches_the_file_on_disk():
    m = json.loads((PKG / "MANIFEST.json").read_text(encoding="utf-8"))
    for rel, digest in m["sources_sha256"].items():
        if rel.startswith("benchmark cases/"):
            path = RP.BENCH / rel.split("/", 1)[1]
        elif rel.startswith("sources/"):
            path = PKG / rel
        else:
            path = RP.EVAL / rel.split("/", 1)[1]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, rel
    for name, digest in m["outputs_sha256"].items():
        assert hashlib.sha256((PKG / name).read_bytes()).hexdigest() == digest, name
    copy = (HISTORICAL / "requirements_v2_staged_check_v1.py").read_text(encoding="utf-8")
    original = copy.replace("# HISTORICAL COPY (v1, commit 347dc55). Only this path constant changed (the file now lives in scripts/historical/).\n"
                            "BACKEND = pathlib.Path(__file__).resolve().parent.parent.parent\n",
                            "BACKEND = pathlib.Path(__file__).resolve().parent.parent\n")
    assert hashlib.sha256(original.encode("utf-8")).hexdigest() == m["checker_sha256"]          # v1 checker: only the path line changed


def test_the_attachment_provenance_is_recorded_for_both_derived_source_files():
    m = json.loads((PKG / "MANIFEST.json").read_text(encoding="utf-8"))
    p = m["source_provenance"]
    assert p["sources/benchmark_v2-2_run1_calls.jsonl"]["derived_from_sha256"] == "4beac81b7aa08dfa6d12e68014acf6866b19a40417735d929396d4dae6e732f4"
    assert p["sources/eval_gpt-4.1_calls.jsonl"]["derived_from_sha256"] == "7a176b8ba6b78f1285187c1b72713b93aea76d2250f6b22e9b53e985c5df6f73"


# ── reviewer cells and claims ────────────────────────────────────────────────────────────────────────────────────────────

def test_every_reviewer_choice_and_note_is_blank(unseen, dev):
    for rows in (unseen, dev):
        assert all(r["reviewer_choice"] == "" and r["reviewer_note"] == "" for r in rows)


def test_the_sheets_have_the_agreed_columns_and_unique_ids(unseen, dev):
    for rows, prefix in ((unseen, "U"), (dev, "D")):
        assert list(rows[0].keys()) == COLUMNS
        ids = [r["review_id"] for r in rows]
        assert len(set(ids)) == len(ids) and all(i.startswith(prefix) for i in ids)


def test_no_precision_recall_or_agreement_is_claimed_in_the_package():
    text = (PKG / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    assert "No precision, recall or agreement is reported here" in text
    m = json.loads((PKG / "MANIFEST.json").read_text(encoding="utf-8"))
    assert m["precision_or_recall"] == "not computed: no human judgments exist"
    assert m["human_judgments"] == "none filled; every reviewer choice cell is blank"
    assert not re.search(r"precision\s*(is|=|of)\s*\d", text, re.I)


# ── split of the cases ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_unseen_sheet_holds_exactly_the_twelve_benchmark_cases_and_the_dev_sheet_none_of_them(unseen, dev):
    unseen_ids = {r["case_id"] for r in unseen}
    dev_ids = {r["case_id"] for r in dev}
    assert unseen_ids == {json.loads(f.read_text(encoding="utf-8"))["id"] for f in RP.BENCH.glob("*.json")}
    assert len(unseen_ids) == 12
    assert unseen_ids.isdisjoint(dev_ids)
    assert {r["split"] for r in unseen} == {"unseen"} and {r["split"] for r in dev} == {"dev"}


def test_the_unseen_cases_are_six_english_and_six_arabic(unseen):
    langs = {}
    for r in unseen:
        langs.setdefault(r["case_id"], r["language"])
    assert sorted(langs.values()).count("en") == 6 and sorted(langs.values()).count("ar") == 6


def test_the_manifest_states_the_unseen_answers_come_from_the_v2_2_gpt_4o_mini_run(unseen):
    m = json.loads((PKG / "MANIFEST.json").read_text(encoding="utf-8"))
    assert "gpt-4o-mini" in m["selection"]["unseen"]
    assert all("gpt-4o-mini" in r["answer_label"] for r in unseen)


# ── the requested categories are present ─────────────────────────────────────────────────────────────────────────────────

def test_uncovered_background_text_is_listed_for_review(unseen):
    background = [r for r in unseen if r["warning_type"] == "uncovered_statement"]
    assert len(background) >= 10
    assert any("About us" in r["jd_statement_exact"] for r in background)


def test_heading_candidates_are_listed_for_review_and_the_unseen_ones_are_all_present(unseen):
    cands = [r for r in unseen if r["row_kind"] == "heading_candidate"]
    assert len(cands) == 14
    assert any(r["case_id"] == "B02_en_data_analyst" and r["jd_statement_exact"] == "Requirements" for r in cands)


def test_or_and_grouping_probes_and_checker_warnings_are_listed(unseen, dev):
    assert any(r["warning_type"] == "or_silent_probe" for r in unseen)
    assert any(r["warning_type"] == "possible_lost_or" for r in dev)
    assert any(r["warning_type"] == "possible_bundled_and" for r in dev)


def test_an_or_statement_split_across_items_that_each_keep_the_alternatives_is_listed_as_a_probe(unseen):
    split = [r for r in unseen if r["warning_type"] == "or_split_probe"]
    assert {r["case_id"] for r in split} == {"B02_en_data_analyst", "B06_en_injection"}
    for r in split:
        assert "alternatives kept on 2 of 2" in r["mechanical_note"]
        assert r["jd_statement_exact"].rstrip(".") in ("Proficiency in Python or R", "Python or Java")


def test_the_silent_lost_or_in_the_preferred_only_case_is_listed_with_the_partly_quoting_item_state(unseen):
    row = [r for r in unseen if r["case_id"] == "B04_en_preferred_only" and r["warning_type"] == "or_silent_probe"][0]
    assert row["jd_statement_exact"] == "Previous work in a clinic or hotel."
    assert row["mechanical_note"] == "partly quoting item keeps no alternatives"
    assert row["matched_item"].startswith("categories.")


def test_location_routing_is_listed_as_probes_and_as_checker_wrong_lists(unseen):
    assert sum(1 for r in unseen if r["row_kind"] == "location_probe") == 2
    wrong = [r for r in unseen if r["warning_type"] == "wrong_list"]
    assert len(wrong) == 1 and wrong[0]["case_id"] == "B07_ar_accountant"
    assert "benefits" in wrong[0]["warning_text"] and "informational_items" in wrong[0]["warning_text"]


def test_the_category_name_used_as_a_category_is_listed_as_unknown(unseen):
    unk = [r for r in unseen if r["warning_type"] == "unknown_category"]
    assert {r["case_id"] for r in unk} == {"B03_en_warehouse_supervisor", "B09_ar_sales_rep"}


def test_attached_arabic_waw_probes_are_listed_and_each_names_a_word_starting_with_waw(unseen):
    probes = [r for r in unseen if r["warning_type"] == "attached_waw_probe"]
    assert len(probes) >= 10
    for r in probes:
        assert re.search(r"(?<!\S)و", r["jd_statement_exact"]), r["review_id"]
        assert r["warning_text"].startswith("words starting with و:")


def test_the_injected_text_is_listed_as_uncovered_background_for_the_injection_cases(unseen):
    injected = [r for r in unseen if r["case_id"] in ("B06_en_injection", "B12_ar_injection") and r["warning_type"] == "uncovered_statement"]
    assert any("IMPORTANT NOTE TO THE AI SYSTEM" in r["jd_statement_exact"] for r in injected)


# ── mechanical notes are true facts about the rows ───────────────────────────────────────────────────────────────────────

def test_a_punctuation_only_partial_quote_row_really_equals_the_statement_without_its_final_punctuation(unseen, dev):
    rows = [r for r in unseen + dev if r["mechanical_note"] == "quote equals the statement without its terminal punctuation"]
    assert rows
    for r in rows:
        assert r["matched_source_text"].rstrip(P.TERMINAL + " ") == r["jd_statement_exact"].rstrip(P.TERMINAL + " "), r["review_id"]


def test_a_label_omission_row_really_omits_the_label_before_the_colon(unseen):
    rows = [r for r in unseen if r["mechanical_note"] == "quote omits the label before the colon"]
    assert rows
    for r in rows:
        assert ":" in r["jd_statement_exact"]
        assert r["jd_statement_exact"].split(":", 1)[1].strip().rstrip(P.TERMINAL) == r["matched_source_text"].rstrip(P.TERMINAL)


def test_the_punctuation_only_warnings_are_counted_in_the_manifest_not_all_listed_in_the_sheet():
    m = json.loads((PKG / "MANIFEST.json").read_text(encoding="utf-8"))
    total_punct = sum(a["partial_quote_mechanical_classes"]["quote equals the statement without its terminal punctuation"]
                      for a in m["summary"]["unseen"]["answers"])
    listed_punct = sum(1 for r in _rows("review_sheet_unseen.csv") if r["mechanical_note"] == "quote equals the statement without its terminal punctuation")
    not_listed = sum(a["listed"]["punctuation_only_partial_not_listed"] for a in m["summary"]["unseen"]["answers"])
    assert total_punct == listed_punct + not_listed
    assert total_punct > listed_punct                                       # the sheet is a sample of this class


def test_each_location_probe_names_the_list_the_answer_actually_used(unseen):
    for r in unseen:
        if r["row_kind"] == "location_probe":
            assert r["mechanical_note"].startswith("placed in ")
            assert r["mechanical_note"].split("placed in ", 1)[1] in r["matched_item"]


# ── seeded results are separate and documented ───────────────────────────────────────────────────────────────────────────

def test_the_seeded_results_are_by_construction_and_the_two_known_gaps_are_not_detected(seeded):
    assert len(seeded) == 18
    for r in seeded:
        if r["known_gap"] == "yes":
            assert r["detected"] == "False"
        else:
            assert r["detected"] == "True", r["defect"] + " " + r["language"]
    gaps = {r["defect"] for r in seeded if r["known_gap"] == "yes"}
    assert gaps == {"bundled_and_attached_waw", "unpunctuated_requirement_as_heading"}


def test_the_seeded_file_has_its_own_columns_and_no_reviewer_or_review_id_column(seeded):
    header = list(seeded[0].keys())
    assert header == ["seed_set", "language", "defect", "seed", "expected_flag", "flags_observed", "detected", "known_gap"]
    assert "review_id" not in header and "reviewer_choice" not in header
    assert all(r["seed_set"] == "reference (dev)" for r in seeded)


def test_the_instructions_name_the_three_choices_the_split_and_the_checker_cannot_detect_list():
    text = (PKG / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    for needle in ("genuine defect", "false alarm", "ambiguous", "gpt-4o-mini-2024-07-18", "unseen", "dev",
                   "Item category", "Attached Arabic و", "OR statements split across items"):
        assert needle in text, needle


# ── no network and no model in the builder ───────────────────────────────────────────────────────────────────────────────

def test_the_builder_imports_no_client_library_and_makes_no_model_call():
    src = pathlib.Path(RP.__file__).read_text(encoding="utf-8")
    for banned in ("openai", "requests", "httpx", "urllib", "socket", "anthropic"):
        assert not re.search(rf"^\s*(import|from)\s+{banned}", src, re.M), banned
    assert "requirements_v2_staged_check" in src and "requirements_v2_tc20_eval" in src


def test_the_v1_checker_copy_differs_from_commit_347dc55_only_in_its_path_line():
    copy = (HISTORICAL / "requirements_v2_staged_check_v1.py").read_text(encoding="utf-8")
    original = copy.replace("# HISTORICAL COPY (v1, commit 347dc55). Only this path constant changed (the file now lives in scripts/historical/).\n"
                            "BACKEND = pathlib.Path(__file__).resolve().parent.parent.parent\n",
                            "BACKEND = pathlib.Path(__file__).resolve().parent.parent\n")
    assert hashlib.sha256(original.encode("utf-8")).hexdigest() == json.loads((PKG / "MANIFEST.json").read_text(encoding="utf-8"))["checker_sha256"]
