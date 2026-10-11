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


EXP = BACKEND / "tests" / "fixtures" / "requirements_v2_ex5_experiment" / "run"
EXP_ROWS = [json.loads(x) for x in (EXP / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
EXP_MANIFEST = json.loads((EXP / "manifest.json").read_text(encoding="utf-8"))
EXP_RUN = json.loads((EXP / "run.json").read_text(encoding="utf-8"))
EXP_SCORED = json.loads((EXP / "scored.json").read_text(encoding="utf-8"))


def test_the_experiment_files_are_stored_and_match_their_hashes():
    expected = {"calls.jsonl": "2cff7b1a333262897ec8402119883b0fa3821cdb045175bc94cf2f2727a1262e",
                "manifest.json": "246e67d4b8542632e9c187365d1e7d25cfabc7001f71e15fe05b0eafaee70bcc",
                "run.json": "2dab3f683d63f5fe6036415b164624694795bbbe22b9bdb0c4e11fb5e996a1d9",
                "scored.json": "b09f593dc2968f5964d3d6e036efb2afdea744ceb3ed332a86bb160432385a34"}
    for name, digest in expected.items():
        assert sha((EXP / name).read_bytes()) == digest, name


def test_the_experiment_manifest_identifies_commit_4ea4323_protocol_5a741243_and_both_arms():
    pins = EXP_MANIFEST["pins"]
    assert pins["git_commit"] == "4ea4323b206f6e32307c902d72d65e5c2a1bbff9"
    assert pins["protocol_sha256"] == "5a7412438a42cdc50f4e41b847031584a1c1a0de34438eeea274650abad853be"
    protocol = BACKEND / "tests" / "fixtures" / "requirements_v2_ex5_experiment" / "PROTOCOL.md"
    assert sha(protocol.read_bytes()) == pins["protocol_sha256"]
    assert pins["identity"] == {"full_sha256": "f1569a8b400257db20b1c0b24fd7728605fe472eef7fff993a384f12f0cbc8db",
                                "variant_sha256": "7cf171c84d65ce74b2531c2ca1f3049c7e539774121c0faae347dc7bf0466e62",
                                "removed_sha256": "33bdd1dcfc0dd3fc761ea719e773ab1bd8db3909950e68cbded19fe8b99e31e6",
                                "removed_bytes": 3223, "variant_bytes": 27207, "full_bytes": 30430}
    assert pins["user_message_sha256"] == "ff6b334ad32300a05933cf96284c6c253d86ed9442ffcac37a8b543a8c240473"
    assert EXP_MANIFEST["plan"]["reservation_usd"] == 0.81101 and EXP_MANIFEST["plan"]["cap_usd"] == 1.0


def test_the_experiment_has_ten_rows_alternating_with_full_first_and_the_pinned_hashes():
    assert [(r["order"], r["arm"], r["call"]) for r in EXP_ROWS] == \
        [(i + 1, "FULL" if i % 2 == 0 else "VARIANT", i // 2 + 1) for i in range(10)]
    pins = EXP_MANIFEST["pins"]
    system_pin = {"FULL": pins["identity"]["full_sha256"], "VARIANT": pins["identity"]["variant_sha256"]}
    for r in EXP_ROWS:
        assert r["error"] is None and r["finish_reason"] == "stop"
        assert r["model"] == "gpt-4.1-2025-04-14" and r["model_returned"] == "gpt-4.1-2025-04-14"
        assert r["input"]["system_sha256"] == system_pin[r["arm"]]
        assert r["input"]["user_sha256"] == pins["user_message_sha256"]


def test_the_experiment_costs_sum_to_the_stated_spend_and_the_run_record_agrees():
    assert round(sum(r["cost_usd"] for r in EXP_ROWS), 6) == 0.412 == EXP_RUN["spent_usd"]
    assert round(sum(r["cost_usd"] for r in EXP_ROWS if r["arm"] == "FULL"), 6) == 0.214806
    assert round(sum(r["cost_usd"] for r in EXP_ROWS if r["arm"] == "VARIANT"), 6) == 0.197194
    assert EXP_RUN == {"spent_usd": 0.412, "calls_attempted": 10, "successful_calls": 10, "stopped": None}
    assert EXP_SCORED["run"] == EXP_RUN


def test_the_saved_report_is_reproduced_exactly_by_the_unchanged_scorer():
    import scripts.requirements_v2_ex5_experiment as X
    import scripts.requirements_v2_tc20_eval as ev
    jd, labels = ev.load_inputs()
    assert X.report(EXP_ROWS, labels, jd) == EXP_SCORED["report"]


def test_the_whole_answer_reporting_check_agrees_with_the_scored_flags_and_gives_four_and_zero():
    sentences = ["submit deliverables", "in coordination with the mone it focal point", "maintain regular communication"]
    flags = {c["order"]: c for c in EXP_SCORED["report"]["per_call"]}
    counts = {"FULL": 0, "VARIANT": 0}
    for r in EXP_ROWS:
        found = [s in r["raw"].lower() for s in sentences]
        assert all(found) or not any(found), r["order"]                     # all three or none, never a partial set
        assert all(found) == flags[r["order"]]["reporting_statement_in_whole_answer"], r["order"]
        counts[r["arm"]] += int(all(found))
    assert counts == {"FULL": 4, "VARIANT": 0}


def test_duty_coverage_is_sixteen_of_sixteen_in_every_experiment_call():
    per_call = EXP_SCORED["report"]["per_call"]
    assert len(per_call) == 10
    assert all(c["duty_lines_found"] == 16 for c in per_call)


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
    assert "superseded by `AUDIT_EX5.md`" in text
    assert "4ea4323b206f6e32307c902d72d65e5c2a1bbff9" in text and "5a741243" in text
    assert "not verified" not in text and "are **not** the experiment's files" not in text
