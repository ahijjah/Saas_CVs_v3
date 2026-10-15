"""Offline tests of the Example 5 removal experiment: the variant's byte identity, the protocol and its interpretation rules, the
alternating plan and the exact reservation, the dry-run, the refusals, the protections (with a fake client only), the reading rule,
and the report's separation of fresh and historical calls. No network and no paid call.
"""
import hashlib
import json
import pathlib

import pytest

import scripts.requirements_v2_candidate_audit as A
import scripts.requirements_v2_ex5_experiment as X
import scripts.requirements_v2_tc20_compare as C
import scripts.requirements_v2_tc20_eval as ev

FULL_BYTES = X.FULL.read_bytes()


def sha(b):
    return hashlib.sha256(b).hexdigest()


def _tc20_raw(path, arm):
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    return [r["raw"] for r in rows if r.get("jd", "tc20") == "tc20" and r["arm"] == arm]


STORED_V25 = _tc20_raw(A.EVIDENCE / "calls.jsonl", "v2-5")          # the paired run's v2-5 answers (historical)
STORED_V23 = _tc20_raw(A.EVIDENCE / "calls.jsonl", "v2-3")


def _client(raws, fail_on=None, exc=None, model=None, usage=None, before_first=None):
    seen = []

    def call(messages):
        seen.append(messages)
        if before_first and len(seen) == 1:
            before_first()
        if fail_on == len(seen):
            raise exc
        return {"raw": raws[(len(seen) - 1) % len(raws)], "finish_reason": "stop", "model": model or X.MODEL,
                "usage": usage or {"prompt_tokens": 1000, "completion_tokens": 500}}
    call.seen = seen
    return call


# ── 1. the variant is the exact byte prefix of FULL before Example 5 ────────────────────────────────────────────────────

def test_the_variant_is_the_exact_prefix_of_full_before_example_5():
    cut = FULL_BYTES.index(X.EXAMPLE5_MARKER)
    assert FULL_BYTES.count(X.EXAMPLE5_MARKER) == 1
    assert X.VARIANT.read_bytes() == FULL_BYTES[:cut]
    assert FULL_BYTES[:cut] + FULL_BYTES[cut:] == FULL_BYTES


def test_the_removed_span_is_exactly_the_pinned_example_5():
    cut = FULL_BYTES.index(X.EXAMPLE5_MARKER)
    removed = FULL_BYTES[cut:]
    assert sha(removed) == X.REMOVED_SHA256 and len(removed) == X.REMOVED_BYTES == 3223
    assert removed.startswith(b"\nExample 5 (EN") and removed.endswith(b'"warnings":[]}\n')


def test_every_other_byte_is_unchanged_so_the_rules_and_examples_1_to_4_are_identical():
    variant = X.VARIANT.read_text(encoding="utf-8")
    full = FULL_BYTES.decode("utf-8")
    assert full.startswith(variant)
    assert "Example 5" not in variant and "Warehouse" not in variant
    assert variant.endswith('"warnings":[]}\n')                    # the end of Example 4's answer


def test_the_variant_and_the_full_prompt_have_the_pinned_hashes_and_the_identity_check_passes():
    assert sha(X.VARIANT.read_bytes()) == X.VARIANT_SHA256 == "7cf171c84d65ce74b2531c2ca1f3049c7e539774121c0faae347dc7bf0466e62"
    assert sha(FULL_BYTES) == X.FULL_SHA256 == "f1569a8b400257db20b1c0b24fd7728605fe472eef7fff993a384f12f0cbc8db"
    ident = X.verify_identity()
    assert ident["variant_bytes"] == 27207 and ident["full_bytes"] == 30430 and ident["removed_bytes"] == 3223


def test_a_tampered_variant_is_refused(monkeypatch, tmp_path):
    bad = tmp_path / "v.txt"
    bad.write_bytes(X.VARIANT.read_bytes() + b"x")
    monkeypatch.setattr(X, "VARIANT", bad)
    with pytest.raises(X.ExperimentError, match="sha256"):
        X.verify_identity()


