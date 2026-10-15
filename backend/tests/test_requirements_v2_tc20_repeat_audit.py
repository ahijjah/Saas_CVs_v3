"""Offline tests for the audit of the TC20 repeat.

(1) Static claims about the v2-3 to v2-5 diff, on the prompt text. (2) The four stored repeat files: bytes, pins, rows, spend,
the report reproduced in full by the unchanged scorer, and the facts kept separate by run. No network, no paid call.
"""
import hashlib
import json
import pathlib

import pytest

import scripts.requirements_v2_candidate_eval as E
import scripts.requirements_v2_tc20_eval as ev
import scripts.requirements_v2_tc20_repeat as R
import scripts.requirements_v2_tc20_repeat_audit as RA

BACKEND = pathlib.Path(__file__).resolve().parents[1]
V23 = (BACKEND / "prompt_candidates" / "criteria_extraction_v2-3" / "criteria_extraction_v2-3.txt").read_text(encoding="utf-8")
V25 = (BACKEND / "prompt_candidates" / "criteria_extraction_v2-5" / "criteria_extraction_v2-5.txt").read_text(encoding="utf-8")
RUN = BACKEND / "tests" / "fixtures" / "requirements_v2_tc20_repeat" / "run"


def section(t, a, z):
    return t[t.index(a):t.index(z)]


# ── 1. static claims about the diff ──────────────────────────────────────────────────────────────────────────────────

def test_rule_9_and_the_output_contract_are_byte_identical_between_v23_and_v25():
    assert section(V23, "9. Not scoreable", "10. Do not output") == section(V25, "9. Not scoreable", "10. Do not output")
    assert section(V23, "OUTPUT FORMAT", "\nRULES\n") == section(V25, "OUTPUT FORMAT", "\nRULES\n")


def test_the_or_sentence_is_unchanged_and_the_complete_alternatives_paragraph_is_appended():
    assert V23.count('OR: when the job description accepts any ONE of several options') == 1
    assert V25.count('OR: when the job description accepts any ONE of several options') == 1
    assert "Complete alternatives:" not in V23 and V25.count("Complete alternatives:") == 1


def test_the_coverage_paragraph_does_not_name_reporting():
    cov = section(V25, "COVERAGE.", "1. Extract only")
    assert "reporting" not in cov.lower() and "informational" not in cov.lower()


def test_example_5_is_the_only_new_worked_example_with_an_informational_reporting_item():
    ex5 = V25[V25.index("Example 5 (EN"):]
    assert '"category":"reporting_line"' in ex5
    assert V23.count('"category":"reporting_line"') == 0
    assert V25[:V25.index("Example 5 (EN")].count('"category":"reporting_line"') == 0


def test_the_bundled_tc20_phrase_is_in_neither_prompt_so_it_is_not_copied_from_an_example():
    assert "Strong communication, coordination" not in V23 and "Strong communication, coordination" not in V25


def test_the_communication_and_teamwork_split_form_appears_only_in_v25():
    assert "Communication and teamwork" not in V23
    assert V25.count("Communication and teamwork skills") >= 2     # the counting line and Example 5


def test_the_changes_are_the_documented_ones_and_nothing_else():
    import difflib
    removed = [l for l in difflib.ndiff(V23.splitlines(), V25.splitlines()) if l.startswith("- ")]
    assert [l.split(":")[0].strip("- ").strip() for l in removed] == ["skills", "soft_skills"]


def test_the_prompt_grew_and_the_example_block_is_3222_characters():
    assert len(V23) == 23810 and len(V25) == 29055
    assert len(V25[V25.index("Example 5 (EN"):]) == 3222


# ── 2. the repeat files: byte identity, reproduction, and the facts (kept separate by run) ───────────────────────────


PINNED = {
    "calls.jsonl": "1c3c9bb12c0c47480dbd015694b9766e11ab0287fd449d605d52c1b19265d9ed",
    "manifest.json": "512536540b4b5c4e20f9e425d80b39ea00ff9319df4585512be941625094e3ba",
    "run.json": "3177650783e65ffbedb8868db1054d2c97311167b179fc2731a0d74bb22a3527",
    "scored.json": "2a3a240885d1d1f477ca8a053c9e0b4047186a019d449497f6903ce64fd50edb",
}


@pytest.fixture(scope="module")
def rows():
    return [json.loads(x) for x in (RUN / "calls.jsonl").read_text(encoding="utf-8").splitlines()]


@pytest.fixture(scope="module")
def analysis():
    return json.loads((RUN / "analysis.json").read_text(encoding="utf-8"))


def test_the_four_stored_files_are_the_attached_bytes():
    for name, digest in PINNED.items():
        assert hashlib.sha256((RUN / name).read_bytes()).hexdigest() == digest, name


def test_the_manifest_pins_equal_the_files_used_now_and_the_run_commit():
    m = json.loads((RUN / "manifest.json").read_text(encoding="utf-8"))
    assert m["pins"]["git_commit"] == "23cce8e96ce32b88fc462204f4b942a7cf7defba"
    assert m["protocol_sha256"] == R.PROTOCOL_SHA256 == m["pins"]["protocol_sha256"]
    assert m["pins"]["repeat_script_sha256"] == hashlib.sha256(pathlib.Path(R.__file__).read_bytes()).hexdigest()
    assert m["pins"]["candidate_sha256"] == E.CANDIDATE_SHA256 and m["pins"]["control_sha256"] == E.CONTROL_SHA256
    assert m["pins"]["labels_sha256"] == hashlib.sha256(ev.LABELS.read_bytes()).hexdigest()


