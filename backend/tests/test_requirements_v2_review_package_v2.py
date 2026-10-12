"""Offline tests of the versioned human-review package, v2 (scripts/requirements_v2_review_package.py).

They check: byte-for-byte regeneration and provenance; blank reviewer cells; that every case is development evidence; the starter
selection rules reproduced from the sorted sheet; the requested categories; the mechanical facts that the revisions depend on (negative
checks that punctuation-only partial quotes are no longer warnings, and that ambiguous heading lines are never called omissions); seeded
results kept apart; and the absence of precision, recall or readiness claims. No network, no model call, no judgment is filled.
"""
import csv
import hashlib
import json
import pathlib
import re

import pytest

import scripts.requirements_v2_review_package as RP
import scripts.requirements_v2_staged_check as P

PKG = RP.PKG


def _rows(name):
    return list(csv.DictReader((PKG / name).read_text(encoding="utf-8").splitlines()))


@pytest.fixture(scope="module")
def sheet():
    return _rows("review_sheet_v2.csv")


@pytest.fixture(scope="module")
def starter():
    return _rows("starter_review_sheet_v2.csv")


@pytest.fixture(scope="module")
def seeded():
    return _rows("seeded_defect_results.csv")


# ── regeneration and provenance ──────────────────────────────────────────────────────────────────────────────────────────