def test_the_variant_manifest_pins_match_the_prompt_identity():
    m = json.loads((X.VARIANT.parent / "MANIFEST.json").read_text(encoding="utf-8"))
    assert m["sha256"] == X.VARIANT_SHA256 and m["base_sha256"] == X.FULL_SHA256 and m["removed_span_sha256"] == X.REMOVED_SHA256


# ── 2. the protocol, its interpretation rules, and the pins ─────────────────────────────────────────────────────────────

def test_the_protocol_is_pinned_and_states_the_corrected_interpretation_rules():
    assert sha(X.PROTOCOL.read_bytes()) == X.PROTOCOL_SHA256
    text = X.PROTOCOL.read_text(encoding="utf-8")
    for needle in ("**supports** Example 5 contributing to the omission", "It does **not** prove causation",
                   "**weakens** the hypothesis", "It does **not** rule out an interaction", "**Compare the two fresh arms directly.**",
                   "**Report five groups separately**", "v2-5 stays inactive regardless of the result",
                   "D ≥ 3", "1 ≤ D ≤ 2", "D ≤ 0", "historical runs"):
        assert needle in text, needle


def test_a_changed_protocol_refuses_to_plan(monkeypatch):
    monkeypatch.setattr(X, "PROTOCOL_SHA256", "0" * 64)
    with pytest.raises(X.ExperimentError, match="protocol"):
        X.plan()


def test_the_pins_record_every_hash_the_run_must_record():
    p = X.pins()
    for key in ("candidate_sha256", "control_sha256", "labels_sha256", "eval_set_manifest_sha256", "script_sha256",
                "tc20_eval_script_sha256", "compare_script_sha256", "protocol_sha256", "experiment_script_sha256",
                "repeat_script_sha256", "user_message_sha256", "git_commit"):
        assert p[key], key
    assert p["labels_sha256"] == sha(ev.LABELS.read_bytes()) == "6a87da80e142640af1327c4a3fd5a21adb648726a9733a699fa8d3ec67135ef9"
    assert p["experiment_script_sha256"] == sha(pathlib.Path(X.__file__).read_bytes())


# ── 3. the alternating plan, the messages and the exact reservation ────────────────────────────────────────────────────

def test_the_calls_alternate_full_first_and_there_are_ten():
    order = X.call_order()
    assert [(c["arm"], c["call"]) for c in order] == [("FULL", 1), ("VARIANT", 1), ("FULL", 2), ("VARIANT", 2), ("FULL", 3),
                                                       ("VARIANT", 3), ("FULL", 4), ("VARIANT", 4), ("FULL", 5), ("VARIANT", 5)]
    assert [c["order"] for c in order] == list(range(1, 11))


def test_each_arm_sends_its_own_system_prompt_and_the_same_user_message():
    f, v = X.messages("FULL"), X.messages("VARIANT")
    assert sha(f[0]["content"].encode()) == X.FULL_SHA256 and sha(v[0]["content"].encode()) == X.VARIANT_SHA256
    assert f[1] == v[1] and sha(f[1]["content"].encode()) == X.USER_SHA256


def test_the_reservation_is_recomputed_and_matches_the_previous_estimate():
    p = X.plan()
    assert p["per_arm_worst_case_usd"] == {"FULL": 0.082712, "VARIANT": 0.07949}
    assert p["reservation_usd"] == pytest.approx(0.81101, abs=1e-9)
    assert p["reservation_usd"] == pytest.approx(5 * (0.082712 + 0.07949), abs=1e-9)
    assert p["cap_usd"] == 1.00 and p["retries"] == 0 and p["call_count"] == 10
    assert p["temperature"] == 0.1 and p["max_tokens"] == 6000 and p["response_format"] == "json_object" and p["timeout_s"] == 90


def test_the_plan_refuses_a_total_above_the_cap():
    with pytest.raises(X.ExperimentError, match="exceeds the cap"):
        X.plan(cap=0.5)


# ── 4. the dry-run and the refusals (no network, no file) ──────────────────────────────────────────────────────────────

