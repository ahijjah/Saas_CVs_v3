"""Offline reproduction and audit tests of the location-routing experiment (commit c55025d), and the per-run consolidation of every
stored experiment. Each run is read from its own stored files and reported separately; no run is pooled into another and no original
score is changed. No network, no paid call.
"""
import hashlib
import json
import pathlib

import pytest

import scripts.requirements_v2_candidate_audit as A
import scripts.requirements_v2_routing_experiment as X
import scripts.requirements_v2_scope_experiment as S
import scripts.requirements_v2_tc20_eval as ev
import scripts.requirements_v2_tc20_repeat_audit as RA

RUN = S.BACKEND / "tests" / "fixtures" / "requirements_v2_routing_experiment" / "run"
COMMIT = "c55025d29bdef1d4cb238c876c252d85f0f8df33"
FILES = {"calls.jsonl": "d221db006fcd32d6eaad8fd8142be860bc3712480cd0791e5e450b1371880bb9",
         "manifest.json": "e7b17cf008c22722f6ac7f440d0619c05d9caf0925fbcd94886b4b57e1dbad8f",
         "run.json": "538c5bd7c5ecc2b19a1e07f1e3527d4476d28a2e44c5443a2f6b86b54d951323",
         "scored.json": "29b4d8c21d471970c06a11c856d2865d3cb42559456ef249c3d7ca470f7e2098"}
SENTENCES = ("submit deliverables", "in coordination with the mone it focal point", "maintain regular communication")


def sha(b):
    return hashlib.sha256(b).hexdigest()


@pytest.fixture(scope="module")
def rows():
    return [json.loads(x) for x in (RUN / "calls.jsonl").read_text(encoding="utf-8").splitlines()]


@pytest.fixture(scope="module")
def scored():
    return json.loads((RUN / "scored.json").read_text(encoding="utf-8"))


def _whole_reporting(raw):
    return all(s in raw.lower() for s in SENTENCES)


# ── files, provenance, costs ─────────────────────────────────────────────────────────────────────────────────────────────

def test_the_four_stored_files_are_byte_identical_to_the_attachments():
    for name, digest in FILES.items():
        assert sha((RUN / name).read_bytes()) == digest, name


def test_the_manifest_identifies_commit_c55025d_the_protocol_and_both_arms():
    m = json.loads((RUN / "manifest.json").read_text(encoding="utf-8"))
    pins = m["pins"]
    assert pins["git_commit"] == COMMIT
    assert pins["protocol_sha256"] == X.PROTOCOL_SHA256 == "73fa404fbeff5edcac00a7da8dbce7283070482c17d222033306abc66625fff8"
    assert pins["identity"]["control_sha256"] == X.CONTROL_SHA256 and pins["identity"]["routed_sha256"] == X.ROUTED_SHA256
    assert pins["control_sha256"] == "21a2f9420c12e1a43579c6817b6a8601547d92b653cd432a693ef590b784b43b"   # frozen v2-3 control of the harness
    assert pins["identity"]["inserted_bytes"] == 197 and pins["identity"]["control_bytes"] == 30550 and pins["identity"]["routed_bytes"] == 30747
    assert pins["routing_script_sha256"] == "22b39be36afff2fb8635c6c2489dd5153d3713d36cf95b9101469328055f3ee6"
    assert pins["scope_script_sha256"] == "b95290af5a9b6d83d8c0763035bcf627e26a6b442bd71082bfd01853914e09ad"
    assert pins["user_message_sha256"] == X.USER_SHA256
    assert m["plan"]["reservation_usd"] == 1.65862 and m["plan"]["cap_usd"] == 2.0 and m["plan"]["call_count"] == 20


def test_the_pins_equal_the_current_files_of_this_repository():
    m = json.loads((RUN / "manifest.json").read_text(encoding="utf-8"))["pins"]
    assert sha(X.CONTROL.read_bytes()) == m["identity"]["control_sha256"] and sha(X.ROUTED.read_bytes()) == m["identity"]["routed_sha256"]
    assert sha(X.PROTOCOL.read_bytes()) == m["protocol_sha256"]
    assert sha(pathlib.Path(X.__file__).read_bytes()) == m["routing_script_sha256"]
    assert sha(pathlib.Path(S.__file__).read_bytes()) == m["scope_script_sha256"]