def test_the_v2_package_regenerates_byte_for_byte_from_its_sources():
    files, summary = RP.build()
    files["MANIFEST.json"] = (json.dumps(RP.manifest_for(files, summary), ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")
    for name, data in files.items():
        assert (PKG / name).read_bytes() == data, name


def test_building_twice_gives_the_same_bytes():
    a, _ = RP.build()
    b, _ = RP.build()
    assert a == b


def test_the_manifest_hashes_match_the_files_on_disk_and_the_checker_is_the_current_one():
    m = json.loads((PKG / "MANIFEST.json").read_text(encoding="utf-8"))
    for name, digest in m["outputs_sha256"].items():
        assert hashlib.sha256((PKG / name).read_bytes()).hexdigest() == digest, name
    for rel, digest in m["sources_sha256"].items():
        if rel.startswith("sources/"):
            path = PKG / rel
        elif rel.startswith("benchmark cases/"):
            path = RP.BENCH / rel.split("/", 1)[1]
        else:
            path = RP.EVAL / rel.split("/", 1)[1]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, rel
    assert hashlib.sha256((RP.BACKEND / "scripts" / "requirements_v2_staged_check.py").read_bytes()).hexdigest() == m["checker_sha256"]


def test_the_v2_manifest_records_the_derived_source_provenance_and_the_supersession():
    m = json.loads((PKG / "MANIFEST.json").read_text(encoding="utf-8"))
    assert m["source_provenance"]["sources/benchmark_v2-2_run1_calls.jsonl"]["derived_from_sha256"] == \
        "4beac81b7aa08dfa6d12e68014acf6866b19a40417735d929396d4dae6e732f4"
    assert "347dc55" in m["supersedes"] and "historical" in m["supersedes"]


def test_the_v1_package_is_untouched_and_still_matches_its_own_manifest():
    v1 = RP.PKG.parent / "requirements_v2_review_package"
    m = json.loads((v1 / "MANIFEST.json").read_text(encoding="utf-8"))
    for name, digest in m["outputs_sha256"].items():
        assert hashlib.sha256((v1 / name).read_bytes()).hexdigest() == digest, name


# ── reviewer cells, development status, and claims ───────────────────────────────────────────────────────────────────────

def test_every_reviewer_choice_and_note_is_blank_in_both_sheets(sheet, starter):
    for rows in (sheet, starter):
        assert all(r["reviewer_choice"] == "" and r["reviewer_note"] == "" for r in rows)


def test_every_case_is_marked_development_and_no_row_claims_an_unseen_or_independent_set(sheet):
    groups = {r["source_group"] for r in sheet}
    assert groups <= {"benchmark", "tc20", "eval"}
    text = (PKG / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    assert "None is an independent, held-out or unseen validation set" in text
    assert "precision, recall, agreement or" in text and "production readiness" in text
    assert not re.search(r"\b(precision|recall)\s*(is|was|=|of)\s*\d", text, re.I)


def test_the_manifest_states_no_precision_recall_or_readiness():
    m = json.loads((PKG / "MANIFEST.json").read_text(encoding="utf-8"))
    assert m["precision_recall_or_readiness"].startswith("not computed")
    assert m["development_status"] == "every case is development evidence; no held-out set exists"


# ── the starter sheet: fixed selection rules, reproduced ─────────────────────────────────────────────────────────────────

def test_the_starter_has_twelve_rows_two_english_and_two_arabic_per_stratum(starter):
    assert len(starter) == 12
    for stratum in ("real_warning", "candidate_warning", "unflagged_probe"):
        rows = [r for r in starter if r["selection_stratum"] == stratum]
        assert len(rows) == 4 and sorted(r["language"] for r in rows) == ["ar", "ar", "en", "en"], stratum


def test_the_starter_is_exactly_the_fixed_rule_applied_to_the_sorted_sheet(sheet, starter):
    rows = RP.build()[0]["review_sheet_v2.csv"].decode("utf-8")
    chosen = RP.starter([dict(r) for r in csv.DictReader(rows.splitlines())])
    assert [r["jd_statement_exact"] for r in chosen] == [r["jd_statement_exact"] for r in starter]
    assert [r["selection_stratum"] for r in chosen] == [r["selection_stratum"] for r in starter]


def test_the_starter_rows_are_drawn_from_the_matching_row_kinds(starter):
    for r in starter:
        if r["selection_stratum"] == "real_warning":
            assert r["row_kind"] == "checker_warning" and r["warning_type"] in (
                "wrong_list", "unknown_category", "possible_lost_or", "possible_or_split", "uncovered_statement", "partial_quote")
        elif r["selection_stratum"] == "candidate_warning":
            assert r["warning_type"] in RP.CANDIDATE_PRIORITY
        else:
            assert r["row_kind"] in ("checker_silent_probe", "location_probe")


def test_the_candidate_stratum_draws_and_candidate_types_before_title_lines(starter):
    cand = [r for r in starter if r["selection_stratum"] == "candidate_warning"]
    types = [r["warning_type"] for r in cand]
    assert "ambiguous_heading_line" not in types or types.index("ambiguous_heading_line") > types.index("possible_bundled_and") \
        if "possible_bundled_and" in types else True
    assert all(t in RP.CANDIDATE_PRIORITY for t in types)


# ── the requested categories are present in the sheet ───────────────────────────────────────────────────────────────────

def test_uncovered_background_is_listed(sheet):
    assert any(r["warning_type"] == "uncovered_statement" and "About us" in r["jd_statement_exact"] for r in sheet)


def test_or_grouping_is_listed_as_a_lost_alternatives_warning_and_as_a_split_warning(sheet):
    lost = [r for r in sheet if r["warning_type"] == "possible_lost_or"]
    split = [r for r in sheet if r["warning_type"] == "possible_or_split"]
    assert any(r["case_id"] == "B04_en_preferred_only" and r["jd_statement_exact"] == "Previous work in a clinic or hotel." for r in lost)
    assert {r["case_id"] for r in split} == {"B02_en_data_analyst", "B06_en_injection"}


def test_location_routing_is_listed_as_probes_and_as_wrong_lists(sheet):
    assert sum(1 for r in sheet if r["row_kind"] == "location_probe") == 7
    wrong = [r for r in sheet if r["warning_type"] == "wrong_list"]
    assert {r["case_id"] for r in wrong} == {"B07_ar_accountant", "TC20"}


def test_arabic_conjunction_candidates_and_the_limitation_probe_are_listed(sheet):
    cands = [r for r in sheet if r["warning_type"] == "possible_attached_and"]
    # the eval v2-3 answer keeps 'مهارات التواصل والعمل ضمن فريق' as ONE item (the reference splits it): a real candidate
    assert {r["case_id"] for r in cands} == {"B08_ar_hr_specialist", "B12_ar_injection", "eval_ar_facilities_01"}
    probes = [r for r in sheet if r["warning_type"] == "attached_waw_limitation_probe"]
    assert probes and all(r["warning_text"].startswith("words starting with و:") for r in probes)


def test_ambiguous_heading_lines_are_listed_and_never_called_omissions(sheet):
    amb = [r for r in sheet if r["warning_type"] == "ambiguous_heading_line"]
    assert len(amb) == 40
    for r in amb:
        assert "may be a title or section label" in r["warning_text"]
        assert "not counted as an omission" in r["rationale"]
    headings = {r["jd_statement_exact"] for r in amb}
    for r in sheet:
        if r["warning_type"] == "uncovered_statement":
            assert r["jd_statement_exact"] not in headings                 # an uncovered heading-shaped line is never an omission row


# ── the mechanical facts the revisions depend on ─────────────────────────────────────────────────────────────────────────

def test_no_punctuation_only_partial_quote_is_listed_because_those_quotes_now_cover_their_statement(sheet):
    for r in sheet:
        if r["warning_type"] == "partial_quote":
            assert r["mechanical_note"] != "quote equals the statement without its terminal punctuation", r["review_id"]


def test_each_partial_quote_row_is_a_genuine_shortened_quotation_by_the_current_checker(sheet):
    for r in sheet:
        if r["warning_type"] == "partial_quote":
            stmt, quote = r["jd_statement_exact"], r["matched_source_text"]
            assert quote.rstrip(P.TERMINAL + " ") != stmt.rstrip(P.TERMINAL + " "), r["review_id"]


def test_each_location_probe_names_the_list_the_answer_actually_used(sheet):
    for r in sheet:
        if r["row_kind"] == "location_probe":
            assert r["mechanical_note"].startswith("placed in ")
            assert r["mechanical_note"].split("placed in ", 1)[1] in r["matched_item"]


def test_each_or_silent_probe_really_has_no_item_quoting_any_part_of_its_statement(sheet):
    # the probe was written only when no item overlapped the statement; the mechanical note states exactly that
    for r in sheet:
        if r["warning_type"] == "or_silent_probe":
            assert r["mechanical_note"] == "no item quotes any part"
            assert r["matched_item"] == "none"


# ── seeded results: separate, and the one remaining gap recorded ────────────────────────────────────────────────────────

def test_the_seeded_results_are_kept_in_their_own_file_with_their_own_columns(seeded):
    assert list(seeded[0].keys()) == ["seed_set", "language", "defect", "seed", "expected_flag", "flags_observed", "detected", "known_gap"]
    assert "review_id" not in seeded[0] and "reviewer_choice" not in seeded[0]
    assert all(r["seed_set"] == "reference (development)" for r in seeded)


def test_the_seeded_results_detect_every_no_gap_seed_and_record_exactly_one_known_gap(seeded):
    for r in seeded:
        if r["known_gap"] == "no":
            assert r["detected"] == "True", r["defect"] + " " + r["language"]
        else:
            assert r["detected"] == "False"
    gaps = [r["defect"] for r in seeded if r["known_gap"] == "yes"]
    assert gaps == ["attached_waw_before_verb_limitation"]


def test_the_seeded_or_split_and_bare_line_omitted_seeds_are_now_detected(seeded):
    by = {(r["language"], r["defect"]): r for r in seeded}
    assert by[("en", "or_split")]["detected"] == "True"
    assert by[("en", "unpunctuated_requirement_omitted")]["detected"] == "True"
    assert by[("en", "unpunctuated_requirement_omitted")]["known_gap"] == "no"


# ── the unconfirmed example judgments are kept, marked, and excluded; the verified S003 split is documented ─────────────────

UNCONFIRMED = PKG / "reviews" / "UNCONFIRMED_example_judgments_S001-S003.csv"


def test_the_blank_starter_is_unchanged_and_all_its_choices_are_blank(starter):
    assert all(r["reviewer_choice"] == "" for r in starter)


def test_the_example_judgments_are_kept_under_an_unconfirmed_name_and_status_excludes_them():
    assert UNCONFIRMED.exists()
    status = (PKG / "reviews" / "STATUS.md").read_text(encoding="utf-8")
    assert "UNCONFIRMED" in status and "Excluded from every metric" in status
    assert "example answers generated by ChatGPT" in status and "**No.**" in status
    assert not any(p.name.startswith("starter_v2_reviewer_submission") for p in (PKG / "reviews").iterdir())


def test_the_unconfirmed_file_holds_the_example_choices_and_matches_the_starter_rows():
    rows = _rows("reviews/UNCONFIRMED_example_judgments_S001-S003.csv")
    done = {r["review_id"]: (r["reviewer_choice"], r["reviewer_note"]) for r in rows if r["reviewer_choice"]}
    assert done == {"S001": ("false alarm", "optional company background"),
                    "S002": ("genuine defect", "should be one OR item"),
                    "S003": ("false alarm", "both parts were extracted separately")}
    starter_rows = {r["review_id"]: r for r in _rows("starter_review_sheet_v2.csv")}
    for r in rows:
        for key in ("case_id", "warning_type", "jd_statement_exact", "matched_item"):
            assert r[key] == starter_rows[r["review_id"]][key], (r["review_id"], key)


def test_the_verified_s003_split_is_flagged_as_partial_quote_by_the_current_checker():
    """Pins the current behaviour: two items together cover the statement, and the checker still raises partial_quote."""
    raw = [json.loads(x) for x in (RP.V1_SRC / "benchmark_v2-2_run1_calls.jsonl").read_text(encoding="utf-8").splitlines()]
    raw = [r for r in raw if r["case"] == "B07_ar_accountant"][0]["raw"]
    jd = json.loads((RP.BENCH / "B07_ar_accountant.json").read_text(encoding="utf-8"))["jd"]
    answer = json.loads(raw)
    salary = answer["non_scoreable_requirements"][0]
    location = answer["non_scoreable_requirements"][1]
    assert salary["category"] == "salary" and location["category"] == "location"
    assert salary["source_text"] == "الراتب بالريال السعودي" and location["source_text"] == "العمل في مدينة الرياض بدوام كامل"
    rep = P.validate(jd, raw)
    partial = [x for x in rep["review_queue"] if x["type"] == "partial_quote"]
    assert len(partial) == 1 and partial[0]["paths"] == ["non_scoreable_requirements[0]", "non_scoreable_requirements[1]"]


def test_the_findings_document_records_the_verified_s003_facts_and_no_judgment():
    text = (PKG / "FINDINGS.md").read_text(encoding="utf-8")
    assert "Together the two quotations cover the statement" in text
    assert "not confirmed" in text
