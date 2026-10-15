"""Offline tests of the pilot. The client is a FAKE: no test makes a network call or imports the OpenAI SDK."""
import json
import pathlib
import re
import subprocess
import sys

import pytest

import pilot as p
import preview as P
import replay as R

PROTOCOL_TEXT = p.PROTOCOL.read_text(encoding="utf-8")
REPEAT = [json.loads(line) for line in (R.SOURCES["repeat_gpt-4.1"]).read_text(encoding="utf-8").splitlines()]
STORED = {(r["arm"], r["call"]): r["raw"] for r in REPEAT}
REQUESTS = p.build_requests()
FROZEN = p.freeze(REQUESTS)
TOTAL = round(sum(p.reservation(REQUESTS[a]) for a, _ in p.schedule()), 6)


class FakeClient:
    """Returns scripted responses in order. An Exception entry is raised instead. Records every call it receives."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def complete(self, messages, settings):
        self.calls.append((messages, settings))
        if not self.script:
            raise AssertionError("the run made more calls than the script allows")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok(raw, model=p.MODEL, finish="stop"):
    return {"raw": raw, "finish_reason": finish, "model": model, "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}


def approval(**over):
    return {"manifest_sha256": FROZEN["manifest_sha256"], "approved_cap_usd": TOTAL, **over}


def analysis(retained, guards=None, review=0):
    g = {name: True for name in p.GUARD_NAMES}
    g.update(guards or {})
    return {"primary_retained_all": retained == 16, "duty_retained": retained, "guards": g, "review_candidates": review}


def pairs(control, inventory):
    recs = []
    for k in range(1, p.PAIRS + 1):
        recs.append({"arm": "CONTROL", "pair": k, "analysis": control})
        recs.append({"arm": "INVENTORY", "pair": k, "analysis": inventory})
    return recs


# ── protocol and code agree ──────────────────────────────────────────────────────────────────────────────────────────────
def test_the_protocol_states_the_same_thresholds_as_the_code():
    assert f"PRIMARY_MIN_CONTROL_LOSSES = {p.PRIMARY_MIN_CONTROL_LOSSES}" in PROTOCOL_TEXT
    assert f"PRIMARY_INVENTORY_RETAINED = {p.PRIMARY_INVENTORY_RETAINED}" in PROTOCOL_TEXT
    assert f"SECONDARY_MEDIAN_REDUCTION = {p.SECONDARY_MEDIAN_REDUCTION}" in PROTOCOL_TEXT
    assert f"GUARD_REGRESSIONS_ALLOWED = {p.GUARD_REGRESSIONS_ALLOWED}" in PROTOCOL_TEXT
    assert f"{p.PAIRS} pairs" in PROTOCOL_TEXT or "10 pairs" in PROTOCOL_TEXT


def test_the_protocol_states_the_reservation_that_the_code_computes():
    assert f"**${TOTAL:.5f}**" in PROTOCOL_TEXT
    for arm in p.ARMS:
        assert f"**${p.reservation(REQUESTS[arm]):.6f}**" in PROTOCOL_TEXT, arm


# ── order and the requests ───────────────────────────────────────────────────────────────────────────────────────────────
def test_the_order_alternates_with_control_first_and_has_twenty_calls():
    order = p.schedule()
    assert len(order) == 2 * p.PAIRS == 20
    assert [a for a, _ in order[:4]] == ["CONTROL", "INVENTORY", "CONTROL", "INVENTORY"]
    assert all(order[i][0] == "CONTROL" for i in range(0, 20, 2))
    assert sorted(order) == sorted((a, k) for a in p.ARMS for k in range(1, p.PAIRS + 1))


def test_the_two_requests_differ_only_by_the_appended_inventory_block():
    c, i = REQUESTS["CONTROL"], REQUESTS["INVENTORY"]
    assert i[0] == c[0], "the system prompt is byte-identical"
    assert i[1]["content"].startswith(c[1]["content"]), "the user message begins with the baseline user message"
    suffix = i[1]["content"][len(c[1]["content"]):]
    assert suffix == "\n\n" + REQUESTS["_addition"]
    assert REQUESTS["_addition"].startswith(P.ADD_BEGIN) and REQUESTS["_addition"].endswith(P.ADD_END)
    assert "unit_decisions" not in suffix


def test_the_baseline_request_matches_the_pinned_hashes():
    from hashlib import sha256
    assert sha256(REQUESTS["CONTROL"][0]["content"].encode()).hexdigest() == P.SYSTEM_SHA256
    assert sha256(REQUESTS["CONTROL"][1]["content"].encode()).hexdigest() == P.USER_SHA256


def test_the_reservation_is_computed_from_the_full_request_and_the_output_ceiling():
    import math
    msgs = REQUESTS["CONTROL"]
    chars = sum(len(m["content"]) for m in msgs)
    assert p.reservation(msgs) == round(math.ceil(chars / 2) * p.PRICE_IN + p.MAX_TOKENS * p.PRICE_OUT, 6)
    assert p.reservation(REQUESTS["INVENTORY"]) > p.reservation(msgs), "the larger request reserves more"


def test_the_total_reservation_is_the_sum_of_every_scheduled_call_and_is_not_an_average():
    assert TOTAL == round(sum(p.reservation(REQUESTS[a]) for a, _ in p.schedule()), 6)
    assert TOTAL == 1.62734
    assert p.dry_run()["cap"]["approved"] is False


# ── the run, with a fake client ──────────────────────────────────────────────────────────────────────────────────────────
def test_a_run_without_an_approval_refuses_before_any_call():
    client = FakeClient([])
    with pytest.raises(p.PilotError, match="no approval"):
        p.run_pilot(client, None)
    assert client.calls == []


def test_an_approval_naming_another_manifest_is_refused():
    client = FakeClient([])
    with pytest.raises(p.PilotError, match="different manifest"):
        p.run_pilot(client, approval(manifest_sha256="0" * 64))
    assert client.calls == []


def test_a_cap_below_the_reservation_is_refused():
    client = FakeClient([])
    with pytest.raises(p.PilotError, match="below the reservation"):
        p.run_pilot(client, approval(approved_cap_usd=TOTAL - 0.01))
    assert client.calls == []


def test_every_call_receives_the_frozen_request_and_the_same_settings():
    client = FakeClient([ok(STORED[("v2-3", 1)]) for _ in range(20)])
    out = p.run_pilot(client, approval())
    assert out["status"] == "complete" and len(client.calls) == 20
    for n, (arm, _) in enumerate(p.schedule()):
        messages, settings = client.calls[n]
        assert messages == REQUESTS[arm]
        assert settings == p.settings()


def test_a_failed_call_stops_the_run_and_is_not_retried():
    client = FakeClient([ok(STORED[("v2-3", 1)]), ok(STORED[("v2-3", 1)]), RuntimeError("timeout")])
    out = p.run_pilot(client, approval())
    assert out["status"] == "stopped" and out["reason"] == "failed call"
    assert len(client.calls) == 3, "no retry after the failure"
    assert out["records"][-1]["failed"] is True and out["records"][-1]["arm"] == "CONTROL"


@pytest.mark.parametrize("bad", [ok("", finish="stop"), ok(STORED[("v2-3", 1)], finish="length")])
def test_an_empty_or_truncated_answer_is_a_failed_call(bad):
    client = FakeClient([bad])
    out = p.run_pilot(client, approval())
    assert out["status"] == "stopped" and out["reason"] == "failed call" and len(client.calls) == 1


def test_an_unexpected_returned_model_stops_the_run_at_once():
    client = FakeClient([ok(STORED[("v2-3", 1)], model="gpt-4o-mini-2024-07-18")])
    out = p.run_pilot(client, approval())
    assert out["status"] == "stopped" and out["reason"] == "unexpected returned model" and len(client.calls) == 1


def test_the_run_stops_before_a_call_whose_reservation_would_exceed_the_cap(monkeypatch):
    """check_approval refuses any cap below the reservation, so this per-call guard is a second line of defence. It is exercised here by
    bypassing the approval check on purpose and giving a cap too small for the second call."""
    monkeypatch.setattr(p, "check_approval", lambda approval, frozen, total: None)
    client = FakeClient([ok(STORED[("v2-3", 1)]) for _ in range(20)])
    out = p.run_pilot(client, {"approved_cap_usd": 0.08})
    assert out["status"] == "stopped" and out["reason"] == "cap would be exceeded"
    assert len(client.calls) == 1, "the first CONTROL call fits; the INVENTORY call would exceed the cap and is not made"


def test_a_complete_run_on_stored_answers_gives_no_difference_when_both_arms_retain_the_duties():
    client = FakeClient([ok(STORED[("v2-3", 1)]) for _ in range(20)])
    out = p.run_pilot(client, approval())
    assert out["status"] == "complete" and out["decision"]["verdict"] == "NO_PRIMARY_DIFFERENCE"
    assert out["decision"]["primary_retained_all_calls"] == {"CONTROL": 10, "INVENTORY": 10}


def test_a_guard_regression_blocks_the_verdict_even_when_the_primary_outcome_improves():
    # CONTROL = v2-3 call 5 (loses all 16 duties, passes the frozen FAM_OR check); INVENTORY = call 1 (keeps them, fails FAM_OR).
    client = FakeClient([ok(STORED[("v2-3", 5)] if arm == "CONTROL" else STORED[("v2-3", 1)]) for arm, _ in p.schedule()])
    out = p.run_pilot(client, approval())
    assert out["status"] == "complete"
    assert out["decision"]["guard_regressions_paired"]["OR"] == 10
    assert out["decision"]["verdict"] == "BLOCKED_GUARD_REGRESSION"


# ── verdicts, from paired analyses ──────────────────────────────────────────────────────────────────────────────────────────
def test_a_primary_and_secondary_signal_needs_full_retention_loss_in_control_and_a_lower_review_median():
    d = p.decide(pairs(analysis(0, review=16), analysis(16, review=0)))
    assert d["verdict"] == "PRIMARY_AND_SECONDARY_SIGNAL"


def test_a_primary_signal_without_the_secondary_reduction_is_reported_as_primary_only():
    d = p.decide(pairs(analysis(0, review=3), analysis(16, review=2)))
    assert d["verdict"] == "PRIMARY_SIGNAL_ONLY"


def test_a_primary_loss_in_inventory_is_a_regression():
    d = p.decide(pairs(analysis(16), analysis(0)))
    assert d["verdict"] == "PRIMARY_REGRESSION"


def test_a_guard_regression_blocks_even_a_perfect_secondary_result():
    d = p.decide(pairs(analysis(0, review=16), analysis(16, guards={"AND": False}, review=0)))
    assert d["verdict"] == "BLOCKED_GUARD_REGRESSION" and d["guard_regressions_paired"]["AND"] == 10


def test_an_incomplete_run_draws_no_verdict():
    recs = pairs(analysis(16), analysis(16))[:-1]
    assert p.decide(recs)["verdict"] == "INCOMPLETE"


def test_a_guard_that_failed_in_control_and_still_fails_is_not_a_regression():
    d = p.decide(pairs(analysis(16, guards={"OR": False}), analysis(16, guards={"OR": False})))
    assert d["guard_regressions_paired"]["OR"] == 0 and d["verdict"] == "NO_PRIMARY_DIFFERENCE"


# ── guards on stored text ───────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_primary_counts_all_sixteen_duties_on_a_stored_answer_and_none_on_a_call_with_no_duty_items():
    assert p.analyse_answer(STORED[("v2-3", 1)], REQUESTS)["duty_retained"] == 16
    lost = p.analyse_answer(STORED[("v2-3", 5)], REQUESTS)
    assert lost["duty_retained"] == 0 and lost["primary_retained_all"] is False


def test_a_non_json_answer_fails_every_guard_and_retains_nothing():
    a = p.analyse_answer("not json at all", REQUESTS)
    assert a["valid_json"] is False and a["duty_retained"] == 0
    assert not any(a["guards"].values())


def test_the_schema_guard_fails_on_a_missing_key_and_on_a_changed_category_set():
    obj = json.loads(STORED[("v2-3", 1)])
    assert p.schema_ok(obj) is True
    missing = {k: v for k, v in obj.items() if k != "warnings"}
    assert p.schema_ok(missing) is False
    renamed = json.loads(json.dumps(obj))
    renamed["categories"]["extras"] = renamed["categories"].pop("other_requirements")
    assert p.schema_ok(renamed) is False


def test_the_invented_guard_fails_on_a_source_text_that_is_not_in_the_jd():
    obj = json.loads(STORED[("v2-3", 1)])
    obj["categories"]["skills"].append({"text": "x", "importance": "required", "source_text": "a sentence that is not in the job description",
                                         "origin": "stated", "alternatives": None, "experience": None})
    a = p.analyse_answer(json.dumps(obj), REQUESTS)
    assert a["guards"]["invented"] is False


def test_the_frozen_guard_groups_match_the_frozen_check_names():
    checks = {"C_EXP_OR", "C_FAM_OR", "C_AND", "C_LOCAL", "C_LOCATION", "C_REPORTING"}
    used = {k for keys in p.GUARD_CHECKS.values() for k in keys}
    assert used == checks


# ── freeze, gate and CLI ────────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_manifest_changes_when_any_frozen_input_changes():
    changed = dict(REQUESTS)
    changed["INVENTORY"] = [REQUESTS["INVENTORY"][0], {"role": "user", "content": REQUESTS["INVENTORY"][1]["content"] + " "}]
    assert p.freeze(changed)["manifest_sha256"] != FROZEN["manifest_sha256"]


def test_the_manifest_hash_covers_both_complete_requests_and_every_script_it_depends_on():
    for key in ("control_request_sha256", "inventory_request_sha256", "jd_sha256", "annotation_sha256", "protocol_sha256",
                "pilot_script_sha256", "scorer_sha256", "replay_logic_sha256", "settings_sha256"):
        assert key in FROZEN
    assert set(FROZEN["replay_logic_sha256"]) == {"replay.py", "coverage.py", "inventory.py", "preview.py"}


def test_the_cli_offers_only_a_dry_run():
    with pytest.raises(SystemExit):
        p.main([])


def test_importing_and_dry_running_loads_no_network_client():
    code = ("import sys; sys.path.insert(0, '.'); import pilot; pilot.dry_run(); "
            "print('openai' in sys.modules or 'httpx' in sys.modules)")
    out = subprocess.run([sys.executable, "-I", "-c", code], cwd=str(p.HERE), capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "False"


def test_the_justification_numbers_match_the_protocol():
    j = p.justification()
    assert j["p_at_least_one_loss_if_rate_is_0.2_by_pairs"][10] == 0.893
    assert list(j["clopper_pearson_95_for_loss_rate"]) == [0.005, 0.716]
    assert "0.672" in PROTOCOL_TEXT and "0.893" in PROTOCOL_TEXT
