"""Offline tests of the scope-clarification experiment: the variant's byte identity (one sentence changed, nothing else), its diff,
the protocol pin and rules, the exact dry-run reservation, the refusals, the protections (with a fake client only), the primary
outcome's routing check on stored answers, the structure checks, and the reading rule. No network and no paid call.
"""
import hashlib
import json
import pathlib
import socket

import pytest

import scripts.requirements_v2_scope_experiment as S
import scripts.requirements_v2_tc20_compare as C
import scripts.requirements_v2_tc20_eval as ev
import scripts.requirements_v2_candidate_eval as E

FIXTURES = S.BACKEND / "tests" / "fixtures" / "requirements_v2_scope_experiment"
EX5_RUN = S.BACKEND / "tests" / "fixtures" / "requirements_v2_ex5_experiment" / "run"
FULL_BYTES = S.FULL.read_bytes()
VAR_BYTES = S.CLARIFIED.read_bytes()


def sha(b):
    return hashlib.sha256(b).hexdigest()


def _ex5_rows():
    return [json.loads(x) for x in (EX5_RUN / "calls.jsonl").read_text(encoding="utf-8").splitlines()]


# ── the variant: byte identity and diff ─────────────────────────────────────────────────────────────────────────────────

def test_the_variant_and_full_are_pinned_and_the_sentences_are_pinned():
    assert sha(FULL_BYTES) == S.FULL_SHA256 == "f1569a8b400257db20b1c0b24fd7728605fe472eef7fff993a384f12f0cbc8db"
    assert sha(VAR_BYTES) == S.CLARIFIED_SHA256 == "266329fc57c34a00362ea6f77984641e42d6900cd5181adf083941ca8d61103e"
    assert sha(S.OLD_SENTENCE) == S.OLD_SHA256 and sha(S.NEW_SENTENCE) == S.NEW_SHA256
    assert S.verify_identity()["clarified_bytes"] == len(VAR_BYTES)


def test_only_the_obligation_sentence_differs_and_every_other_byte_is_identical():
    assert FULL_BYTES.count(S.OLD_SENTENCE) == 1
    cut = FULL_BYTES.index(S.OLD_SENTENCE)
    end = cut + len(S.OLD_SENTENCE)
    assert VAR_BYTES[:cut] == FULL_BYTES[:cut]
    assert VAR_BYTES[cut + len(S.NEW_SENTENCE):] == FULL_BYTES[end:]
    assert VAR_BYTES == FULL_BYTES[:cut] + S.NEW_SENTENCE + FULL_BYTES[end:]


def test_example_5_rule_9_and_the_output_contract_are_byte_identical():
    f, v = FULL_BYTES.decode("utf-8"), VAR_BYTES.decode("utf-8")

    def block(text, start, end):
        return text[text.index(start):] if end is None else text[text.index(start):text.index(end)]
    assert block(f, "\nExample 5 (EN", None) == block(v, "\nExample 5 (EN", None)           # Example 5 to the end of the file
    assert block(f, "9. Not scoreable", "10. Do not output") == block(v, "9. Not scoreable", "10. Do not output")   # rule 9
    assert block(f, "OUTPUT FORMAT", "\nRULES") == block(v, "OUTPUT FORMAT", "\nRULES")  # the output contract
    assert block(f, "TASK", "OUTPUT FORMAT") == block(v, "TASK", "OUTPUT FORMAT")
    assert block(f, "\n1. Extract only", "\nExample 5 (EN") == block(v, "\n1. Extract only", "\nExample 5 (EN")  # rules 1 to 13


def test_the_removed_and_replacing_text_is_general_and_contains_the_three_named_categories():
    s = S.NEW_SENTENCE.decode("utf-8")
    for phrase in ("non-scoreable requirements", "post-hiring conditions", "informational statements", "rule 9", "three lists"):
        assert phrase in s
    for tc20_word in ("Technical Coordinator", "IPSD", "MoNE", "ABRS", "submit deliverables", "Project Director"):
        assert tc20_word not in s and tc20_word not in VAR_BYTES.decode("utf-8")


def test_the_diff_patch_contains_exactly_one_removed_and_one_added_line_and_both_texts():
    patch = (S.CLARIFIED.parent / "DIFF.patch").read_text(encoding="utf-8").splitlines()
    removed = [l for l in patch if l.startswith("-") and not l.startswith("---")]
    added = [l for l in patch if l.startswith("+") and not l.startswith("+++")]
    assert len(removed) == 1 and len(added) == 1
    assert removed[0][1:] == [l for l in FULL_BYTES.decode("utf-8").splitlines() if "COVERAGE." in l][0]
    assert added[0][1:] == [l for l in VAR_BYTES.decode("utf-8").splitlines() if "COVERAGE." in l][0]


def test_the_variant_is_not_referenced_by_the_production_code():
    assert S.CLARIFIED.parent.name == "criteria_extraction_v2-5-coverage"
    prod = (S.BACKEND / "services").rglob("*.py")
    assert not any("criteria_extraction_v2-5-coverage" in p.read_text(encoding="utf-8") for p in prod)