def test_there_are_twenty_rows_alternating_with_control_first(rows):
    assert [(r["order"], r["arm"], r["call"]) for r in rows] == \
        [(i + 1, "CONTROL" if i % 2 == 0 else "ROUTED", i // 2 + 1) for i in range(20)]


def test_every_row_has_its_arm_pin_the_user_hash_the_model_and_a_clean_finish(rows):
    pins = {"CONTROL": X.CONTROL_SHA256, "ROUTED": X.ROUTED_SHA256}
    for r in rows:
        assert r["input"]["system_sha256"] == pins[r["arm"]]
        assert r["input"]["user_sha256"] == X.USER_SHA256
        assert r["model"] == r["model_returned"] == "gpt-4.1-2025-04-14"
        assert r["finish_reason"] == "stop" and r["error"] is None


def test_each_cost_is_the_token_formula_and_the_spend_is_0_709932(rows):
    for r in rows:
        u = r["usage"]
        assert round(u["prompt_tokens"] * 2e-6 + u["completion_tokens"] * 8e-6, 6) == r["cost_usd"]
    assert round(sum(r["cost_usd"] for r in rows), 6) == 0.709932
    assert round(sum(r["cost_usd"] for r in rows if r["arm"] == "CONTROL"), 6) == 0.361516
    assert round(sum(r["cost_usd"] for r in rows if r["arm"] == "ROUTED"), 6) == 0.348416


def test_run_json_equals_the_run_record_in_scored_json(scored):
    run = json.loads((RUN / "run.json").read_text(encoding="utf-8"))
    assert run == scored["run"] == {"spent_usd": 0.709932, "calls_attempted": 20, "successful_calls": 20, "stopped": None}


def test_the_scored_report_is_reproduced_exactly_by_the_unchanged_scorer(rows, scored):
    jd, labels = ev.load_inputs()
    assert X.report(rows, labels, jd) == scored["report"]


# ── reporting against the whole raw answer ───────────────────────────────────────────────────────────────────────────────

def test_reporting_is_complete_in_six_control_and_three_routed_answers_and_never_partial(rows):
    for arm, want in (("CONTROL", 6), ("ROUTED", 3)):
        counts = [sum(1 for s in SENTENCES if s in r["raw"].lower()) for r in rows if r["arm"] == arm]
        assert sum(1 for c in counts if c == 3) == want, arm
        assert all(c in (0, 3) for c in counts), arm                       # all three or none, never partial


def test_the_whole_answer_reporting_check_agrees_with_the_scored_flags_in_all_twenty_calls(rows, scored):
    for r, sc in zip(rows, scored["report"]["per_call"]):
        assert _whole_reporting(r["raw"]) == sc["primary_retained_and_routed"] == sc["reporting_statement_in_whole_answer"], r["order"]


def test_every_present_reporting_sentence_is_routed_as_reporting_line(rows):
    for r in rows:
        if not _whole_reporting(r["raw"]):
            continue
        o = json.loads(r["raw"])
        info = [c for c in o["informational_items"] if c["category"] == "reporting_line"]
        for s in SENTENCES:
            assert any(s in c["source_text"].lower() for c in info), (r["order"], s)


# ── location in all twenty calls ─────────────────────────────────────────────────────────────────────────────────────────

def test_location_is_correctly_routed_in_all_twenty_calls(rows):
    for r in rows:
        f = X.location_facts(r["raw"])
        assert f["location_retained"] and f["location_correctly_routed"] and f["location_in_non_scoreable"], r["order"]
        assert f["location_elsewhere"] == 0, r["order"]
        o = json.loads(r["raw"])
        assert any("MoNE office in Ramallah" in c["source_text"] for c in o["non_scoreable_requirements"] if c["category"] == "location")


def test_no_location_condition_appears_in_informational_or_post_hiring_in_any_call(rows):
    for r in rows:
        o = json.loads(r["raw"])
        assert not [c for c in o["informational_items"] + o["post_hiring_conditions"] if c["category"] == "location"], r["order"]


# ── the other groups ─────────────────────────────────────────────────────────────────────────────────────────────────────

def test_duties_are_complete_in_every_call_and_the_frozen_checks_match_the_stored_counts(scored):
    per = scored["report"]["per_call"]
    assert all(c["duty_lines_found"] == 16 for c in per)
    fresh = scored["report"]["fresh_arms"]
    assert fresh["CONTROL"]["checks_passed"] == {"C_RESP": 10, "C_COMP": 10, "C_EXP_OR": 0, "C_FAM_OR": 0, "C_LOCAL": 10,
                                                 "C_LOCATION": 10, "C_REPORTING": 6, "C_AND": 0}
    assert fresh["ROUTED"]["checks_passed"] == {"C_RESP": 10, "C_COMP": 10, "C_EXP_OR": 0, "C_FAM_OR": 0, "C_LOCAL": 10,
                                                "C_LOCATION": 10, "C_REPORTING": 3, "C_AND": 0}


def test_or_and_and_split_fail_in_both_arms(scored):
    for c in scored["report"]["per_call"]:
        assert c["checks"]["C_AND"] is False
        assert c["checks"]["C_EXP_OR"] is False and c["checks"]["C_FAM_OR"] is False


def test_schema_and_invented_sources_are_clean_in_every_call(rows):
    jd, _ = ev.load_inputs()
    for r in rows:
        assert S.schema_violations(r["raw"]) == [] and S.invented_sources(r["raw"], jd) == 0


def test_the_reporting_difference_is_reported_as_a_guard_regression_and_location_does_not_offset_it(scored):
    d = scored["report"]["direct_comparison"]
    assert d["location_correctly_routed_difference_ROUTED_minus_CONTROL"] == 0
    assert d["guards"]["reporting_complete"] == {"CONTROL": 6, "ROUTED": 3, "regressed": True}
    assert d["primary_reading"].startswith("regression in a guarded group (reporting_complete)")


# ── historical runs: each read from its own stored file, never pooled ────────────────────────────────────────────────────

def _run_facts(rows, arm):
    jd, labels = ev.load_inputs()
    out = []
    for r in rows:
        if r["arm"] != arm:
            continue
        f = RA._facts(r["raw"], labels, jd)
        out.append({"rep": _whole_reporting(r["raw"]), "loc": X.location_facts(r["raw"])["location_correctly_routed"],
                    "duty": f["duty_lines_found"] == 16, "exp_or": f["checks"]["C_EXP_OR"], "fam_or": f["checks"]["C_FAM_OR"],
                    "and": f["checks"]["C_AND"]})
    return out


def _stored(name):
    base = S.BACKEND / "tests" / "fixtures"
    if name == "paired":
        return [r for r in (json.loads(x) for x in (A.EVIDENCE / "calls.jsonl").read_text(encoding="utf-8").splitlines()) if r["jd"] == "tc20"]
    if name == "repeat":
        return [json.loads(x) for x in (RA.RUN / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    if name == "ex5":
        return [json.loads(x) for x in (base / "requirements_v2_ex5_experiment" / "run" / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    if name == "scope":
        return [json.loads(x) for x in (base / "requirements_v2_scope_experiment" / "run" / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    return [json.loads(x) for x in (base / "requirements_v2_routing_experiment" / "run" / "calls.jsonl").read_text(encoding="utf-8").splitlines()]


def _count(facts, key):
    return sum(1 for f in facts if f[key])


PER_RUN = {  # (run, arm): (calls, reporting complete, location, duties 16, C_EXP_OR, C_FAM_OR, C_AND)
    ("paired 1e73231", "v2-3"): (5, 4, 4, 4, 0, 0, 0),
    ("paired 1e73231", "v2-5"): (5, 1, 5, 5, 0, 0, 0),
    ("repeat 23cce8e", "v2-3"): (5, 5, 5, 4, 4, 2, 0),
    ("repeat 23cce8e", "v2-5"): (5, 0, 5, 5, 0, 0, 0),
    ("ex5 4ea4323", "FULL"): (5, 4, 5, 5, 1, 1, 0),
    ("ex5 4ea4323", "VARIANT"): (5, 0, 5, 5, 1, 0, 0),
    ("scope 22efcc0", "FULL"): (5, 1, 5, 5, 0, 0, 0),
    ("scope 22efcc0", "CLARIFIED"): (5, 5, 3, 5, 1, 1, 0),
    ("routing c55025d", "CONTROL"): (10, 6, 10, 10, 0, 0, 0),
    ("routing c55025d", "ROUTED"): (10, 3, 10, 10, 0, 0, 0),
}


def _runs_by_name():
    return {"paired 1e73231": _stored("paired"), "repeat 23cce8e": _stored("repeat"), "ex5 4ea4323": _stored("ex5"),
            "scope 22efcc0": _stored("scope"), "routing c55025d": _stored("routing")}


@pytest.mark.parametrize("key", sorted(PER_RUN))
def test_each_historical_run_reproduces_its_own_counts_from_its_stored_file(key):
    run, arm = key
    rows = _runs_by_name()[run]
    facts = _run_facts(rows, arm)
    calls, rep, loc, duty, exp_or, fam_or, and_ = PER_RUN[key]
    assert len(facts) == calls
    assert (_count(facts, "rep"), _count(facts, "loc"), _count(facts, "duty"), _count(facts, "exp_or"),
            _count(facts, "fam_or"), _count(facts, "and")) == (rep, loc, duty, exp_or, fam_or, and_)


def test_the_same_prompt_bytes_gave_different_results_in_different_runs():
    """FULL (v2-5, f1569a8b) reporting: 1 of 5, 0 of 5, 4 of 5, 1 of 5 in four runs; CLARIFIED (266329fc) location: 3 of 5 in one run,
    10 of 10 in another. These are descriptive, not pooled, and they bound how far any one batch can be read."""
    assert PER_RUN[("paired 1e73231", "v2-5")][1] == 1
    assert PER_RUN[("repeat 23cce8e", "v2-5")][1] == 0
    assert PER_RUN[("ex5 4ea4323", "FULL")][1] == 4
    assert PER_RUN[("scope 22efcc0", "FULL")][1] == 1
    assert PER_RUN[("scope 22efcc0", "CLARIFIED")][2] == 3
    assert PER_RUN[("routing c55025d", "CONTROL")][2] == 10


def test_across_all_five_batches_exactly_one_reporting_answer_is_partial_and_it_is_the_paired_control():
    """The whole-answer check is never partial in the routing batch. Across all sixty stored calls there is one partial answer: the
    paired v2-3 call 1, with only 'maintain regular communication'. The frozen C_REPORTING is false there."""
    partial = []
    for name, rows in _runs_by_name().items():
        for r in rows:
            c = sum(1 for s in SENTENCES if s in r["raw"].lower())
            if 0 < c < 3:
                partial.append((name, r["arm"], r["call"], c))
    assert partial == [("paired 1e73231", "v2-3", 1, 1)]
    assert sum(len(rows) for rows in _runs_by_name().values()) == 60
