"""Offline tests of the location-routing experiment: the byte identity of the routed prompt (one rule 9 sentence added, nothing else),
the protocol pin and rules, the exact dry-run reservation under the 2.00 cap, the refusals, the run loop with a fake client only,
the location and guard logic, and the reading rule. No network and no paid call.
"""
import hashlib
import json
import pathlib
import socket

import pytest

import scripts.requirements_v2_routing_experiment as X
import scripts.requirements_v2_scope_experiment as S
import scripts.requirements_v2_candidate_eval as E
import scripts.requirements_v2_tc20_compare as C
import scripts.requirements_v2_tc20_eval as ev

RUN_SCOPE = S.BACKEND / "tests" / "fixtures" / "requirements_v2_scope_experiment" / "run"
CONTROL_BYTES = X.CONTROL.read_bytes()
ROUTED_BYTES = X.ROUTED.read_bytes()


def sha(b):
    return hashlib.sha256(b).hexdigest()


def _scope_rows():
    return [json.loads(x) for x in (RUN_SCOPE / "calls.jsonl").read_text(encoding="utf-8").splitlines()]


# ── the routed prompt: byte identity and diff ────────────────────────────────────────────────────────────────────────────

def test_the_arms_and_the_inserted_span_are_pinned():
    assert sha(CONTROL_BYTES) == X.CONTROL_SHA256 == "266329fc57c34a00362ea6f77984641e42d6900cd5181adf083941ca8d61103e"
    assert sha(ROUTED_BYTES) == X.ROUTED_SHA256 == "f5042e34777a45c4e55f686c1d0e1bccb5fd8cea0e8adf69a85204ecd5fe56d5"
    assert sha(X.INSERTED) == X.INSERTED_SHA256 and len(X.INSERTED) == 197
    assert X.verify_identity()["routed_bytes"] == len(ROUTED_BYTES) == 30747


def test_routed_is_control_with_exactly_the_inserted_span_after_the_rule_9_anchor():
    cut = CONTROL_BYTES.index(X.ANCHOR) + len(X.ANCHOR)
    assert CONTROL_BYTES.count(X.ANCHOR) == 1
    assert ROUTED_BYTES == CONTROL_BYTES[:cut] + X.INSERTED + CONTROL_BYTES[cut:]


def test_the_only_added_text_is_one_rule_9_sentence_and_the_coverage_clarification_is_unchanged():
    text = ROUTED_BYTES.decode("utf-8")
    sentence = "A statement whose category is location goes in non_scoreable_requirements regardless of the job description heading it appears under, and never in informational_items or post_hiring_conditions."
    assert text.count(sentence) == 1
    assert text.index(sentence) > text.index("9. Not scoreable") and text.index(sentence) < text.index("10. Do not output")
    assert S.NEW_SENTENCE.decode("utf-8") in text                       # the COVERAGE clarification from the scope variant
    assert ROUTED_BYTES.decode("utf-8").replace("\n   " + sentence, "") == CONTROL_BYTES.decode("utf-8")


def test_the_or_and_and_example_text_is_byte_identical_in_both_arms():
    c, r = CONTROL_BYTES.decode("utf-8"), ROUTED_BYTES.decode("utf-8")
    for start, end in (("6. Experience requirements", "7. Responsibilities"), ("\nExample 5 (EN", None)):
        cs = c[c.index(start):] if end is None else c[c.index(start):c.index(end)]
        rs = r[r.index(start):] if end is None else r[r.index(start):r.index(end)]
        assert cs == rs, start
    assert "alternatives" in c and c.count("alternatives") == r.count("alternatives")


def test_the_routed_sentence_names_no_tc20_vocabulary():
    sentence = X.INSERTED.decode("utf-8")
    for word in ("Technical Coordinator", "MoNE", "IPSD", "ABRS", "Ramallah", "Reporting and Supervision"):
        assert word not in sentence


def test_the_diff_patch_contains_one_added_line_and_no_removed_line():
    patch = (X.ROUTED.parent / "DIFF.patch").read_text(encoding="utf-8").splitlines()
    assert [l for l in patch if l.startswith("-") and not l.startswith("---")] == []
    added = [l for l in patch if l.startswith("+") and not l.startswith("+++")]
    assert len(added) == 1 and added[0][1:].strip() == X.INSERTED.decode("utf-8").strip()


def test_the_routed_variant_is_not_referenced_by_production_code():
    prod = (X.BACKEND / "services").rglob("*.py")
    assert not any("criteria_extraction_v2-5-routing" in p.read_text(encoding="utf-8") for p in prod)


# ── the protocol ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_protocol_is_the_pinned_one_and_states_the_primary_outcome_guards_and_ceiling():
    assert sha(X.PROTOCOL.read_bytes()) == X.PROTOCOL_SHA256 == "73fa404fbeff5edcac00a7da8dbce7283070482c17d222033306abc66625fff8"
    text = X.PROTOCOL.read_text(encoding="utf-8")
    for needle in ("Primary outcome", "location retained and correctly routed", "Guard first", "Ceiling", "D ≥ 4", "D ≤ −4",
                   "−3 ≤ D ≤ 3", "Exploratory", "No activation", "Ten calls per arm", "USD 2.00", "No retries", "Temperature 0.1",
                   "max_tokens 6000", "JSON-object", "timeout 90", "gpt-4.1-2025-04-14"):
        assert needle in text, needle


