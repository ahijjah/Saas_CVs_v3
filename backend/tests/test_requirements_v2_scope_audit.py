"""Offline reproduction and audit tests of the scope-clarification experiment (commit 22efcc0): the stored files byte for byte,
the manifest's pins and identity, the costs, the exact reproduction of scored.json with the unchanged scorer, the routing of the
reporting sentences and of the location statement in every raw answer, and the OR, AND, duty, schema and source facts.
Historical runs are read only from their own stored files and are never pooled with the fresh arms. No network, no paid call.
"""
import hashlib
import json
import pathlib

import pytest

import scripts.requirements_v2_candidate_eval as E
import scripts.requirements_v2_scope_experiment as S
import scripts.requirements_v2_tc20_eval as ev
import scripts.requirements_v2_tc20_repeat_audit as RA
import scripts.requirements_v2_candidate_audit as A

RUN = S.BACKEND / "tests" / "fixtures" / "requirements_v2_scope_experiment" / "run"
EX5_RUN = S.BACKEND / "tests" / "fixtures" / "requirements_v2_ex5_experiment" / "run"
COMMIT = "22efcc0132014f5efdccaf24cedd155bbb0502cc"
FILES = {"calls.jsonl": "6cda6885348189bcb8c8afceaa94a763e96cac014b18061870443dc5e7e376a3",
         "manifest.json": "c688df6e3f6596cb589dd6328279b07e5182af8b3968cab1f909dc9f686fee9f",
         "run.json": "980a117036e0da257a2b9ea031a9e77ff9dd31f50e1f5f2bc227babd3c0af090",
         "scored.json": "8017082df622a891c60508e8b5c82a02033b6808667ce295e57ddf92859131ac"}


def sha(b):
    return hashlib.sha256(b).hexdigest()


@pytest.fixture(scope="module")
def rows():
    return [json.loads(x) for x in (RUN / "calls.jsonl").read_text(encoding="utf-8").splitlines()]


@pytest.fixture(scope="module")
def answers(rows):
    return [(r, json.loads(r["raw"])) for r in rows]