def test_the_dry_run_makes_no_network_call_writes_nothing_and_prints_the_reservation(monkeypatch, tmp_path, capsys):
    def boom(*_a, **_k):
        raise AssertionError("the dry-run must not reach the network")
    monkeypatch.setattr(C, "openai_call_model", boom)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    before = set(tmp_path.iterdir())
    assert X.main([]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "dry-run" and out["reservation_usd"] == pytest.approx(0.81101) and out["cap_usd"] == 1.0
    assert out["pricing_verified"] is True and out["expected_output_files"] == X.OUTPUT_FILES
    assert out["git_commit"] == out["plan"]["pins"]["git_commit"] and out["previous_estimate_usd"] == 0.81101
    assert set(tmp_path.iterdir()) == before


def test_execute_refuses_without_an_output_directory_or_a_key(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        X.main(["--execute", "--out", str(tmp_path / "x")])
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    with pytest.raises(SystemExit):
        X.main(["--execute"])
    assert not (tmp_path / "x").exists()


def test_execute_refuses_while_pricing_is_not_recorded_as_verified(monkeypatch, tmp_path):
    def boom(*_a, **_k):
        raise AssertionError("no call while pricing is not verified")
    monkeypatch.setattr(C, "PRICING_VERIFIED", False)
    monkeypatch.setattr(C, "openai_call_model", boom)
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    with pytest.raises(X.ExperimentError, match="not recorded as verified"):
        X.main(["--execute", "--out", str(tmp_path / "x")])


# ── 5. the protected run, with a fake client only ─────────────────────────────────────────────────────────────────────

def test_a_full_run_writes_the_manifest_first_and_makes_ten_alternating_calls(tmp_path):
    out = tmp_path / "run"
    seen = []
    client = _client(STORED_V25, before_first=lambda: seen.append((out / "manifest.json").exists()))
    res = X.run(out, client)
    assert res["calls_attempted"] == 10 and res["successful_calls"] == 10 and res["stopped"] is None
    assert seen == [True]
    rows = [json.loads(x) for x in (out / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(r["order"], r["arm"], r["call"]) for r in rows] == [(c["order"], c["arm"], c["call"]) for c in X.call_order()]
    assert all(r["input"]["system_sha256"] == (X.FULL_SHA256 if r["arm"] == "FULL" else X.VARIANT_SHA256) for r in rows)
    assert all(r["input"]["user_sha256"] == X.USER_SHA256 and r["model_returned"] == X.MODEL for r in rows)
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert m["pins"]["protocol_sha256"] == X.PROTOCOL_SHA256 and m["plan"]["retries"] == 0 and m["expected_outputs"] == X.OUTPUT_FILES
    assert json.loads((out / "run.json").read_text(encoding="utf-8"))["successful_calls"] == 10


def test_the_run_stops_at_the_first_failure_with_no_retry(tmp_path):
    class BadRequest(Exception):
        status_code = 400

    out = tmp_path / "run"
    client = _client(STORED_V25, fail_on=4, exc=BadRequest("provider text not kept"))
    res = X.run(out, client)
    assert len(client.seen) == 4 and res["calls_attempted"] == 4 and res["stopped"] == "error:BadRequest"
    rows = [json.loads(x) for x in (out / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["order"] for r in rows] == [1, 2, 3, 4] and rows[-1]["error"]["http_status"] == 400


def test_an_unexpected_returned_model_stops_the_run(tmp_path):
    client = _client(STORED_V25, model="gpt-4o-mini-2024-07-18")
    res = X.run(tmp_path / "run", client)
    assert res["stopped"] == "error:ModelMismatch" and res["calls_attempted"] == 1 and len(client.seen) == 1


def test_the_cap_guard_stops_before_a_call_that_could_exceed_the_cap(tmp_path):
    client = _client(STORED_V25, usage={"prompt_tokens": 2_000_000, "completion_tokens": 0})
    res = X.run(tmp_path / "run", client)
    assert res["stopped"] == "cap_guard" and res["calls_attempted"] == 1 and len(client.seen) == 1


def test_an_existing_output_directory_is_never_overwritten(tmp_path):
    out = tmp_path / "run"
    out.mkdir()
    (out / "calls.jsonl").write_text("earlier evidence\n", encoding="utf-8")
    client = _client(STORED_V25)
    with pytest.raises(Exception, match="not empty"):
        X.run(out, client)
    assert client.seen == [] and (out / "calls.jsonl").read_text(encoding="utf-8") == "earlier evidence\n"


# ── 6. the reading rule and the report ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("full,variant,expected", [
    (0, 3, "supports the contribution of Example 5 (not proof of causation)"),
    (0, 5, "supports the contribution of Example 5 (not proof of causation)"),
    (1, 2, "inconclusive"),
    (2, 4, "inconclusive"),
    (3, 3, "weakens the hypothesis; does not rule out interactions with other instructions"),
    (4, 1, "weakens the hypothesis; does not rule out interactions with other instructions"),
])
def test_the_reading_rule_of_the_protocol(full, variant, expected):
    assert X.reading(full, variant) == expected


def _records(full_raws, variant_raws):
    """Synthetic records for exercising the report: the raws are historical answers, not new evidence."""
    rows = []
    for c in X.call_order():
        raws = full_raws if c["arm"] == "FULL" else variant_raws
        idx = (c["call"] - 1)
        rows.append({"order": c["order"], "arm": c["arm"], "call": c["call"], "raw": raws[idx], "error": None})
    return rows


def test_the_report_compares_the_fresh_arms_directly_and_keeps_history_separate():
    jd, labels = ev.load_inputs()
    recs = _records(STORED_V25[:5], STORED_V23[:5])
    rep = X.report(recs, labels, jd)
    assert rep["status"] == "scored" and rep["successful_calls"] == 10
    fresh = rep["fresh_arms"]
    assert fresh["FULL"]["calls"] == 5 and fresh["VARIANT"]["calls"] == 5
    assert set(fresh["FULL"]["checks_passed"]) == set(X.CHECKS) and len(fresh["FULL"]["checks_passed"]) == 8
    # the direct comparison uses only the fresh calls
    assert rep["direct_comparison"]["reporting_present_difference"] == fresh["VARIANT"]["reporting_statement_in_whole_answer"] - \
        fresh["FULL"]["reporting_statement_in_whole_answer"]
    assert rep["direct_comparison"]["reporting_reading"] == X.reading(fresh["FULL"]["reporting_statement_in_whole_answer"],
                                                                      fresh["VARIANT"]["reporting_statement_in_whole_answer"])
    hist = rep["historical_context_not_pooled"]
    assert set(hist) == {"paired_run", "repeat"}
    assert hist["paired_run"]["v2-5_reporting_missing_whole_answer"] == 4


def test_the_fresh_counts_do_not_include_any_historical_call(tmp_path):
    jd, labels = ev.load_inputs()
    recs = _records(STORED_V25[:5], STORED_V23[:5])
    fresh = X.report(recs, labels, jd)["fresh_arms"]
    assert sum(fresh[a]["calls"] for a in ("FULL", "VARIANT")) == 10       # not 10 + 22 or 10 + 10 historical


def test_a_partial_report_reports_no_reading():
    jd, labels = ev.load_inputs()
    recs = [r for r in _records(STORED_V25[:5], STORED_V23[:5]) if r["arm"] == "FULL"]
    rep = X.report(recs, labels, jd)
    assert rep["direct_comparison"]["reporting_reading"] is None and rep["status"].startswith("partial")


def test_the_scorer_and_the_labels_are_the_frozen_ones():
    assert sha(pathlib.Path(ev.__file__).read_bytes()) == "1c40def472760b9b79b6878cfe3f3186080786d07668d4eadbfe7ae0f5825129"
    assert sha(ev.LABELS.read_bytes()) == "6a87da80e142640af1327c4a3fd5a21adb648726a9733a699fa8d3ec67135ef9"