def test_a_changed_protocol_refuses_to_plan(monkeypatch):
    monkeypatch.setattr(X, "PROTOCOL_SHA256", "0" * 64)
    with pytest.raises(X.ExperimentError, match="protocol"):
        X.plan()


# ── dry run: exact reservation under the 2.00 cap ────────────────────────────────────────────────────────────────────────

def test_the_reservation_is_the_exact_sum_of_twenty_worst_cases_under_the_two_dollar_cap():
    p = X.plan()
    assert p["call_count"] == 20 and [c["arm"] for c in p["calls"]] == ["CONTROL", "ROUTED"] * 10
    assert p["per_arm_worst_case_usd"] == {"CONTROL": 0.082832, "ROUTED": 0.08303}
    assert p["reservation_usd"] == 1.65862 and p["cap_usd"] == 2.0
    assert p["temperature"] == 0.1 and p["max_tokens"] == 6000 and p["timeout_s"] == 90 and p["retries"] == 0
    assert p["response_format"] == "json_object" and p["model"] == "gpt-4.1-2025-04-14"


def test_the_pins_record_every_hash_the_manifest_needs():
    pins = X.plan()["pins"]
    for key in ("control_sha256", "labels_sha256", "tc20_eval_script_sha256", "compare_script_sha256", "git_commit",
                "routing_script_sha256", "scope_script_sha256", "protocol_sha256", "user_message_sha256", "eval_set_manifest_sha256",
                "candidate_sha256"):
        assert key in pins, key
    assert pins["identity"]["routed_sha256"] == X.ROUTED_SHA256 and pins["user_message_sha256"] == X.USER_SHA256


def test_the_cap_refuses_a_plan_above_it():
    with pytest.raises(X.ExperimentError, match="exceeds the cap"):
        X.plan(cap=1.0)


