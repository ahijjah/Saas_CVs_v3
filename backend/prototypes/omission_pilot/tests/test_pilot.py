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


def arm(k, guards=None, review=0):
    """k of the PAIRS calls retain all 16 duty lines (the first k pairs); the rest lose them. Guards and review counts apply to every pair."""
    return [analysis(16 if j <= k else 0, guards, review) for j in range(1, p.PAIRS + 1)]


def pairs(control, inventory):
    """Records for both arms. Each argument is either one analysis (the same in every pair) or a list with one analysis per pair."""
    per = lambda x: x if isinstance(x, list) else [x] * p.PAIRS  # noqa: E731
    recs = []
    for k, (c, i) in enumerate(zip(per(control), per(inventory)), start=1):
        recs.append({"arm": "CONTROL", "pair": k, "analysis": c})
        recs.append({"arm": "INVENTORY", "pair": k, "analysis": i})
    return recs


# ── protocol and code agree ──────────────────────────────────────────────────────────────────────────────────────────────
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


def test_a_complete_run_with_equal_retention_is_inconclusive():
    client = FakeClient([ok(STORED[("v2-3", 1)]) for _ in range(20)])
    out = p.run_pilot(client, approval())
    assert out["status"] == "complete" and out["decision"]["verdict"] == "INCONCLUSIVE"
    assert out["decision"]["counts"]["INVENTORY"]["retained_all_duties"] == 10 and out["decision"]["fisher_two_sided_p"] == 1.0


def test_a_guard_regression_blocks_the_verdict_even_when_the_primary_outcome_improves():
    # CONTROL = v2-3 call 5 (loses all 16 duties, passes the frozen FAM_OR check); INVENTORY = call 1 (keeps them, fails FAM_OR).
    client = FakeClient([ok(STORED[("v2-3", 5)] if arm == "CONTROL" else STORED[("v2-3", 1)]) for arm, _ in p.schedule()])
    out = p.run_pilot(client, approval())
    assert out["status"] == "complete"
    assert out["decision"]["guard_regressions_paired"]["OR"] == 10
    assert out["decision"]["verdict"] == "BLOCKED_GUARD_REGRESSION"


# ── verdicts, from paired analyses ──────────────────────────────────────────────────────────────────────────────────────────
def test_a_guard_that_failed_in_control_and_still_fails_is_not_a_regression():
    d = p.decide(pairs(analysis(16, guards={"OR": False}), analysis(16, guards={"OR": False})))
    assert d["guard_regressions_paired"]["OR"] == 0 and d["verdict"] == "INCONCLUSIVE"


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
    assert j["p_at_least_one_control_loss_if_loss_rate_0.2_by_pairs"][10] == 0.893
    assert list(j["clopper_pearson_95_for_historical_loss_rate"]) == [0.0051, 0.7164]
    assert "0.672" in PROTOCOL_TEXT and "0.893" in PROTOCOL_TEXT


# ── statistical rules (the corrected interpretation) ───────────────────────────────────────────────────────────────────────────
def test_the_fisher_examples_are_exact():
    assert p.fisher_two_sided(10, 10, 9, 10) == 1.0, "10/10 against 9/10 is never significant"
    assert abs(p.fisher_two_sided(10, 10, 5, 10) - 0.0325) < 0.0001, "10/10 against 5/10: about 0.0325"


def test_the_fisher_test_is_symmetric_in_the_two_groups_and_bounded():
    for a, b in ((10, 5), (7, 3), (9, 0), (4, 4)):
        assert p.fisher_two_sided(a, 10, b, 10) == p.fisher_two_sided(b, 10, a, 10)
        assert 0.0 <= p.fisher_two_sided(a, 10, b, 10) <= 1.0
    assert p.fisher_two_sided(4, 10, 4, 10) == 1.0


def test_the_clopper_pearson_intervals_are_exact_at_the_extremes():
    assert p.clopper_pearson(10, 10) == (0.6915, 1.0)
    assert p.clopper_pearson(0, 10) == (0.0, 0.3085)
    lower, upper = p.clopper_pearson(5, 10)
    assert abs(lower - 0.1871) < 0.0005 and abs(upper - 0.8129) < 0.0005