@pytest.fixture(scope="module")
def manifest():
    return json.loads((RUN / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def scored():
    return json.loads((RUN / "scored.json").read_text(encoding="utf-8"))


def items(o):
    return [(c, it) for c, lst in o["categories"].items() for it in lst]


# ── files and provenance ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_four_stored_files_are_byte_identical_to_the_attachments():
    for name, digest in FILES.items():
        assert sha((RUN / name).read_bytes()) == digest, name


def test_the_manifest_identifies_commit_22efcc0_the_protocol_the_script_and_both_arms(manifest):
    pins = manifest["pins"]
    assert pins["git_commit"] == COMMIT
    assert pins["protocol_sha256"] == S.PROTOCOL_SHA256 == "5210b3edecffa2d7844ae09f52352375f02ad043cdb53aba3114519365091482"
    assert pins["scope_script_sha256"] == "b95290af5a9b6d83d8c0763035bcf627e26a6b442bd71082bfd01853914e09ad"
    assert pins["identity"]["full_sha256"] == S.FULL_SHA256 and pins["identity"]["clarified_sha256"] == S.CLARIFIED_SHA256
    assert pins["identity"]["old_sentence_sha256"] == S.OLD_SHA256 and pins["identity"]["new_sentence_sha256"] == S.NEW_SHA256
    assert pins["identity"]["full_bytes"] == 30430 and pins["identity"]["clarified_bytes"] == 30550
    assert pins["user_message_sha256"] == S.USER_SHA256
    assert manifest["plan"]["reservation_usd"] == 0.82772 and manifest["plan"]["cap_usd"] == 1.0


def test_the_pins_equal_the_current_files_of_this_repository(manifest):
    pins = manifest["pins"]
    assert sha(S.FULL.read_bytes()) == pins["candidate_sha256"]
    assert sha(S.CLARIFIED.read_bytes()) == pins["identity"]["clarified_sha256"]
    assert sha(S.PROTOCOL.read_bytes()) == pins["protocol_sha256"]
    assert sha(pathlib.Path(S.__file__).read_bytes()) == pins["scope_script_sha256"]
    assert sha(pathlib.Path(E.__file__).read_bytes()) == pins["script_sha256"] == "999a94a25d5da14880b2c0b818a280bc29b94053c5f7fc95b23ed0926ec89c6b"
    assert sha(ev.LABELS.read_bytes()) == pins["labels_sha256"] == "6a87da80e142640af1327c4a3fd5a21adb648726a9733a699fa8d3ec67135ef9"
    assert sha(pathlib.Path(ev.__file__).read_bytes()) == pins["tc20_eval_script_sha256"] == \
        "1c40def472760b9b79b6878cfe3f3186080786d07668d4eadbfe7ae0f5825129"


def test_the_manifest_lists_the_expected_outputs_and_the_no_retry_policy(manifest):
    assert manifest["expected_outputs"] == ["manifest.json", "calls.jsonl", "run.json", "scored.json"]
    assert "no retries" in manifest["policy"]


# ── calls and costs ──────────────────────────────────────────────────────────────────────────────────────────────────────

def test_there_are_ten_rows_alternating_with_full_first(rows):
    assert [(r["order"], r["arm"], r["call"]) for r in rows] == \
        [(i + 1, "FULL" if i % 2 == 0 else "CLARIFIED", i // 2 + 1) for i in range(10)]


def test_every_row_has_the_arm_pin_the_user_hash_the_model_and_a_clean_finish(rows):
    pins = {"FULL": S.FULL_SHA256, "CLARIFIED": S.CLARIFIED_SHA256}
    for r in rows:
        assert r["input"]["system_sha256"] == pins[r["arm"]]
        assert r["input"]["user_sha256"] == S.USER_SHA256
        assert r["model"] == r["model_returned"] == "gpt-4.1-2025-04-14"
        assert r["finish_reason"] == "stop" and r["error"] is None


def test_each_cost_is_the_token_formula_and_the_spend_is_0_354424(rows):
    for r in rows:
        u = r["usage"]
        assert round(u["prompt_tokens"] * 2e-6 + u["completion_tokens"] * 8e-6, 6) == r["cost_usd"]
    assert round(sum(r["cost_usd"] for r in rows), 6) == 0.354424
    assert round(sum(r["cost_usd"] for r in rows if r["arm"] == "FULL"), 6) == 0.172542
    assert round(sum(r["cost_usd"] for r in rows if r["arm"] == "CLARIFIED"), 6) == 0.181882


def test_run_json_equals_the_run_record_in_scored_json(scored):
    run = json.loads((RUN / "run.json").read_text(encoding="utf-8"))
    assert run == scored["run"] == {"spent_usd": 0.354424, "calls_attempted": 10, "successful_calls": 10, "stopped": None}


def test_the_scored_report_is_reproduced_exactly_by_the_unchanged_scorer(rows, scored):
    jd, labels = ev.load_inputs()
    assert S.report(rows, labels, jd) == scored["report"]


# ── reporting: retention and routing ─────────────────────────────────────────────────────────────────────────────────────

SENTENCES = ("submit deliverables", "in coordination with the MoNE IT focal point", "maintain regular communication")


def test_the_three_reporting_sentences_are_in_every_clarified_answer_and_in_one_full_answer(answers):
    whole = {(r["arm"], r["call"]): all(s in r["raw"] for s in SENTENCES) for r, _ in answers}
    assert [whole[("CLARIFIED", n)] for n in range(1, 6)] == [True] * 5
    assert [whole[("FULL", n)] for n in range(1, 6)] == [False, False, True, False, False]


def test_every_reporting_sentence_present_is_routed_as_reporting_line(answers):
    for r, o in answers:
        if not all(s in r["raw"] for s in SENTENCES):
            continue
        info = [c for c in o["informational_items"] if c["category"] == "reporting_line"]
        for s in SENTENCES:
            assert any(s in c["source_text"] for c in info), (r["order"], s)


def test_reporting_is_absent_from_the_whole_answer_when_it_is_missing(answers):
    for r, o in answers:
        if all(s in r["raw"] for s in SENTENCES):
            continue
        for s in SENTENCES:
            assert s not in r["raw"], (r["order"], s)
        assert all(c["category"] != "reporting_line" for c in o["informational_items"])


def test_the_two_reporting_items_of_a_three_sentence_answer_merge_two_statements_into_one(answers):
    """Shared by FULL call 3 and all five CLARIFIED answers: one reporting item holds three sentences (rule 9: each statement goes
    in one list; rule 10: restated statements stay separate). It is a source defect of the same kind in both arms."""
    merged = {}
    for r, o in answers:
        if not all(s in r["raw"] for s in SENTENCES):
            continue
        rl = [c for c in o["informational_items"] if c["category"] == "reporting_line"]
        merged[r["order"]] = sorted(c["source_text"].count(". ") + 1 for c in rl)
    assert merged == {2: [1, 3], 4: [1, 3], 5: [1, 3], 6: [1, 3], 8: [1, 3], 10: [1, 3]}


# ── location: the regression in CLARIFIED calls 2 and 5 ──────────────────────────────────────────────────────────────────

LOCATION_TEXT = "work at the MoNE office in Ramallah and as needed"


def location_routes(o):
    return {"non_scoreable": [c for c in o["non_scoreable_requirements"] if c["category"] == "location"],
            "informational": [c for c in o["informational_items"] if c["category"] == "location"]}


def test_location_is_in_non_scoreable_requirements_in_all_five_full_answers_and_in_three_of_five_clarified(answers):
    nonscore = {r["order"]: bool(location_routes(o)["non_scoreable"]) for r, o in answers}
    assert [nonscore[n] for n in (1, 3, 5, 7, 9)] == [True] * 5
    assert [nonscore[n] for n in (2, 4, 6, 8, 10)] == [True, False, True, True, False]


def test_clarified_calls_2_and_5_put_location_in_informational_items_with_non_scoreable_empty(answers):
    for n in (4, 10):                                    # CLARIFIED call 2 (order 4) and call 5 (order 10)
        r, o = answers[n - 1]
        assert o["non_scoreable_requirements"] == []
        info = location_routes(o)["informational"]
        assert len(info) == 1 and LOCATION_TEXT in info[0]["source_text"]


def test_the_misrouted_location_items_still_say_in_their_reason_that_location_is_not_scoreable(answers):
    for n in (4, 10):
        _, o = answers[n - 1]
        reason = location_routes(o)["informational"][0]["reason"]
        assert reason == "work location is not a scoreable requirement"


def test_the_misrouted_location_sits_under_the_reporting_heading_of_the_job_description():
    jd, _ = ev.load_inputs()
    i = jd.index("Reporting and Supervision")
    j = jd.index(LOCATION_TEXT)
    assert 0 < j - i < 120                               # the location sentence is the first line under the heading


def test_the_location_sentence_is_in_the_job_description_verbatim(answers):
    jd, _ = ev.load_inputs()
    for r, o in answers:
        for c in o["non_scoreable_requirements"] + o["informational_items"]:
            if c["category"] == "location":
                assert S.squash(c["source_text"]) in S.squash(jd)


# ── duties, OR, AND, importance, schema, invented content ───────────────────────────────────────────────────────────────

def test_duty_coverage_is_sixteen_of_sixteen_in_every_answer(scored):
    assert [c["duty_lines_found"] for c in scored["report"]["per_call"]] == [16] * 10


def test_the_communication_line_is_one_item_in_every_answer_so_and_splitting_never_happens(answers):
    for _, o in answers:
        comm = [it for _, it in items(o) if "Strong communication, coordination, and teamwork skills" == it["text"]]
        assert len(comm) == 1 and comm[0]["alternatives"] is None


def test_the_or_alternatives_are_kept_only_in_clarified_call_5(answers, scored):
    kept = {}
    for r, o in answers:
        kept[r["order"]] = {"ict": any(it["alternatives"] and "ICT systems support" in it["source_text"] for _, it in items(o)),
                            "fam": any(it["alternatives"] and it["source_text"].startswith("Familiarity") for _, it in items(o))}
    assert [n for n, v in kept.items() if v["ict"]] == [10]
    assert [n for n, v in kept.items() if v["fam"]] == [10]
    assert scored["report"]["fresh_arms"]["FULL"]["checks_passed"]["C_EXP_OR"] == 0
    assert scored["report"]["fresh_arms"]["CLARIFIED"]["checks_passed"]["C_EXP_OR"] == 1


def test_the_frozen_and_check_fails_in_all_ten_answers(scored):
    assert [c["checks"]["C_AND"] for c in scored["report"]["per_call"]] == [False] * 10
    assert scored["report"]["fresh_arms"]["FULL"]["checks_passed"]["C_AND"] == 0
    assert scored["report"]["fresh_arms"]["CLARIFIED"]["checks_passed"]["C_AND"] == 0


def test_importance_and_its_cue_agree_in_every_item(answers):
    jd, _ = ev.load_inputs()
    jd_s = S.squash(jd)
    for _, o in answers:
        for _, it in items(o):
            assert (it["importance"] == "preferred") == (it["importance_cue"] is not None)
            if it["importance_cue"]:
                assert S.squash(it["importance_cue"]) in jd_s


def test_no_source_text_is_invented_and_every_answer_conforms_to_the_schema(answers):
    jd, _ = ev.load_inputs()
    for r, _ in answers:
        assert S.schema_violations(r["raw"]) == []
        assert S.invented_sources(r["raw"], jd) == 0


def test_the_warnings_are_empty_in_every_answer(answers):
    assert all(o["warnings"] == [] for _, o in answers)


def test_the_experience_field_of_the_stated_experience_advantage_is_null_in_nine_answers_and_an_object_in_one(answers):
    lines = {}
    for r, o in answers:
        it = [it for it in o["categories"]["experience"] if it["text"].startswith("Experience in supporting")][0]
        lines[r["order"]] = it["experience"] is None
    assert [n for n, is_null in lines.items() if not is_null] == [10]


def test_category_weights_vary_across_answers_and_are_not_scored(answers):
    education = {o["category_weights"]["education"] for _, o in answers}
    domain = {o["category_weights"]["domain_knowledge"] for _, o in answers}
    assert education == {10, 15, 20} and domain == {0, 5, 10}


# ── reading, and the historical runs kept apart ──────────────────────────────────────────────────────────────────────────

def test_the_primary_reading_of_this_run_is_the_supporting_band_by_the_protocol_rule(scored):
    fresh = scored["report"]["fresh_arms"]
    assert fresh["FULL"]["primary_retained_and_routed"] == 1 and fresh["CLARIFIED"]["primary_retained_and_routed"] == 5
    assert scored["report"]["direct_comparison"]["primary_difference_CLARIFIED_minus_FULL"] == 4
    assert S.reading(1, 5).startswith("supports the clarification helping on this JD")
    assert "not proof of causation" in S.reading(1, 5)


def test_the_location_regression_is_a_separate_group_and_is_not_netted_against_the_primary_gain(scored):
    d = scored["report"]["direct_comparison"]["checks_passed_difference_CLARIFIED_minus_FULL"]
    assert d["C_LOCATION"] == -2 and d["C_REPORTING"] == 4
    assert scored["report"]["fresh_arms"]["FULL"]["checks_passed"]["C_LOCATION"] == 5
    assert scored["report"]["fresh_arms"]["CLARIFIED"]["checks_passed"]["C_LOCATION"] == 3


def test_the_historical_runs_are_read_from_their_own_files_and_match_the_context_section(scored):
    paired = [json.loads(x) for x in (A.EVIDENCE / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    paired = [r for r in paired if r["jd"] == "tc20"]
    repeat = [json.loads(x) for x in (RA.RUN / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    ex5 = [json.loads(x) for x in (EX5_RUN / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    jd, labels = ev.load_inputs()
    hist = scored["report"]["historical_context_not_pooled"]

    def whole(rows, arm):
        return sum(1 for r in rows if r["arm"] == arm and all(s in r["raw"] for s in SENTENCES))
    assert hist["paired_run_v2-5"]["primary_retained_and_routed"] == whole(paired, "v2-5") == 1
    assert hist["repeat_v2-5"]["primary_retained_and_routed"] == whole(repeat, "v2-5") == 0
    assert hist["ex5_experiment_FULL"]["primary_retained_and_routed"] == whole(ex5, "FULL") == 4
