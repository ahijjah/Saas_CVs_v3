"""Replay of the STORED TC20 answers. Coverage and the frozen OR/AND checks are reported separately; the stored files are never changed."""
import hashlib
import json

import pytest

import replay as R

JD, LABELS = R.load_jd()
DUTIES = set(LABELS["expected_values"]["duty_lines"])
REPORT = json.loads((R.RESULTS / "replay.json").read_text(encoding="utf-8"))


def rows(source):
    return {(p["arm"], p["call"]): p for p in REPORT["sources"][source]}


def test_the_stored_answers_are_unchanged_by_the_replay():
    before = {k: hashlib.sha256(p.read_bytes()).hexdigest() for k, p in R.SOURCES.items()}
    R.main()
    after = {k: hashlib.sha256(p.read_bytes()).hexdigest() for k, p in R.SOURCES.items()}
    assert before == after


def test_the_sixteen_duty_omission_is_found_in_the_repeat_call_that_lost_them():
    p = rows("repeat_gpt-4.1")[("v2-3", 5)]
    assert set(p["duty_omissions"]) == DUTIES
    assert p["coverage"]["omitted"] == 16 and len(p["duty_omissions"]) == 16


def test_the_other_repeat_calls_account_for_every_duty_line():
    for key, p in rows("repeat_gpt-4.1").items():
        if key != ("v2-3", 5):
            assert p["duty_omissions"] == [], key


def test_every_audit_call_omits_all_sixteen_duty_lines_as_the_frozen_check_says():
    for key, p in rows("audit_gpt-4o-mini").items():
        assert set(p["duty_omissions"]) == DUTIES, key
        assert p["frozen_checks"]["C_RESP"] is False, key


def test_coverage_is_reported_separately_from_or_and_checks():
    p = rows("repeat_gpt-4.1")[("v2-3", 1)]
    assert p["coverage"]["covered"] == 34 and p["coverage"]["partial"] == 1, "coverage: one partial reporting paragraph"
    assert p["frozen_checks"]["C_AND"] is False, "the OR/AND check fails here and is reported on its own, not as coverage"


def test_a_non_duty_omission_in_one_call_is_reported_and_not_hidden():
    p = rows("repeat_gpt-4.1")[("v2-5", 1)]
    assert p["coverage"]["omitted"] == 1
    assert any("perform his/her duties" in t for t in p["omitted_units"])
    assert p["frozen_checks"]["C_REPORTING"] is False


def test_a_paraphrased_reporting_quote_is_not_credited_so_its_paragraph_is_partial():
    p = rows("audit_gpt-4o-mini")[("v2-4", 1)]
    assert p["quote_counts"]["not_verbatim"] >= 1
    assert any("Ramallah" in t for t in p["partial_units"])


def test_no_stored_answer_is_invalid_json():
    for per in REPORT["sources"].values():
        assert all(not p["invalid_json"] for p in per)


def test_the_preview_lists_every_requirement_unit_once_and_is_not_sent():
    import preview as P
    jd, _ = R.load_jd()
    import inventory as I
    inv = I.build_inventory(jd, json.loads(R.ANNOTATION.read_text(encoding="utf-8")))
    text = P.render(jd, inv)
    for u in inv.requirements():
        assert text.count(f"[{u.unit_id}] ") == 1, u.unit_id
    assert "unit_decisions" in text and "Do not paraphrase" in text
    assert "Responsibilities" in text and "context" in text.lower()