# ── the protocol: pinned before the calls, and its rules ────────────────────────────────────────────────────────────────

def test_the_protocol_is_the_pinned_one_and_states_the_rules_and_the_arms():
    assert sha(S.PROTOCOL.read_bytes()) == S.PROTOCOL_SHA256 == "5210b3edecffa2d7844ae09f52352375f02ad043cdb53aba3114519365091482"
    text = S.PROTOCOL.read_text(encoding="utf-8")
    for needle in ("Primary outcome", "D = P(CLARIFIED) − P(FULL)", "D ≥ 3", "−2 ≤ D ≤ 2", "D ≤ −3",
                   "**not** proof", "Historical runs", "stay inactive", "gpt-4.1-2025-04-14", "Temperature 0.1",
                   "max_tokens 6000", "JSON-object", "timeout 90", "USD 1.00", "No retries"):
        assert needle in text, needle
    assert "266329fc" in text and "64f3f06c" in text and "1ef27d14" in text


def test_the_protocol_mentions_all_the_groups_that_must_be_reported_separately():
    text = S.PROTOCOL.read_text(encoding="utf-8")
    for group in ("eight frozen TC20 checks", "Duty coverage", "OR alternatives", "AND splitting", "Schema", "Invented content"):
        assert group in text, group


def test_a_changed_protocol_refuses_to_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "PROTOCOL_SHA256", "0" * 64)
    with pytest.raises(S.ExperimentError, match="protocol"):
        S.plan()


# ── dry run: exact reservation, no network ───────────────────────────────────────────────────────────────────────────────

def test_the_reservation_is_the_exact_sum_of_the_ten_worst_cases():
    p = S.plan()
    assert p["call_count"] == 10 and [c["arm"] for c in p["calls"]] == ["FULL", "CLARIFIED"] * 5
    assert p["per_arm_worst_case_usd"] == {"FULL": 0.082712, "CLARIFIED": 0.082832}
    assert p["reservation_usd"] == 0.82772 and p["cap_usd"] == 1.0
    assert p["temperature"] == 0.1 and p["max_tokens"] == 6000 and p["timeout_s"] == 90 and p["retries"] == 0
    assert p["response_format"] == "json_object" and p["model"] == "gpt-4.1-2025-04-14"


def test_the_pins_record_every_hash_the_manifest_needs():
    pins = S.plan()["pins"]
    for key in ("candidate_sha256", "labels_sha256", "tc20_eval_script_sha256", "compare_script_sha256", "git_commit",
                "scope_script_sha256", "protocol_sha256", "user_message_sha256", "eval_set_manifest_sha256", "control_sha256"):
        assert key in pins, key
    assert pins["user_message_sha256"] == S.USER_SHA256 and pins["labels_sha256"] == "6a87da80e142640af1327c4a3fd5a21adb648726a9733a699fa8d3ec67135ef9"
    assert pins["identity"]["clarified_sha256"] == S.CLARIFIED_SHA256


def test_the_cap_refuses_a_plan_whose_worst_case_is_above_it():
    with pytest.raises(S.ExperimentError, match="exceeds the cap"):
        S.plan(cap=0.5)


