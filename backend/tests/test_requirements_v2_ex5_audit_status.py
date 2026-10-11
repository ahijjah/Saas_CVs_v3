"""Offline checks for the Example 5 experiment audit status: the attached files, the quotations used in the recommendation, and the
stored evidence for each run. No network, no paid call, nothing from the experiment's calls is asserted."""
import hashlib
import json
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
V25 = (BACKEND / "prompt_candidates" / "criteria_extraction_v2-5" / "criteria_extraction_v2-5.txt").read_text(encoding="utf-8")
V23 = (BACKEND / "prompt_candidates" / "criteria_extraction_v2-3" / "criteria_extraction_v2-3.txt").read_text(encoding="utf-8")
REPEAT = BACKEND / "tests" / "fixtures" / "requirements_v2_tc20_repeat" / "run"
PAIRED = BACKEND / "tests" / "fixtures" / "requirements_v2_candidate_v25" / "audit" / "run_v2-3_v2-5_20261010"
STATUS = BACKEND / "tests" / "fixtures" / "requirements_v2_ex5_experiment" / "audit" / "AUDIT_STATUS.md"

COVERAGE_OBLIGATION = ("Every requirement, duty and competency line found in a section becomes an item (rules 4 to 8) or a condition (rule 9).")
RULE9_LEAD = 'Each statement goes in exactly ONE of three lists.'
RULE9_INFO = "informational_items: company_description (also the employer's own opening hours or size), benefits"
RULE9_POST = "post_hiring_conditions: background_check (also police or criminal-record checks), reference_check (also references), medical_check (a health examination required of the candidate), document_submission."


def sha(b):
    return hashlib.sha256(b).hexdigest()


def test_the_attached_files_equal_the_stored_repeat_files_not_the_experiment():
    attached = {"calls.jsonl": "1c3c9bb12c0c47480dbd015694b9766e11ab0287fd449d605d52c1b19265d9ed",
                "manifest.json": "512536540b4b5c4e20f9e425d80b39ea00ff9319df4585512be941625094e3ba",
                "run.json": "3177650783e65ffbedb8868db1054d2c97311167b179fc2731a0d74bb22a3527",
                "scored.json": "2a3a240885d1d1f477ca8a053c9e0b4047186a019d449497f6903ce64fd50edb"}
    for name, digest in attached.items():
        assert sha((REPEAT / name).read_bytes()) == digest, name
    m = json.loads((REPEAT / "manifest.json").read_text(encoding="utf-8"))
    assert m["protocol_sha256"] == "860eed9e7cbb3d9982f0d7999309f11874878961b62813de71ca56dd5393c0fe"
    assert m["pins"]["git_commit"] == "23cce8e96ce32b88fc462204f4b942a7cf7defba"
    assert m["protocol_sha256"] != "5a7412438a42cdc50f4e41b847031584a1c1a0de34438eeea274650abad853be"


def test_the_experiment_protocol_is_not_among_the_stored_evidence():
    assert not (BACKEND / "tests" / "fixtures" / "requirements_v2_ex5_experiment" / "run").exists()


def test_the_coverage_obligation_is_quoted_exactly_and_names_no_statement():
    assert COVERAGE_OBLIGATION in V25
    assert "statement" not in V25[V25.index("COVERAGE."):V25.index("1. Extract only")].lower()
    assert "reporting" not in V25[V25.index("COVERAGE."):V25.index("1. Extract only")].lower()


def test_the_coverage_walk_says_each_sentence_while_the_obligation_names_three_kinds_of_line():
    cov = V25[V25.index("COVERAGE."):V25.index("1. Extract only")]
    assert "each heading, each list and each sentence" in cov
    assert "requirement, duty and competency line" in cov


def test_rule_9_quantifies_over_every_statement_and_is_the_same_in_both_arms():
    assert RULE9_LEAD in V25 and RULE9_LEAD in V23
    r9 = lambda t: t[t.index("9. Not scoreable"):t.index("10. Do not output")]
    assert r9(V25) == r9(V23)


def test_rule_9_lists_reporting_line_without_a_definition_and_document_submission_under_post_hiring():
    assert RULE9_INFO in V25 and ", reporting_line, hr_statement" in V25
    assert RULE9_POST in V25
    assert V25.count("reporting_line") == 3 and V23.count("reporting_line") == 2   # rule 9, the enum, and (v2-5 only) Example 5


def test_the_tc20_reporting_sentence_contains_the_word_that_rule_9_uses_for_a_post_hiring_label():
    jd = json.loads((BACKEND / "tests" / "fixtures" / "requirements_v2_technical_coordinator_20" / "evidence.json").read_text(encoding="utf-8"))["description"]
    sentence = "The Coordinator shall submit deliverables to the IPSD II Technical Resident Advisor and the IPSD II Project Director for review and approval."
    assert sentence in jd
    assert "submit deliverables" in sentence and "document_submission" in RULE9_POST


def test_the_stored_reporting_counts_by_run_are_the_ones_the_status_states():
    paired = [json.loads(x) for x in (PAIRED / "calls.jsonl").read_text(encoding="utf-8").splitlines() if json.loads(x)["jd"] == "tc20"]
    repeat = [json.loads(x) for x in (REPEAT / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    marker = "submit deliverables"
    present = lambda rows, arm: sum(1 for r in rows if r["arm"] == arm and marker in r["raw"])
    assert (present(paired, "v2-3"), present(paired, "v2-5")) == (4, 1)
    assert (present(repeat, "v2-3"), present(repeat, "v2-5")) == (5, 0)


def test_the_status_document_states_the_unverified_experiment_numbers_as_unverified():
    text = STATUS.read_text(encoding="utf-8")
    assert "not verified" in text and "are **not** the experiment's files" in text
    assert "5a7412438a42cdc50f4e41b847031584a1c1a0de34438eeea274650abad853be" in text
