"""Offline tests of the TC20-only repeat: its protocol, its plan and reservation, its dry-run, its protections and its reading rules.

No network and no paid call. The paid path is reached only through a fake client; the real client is never constructed.
"""
import json
import pathlib

import pytest

import scripts.requirements_v2_candidate_eval as E
import scripts.requirements_v2_tc20_compare as C
import scripts.requirements_v2_tc20_eval as ev
import scripts.requirements_v2_tc20_repeat as R

PROTO = R.PROTOCOL
PREV = R.PREVIOUS_RUN


def _rows(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture(scope="module")
def prev_rows():
    return [r for r in _rows(PREV / "calls.jsonl") if r["jd"] == "tc20"]


@pytest.fixture(scope="module")
def jd_labels():
    return ev.load_inputs()


def _client(stored, fail_on=None, exc=None, model=None, usage=None, before_first=None):
    """A fake client that returns the stored v2-3 or v2-5 answer for the arm named in the system prompt."""
    by_arm = {}
    for r in stored:
        by_arm.setdefault(r["arm"], []).append(r["raw"])
    seen = []

    def call(messages):
        seen.append(messages)
        if before_first and len(seen) == 1:
            before_first()
        if fail_on == len(seen):
            raise exc
        arm = "v2-3" if E.sha256_bytes(messages[0]["content"].encode()) == E.CONTROL_SHA256 else "v2-5"
        raw = by_arm[arm][(len(seen) - 1) // 2 % len(by_arm[arm])]
        return {"raw": raw, "finish_reason": "stop", "model": model or E.MODEL,
                "usage": usage or {"prompt_tokens": 1000, "completion_tokens": 500}}
    call.seen = seen
    return call


# ── 1. the protocol is recorded before the calls and pinned ─────────────────────────────────────────────────────────

def test_the_protocol_file_is_the_pinned_one():
    assert E.sha256_bytes(PROTO.read_bytes()) == R.PROTOCOL_SHA256 == \
        "860eed9e7cbb3d9982f0d7999309f11874878961b62813de71ca56dd5393c0fe"


def test_the_protocol_states_the_five_decision_rules_and_the_protections():
    text = PROTO.read_text(encoding="utf-8")
    for needle in ("All eight TC20 checks", "Duty completeness and reporting coverage", "OR and AND reported separately",
                   "exploratory evidence of consistency", "v2-5 stays inactive", "Hard cost cap USD 1.00",
                   "new or empty", "alternating", "no retries", "Stop at the first failed call or the first unexpected returned model"):
        assert needle in text, needle


def test_a_changed_protocol_refuses_to_run(monkeypatch):
    monkeypatch.setattr(R, "PROTOCOL_SHA256", "0" * 64)
    with pytest.raises(R.RepeatError, match="protocol"):
        R.plan()


# ── 2. the order, the plan and the reservation ─────────────────────────────────────────────────────────────────────

def test_the_calls_alternate_between_the_arms_and_there_are_ten_of_them():
    order = R.call_order()
    assert [(c["arm"], c["call"]) for c in order] == [("v2-3", 1), ("v2-5", 1), ("v2-3", 2), ("v2-5", 2), ("v2-3", 3),
                                                       ("v2-5", 3), ("v2-3", 4), ("v2-5", 4), ("v2-3", 5), ("v2-5", 5)]
    assert [c["order"] for c in order] == list(range(1, 11))


def test_the_plan_reserves_the_worst_case_under_the_one_dollar_cap():
    p = R.plan()
    assert p["call_count"] == 10 and p["retries"] == 0 and p["cap_usd"] == R.CAP_USD == 1.00
    assert p["worst_case_total_usd"] == pytest.approx(0.8009, abs=1e-9) and p["worst_case_total_usd"] <= 1.00
    assert p["model"] == "gpt-4.1-2025-04-14" and p["temperature"] == 0.1 and p["max_tokens"] == 6000
    assert p["response_format"] == "json_object" and p["timeout_s"] == 90
    per_arm = {c["arm"]: c["worst_case_usd"] for c in p["calls"]}
    assert per_arm == {"v2-3": 0.077468, "v2-5": 0.082712}


def test_the_plan_refuses_a_total_above_the_cap():
    with pytest.raises(R.RepeatError, match="exceeds the cap"):
        R.plan(cap=0.5)


def test_the_user_message_and_the_prompts_are_the_pinned_ones():
    unit = R.tc20_unit()
    v23 = E.messages("v2-3", unit)
    v25 = E.messages("v2-5", unit)
    assert E.sha256_bytes(v23[1]["content"].encode()) == R.USER_SHA256 == E.sha256_bytes(v25[1]["content"].encode())
    assert E.sha256_bytes(v23[0]["content"].encode()) == E.CONTROL_SHA256
    assert E.sha256_bytes(v25[0]["content"].encode()) == E.CANDIDATE_SHA256
    assert v23[1]["content"].startswith("Job Context:\nJob Title: Technical Coordinator 20\n")


def test_the_pins_record_the_commit_and_the_script_and_protocol_hashes():
    p = R.pins()
    assert p["protocol_sha256"] == R.PROTOCOL_SHA256
    assert p["repeat_script_sha256"] == E.sha256_bytes(pathlib.Path(R.__file__).read_bytes())
    assert p["candidate_sha256"] == E.CANDIDATE_SHA256 and p["control_sha256"] == E.CONTROL_SHA256
    assert p["labels_sha256"] == E.sha256_bytes(ev.LABELS.read_bytes())
    assert p["git_commit"] and p["git_commit"] != "unknown"


# ── 3. the dry-run: no network, no file, prints every pin and the reservation ───────────────────────────────────────

def test_the_dry_run_makes_no_network_call_and_writes_nothing(monkeypatch, tmp_path, capsys):
    def boom(*_a, **_k):
        raise AssertionError("the dry-run must not reach the network")
    monkeypatch.setattr(C, "openai_call_model", boom)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    before = set(tmp_path.iterdir())
    assert R.main([]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "dry-run" and out["reservation_usd"] == pytest.approx(0.8009) and out["cap_usd"] == 1.00
    assert out["user_message_sha256"] == R.USER_SHA256 and out["expected_output_files"] == R.OUTPUT_FILES
    assert out["git_commit"] == out["plan"]["pins"]["git_commit"] and out["pricing_verified"] is True
    assert set(tmp_path.iterdir()) == before


# ── 4. the protections of the paid path (no real client is created) ─────────────────────────────────────────────────

def test_execute_refuses_without_an_output_directory_or_a_key(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        R.main(["--execute", "--out", str(tmp_path / "x")])
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    with pytest.raises(SystemExit):
        R.main(["--execute"])
    assert not (tmp_path / "x").exists()


def test_execute_refuses_while_pricing_is_not_recorded_as_verified(monkeypatch, tmp_path):
    def boom(*_a, **_k):
        raise AssertionError("no call while pricing is not verified")
    monkeypatch.setattr(C, "PRICING_VERIFIED", False)
    monkeypatch.setattr(C, "openai_call_model", boom)
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    with pytest.raises(R.RepeatError, match="not recorded as verified"):
        R.main(["--execute", "--out", str(tmp_path / "x")])


def test_a_non_empty_output_directory_is_never_overwritten(tmp_path, prev_rows):
    out = tmp_path / "run"
    out.mkdir()
    (out / "calls.jsonl").write_text("earlier evidence\n", encoding="utf-8")
    client = _client(prev_rows)
    with pytest.raises(E.EvalError, match="not empty"):
        R.run(out, client)
    assert client.seen == [] and (out / "calls.jsonl").read_text(encoding="utf-8") == "earlier evidence\n"


# ── 5. the protected run with a fake client ──────────────────────────────────────────────────────────────────────────

def test_a_full_run_makes_ten_alternating_calls_and_writes_the_manifest_first(tmp_path, prev_rows):
    out = tmp_path / "run"
    seen_manifest = []
    client = _client(prev_rows, before_first=lambda: seen_manifest.append((out / "manifest.json").exists()))
    res = R.run(out, client)
    assert res == {"spent_usd": pytest.approx(10 * (1000 * C.PRICE_IN + 500 * C.PRICE_OUT), abs=1e-6), "calls_attempted": 10,
                   "successful_calls": 10, "stopped": None}
    assert seen_manifest == [True]                                     # the manifest exists before the first call
    rows = _rows(out / "calls.jsonl")
    assert [(r["order"], r["arm"], r["call"]) for r in rows] == [(c["order"], c["arm"], c["call"]) for c in R.call_order()]
    assert all(r["model_returned"] == E.MODEL and r["error"] is None for r in rows)
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert m["protocol_sha256"] == R.PROTOCOL_SHA256 and m["pins"]["repeat_script_sha256"] and m["plan"]["retries"] == 0
    assert m["expected_outputs"] == R.OUTPUT_FILES
    assert json.loads((out / "run.json").read_text(encoding="utf-8"))["successful_calls"] == 10


def test_the_run_stops_at_the_first_failure_and_makes_no_retry(tmp_path, prev_rows):
    class BadRequest(Exception):
        status_code = 400

    out = tmp_path / "run"
    client = _client(prev_rows, fail_on=3, exc=BadRequest("provider text that is not kept"))
    res = R.run(out, client)
    assert len(client.seen) == 3 and res["calls_attempted"] == 3 and res["stopped"] == "error:BadRequest"
    rows = _rows(out / "calls.jsonl")
    assert rows[-1]["error"]["kind"] == "BadRequest" and rows[-1]["error"]["http_status"] == 400
    assert [r["order"] for r in rows] == [1, 2, 3]                   # no call after the failure


def test_an_unexpected_returned_model_stops_the_run(tmp_path, prev_rows):
    out = tmp_path / "run"
    client = _client(prev_rows, model="gpt-4o-mini-2024-07-18")
    res = R.run(out, client)
    assert res["stopped"] == "error:ModelMismatch" and res["calls_attempted"] == 1 and len(client.seen) == 1


def test_the_cap_guard_stops_before_a_call_that_could_exceed_the_cap(tmp_path, prev_rows):
    out = tmp_path / "run"
    client = _client(prev_rows, usage={"prompt_tokens": 2_000_000, "completion_tokens": 0})
    res = R.run(out, client)
    assert res["stopped"] == "cap_guard" and res["calls_attempted"] == 1 and len(client.seen) == 1


def test_a_changed_input_file_refuses_before_any_call(tmp_path, monkeypatch, prev_rows):
    monkeypatch.setattr(E, "CONTROL_SHA256", "0" * 64)
    client = _client(prev_rows)
    with pytest.raises(E.EvalError):
        R.run(tmp_path / "run", client)
    assert client.seen == []


# ── 6. the report and the reading rules, on stored data ─────────────────────────────────────────────────────────────

def _as_records(rows):
    """The stored TC20 rows in protocol order, with the order number the repeat would give them."""
    out = []
    for c in R.call_order():
        r = next(x for x in rows if x["arm"] == c["arm"] and x["call"] == c["call"])
        out.append({**r, "order": c["order"]})
    return out


def test_the_previous_paired_run_has_the_facts_the_protocol_quotes(jd_labels):
    jd, labels = jd_labels
    prev = R.previous_paired(labels, jd)
    assert prev["v2-3"]["duty_lines_found"] == [0, 16, 16, 16, 16]
    assert prev["v2-5"]["duty_lines_found"] == [16] * 5
    assert [x for x in prev["v2-3"]["reporting_present_in_answer"]].count(False) == 1
    assert [x for x in prev["v2-5"]["reporting_present_in_answer"]].count(False) == 4


def test_the_reading_rules_applied_to_the_previous_run_reproduce_its_own_reading(prev_rows, jd_labels):
    jd, labels = jd_labels
    rep = R.report(_as_records(prev_rows), labels, jd)
    assert rep["status"] == "scored" and rep["successful_calls"] == 10
    rd = rep["reading_rules"]
    assert rd["duty_completeness_reproduced"] is True
    assert rd["reporting_regression_reproduced"] is True
    assert rd["reporting_missing_now"] == {"v2-3": 1, "v2-5": 4}


def test_every_one_of_the_eight_checks_is_reported_per_call_and_per_arm(prev_rows, jd_labels):
    jd, labels = jd_labels
    rep = R.report(_as_records(prev_rows), labels, jd)
    assert all(set(c["checks"]) == set(R.CHECKS) and len(c["checks"]) == 8 for c in rep["per_call"])
    for arm in R.ARMS:
        assert set(rep["per_arm_now"][arm]["checks_passed"]) == set(R.CHECKS)
    assert set(rep["reading_rules"]["or_and_failed_calls_now"]["v2-5"]) == {"C_EXP_OR", "C_FAM_OR", "C_AND"}


def test_or_and_failures_are_counted_on_their_own_and_are_not_offset(prev_rows, jd_labels):
    jd, labels = jd_labels
    rep = R.report(_as_records(prev_rows), labels, jd)
    rd = rep["reading_rules"]["or_and_failed_calls_now"]
    assert rd == {"v2-3": {"C_EXP_OR": 5, "C_FAM_OR": 5, "C_AND": 5}, "v2-5": {"C_EXP_OR": 5, "C_FAM_OR": 5, "C_AND": 5}}


def test_an_invalid_answer_fails_every_check_and_does_not_crash(jd_labels):
    jd, labels = jd_labels
    f = R._facts("not json", labels, jd)
    assert f["valid_json"] is False and not any(f["checks"].values()) and f["duty_lines_found"] == 0


def test_a_partial_run_reports_no_reading_and_says_why(prev_rows, jd_labels):
    jd, labels = jd_labels
    only_v23 = [r for r in _as_records(prev_rows) if r["arm"] == "v2-3"]
    rep = R.report(only_v23, labels, jd)
    assert rep["reading_rules"] is None and rep["status"].startswith("partial")


def test_the_reading_rule_for_reporting_is_the_one_stated_in_the_protocol(jd_labels):
    jd, labels = jd_labels
    before = R.previous_paired(labels, jd)
    fake_now = {"v2-3": {**before["v2-3"]}, "v2-5": {**before["v2-5"]}}
    # three calls of v2-5 with the statement present: the regression is not reproduced
    fake_now["v2-5"] = {**before["v2-5"], "reporting_present_in_answer": [True, True, True, False, False]}
    rd = R.reading(fake_now, before)
    assert rd["reporting_regression_reproduced"] is False
    fake_now["v2-5"] = {**before["v2-5"], "reporting_present_in_answer": [True, False, False, False, True]}
    assert R.reading(fake_now, before)["reporting_regression_reproduced"] is True