def test_the_dry_run_prints_the_plan_and_makes_no_network_call(monkeypatch, capsys):
    def no_network(*a, **k):
        raise AssertionError("network access in a dry run")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(C, "openai_call_model", no_network)
    assert S.main(["--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reservation_usd"] == 0.82772 and out["cap_usd"] == 1.0 and out["mode"] == "dry-run"
    assert len(out["plan"]["calls"]) == 10


def test_execute_needs_an_output_directory_and_a_key(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        S.main(["--execute"])
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    with pytest.raises(SystemExit):
        S.main(["--execute"])


def test_a_non_empty_output_directory_is_refused(tmp_path):
    (tmp_path / "old.txt").write_text("evidence", encoding="utf-8")
    with pytest.raises(E.EvalError):
        S.run(tmp_path, lambda m: {})
    assert (tmp_path / "old.txt").read_text(encoding="utf-8") == "evidence"


# ── the run loop with a fake client only ─────────────────────────────────────────────────────────────────────────────────

def _fake_from_ex5(rows):
    by_order = {r["order"]: r for r in rows}
    calls = []

    def api(msgs):
        calls.append(msgs)
        r = by_order[len(calls)]
        return {"raw": r["raw"], "finish_reason": "stop", "model": S.MODEL, "usage": r["usage"]}
    return api, calls


def test_a_full_fake_run_writes_manifest_first_and_scores_the_routing_check(tmp_path):
    api, calls = _fake_from_ex5(_ex5_rows())
    out = tmp_path / "run"
    res = S.run(out, api)
    assert res["calls_attempted"] == 10 and res["successful_calls"] == 10 and res["stopped"] is None
    assert len(calls) == 10 and (out / "manifest.json").exists()
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["pins"]["protocol_sha256"] == S.PROTOCOL_SHA256
    rows = [json.loads(x) for x in (out / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["arm"] for r in rows] == ["FULL", "CLARIFIED"] * 5
    assert all(r["input"]["system_sha256"] == (S.FULL_SHA256 if r["arm"] == "FULL" else S.CLARIFIED_SHA256) for r in rows)


def test_a_model_mismatch_stops_the_run_after_the_first_unexpected_model(tmp_path):
    ex5 = _ex5_rows()
    seen = []

    def api(msgs):
        seen.append(1)
        return {"raw": ex5[0]["raw"], "finish_reason": "stop", "model": "gpt-4.1-other", "usage": ex5[0]["usage"]}
    res = S.run(tmp_path / "m", api)
    assert res["stopped"] == "error:ModelMismatch" and len(seen) == 1
    rows = [json.loads(x) for x in (tmp_path / "m" / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["error"]["kind"] == "ModelMismatch"


def test_an_exception_stops_the_run_without_retry_and_is_recorded(tmp_path):
    seen = []

    def api(msgs):
        seen.append(1)
        if len(seen) == 3:
            raise RuntimeError("simulated failure")
        return {"raw": "{}", "finish_reason": "stop", "model": S.MODEL, "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    res = S.run(tmp_path / "e", api)
    assert len(seen) == 3 and res["stopped"].startswith("error:") and res["calls_attempted"] == 3
    rows = [json.loads(x) for x in (tmp_path / "e" / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 3 and rows[-1]["error"] is not None


# ── the primary outcome on stored answers ────────────────────────────────────────────────────────────────────────────────

def test_the_routing_check_gives_four_of_five_for_the_stored_full_arm_and_zero_for_the_stored_variant():
    jd, labels = ev.load_inputs()
    rows = _ex5_rows()
    prim = {"FULL": 0, "VARIANT": 0}
    for r in rows:
        prim[r["arm"]] += int(S.facts(r["raw"], labels, jd)["primary_retained_and_routed"])
    assert prim == {"FULL": 4, "VARIANT": 0}


def test_the_routing_check_fails_when_a_sentence_is_present_but_not_routed_as_reporting_line():
    raw = json.dumps({"informational_items": [
        {"text": "a", "category": "benefits", "reason": "x", "source_text": "submit deliverables to the office"},
        {"text": "b", "category": "reporting_line", "reason": "x", "source_text": "maintain regular communication with the team"},
        {"text": "c", "category": "reporting_line", "reason": "x", "source_text": "in coordination with the MoNE IT focal point"}]})
    routed = S.routed_reporting(raw)
    assert routed == {"submit deliverables": False, "in coordination with the MoNE IT focal point": True,
                      "maintain regular communication": True}


def test_the_structure_checks_pass_on_stored_answers_and_catch_a_broken_one():
    jd, _ = ev.load_inputs()
    for r in _ex5_rows():
        assert S.schema_violations(r["raw"]) == []
        assert S.invented_sources(r["raw"], jd) == 0
    assert S.schema_violations("not json") == ["not_json"]
    o = json.loads(_ex5_rows()[0]["raw"])
    del o["warnings"]
    assert "top_level_keys" in S.schema_violations(json.dumps(o))
    o = json.loads(_ex5_rows()[0]["raw"])
    o["informational_items"].append({"text": "x", "category": "made_up_label", "reason": "r", "source_text": "x"})
    assert "condition_category" in S.schema_violations(json.dumps(o))


def test_an_invented_source_text_is_counted():
    jd, _ = ev.load_inputs()
    o = json.loads(_ex5_rows()[0]["raw"])
    o["informational_items"].append({"text": "x", "category": "benefits", "reason": "r", "source_text": "a sentence not in the job description"})
    assert S.invented_sources(json.dumps(o), jd) == 1


# ── reading rule and reporting ───────────────────────────────────────────────────────────────────────────────────────────

def test_the_reading_rule_matches_the_protocol_thresholds():
    assert S.reading(0, 3).startswith("supports") and "not proof of causation" in S.reading(0, 3)
    assert S.reading(0, 5).startswith("supports")
    assert S.reading(0, 2).startswith("inconclusive") and S.reading(2, 3).startswith("inconclusive")
    assert S.reading(4, 4).startswith("inconclusive") and S.reading(4, 2).startswith("inconclusive")
    assert S.reading(3, 0).startswith("the clarification did not help")


def test_a_partial_report_gives_no_reading_and_historical_runs_are_kept_separate():
    jd, labels = ev.load_inputs()
    rows = [dict(r, error=None) for r in _ex5_rows()]
    for r in rows:                                                  # the stored VARIANT answers are the CLARIFIED arm in this test
        r["arm"] = {"FULL": "FULL", "VARIANT": "CLARIFIED"}[r["arm"]]
    part = S.report([r for r in rows if r["arm"] == "FULL"], labels, jd)
    assert part["direct_comparison"]["primary_reading"] is None and part["status"].startswith("partial")
    full = S.report(rows, labels, jd)
    assert full["status"] == "scored" and full["direct_comparison"]["primary_reading"] is not None
    assert full["direct_comparison"]["primary_difference_CLARIFIED_minus_FULL"] == -4
    assert full["direct_comparison"]["primary_reading"].startswith("the clarification did not help")
    assert set(full["historical_context_not_pooled"]) == {"paired_run_v2-5", "repeat_v2-5", "ex5_experiment_FULL"}