def test_the_dry_run_prints_the_plan_and_makes_no_network_call(monkeypatch, capsys):
    def no_network(*a, **k):
        raise AssertionError("network access in a dry run")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(C, "openai_call_model", no_network)
    assert X.main(["--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reservation_usd"] == 1.65862 and out["cap_usd"] == 2.0 and out["mode"] == "dry-run"
    assert len(out["plan"]["calls"]) == 20


def test_execute_needs_an_output_directory_and_a_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        X.main(["--execute"])
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    with pytest.raises(SystemExit):
        X.main(["--execute"])


def test_a_non_empty_output_directory_is_refused(tmp_path):
    (tmp_path / "old.txt").write_text("evidence", encoding="utf-8")
    with pytest.raises(E.EvalError):
        X.run(tmp_path, lambda m: {})
    assert (tmp_path / "old.txt").read_text(encoding="utf-8") == "evidence"


# ── the run loop with a fake client only ─────────────────────────────────────────────────────────────────────────────────

def _fake(rows):
    calls = []

    def api(msgs):
        calls.append(msgs)
        r = rows[(len(calls) - 1) % len(rows)]
        return {"raw": r["raw"], "finish_reason": "stop", "model": X.MODEL, "usage": r["usage"]}
    return api, calls


def test_a_full_fake_run_writes_the_manifest_first_and_alternates_twenty_calls(tmp_path):
    api, calls = _fake(_scope_rows())
    out = tmp_path / "run"
    res = X.run(out, api)
    assert res["calls_attempted"] == 20 and res["successful_calls"] == 20 and res["stopped"] is None and len(calls) == 20
    rows = [json.loads(x) for x in (out / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["arm"] for r in rows] == ["CONTROL", "ROUTED"] * 10
    assert all(r["input"]["system_sha256"] == (X.CONTROL_SHA256 if r["arm"] == "CONTROL" else X.ROUTED_SHA256) for r in rows)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["pins"]["protocol_sha256"] == X.PROTOCOL_SHA256


def test_a_model_mismatch_stops_the_run_after_one_call(tmp_path):
    seen = []

    def api(msgs):
        seen.append(1)
        return {"raw": "{}", "finish_reason": "stop", "model": "gpt-4.1-other", "usage": {}}
    res = X.run(tmp_path / "m", api)
    assert res["stopped"] == "error:ModelMismatch" and len(seen) == 1


def test_an_exception_stops_the_run_without_retry(tmp_path):
    seen = []

    def api(msgs):
        seen.append(1)
        if len(seen) == 4:
            raise RuntimeError("simulated failure")
        return {"raw": "{}", "finish_reason": "stop", "model": X.MODEL, "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    res = X.run(tmp_path / "e", api)
    assert len(seen) == 4 and res["calls_attempted"] == 4 and res["stopped"].startswith("error:")


# ── location, guards and reading on stored answers ───────────────────────────────────────────────────────────────────────

def test_the_location_check_reproduces_the_scope_audit_on_stored_clarified_answers():
    """The scope experiment's CLARIFIED answers: location is correctly routed in calls 1, 3 and 4 (orders 2, 6 and 8) only."""
    rows = [r for r in _scope_rows() if r["arm"] == "CLARIFIED"]
    correct = [X.location_facts(r["raw"])["location_correctly_routed"] for r in rows]
    assert correct == [True, False, True, True, False]


def test_a_location_in_informational_items_is_not_correctly_routed_even_if_it_also_appears_in_non_scoreable():
    raw = json.dumps({"non_scoreable_requirements": [{"text": "w", "category": "location", "reason": "r", "source_text": "work at the MoNE office in Ramallah"}],
                      "post_hiring_conditions": [], "informational_items": [{"text": "w", "category": "location", "reason": "r", "source_text": "work at the MoNE office in Ramallah"}]})
    f = X.location_facts(raw)
    assert f["location_in_non_scoreable"] and f["location_elsewhere"] == 1 and not f["location_correctly_routed"]


def test_a_location_in_post_hiring_conditions_is_not_correctly_routed():
    raw = json.dumps({"non_scoreable_requirements": [], "post_hiring_conditions": [{"text": "w", "category": "location", "reason": "r",
                      "source_text": "work at the MoNE office in Ramallah"}], "informational_items": []})
    assert not X.location_facts(raw)["location_correctly_routed"]


def test_the_location_marker_is_retained_in_the_whole_answer_even_when_misrouted():
    raw = json.dumps({"non_scoreable_requirements": [], "post_hiring_conditions": [], "informational_items": [
        {"text": "w", "category": "location", "reason": "r", "source_text": "It is expected that the Technical Coordinator. will work at the MoNE office in Ramallah"}]})
    f = X.location_facts(raw)
    assert f["location_retained"] and not f["location_correctly_routed"] and not f["location_in_non_scoreable"]


def test_the_reading_applies_the_guard_first_then_the_ceiling_then_the_difference():
    clean = {"reporting_complete": (5, 5), "duty_complete": (10, 10), "schema_conforming": (10, 10), "invented_source_free": (10, 10)}
    assert X.reading(3, 8, clean).startswith("supports") and "not proof of causation" in X.reading(3, 8, clean)
    assert X.reading(3, 7, clean).startswith("supports")                       # D = 4 meets the supporting threshold
    assert X.reading(3, 6, clean).startswith("inconclusive")                   # D = 3 is inside the inconclusive band
    assert X.reading(5, 5, clean).startswith("inconclusive")
    assert X.reading(6, 2, clean).startswith("the routing sentence did not help")
    assert X.reading(9, 10, clean).startswith("inconclusive: the control is near ceiling")
    regressed = dict(clean, reporting_complete=(5, 4))
    assert X.reading(3, 9, regressed).startswith("regression in a guarded group (reporting_complete)")   # location gain does not offset
    assert "duty_complete" in X.reading(3, 9, dict(clean, duty_complete=(10, 9)))
    assert "schema_conforming" in X.reading(3, 9, dict(clean, schema_conforming=(10, 9)))


def test_a_full_report_on_stored_answers_is_complete_and_keeps_history_apart():
    jd, labels = ev.load_inputs()
    clar = [r for r in _scope_rows() if r["arm"] == "CLARIFIED"]
    recs = []
    for order in range(1, 21):                                  # twenty records, CONTROL first, each arm ten calls
        call = (order + 1) // 2
        recs.append({"order": order, "arm": "CONTROL" if order % 2 else "ROUTED", "call": call,
                     "raw": clar[(call - 1) % 5]["raw"], "error": None})
    rep = X.report(recs, labels, jd)
    assert rep["status"] == "scored" and rep["successful_calls"] == 20
    assert rep["fresh_arms"]["CONTROL"]["location_correctly_routed"] == rep["fresh_arms"]["ROUTED"]["location_correctly_routed"] == 6
    assert rep["direct_comparison"]["location_correctly_routed_difference_ROUTED_minus_CONTROL"] == 0
    assert rep["direct_comparison"]["primary_reading"].startswith("inconclusive (small or absent")
    assert set(rep["historical_context_not_pooled"]) == {"scope_experiment_CLARIFIED", "scope_experiment_FULL"}
    assert rep["historical_context_not_pooled"]["scope_experiment_CLARIFIED"]["calls"] == 5


def test_a_partial_report_gives_no_reading():
    jd, labels = ev.load_inputs()
    clar = [r for r in _scope_rows() if r["arm"] == "CLARIFIED"]
    recs = [{"order": 1, "arm": "CONTROL", "call": 1, "raw": clar[0]["raw"], "error": None}]
    rep = X.report(recs, labels, jd)
    assert rep["status"].startswith("partial") and rep["direct_comparison"]["primary_reading"] is None