def test_every_row_matches_its_inputs_model_finish_and_cost(rows):
    unit = R.tc20_unit()
    for r in rows:
        msgs = E.messages(r["arm"], unit)
        assert r["input"]["system_sha256"] == E.sha256_bytes(msgs[0]["content"].encode())
        assert r["input"]["user_sha256"] == R.USER_SHA256
        assert r["model_returned"] == r["model"] == E.MODEL and r["finish_reason"] == "stop" and r["error"] is None
        assert r["cost_usd"] == pytest.approx(r["usage"]["prompt_tokens"] * 2e-6 + r["usage"]["completion_tokens"] * 8e-6, abs=2e-6)
    assert [(r["order"], r["arm"], r["call"]) for r in rows] == [(c["order"], c["arm"], c["call"]) for c in R.call_order()]


def test_the_spend_and_the_run_summary_match_the_rows(rows):
    run = json.loads((RUN / "run.json").read_text(encoding="utf-8"))
    assert run == {"spent_usd": 0.391792, "calls_attempted": 10, "successful_calls": 10, "stopped": None}
    assert sum(r["cost_usd"] for r in rows) == pytest.approx(0.391792, abs=2e-5)


def test_the_frozen_scorer_reproduces_scored_json_in_full(rows):
    jd, labels = ev.load_inputs()
    scored = json.loads((RUN / "scored.json").read_text(encoding="utf-8"))
    assert R.report(rows, labels, jd) == scored["report"]


def test_the_analysis_file_is_reproducible(rows, analysis):
    jd, labels = ev.load_inputs()
    assert RA.analysis(rows, labels, jd) == analysis


def test_the_two_runs_stay_separate_and_the_repeat_matches_its_own_report(analysis):
    assert set(analysis["runs_kept_separate"]) == {"paired_run", "repeat"}
    assert analysis["runs_kept_separate"]["repeat"]["source"].endswith("tests/fixtures/requirements_v2_tc20_repeat/run")


def test_the_reporting_statement_is_missing_from_the_whole_answer_in_nine_of_ten_v25_calls(analysis):
    rep = analysis["runs_kept_separate"]["repeat"]["arms"]
    assert rep["v2-5"]["reporting_missing_whole_answer"] == 5 and rep["v2-3"]["reporting_missing_whole_answer"] == 0
    prev = analysis["runs_kept_separate"]["paired_run"]["arms"]
    assert prev["v2-5"]["reporting_missing_whole_answer"] == 4 and prev["v2-3"]["reporting_missing_whole_answer"] == 1
    assert analysis["pooled_for_description_only"]["v2-5"]["reporting_missing_whole_answer"] == 9
    assert analysis["pooled_for_description_only"]["v2-3"]["reporting_missing_whole_answer"] == 1


def test_no_v25_repeat_call_contains_any_of_the_three_reporting_sentences(analysis):
    for c in analysis["runs_kept_separate"]["repeat"]["arms"]["v2-5"]["calls"]:
        assert not any(c["reporting_sentences_in_whole_answer"].values()), c["call"]
        assert c["informational_items"] == 0


def test_the_omission_is_not_a_misrouting_of_the_reporting_sentence(rows):
    jd, labels = ev.load_inputs()
    for r in rows:
        if r["arm"] != "v2-5":
            continue
        o = json.loads(r["raw"])
        text = json.dumps(o, ensure_ascii=False)
        assert "deliverables" not in text and "Technical Resident Advisor" not in text, r["order"]


def test_the_or_alternatives_are_lost_in_every_v25_call_of_both_runs_and_kept_in_some_v23_calls(analysis):
    rep = analysis["runs_kept_separate"]
    assert rep["repeat"]["arms"]["v2-5"]["ict_alternatives_kept"] == 0 and rep["paired_run"]["arms"]["v2-5"]["ict_alternatives_kept"] == 0
    assert rep["repeat"]["arms"]["v2-3"]["ict_alternatives_kept"] == 4 and rep["paired_run"]["arms"]["v2-3"]["ict_alternatives_kept"] == 0
    assert rep["repeat"]["arms"]["v2-3"]["familiarity_alternatives_kept"] == 2
    assert rep["repeat"]["arms"]["v2-5"]["familiarity_alternatives_kept"] == 0


def test_the_communication_line_is_never_split_in_any_call_of_either_run(analysis):
    for run in ("paired_run", "repeat"):
        for arm in ("v2-3", "v2-5"):
            assert analysis["runs_kept_separate"][run]["arms"][arm]["communication_split_calls"] == 0


def test_the_communication_line_moves_to_soft_skills_in_v25_but_stays_one_item(analysis):
    for c in analysis["runs_kept_separate"]["repeat"]["arms"]["v2-5"]["calls"]:
        assert c["communication_line_categories"] == ["soft_skills"]
    for c in analysis["runs_kept_separate"]["repeat"]["arms"]["v2-3"]["calls"]:
        assert c["communication_line_categories"] == ["skills"]


def test_the_duty_omission_is_a_whole_answer_omission_in_the_control_only(analysis):
    rep = analysis["runs_kept_separate"]["repeat"]["arms"]
    assert rep["v2-3"]["duty_zero_calls"] == 1 and rep["v2-5"]["duty_zero_calls"] == 0
    zero = [c for c in rep["v2-3"]["calls"] if c["duty_lines_found"] == 0]
    assert [c["call"] for c in zero] == [5]


def test_the_reading_rules_of_the_protocol_hold_on_the_repeat(rows):
    jd, labels = ev.load_inputs()
    rd = R.report(rows, labels, jd)["reading_rules"]
    assert rd["duty_completeness_reproduced"] is True
    assert rd["reporting_regression_reproduced"] is True
    assert rd["reporting_missing_now"] == {"v2-3": 0, "v2-5": 5}
