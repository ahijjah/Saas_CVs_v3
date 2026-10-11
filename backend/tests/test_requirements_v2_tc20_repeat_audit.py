"""Offline tests for the audit of the TC20 repeat.

Two groups. (1) Static claims about the v2-3 to v2-5 diff, checked on the prompt text (no run needed); these run now.
(2) Reproduction of the four repeat files, byte for byte and against the unchanged scorer. The files were not attached to the
audit request, so these tests skip with that reason until they are stored under tests/fixtures/requirements_v2_tc20_repeat/run/.
"""
import json
import pathlib

import pytest

import scripts.requirements_v2_tc20_eval as ev
import scripts.requirements_v2_tc20_repeat as R

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


# ── 2. the repeat files: reproduction (skipped until the files are stored) ───────────────────────────────────────────

def _files_present():
    return all((RUN / n).exists() for n in ("calls.jsonl", "manifest.json", "run.json", "scored.json"))


needs_files = pytest.mark.skipif(not _files_present(), reason="the four repeat files were not attached and are not stored yet")


@needs_files
def test_the_stored_repeat_files_are_the_attached_bytes():
    import hashlib
    expected = json.loads((RUN / "MANIFEST_OF_FILES.json").read_text(encoding="utf-8"))
    for name, digest in expected.items():
        assert hashlib.sha256((RUN / name).read_bytes()).hexdigest() == digest, name


@needs_files
def test_the_frozen_scorer_reproduces_the_saved_report():
    rows = [json.loads(x) for x in (RUN / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    scored = json.loads((RUN / "scored.json").read_text(encoding="utf-8"))
    jd, labels = ev.load_inputs()
    rep = R.report(rows, labels, jd)
    assert rep["per_call"] and scored["report"]["per_call"] == rep["per_call"]