def test_a_ten_of_ten_against_nine_of_ten_is_inconclusive_with_p_one():
    d = p.decide(pairs(arm(9), arm(10)))
    assert d["verdict"] == "INCONCLUSIVE" and d["fisher_two_sided_p"] == 1.0


def test_a_ten_of_ten_against_five_of_ten_is_supported():
    d = p.decide(pairs(arm(5), arm(10)))
    assert d["verdict"] == "PRIMARY_SUPPORTED" and d["fisher_two_sided_p"] < p.FISHER_ALPHA


def test_a_higher_inventory_that_is_not_significant_is_inconclusive_not_supported():
    d = p.decide(pairs(arm(8), arm(10)))
    assert d["verdict"] == "INCONCLUSIVE" and d["fisher_two_sided_p"] >= p.FISHER_ALPHA


def test_a_lower_inventory_is_a_primary_regression_even_when_not_significant():
    d = p.decide(pairs(arm(10), arm(9)))
    assert d["verdict"] == "PRIMARY_REGRESSION"


def test_a_guard_regression_blocks_even_a_supported_primary_result():
    d = p.decide(pairs(arm(5), arm(10, guards={"AND": False})))
    assert d["verdict"] == "BLOCKED_GUARD_REGRESSION" and d["fisher_two_sided_p"] < p.FISHER_ALPHA


def test_any_single_guard_regression_blocks_the_verdict():
    for g in p.GUARD_NAMES:
        d = p.decide(pairs(arm(5, guards={g: True}), arm(10, guards={g: False})))
        assert d["verdict"] == "BLOCKED_GUARD_REGRESSION", g


def test_review_candidate_counts_are_descriptive_and_never_change_the_verdict():
    base = p.decide(pairs(arm(5, review=0), arm(10, review=0)))
    many_fewer = p.decide(pairs(arm(5, review=16), arm(10, review=0)))
    assert base["verdict"] == many_fewer["verdict"] == "PRIMARY_SUPPORTED"
    assert many_fewer["review_candidates_descriptive_only"]["CONTROL"]["median"] == 16


def test_an_incomplete_run_draws_no_verdict_and_no_p_value():
    d = p.decide(pairs(arm(10), arm(10))[:-1])
    assert d["verdict"] == "INCOMPLETE" and "fisher_two_sided_p" not in d


def test_the_decision_reports_exact_counts_proportions_and_intervals():
    d = p.decide(pairs(arm(5), arm(10)))
    assert d["counts"]["INVENTORY"] == {"retained_all_duties": 10, "calls": 10, "proportion": 1.0,
                                        "ci95_clopper_pearson": [0.6915, 1.0]}
    assert d["counts"]["CONTROL"]["retained_all_duties"] == 5 and d["counts"]["CONTROL"]["proportion"] == 0.5


def test_with_inventory_at_ten_of_ten_the_control_count_must_be_at_most_five():
    j = p.justification()
    assert j["with_inventory_10_of_10_the_largest_control_count_that_is_supported"] == 5


def test_the_power_at_the_historical_rate_is_reported_as_low():
    j = p.justification()
    assert j["exact_power_of_the_rule"]["CONTROL 0.8 vs INVENTORY 1.0"] < 0.1


def test_the_protocol_states_the_fisher_rule_and_the_worked_examples():
    assert "two-sided" in PROTOCOL_TEXT and "p < 0.05" in PROTOCOL_TEXT
    assert "p = 1.0" in PROTOCOL_TEXT and "0.0325" in PROTOCOL_TEXT
    assert "PRIMARY_SUPPORTED" in PROTOCOL_TEXT and "INCONCLUSIVE" in PROTOCOL_TEXT
    assert "PRIMARY_AND_SECONDARY_SIGNAL" not in PROTOCOL_TEXT and "PRIMARY_SIGNAL_ONLY" not in PROTOCOL_TEXT
    assert "NO_PRIMARY_DIFFERENCE" not in PROTOCOL_TEXT
