"""Offline tests: the audit of the stored no-examples run, and the stronger-model comparison (dry-run and fake-client paths only).

No network and no paid call. The audit reproduces the stored scores with the frozen scorer and checks the evidence hashes; the
comparison's plan, cap, pricing refusal, stop-on-failure, model-match, cap guard and output protection are checked with a fake client.
"""
import json
import pathlib

import pytest

import scripts.requirements_v2_tc20_compare as C
import scripts.requirements_v2_tc20_eval as ev
import scripts.requirements_v2_tc20_noexamples as nx

AUDIT = pathlib.Path(__file__).parent / "fixtures" / "requirements_v2_technical_coordinator_20" / "audit"
NOEX = AUDIT / "run_tc20_v2-3-noex"
STORED = AUDIT / "run_tc20_v2-3_v2-4"
USER_SHA = "ff6b334ad32300a05933cf96284c6c253d86ed9442ffcac37a8b543a8c240473"


@pytest.fixture(scope="module")
def jd_and_labels():
    return ev.load_inputs()


@pytest.fixture(scope="module")
def noex_rows():
    return [json.loads(x) for x in (NOEX / "calls.jsonl").read_text(encoding="utf-8").splitlines()]


@pytest.fixture(scope="module")
def stored_v23_rows():
    rows = [json.loads(x) for x in (STORED / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    return [r for r in rows if r["arm"] == "v2-3"]


def good_answer(**over):
    """A minimal answer that satisfies the output contract (used for schema and weight tests)."""
    cats = {c: [] for c in C.CATEGORY_KEYS}
    obj = {"scoreability": {"status": "scoreable", "reason": ""}, "categories": cats,
           "category_weights": {c: 0 for c in C.CATEGORY_KEYS}, "non_scoreable_requirements": [],
           "post_hiring_conditions": [], "informational_items": [], "warnings": []}
    obj.update(over)
    return obj


# ── 1. provenance and reproduction of the stored no-examples run ─────────────────────────────────────────────────────

def test_stored_evidence_is_the_uploaded_bytes():
    assert nx.sha256_bytes((NOEX / "calls.jsonl").read_bytes()) == "28632bd29e407bc0b04716ca3ea258f8bfa5ffec185658815c19619460e3239b"
    assert nx.sha256_bytes((NOEX / "manifest.json").read_bytes()) == "d0e7bd71bce08e6c97a6c59f536277b85d638582cd452d79b2256e4c5d907047"
    assert nx.sha256_bytes((NOEX / "scored.json").read_bytes()) == "344f837487da06f868278fe3fe53824a39e7e9df43cab702524f0b33bc326d43"


def test_manifest_pins_the_variant_and_the_stored_user_message(noex_rows):
    m = json.loads((NOEX / "manifest.json").read_text(encoding="utf-8"))
    assert m["prompt"]["sha256"] == nx.VARIANT_SHA256 and m["prompt"]["base_sha256"] == nx.BASE_SHA256
    assert m["input"]["system_sha256"] == nx.VARIANT_SHA256 and m["input"]["user_sha256"] == USER_SHA
    assert all(r["input"] == m["input"] for r in noex_rows)
    assert m["plan"]["retries"] == 0 and m["plan"]["cap_usd"] == 0.03 and m["plan"]["worst_case_total_usd"] == pytest.approx(0.02484)


def test_all_five_calls_succeeded_on_the_pinned_model(noex_rows):
    assert [r["call"] for r in noex_rows] == [1, 2, 3, 4, 5]
    assert all(r["error"] is None and r["model"] == ev.MODEL and r["finish_reason"] == "stop" for r in noex_rows)
    assert sum(r["cost_usd"] for r in noex_rows) == pytest.approx(0.007568, abs=2e-6)   # per-call costs are rounded to 6 places


def test_the_frozen_scorer_reproduces_the_stored_scores(noex_rows, jd_and_labels):
    jd, labels = jd_and_labels
    stored = json.loads((NOEX / "scored.json").read_text(encoding="utf-8"))
    assert nx.report(noex_rows, jd, labels) == stored["report"]


def test_raw_answers_repeat_in_two_pairs(noex_rows):
    raws = [r["raw"] for r in noex_rows]
    assert raws[1] == raws[3] and raws[2] == raws[4] and raws[0] != raws[1] != raws[2]


def test_the_analysis_file_is_reproducible(noex_rows, stored_v23_rows, jd_and_labels):
    jd, labels = jd_and_labels
    stored = json.loads((NOEX / "analysis.json").read_text(encoding="utf-8"))
    assert C.analysis_document(noex_rows, stored_v23_rows, jd, labels) == stored


def test_the_observed_defects_hold_for_all_five_answers(noex_rows, jd_and_labels):
    jd, labels = jd_and_labels
    f = [C.facts(r["raw"], jd, labels) for r in noex_rows]
    assert all(x["checks"]["C_COMP"] for x in f)                          # the three competency lines are extracted
    assert all(x["weights"]["total"] == 300 for x in f)                   # 100 / 100 / 100
    assert all(x["soft_skills_items"] == 0 for x in f)                    # soft_skills left empty
    assert all(x["javascript_category"] == ["experience"] for x in f)     # web knowledge in experience
    assert all(x["arabic_english_single_item"] for x in f)                # Arabic and English merged
    assert all(x["education_alternatives_retained"] is False for x in f)  # no alternatives on the degree item
    assert all(x["responsibility_items"] == 0 for x in f)                 # no duties
    assert all(sum(x["condition_items"].values()) == 0 for x in f)        # all condition lists empty


def test_the_schema_defects_are_the_stored_missing_keys(noex_rows, jd_and_labels):
    jd, labels = jd_and_labels
    f = [C.facts(r["raw"], jd, labels) for r in noex_rows]
    assert sum(x["schema_problems"].count("item_missing:alternatives") for x in f) == 16
    assert sum(x["schema_problems"].count("item_missing:experience") for x in f) == 2
    assert [bool(x["schema_problems"]) for x in f] == [True, True, False, True, False]


def test_the_stored_baseline_is_schema_compliant_and_keeps_its_education_alternatives(stored_v23_rows, jd_and_labels):
    jd, labels = jd_and_labels
    f = [C.facts(r["raw"], jd, labels) for r in stored_v23_rows]
    assert all(x["schema_problems"] == [] for x in f)
    assert all(x["education_alternatives_retained"] is True for x in f)
    assert all(x["weights"]["total"] == 100 for x in f)


# ── 2. schema and weight checks ──────────────────────────────────────────────────────────────────────────────────────

def test_a_full_contract_answer_has_no_schema_problems():
    assert C.schema_problems(good_answer()) == []


def test_schema_problems_name_each_departure():
    assert C.schema_problems([1, 2]) == ["not_an_object"]
    assert "top_missing:warnings" in C.schema_problems({k: v for k, v in good_answer().items() if k != "warnings"})
    bad = good_answer()
    bad["categories"]["skills"] = [{"text": "x", "importance": "maybe", "importance_cue": None, "source_text": "x",
                                    "origin": "stated", "alternatives": ["only one"], "experience": None}]
    problems = C.schema_problems(bad)
    assert "item_importance_invalid" in problems and "item_alternatives_invalid" in problems
    assert "item_missing:experience" not in problems


def test_an_experience_object_outside_experience_is_a_problem():
    bad = good_answer()
    bad["categories"]["skills"] = [{"text": "x", "importance": "required", "importance_cue": None, "source_text": "x",
                                    "origin": "stated", "alternatives": None, "experience": {"subject": "s", "min_years": 1}}]
    assert "item_experience_outside_experience" in C.schema_problems(bad)


def test_weights_above_100_or_not_whole_are_not_valid():
    obj = good_answer()
    obj["category_weights"]["skills"] = 150
    assert "weight_not_whole_0_100" in C.schema_problems(obj)
    assert C.weight_validity(obj)["whole_0_100"] is False


def test_a_raw_total_of_300_is_within_the_contract_and_usable_when_a_required_category_is_positive():
    obj = good_answer()
    obj["categories"]["skills"] = [{"text": "x", "importance": "required", "importance_cue": None, "source_text": "x",
                                    "origin": "stated", "alternatives": None, "experience": None}]
    obj["category_weights"].update({"skills": 100, "experience": 100, "education": 100})
    w = C.weight_validity(obj)
    assert w["whole_0_100"] and w["usable"] and w["total"] == 300 and not w["total_is_100"]
    assert w["positive_in_category_without_required"] == ["education", "experience"]


def test_weights_with_no_required_item_are_not_usable():
    obj = good_answer()
    obj["category_weights"]["skills"] = 50
    assert C.weight_validity(obj)["usable"] is False


# ── 3. the stronger-model comparison: plan, pricing refusal, dry-run ─────────────────────────────────────────────────

def test_the_plan_is_five_calls_within_the_cap(jd_and_labels):
    jd, _ = jd_and_labels
    p = C.plan(jd)
    assert len(p["calls"]) == C.CALLS == 5
    assert p["model"] == "gpt-4.1-2025-04-14" and p["retries"] == 0 and p["temperature"] == 0.1
    assert p["max_tokens"] == 6000 and p["timeout_s"] == 90 and p["response_format"] == "json_object"
    assert p["cap_usd"] == C.CAP_USD == 0.40
    assert 0 < p["worst_case_total_usd"] <= 0.40
    assert p["worst_case_total_usd"] == pytest.approx(0.387340, abs=1e-6)


def test_the_plan_refuses_a_total_above_the_cap(jd_and_labels):
    jd, _ = jd_and_labels
    with pytest.raises(C.ComparisonError, match="exceeds the cap"):
        C.plan(jd, cap=0.10)


def test_the_comparison_uses_the_unchanged_full_v23_prompt_and_the_stored_user_message(jd_and_labels):
    jd, _ = jd_and_labels
    msgs = C.messages(jd)
    assert msgs[0]["content"] == ev.ARMS["v2-3"]["file"].read_text(encoding="utf-8")
    assert nx.sha256_bytes(msgs[0]["content"].encode("utf-8")) == ev.ARMS["v2-3"]["sha256"]
    assert nx.sha256_bytes(msgs[1]["content"].encode("utf-8")) == USER_SHA


def test_the_paid_path_refuses_while_pricing_is_unverified(monkeypatch, tmp_path):
    def boom(*_a, **_k):
        raise AssertionError("no call may be made while pricing is unverified")
    monkeypatch.setattr(C, "openai_call_model", boom)
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    with pytest.raises(C.ComparisonError, match="not verified"):
        C.main(["--execute", "--out", str(tmp_path / "x")])
    assert not (tmp_path / "x").exists()


def test_the_dry_run_makes_no_network_call_and_says_pricing_is_unverified(monkeypatch, capsys):
    def boom(*_a, **_k):
        raise AssertionError("the dry-run must not reach the network")
    monkeypatch.setattr(C, "openai_call_model", boom)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert C.main([]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["pricing_verified"] is False and out["plan"]["worst_case_total_usd"] <= 0.40
    assert out["user_message_sha256"] == USER_SHA and out["disclosed_differences"]


# ── 4. the protected run (fake client only) ──────────────────────────────────────────────────────────────────────────

class FakeBadRequest(Exception):
    def __init__(self, message, body=None, status_code=400):
        super().__init__(message)
        self.body = body
        self.status_code = status_code


def fake_client(raw, model=None, usage=None, fail_on=None, exc=None):
    seen = []

    def call(messages):
        seen.append(messages)
        if fail_on == len(seen):
            raise exc
        return {"raw": raw, "finish_reason": "stop", "model": model or C.MODEL,
                "usage": usage or {"prompt_tokens": 1000, "completion_tokens": 500}}
    call.seen = seen
    return call


def test_success_path_writes_the_manifest_and_five_records(tmp_path, jd_and_labels, noex_rows):
    jd, labels = jd_and_labels
    client = fake_client(noex_rows[0]["raw"])
    out = tmp_path / "run"
    res = C.run(out, jd, labels, client)
    assert res["calls_attempted"] == 5 and res["successful_calls"] == 5 and res["stopped"] is None and len(client.seen) == 5
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert m["model"] == C.MODEL and m["prompt"]["sha256"] == ev.ARMS["v2-3"]["sha256"] and m["disclosed_differences"]
    assert m["input"]["user_sha256"] == USER_SHA
    rows = [json.loads(x) for x in (out / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["call"] for r in rows] == [1, 2, 3, 4, 5] and all(r["model_returned"] == C.MODEL for r in rows)


def test_a_different_returned_model_stops_the_run(tmp_path, jd_and_labels, noex_rows):
    jd, labels = jd_and_labels
    client = fake_client(noex_rows[0]["raw"], model="gpt-4o-mini-2024-07-18")
    res = C.run(tmp_path / "run", jd, labels, client)
    assert res["stopped"] == "error:ModelMismatch" and res["calls_attempted"] == 1 and len(client.seen) == 1


def test_the_run_stops_at_the_first_failure_and_keeps_a_sanitized_error(tmp_path, jd_and_labels, noex_rows):
    jd, labels = jd_and_labels
    echo = "request content: " + jd[:200]
    client = fake_client(noex_rows[0]["raw"], fail_on=2, exc=FakeBadRequest(echo, body={"error": {"code": "bad_param", "type": "invalid_request_error"}}))
    out = tmp_path / "run"
    res = C.run(out, jd, labels, client)
    assert len(client.seen) == 2 and res["stopped"] == "error:FakeBadRequest" and res["successful_calls"] == 1
    rows = [json.loads(x) for x in (out / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    err = rows[1]["error"]
    assert (err["http_status"], err["code"], err["type"]) == (400, "bad_param", "invalid_request_error")
    assert err["message"] is None and jd[:200] not in json.dumps(rows)


def test_the_cap_guard_stops_before_a_call_that_could_exceed_the_cap(tmp_path, jd_and_labels, noex_rows):
    jd, labels = jd_and_labels
    client = fake_client(noex_rows[0]["raw"], usage={"prompt_tokens": 2_000_000, "completion_tokens": 0})
    res = C.run(tmp_path / "run", jd, labels, client)
    assert res["stopped"] == "cap_guard" and res["calls_attempted"] == 1 and len(client.seen) == 1


def test_an_existing_output_directory_is_never_overwritten(tmp_path, jd_and_labels, noex_rows):
    jd, labels = jd_and_labels
    out = tmp_path / "run"
    out.mkdir()
    (out / "calls.jsonl").write_text("earlier evidence\n", encoding="utf-8")
    client = fake_client(noex_rows[0]["raw"])
    with pytest.raises(ev.PlanError, match="not empty"):
        C.run(out, jd, labels, client)
    assert client.seen == [] and (out / "calls.jsonl").read_text(encoding="utf-8") == "earlier evidence\n"


def test_the_report_keeps_groups_apart_and_is_unavailable_without_a_success(jd_and_labels, noex_rows):
    jd, labels = jd_and_labels
    rep = C.report(noex_rows, jd, labels)
    assert rep["status"] == "scored" and rep["groups_reported_separately"] is True
    assert rep["summary"]["official_checks_pass"]["C_COMP"] == 5
    assert rep["summary"]["education_alternatives_retained"] == 0 and rep["summary"]["schema_compliant"] == 2
    assert C.report([{"arm": C.ARM, "call": 1, "error": {"kind": "X"}}], jd, labels)["status"] == "unavailable"
