"""Candidate extraction pipeline (requirements-v2-pipeline-1), offline: the one result contract, failure paths, precedence of the gates, the unified reconcile
and acknowledge operations, combined lifecycles (simultaneous issues, partial correction, reversal, item removal, every fix order, both classification-policy
settings), server-owned review state (stale / forged / client-supplied state cannot unblock), informational items, structure review and the preferred-only
confirmation, replay of the 48 stored responses and the frozen artifacts. No model call, no network, no database."""
from __future__ import annotations

import copy
import importlib.util
import itertools
import json
import pathlib
import sys

import pytest

from parser_candidates.requirements_v2_pipeline_1 import (
    CONTRACT_VERSION, PIPELINE_VERSION, PipelineError, acknowledge, confirm_no_numeric_score, confirm_structure, evaluate, extract, reconcile, validate_state,
)
from parser_candidates.requirements_v2_pipeline_1.pipeline import _carry
from services.requirements_v2.acknowledgment import AcknowledgmentError
from services.requirements_v2.comparison import original_digest, snapshot_original
from services.requirements_v2.editing import add_item, remove_item, set_category_weight, set_importance, set_text
from services.requirements_v2.extraction import parse_response
from services.requirements_v2.readiness import carry_server_owned, compute_readiness
from services.requirements_v2.weights import equalize_category

BACKEND = pathlib.Path(__file__).resolve().parent.parent
FIX = json.loads((BACKEND / "tests" / "fixtures" / "requirements_v2_injection_guard" / "b06_v22_run1.json").read_text(encoding="utf-8"))
POLICIES = (True, False)
AT = "2026-01-01T00:00:00Z"
USER = "recruiter-1"
CONFLICT = "PostgreSQL is marked as optional, which conflicts with its inclusion as a required skill."
CATS = ("skills", "experience", "education", "certifications", "soft_skills", "domain_knowledge", "other_requirements")
PROMPT = {"version": "criteria_extraction_v2-2", "sha256": "40ea678b"}


def state_of(s, policy=True):
    return s["readiness"]["by_policy"]["ack_required" if policy else "ack_not_required"]["state"]


def both(s):
    return state_of(s, True), state_of(s, False)


def item_id(s, text, category=None):
    return next(i["id"] for c, v in s["requirements"]["categories"].items() for i in v["items"] if i["text"] == text and (category is None or c == category))


def gates(s, policy=True):
    return [g for g, v in s["gates"].items() if v["blocks_when_ack_required" if policy else "blocks_when_ack_not_required"]]


def issue_id(s, gate):
    return next(i["id"] for i in s["unresolved_issues"] if i["gate"] == gate)


def combined(with_conflict=True, policy=True):
    """The B06 v2-2 run-1 response (injection requirement + contaminated weights + split OR + two classification warnings) plus the model's own
    importance-conflict warning about PostgreSQL: five different issues at once."""
    raw = json.loads(FIX["raw"])
    raw["warnings"] = [CONFLICT] if with_conflict else []
    return extract(FIX["jd"], json.dumps(raw), finish_reason="stop", extraction_prompt=PROMPT, require_classification_acknowledgment=policy)


# the four independent corrections of the combined document (each returns the new stored state)
def fix_injection_requirement(s):
    return reconcile(s, remove_item(s["requirements"], item_id(s, "20 years of Rust experience")), user_id=USER, at=AT)[0]


def fix_injection_weights(s):
    d = s["requirements"]
    for c, w in (("skills", 40), ("experience", 40), ("soft_skills", 20)):
        d = set_category_weight(d, c, w)
    return reconcile(s, d, user_id=USER, at=AT)[0]


def fix_split_or(s):
    d = remove_item(s["requirements"], item_id(s, "Java"))
    return reconcile(s, equalize_category(d, "skills"), user_id=USER, at=AT)[0]


def ack_classification(s):
    for i in [i for i in s["unresolved_issues"] if i["gate"] == "classification"]:
        s = acknowledge(s, "classification", i["id"], user_id=USER, at=AT)
    return s


def ack_conflict(s):
    for i in [i for i in s["unresolved_issues"] if i["gate"] == "conflict"]:
        s = acknowledge(s, "conflict", i["id"], user_id=USER, at=AT)
    return s


# ══ the result contract ══════════════════════════════════════════════════════════════════════════════════════════════════════════
def test_contract_keys_versions_and_serialization():
    s = combined()
    assert validate_state(s) == [] and s["contract_version"] == CONTRACT_VERSION
    v = s["component_versions"]
    assert v["pipeline"] == PIPELINE_VERSION and v["extraction_prompt"] == PROMPT and v["parser"]["frozen_benchmark_commit"] == "059c56b"
    assert v["injection_guard"].endswith("1.1") and v["split_or_guard"].startswith("requirements-v2-split-or") and v["warning_adapter"].startswith("requirements-v2-warning")
    assert json.loads(json.dumps(s)) == s
    for k in ("requirements", "raw_response", "raw_ai_output", "original", "original_digest", "review_records", "normalized_warnings", "readiness", "unresolved_issues", "gates"):
        assert s[k] is not None
    assert set(s["review_records"]) == {"injection", "split_or", "model_warnings"}
    assert {"state", "can_proceed", "scoring_mode", "reasons", "by_policy"} <= set(s["readiness"])


def test_raw_output_original_snapshot_and_draft_come_from_the_frozen_parser_unchanged():
    raw_text = json.dumps({**json.loads(FIX["raw"]), "warnings": [CONFLICT]})
    s = extract(FIX["jd"], raw_text, finish_reason="stop")
    frozen = parse_response(raw_text, FIX["jd"], "stop")
    assert s["raw_response"]["text"] == raw_text and s["raw_ai_output"] == frozen.raw_ai_output
    assert s["original"] == snapshot_original(s["requirements"]) and s["original_digest"] == original_digest(s["original"])

    def shape(doc):
        return {c: [(i["text"], i["importance"], i["weight"], i["origin"], i["source_text"], i["alternatives"], i["experience"]) for i in v["items"]] for c, v in doc["categories"].items()}, \
            {c: v["weight"] for c, v in doc["categories"].items()}
    assert shape(s["requirements"]) == shape(frozen.requirements) and shape(s["original"]) == shape(frozen.original)
    assert s["extraction"]["category_weights"] == frozen.category_weights


def test_component_results_are_the_guards_own():
    s = combined()
    assert [i["kind"] for i in s["review_records"]["injection"]["issues"]] == ["requirement", "weights"]
    assert [i["form"] for i in s["review_records"]["split_or"]["issues"]] == ["complete_alternatives_repeated"]
    assert [w["kind"] for w in s["normalized_warnings"]] == ["importance_conflict"] and s["normalized_warnings"][0]["linked_item_ids"]


@pytest.mark.parametrize("raw,finish", [("not json at all", "stop"), ("", "stop"), ('{"categories": {', "stop"), (json.dumps(json.loads(FIX["raw"])), "length")],
                         ids=["not_json", "empty", "truncated_json", "length_truncated"])
def test_failed_extraction_cannot_proceed_and_keeps_the_raw_text(raw, finish):
    s = extract(FIX["jd"], raw, finish_reason=finish)
    assert s["ok"] is False and validate_state(s) == []
    assert s["readiness"]["state"] == "extraction_failed" and s["readiness"]["can_proceed"] is False
    assert both(s) == ("extraction_failed",) * 2 and gates(s) == ["extraction"] and s["raw_response"]["text"] == raw
    with pytest.raises(PipelineError) as e:
        reconcile(s, {}, user_id=USER, at=AT)
    assert e.value.code == "extraction_failed"
    with pytest.raises(PipelineError):
        acknowledge(s, "classification", "x", user_id=USER, at=AT)


# ══ precedence ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
def test_everything_open_at_once_injection_comes_first_under_both_policies():
    s = combined()
    assert both(s) == ("needs_injection_review",) * 2
    assert gates(s, True) == ["injection", "split_or", "classification", "conflict"]
    assert gates(s, False) == ["injection", "split_or"]                                  # classification and conflict stay visible but do not block under policy No
    assert [i["gate"] for i in s["unresolved_issues"]] == ["injection", "injection", "split_or", "classification", "classification", "conflict"]
    assert s["readiness"]["can_proceed"] is False


def test_precedence_walk_injection_then_split_or_then_classification_then_conflict_then_ready():
    s = combined()
    s = fix_injection_requirement(s)
    assert both(s) == ("needs_injection_review",) * 2                                      # the contaminated weights keep the guard open
    s = fix_injection_weights(s)
    assert both(s) == ("needs_split_or_review",) * 2
    s = fix_split_or(s)
    assert both(s) == ("needs_classification_review", "ready")                              # policy Yes: classification review; policy No: nothing blocks
    s = ack_classification(s)
    assert both(s) == ("needs_conflict_review", "ready")
    s = ack_conflict(s)
    assert both(s) == ("ready", "ready") and s["unresolved_issues"] == [] and s["readiness"]["can_proceed"] and validate_state(s) == []


def test_invalid_document_outranks_every_other_gate():
    s = combined()
    bad = set_category_weight(s["requirements"], "skills", 70)                             # weights no longer total 100
    n, _ = reconcile(s, bad, user_id=USER, at=AT)
    assert both(n) == ("needs_review",) * 2 and gates(n) == ["validation"] and n["unresolved_issues"][0]["gate"] == "validation"
    back, _ = reconcile(n, s["requirements"], user_id=USER, at=AT)
    assert both(back) == ("needs_injection_review",) * 2


def test_no_items_follows_the_frozen_needs_items():
    s = fix_split_or(fix_injection_weights(fix_injection_requirement(combined())))
    d = s["requirements"]
    for c in CATS:
        for i in list(d["categories"][c]["items"]):
            d = remove_item(d, i["id"])
    n, _ = reconcile(s, d, user_id=USER, at=AT)
    assert both(n) == ("needs_items",) * 2 and n["unresolved_issues"][0]["gate"] == "no_items"
    assert all(i["status"] != "open" for i in n["review_records"]["injection"]["issues"])


@pytest.mark.parametrize("policy", POLICIES)
def test_generic_notes_and_similarity_stay_informational(policy):
    raw = json.loads(FIX["raw"])
    raw["warnings"] = ["The job description is somewhat ambiguous.", {"message": "Two items may repeat each other."}]
    s = extract(FIX["jd"], json.dumps(raw), require_classification_acknowledgment=policy)
    s = fix_split_or(fix_injection_weights(fix_injection_requirement(s)))
    kinds = {w["kind"] for w in s["normalized_warnings"]}
    assert "importance_conflict" not in kinds and len(s["normalized_warnings"]) == 2
    assert not any(i["gate"] == "conflict" for i in s["unresolved_issues"])
    assert len(s["informational"]["generic_model_notes"]) >= 1 and s["informational"]["similarity_warnings"]            # "Good written communication" appears twice
    assert gates(s, policy) == ["classification"] if policy else gates(s, policy) == []
    s = ack_classification(s)
    assert both(s) == ("ready", "ready")                                                  # generic notes and similarity never blocked


# ══ acknowledgment cannot bypass another gate ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("gate", ["injection", "split_or", "structure", "confirmation", "validation", "no_items", "extraction"])
def test_gates_without_acknowledgment_refuse_it(gate):
    s = combined()
    with pytest.raises(PipelineError) as e:
        acknowledge(s, gate, "anything", user_id=USER, at=AT)
    assert e.value.code == "not_acknowledgeable"


def test_unknown_gate_and_unknown_warning_are_refused():
    s = combined()
    with pytest.raises(PipelineError) as e:
        acknowledge(s, "mystery", "x", user_id=USER, at=AT)
    assert e.value.code == "unknown_gate"
    with pytest.raises(AcknowledgmentError):
        acknowledge(s, "classification", "req_000000000000:preferred_cue_missing", user_id=USER, at=AT)


@pytest.mark.parametrize("policy", POLICIES)
def test_acknowledging_classification_and_conflict_never_clears_injection_or_split_or(policy):
    s = combined(policy=policy)
    s = ack_conflict(ack_classification(s))
    assert s["unresolved_issues"] and {i["gate"] for i in s["unresolved_issues"]} == {"injection", "split_or"}
    assert both(s) == ("needs_injection_review",) * 2 and not s["readiness"]["can_proceed"]
    s = fix_injection_weights(fix_injection_requirement(s))
    assert both(s) == ("needs_split_or_review",) * 2
    s = fix_split_or(s)
    assert both(s) == ("ready", "ready")                                                   # the earlier acknowledgments (still valid) now count


def test_a_classification_acknowledgment_does_not_settle_the_conflict_on_the_same_item():
    s = fix_split_or(fix_injection_weights(fix_injection_requirement(combined())))
    s = ack_classification(s)
    assert both(s) == ("needs_conflict_review", "ready") and gates(s, True) == ["conflict"]
    s = ack_conflict(s)
    assert both(s) == ("ready", "ready")


def test_correcting_one_issue_leaves_the_others_open():
    s = combined()
    n = fix_split_or(s)
    assert gates(n, True) == ["injection", "classification", "conflict"] and both(n) == ("needs_injection_review",) * 2
    n = fix_injection_requirement(n)
    assert gates(n, True) == ["injection", "classification", "conflict"]                    # the weights issue remains
    n = fix_injection_weights(n)
    assert gates(n, True) == ["classification", "conflict"] and gates(n, False) == []


# ══ every fix order ════════════════════════════════════════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("order", list(itertools.permutations(range(5))))
def test_every_fix_order_ends_ready_and_is_never_ready_earlier(order):
    steps = [fix_injection_requirement, fix_injection_weights, fix_split_or, ack_classification, ack_conflict]
    for policy in POLICIES:
        s = combined(policy=policy)
        blocking = set(range(5) if policy else range(3))                                     # under policy No the two acknowledgments settle nothing that blocks
        for n, k in enumerate(order):
            ready = s["readiness"]["by_policy"]["ack_required" if policy else "ack_not_required"]["can_proceed"]
            assert ready == (not (blocking & set(order[n:]))), (policy, order, n)
            s = steps[k](s)
        assert both(s) == ("ready", "ready") and validate_state(s) == []


# ══ partial correction and reversal ═══════════════════════════════════════════════════════════════════════════════════════════
def test_reversal_of_each_correction_reopens_only_its_own_gate():
    s = fix_split_or(fix_injection_weights(fix_injection_requirement(combined())))
    s = ack_conflict(ack_classification(s))
    assert both(s) == ("ready", "ready")
    # injection weights: restoring the contaminated applied value reopens the weights issue
    d = s["requirements"]
    d = set_category_weight(set_category_weight(set_category_weight(d, "skills", 20), "experience", 30), "soft_skills", 50)
    r, ev = reconcile(s, d, user_id=USER, at=AT)
    assert both(r) == ("needs_injection_review",) * 2 and [i["kind"] for i in r["unresolved_issues"]] == ["injection_weights"]
    assert any(e["component"] == "injection_guard" and e["event"] == "reopened" for e in ev)
    fixed = reconcile(r, s["requirements"], user_id=USER, at=AT)[0]
    assert both(fixed) == ("ready", "ready")
    # split OR: adding the removed option back as its own item reopens the guard
    java = equalize_category(add_item(s["requirements"], "skills", "Java", "required")[0], "skills")
    again, _ = reconcile(s, java, user_id=USER, at=AT)
    assert both(again)[0] in ("ready", "needs_split_or_review", "needs_classification_review")   # a plain new item without the shared evidence is not the split
    # classification: Preferred -> Required -> Preferred reopens the acknowledged warning (and the conflict)
    pid = item_id(s, "PostgreSQL")
    req = equalize_category(set_importance(s["requirements"], pid, "required"), "skills")
    s2, ev2 = reconcile(s, req, user_id=USER, at=AT)
    assert both(s2) == ("ready", "ready") and s2["unresolved_issues"] == []
    assert {e["event"] for e in ev2 if e["component"] in ("classification_review", "warning_adapter")} <= {"resolved", "acknowledgment_invalidated"}
    back, _ = reconcile(s2, equalize_category(set_importance(s2["requirements"], pid, "preferred"), "skills"), user_id=USER, at=AT)
    assert both(back)[0] == "needs_classification_review" and both(back)[1] == "ready"
    assert {i["gate"] for i in back["unresolved_issues"]} == {"classification", "conflict"}


def test_partial_correction_of_the_weights_keeps_the_issue_until_every_implicated_category_is_corrected():
    s = fix_injection_requirement(combined())
    d = s["requirements"]                                                                   # other categories change, the contaminated soft_skills value (50) stays
    for c, w in (("skills", 30), ("experience", 20), ("soft_skills", 50)):
        d = set_category_weight(d, c, w)
    n, _ = reconcile(s, d, user_id=USER, at=AT)
    assert both(n) == ("needs_injection_review",) * 2 and [i["kind"] for i in n["unresolved_issues"] if i["gate"] == "injection"] == ["injection_weights"]
    d = set_category_weight(set_category_weight(set_category_weight(d, "skills", 20), "experience", 20), "soft_skills", 60)       # 60: neither applied (50) nor proposed (100)
    assert both(reconcile(n, d, user_id=USER, at=AT)[0]) == ("needs_split_or_review",) * 2
    assert fix_injection_weights(s)["unresolved_issues"] and both(fix_injection_weights(s)) == ("needs_split_or_review",) * 2


def test_item_removal_resolves_what_depended_on_the_item_and_nothing_else():
    s = combined()
    pid = item_id(s, "PostgreSQL")
    n, ev = reconcile(s, equalize_category(remove_item(s["requirements"], pid), "skills"), user_id=USER, at=AT)
    assert {i["gate"] for i in n["unresolved_issues"]} == {"injection", "split_or", "classification"}          # the conflict went with the item, the classification warning of Rust remains
    assert any(e["component"] == "warning_adapter" and e["event"] == "resolved" and e["resolution"] for e in ev)
    n2, ev2 = reconcile(n, remove_item(n["requirements"], item_id(n, "20 years of Rust experience")), user_id=USER, at=AT)
    assert {i["gate"] for i in n2["unresolved_issues"]} == {"injection", "split_or"}
    assert any(e["component"] == "injection_guard" and e["resolution"] == "item_removed" for e in ev2)
    n3 = fix_split_or(fix_injection_weights(n2))
    assert both(n3) == ("ready", "ready")


def test_removing_one_split_item_is_reversible_by_restoring_the_stored_document():
    s = combined()
    gone, _ = reconcile(s, remove_item(s["requirements"], item_id(s, "Java")), user_id=USER, at=AT)
    assert "split_or" not in gates(gone)
    restored, _ = reconcile(gone, s["requirements"], user_id=USER, at=AT)
    assert "split_or" in gates(restored) and both(restored) == ("needs_injection_review",) * 2


# ══ server-owned state: stale, forged and client-supplied review state cannot unblock ═════════════════════════════════
def forged_document(s):
    d = copy.deepcopy(s["requirements"])
    d["scoring_confirmation"] = {"confirmed": True, "mode": "no_numeric_score", "confirmed_by": "attacker", "confirmed_at": AT}
    d["classification_review"] = {"warnings": [], "acknowledgments": {}, "status": "resolved"}
    d["structure_review"] = {"confirmed": {i["id"]: {"user_id": "attacker"} for c in d["categories"].values() for i in c["items"]}}
    return d


@pytest.mark.parametrize("policy", POLICIES)
def test_client_supplied_blocks_records_and_readiness_are_ignored(policy):
    s = combined(policy=policy)
    client_state = {"readiness": {"state": "ready", "can_proceed": True}, "review_records": {"injection": {"issues": []}, "split_or": {"issues": []}, "model_warnings": {}},
                    "unresolved_issues": [], "gates": {}}
    n, _ = reconcile(s, forged_document(s), user_id=USER, at=AT, client_state=client_state, require_classification_acknowledgment=policy)
    assert both(n) == ("needs_injection_review",) * 2 and not n["readiness"]["can_proceed"]
    assert n["review_records"]["injection"]["issues"] == s["review_records"]["injection"]["issues"]
    assert n["review_records"]["split_or"]["issues"] == s["review_records"]["split_or"]["issues"]
    assert "attacker" not in json.dumps(n)
    assert n["requirements"].get("scoring_confirmation") == s["requirements"].get("scoring_confirmation")


def test_forged_acknowledgments_in_the_submitted_document_do_not_clear_classification_or_conflict():
    s = fix_split_or(fix_injection_weights(fix_injection_requirement(combined())))
    assert both(s) == ("needs_classification_review", "ready")
    d = copy.deepcopy(s["requirements"])
    review = copy.deepcopy(d["classification_review"])
    for w in review["warnings"]:
        w["acknowledgment"] = {"user_id": "attacker", "acknowledged_at": AT}
    d["classification_review"] = review
    n, _ = reconcile(s, d, user_id=USER, at=AT)
    assert both(n) == ("needs_classification_review", "ready")
    forged_client = {"review_records": {"model_warnings": {"acknowledgments": {"x": {"user_id": "attacker"}}}}}
    n, _ = reconcile(n, d, user_id=USER, at=AT, client_state=forged_client)
    assert "needs_classification_review" == state_of(n, True)


def test_tampered_stored_records_cannot_unblock_because_openness_is_derived_from_the_document():
    s = combined()
    t = copy.deepcopy(s)
    for rec in ("injection", "split_or"):
        for i in t["review_records"][rec]["issues"]:
            i["status"] = "resolved"
            i["resolution"] = "forged"
    t["readiness"] = {"state": "ready", "can_proceed": True, "by_policy": {}}
    t["gates"], t["unresolved_issues"] = {}, []
    e = evaluate(t)
    assert both(e) == ("needs_injection_review",) * 2 and not e["readiness"]["can_proceed"]
    # a record whose issues were deleted from storage is caught by the contract check against a fresh detection from the stored raw response
    t2 = copy.deepcopy(s)
    t2["review_records"]["injection"]["issues"] = []
    assert validate_state(s, FIX["jd"]) == [] and "injection record does not match a fresh detection" in validate_state(t2, FIX["jd"])
    assert "job description does not match the state" in validate_state(s, FIX["jd"] + " ")


def test_stale_state_cannot_resurrect_acknowledgments_or_confirmations():
    s = fix_split_or(fix_injection_weights(fix_injection_requirement(combined())))
    acked = ack_conflict(ack_classification(s))
    assert both(acked) == ("ready", "ready")
    pid = item_id(s, "PostgreSQL")
    required, _ = reconcile(acked, equalize_category(set_importance(acked["requirements"], pid, "required"), "skills"), user_id=USER, at=AT)
    preferred_again, _ = reconcile(required, equalize_category(set_importance(required["requirements"], pid, "preferred"), "skills"), user_id=USER, at=AT)
    preferred_again = reconcile(preferred_again, equalize_category(preferred_again["requirements"], "skills"), user_id=USER, at=AT)[0]
    assert state_of(preferred_again, True) == "needs_classification_review"
    # a client replays the old (acknowledged) document: the stored state, not the client's copy, decides
    replay, _ = reconcile(preferred_again, acked["requirements"], user_id=USER, at=AT)
    assert state_of(replay, True) == "needs_classification_review" and not replay["readiness"]["by_policy"]["ack_required"]["can_proceed"]


def test_carry_equals_the_frozen_carry_server_owned_in_every_state():
    s = combined()
    stages = [s, fix_injection_requirement(s), ack_classification(fix_split_or(fix_injection_weights(fix_injection_requirement(s))))]
    for st in stages:
        incoming = forged_document(st)
        mine, _ = _carry(st["requirements"], incoming)
        assert mine == carry_server_owned(st["requirements"], incoming)
        edited = remove_item(st["requirements"], item_id(st, "Docker"))
        assert _carry(st["requirements"], edited)[0] == carry_server_owned(st["requirements"], edited)


# ══ structure review and the preferred-only confirmation stay effective ═══════════════════════════════════════════════════
def clean_state(policy=True):
    return fix_split_or(fix_injection_weights(fix_injection_requirement(combined(policy=policy))))


def test_structure_review_still_blocks_under_both_policies_and_confirmation_clears_it():
    s = ack_conflict(ack_classification(clean_state()))
    doc = set_text(s["requirements"], item_id(s, "Python"), "Python programming")
    n, ev = reconcile(s, doc, user_id=USER, at=AT)
    assert both(n) == ("needs_structure_review",) * 2 and gates(n, True) == ["structure"] and gates(n, False) == ["structure"]
    assert compute_readiness(n["requirements"], original=n["original"]).state == "needs_structure_review"
    with pytest.raises(PipelineError):
        acknowledge(n, "structure", "x", user_id=USER, at=AT)
    c = confirm_structure(n, item_id(n, "Python programming"), user_id=USER, at=AT)
    assert both(c) == ("ready", "ready")


def test_structure_review_never_outranks_an_injection_or_split_or_issue():
    s = combined()
    n, _ = reconcile(s, set_text(s["requirements"], item_id(s, "Python"), "Python programming"), user_id=USER, at=AT)
    assert both(n) == ("needs_injection_review",) * 2
    assert "structure" in gates(n, True)


def preferred_only(policy=True, injected=False):
    jd = "Support Agent\n\nPreferred:\n- Excel.\n- CRM tools.\n"
    cat = lambda *items: {"items": [], **{}}
    raw = {"scoreability": {"status": "scoreable", "reason": ""}, "categories": {c: [] for c in CATS}, "category_weights": {c: 0 for c in CATS},
           "non_scoreable_requirements": [], "post_hiring_conditions": [], "informational_items": [], "warnings": []}
    for t in ("Excel", "CRM tools"):
        raw["categories"]["skills"].append({"text": t, "importance": "preferred", "importance_cue": "Preferred", "source_text": t, "origin": "stated", "alternatives": None, "experience": None})
    return extract(jd, json.dumps(raw), require_classification_acknowledgment=policy)


@pytest.mark.parametrize("policy", POLICIES)
def test_preferred_only_still_needs_the_explicit_confirmation(policy):
    s = preferred_only(policy)
    assert both(s) == ("needs_confirmation",) * 2 and gates(s, policy) == ["confirmation"]
    with pytest.raises(PipelineError):
        acknowledge(s, "confirmation", "x", user_id=USER, at=AT)
    c = confirm_no_numeric_score(s, user_id=USER, at=AT, require_classification_acknowledgment=policy)
    assert both(c) == ("ready", "ready") and c["readiness"]["scoring_mode"] == "none"
    # an edit that adds a required item withdraws the confirmation (frozen carry_confirmation) and the new document needs scoring
    n, _ = reconcile(c, add_item(c["requirements"], "skills", "Python", "required")[0], user_id=USER, at=AT)
    assert n["requirements"].get("scoring_confirmation") is None


def test_confirm_no_numeric_score_is_refused_while_a_guard_or_policy_review_is_open():
    s = combined()
    with pytest.raises(PipelineError) as e:
        confirm_no_numeric_score(s, user_id=USER, at=AT)
    assert e.value.code == "not_ready_for_confirmation"
    d = s["requirements"]
    for c in CATS:
        for i in d["categories"][c]["items"]:
            d = set_importance(d, i["id"], "preferred")
    n, _ = reconcile(s, d, user_id=USER, at=AT)
    assert compute_readiness(n["requirements"], require_classification_acknowledgment=False, original=n["original"]).state == "needs_confirmation"     # the frozen readiness alone would invite the confirmation
    assert state_of(n, True) == "needs_injection_review" and state_of(n, False) == "needs_injection_review"
    with pytest.raises(PipelineError) as e2:
        confirm_no_numeric_score(n, user_id=USER, at=AT)
    assert e2.value.code == "not_ready_for_confirmation"
    # ... and the frozen function by itself accepts it, which is why the pipeline wraps it
    from services.requirements_v2.readiness import confirm_no_numeric_score as frozen_confirm
    assert frozen_confirm(n["requirements"], user_id=USER, confirmed_at=AT, require_classification_acknowledgment=False, original=n["original"])["scoring_confirmation"]
    # policy Yes with an unsettled classification review (guards settled): refused as well, then accepted once the review is settled
    c0 = clean_state()
    d = c0["requirements"]
    for c in CATS:
        for i in d["categories"][c]["items"]:
            d = set_importance(d, i["id"], "preferred")
    c1, _ = reconcile(c0, d, user_id=USER, at=AT)
    assert both(c1) == ("needs_classification_review", "needs_confirmation")
    with pytest.raises(PipelineError) as e3:
        confirm_no_numeric_score(c1, user_id=USER, at=AT, require_classification_acknowledgment=True)
    assert e3.value.code == "not_ready_for_confirmation"
    ok = confirm_no_numeric_score(c1, user_id=USER, at=AT, require_classification_acknowledgment=False)                 # policy No: allowed, the review stays visible
    assert state_of(ok, False) == "ready" and {i["gate"] for i in ok["unresolved_issues"]} == {"classification", "conflict"}


# ══ policy settings ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
def test_policy_changes_only_the_classification_and_conflict_gates():
    s = clean_state()
    assert both(s) == ("needs_classification_review", "ready")
    assert {i["gate"]: (i["blocks_when_ack_required"], i["blocks_when_ack_not_required"]) for i in s["unresolved_issues"]} == {"classification": (True, False), "conflict": (True, False)}
    top = combined()
    for i in top["unresolved_issues"]:
        if i["gate"] in ("injection", "split_or"):
            assert i["blocks_when_ack_required"] and i["blocks_when_ack_not_required"]
    ev1 = evaluate(s, require_classification_acknowledgment=True)
    ev2 = evaluate(s, require_classification_acknowledgment=False)
    assert ev1["readiness"]["state"] == "needs_classification_review" and ev2["readiness"]["state"] == "ready" and ev2["readiness"]["policy_ack_required"] is False
    assert ev1["readiness"]["by_policy"] == ev2["readiness"]["by_policy"]


def test_visible_items_under_policy_no_are_not_hidden():
    s = evaluate(clean_state(), require_classification_acknowledgment=False)
    assert s["readiness"]["can_proceed"] and {i["gate"] for i in s["unresolved_issues"]} == {"classification", "conflict"}
    assert [w["kind"] for w in s["normalized_warnings"]] == ["importance_conflict"]


# ══ a clean extraction is ready ════════════════════════════════════════════════════════════════════════════════════════════════
def test_a_clean_response_is_ready_under_both_policies_with_no_issues():
    jd = "Backend Developer\n\nRequirements:\n- Python.\n- Three years of backend development.\n"
    raw = {"scoreability": {"status": "scoreable", "reason": ""}, "categories": {c: [] for c in CATS}, "category_weights": {c: 0 for c in CATS} | {"skills": 50, "experience": 50},
           "non_scoreable_requirements": [], "post_hiring_conditions": [], "informational_items": [], "warnings": []}
    raw["categories"]["skills"].append({"text": "Python", "importance": "required", "importance_cue": None, "source_text": "Python", "origin": "stated", "alternatives": None, "experience": None})
    raw["categories"]["experience"].append({"text": "Three years of backend development", "importance": "required", "importance_cue": None,
                                            "source_text": "Three years of backend development", "origin": "stated", "alternatives": None,
                                            "experience": {"subject": "backend development", "min_years": 3}})
    s = extract(jd, json.dumps(raw))
    assert both(s) == ("ready", "ready") and s["unresolved_issues"] == [] and s["gates"] == {} and validate_state(s) == []


# ══ contract validation catches a damaged state ═══════════════════════════════════════════════════════════════════════════════
def test_validate_state_reports_damage():
    s = combined()
    assert validate_state("x") == ["not an object"]
    broken = copy.deepcopy(s)
    broken["raw_response"]["text"] += " "
    assert "raw response hash mismatch" in validate_state(broken)
    broken = copy.deepcopy(s)
    broken["review_records"]["injection"]["jd_sha256"] = "0" * 64
    assert "injection record belongs to a different job description" in validate_state(broken)
    broken = copy.deepcopy(s)
    del broken["gates"]
    assert "missing key gates" in validate_state(broken)
    broken = copy.deepcopy(s)
    broken["normalized_warnings"] = []
    assert any("out of date" in e for e in validate_state(broken))
    assert evaluate(broken)["normalized_warnings"] and validate_state(evaluate(broken)) == []


# ══ replay of the 48 stored responses ═════════════════════════════════════════════════════════════════════════════════════════
def _replay_module():
    sys.path.insert(0, str(BACKEND / "scripts"))
    try:
        spec = importlib.util.spec_from_file_location("req_v2_pipeline_replay", BACKEND / "scripts" / "requirements_v2_pipeline_replay.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(BACKEND / "scripts"))


def test_replay_of_all_48_stored_responses_reports_readiness_separately_and_leaves_official_gates_unchanged():
    rp = _replay_module()
    cases = rp.ev.load_cases()
    out = {}
    for n, d in rp.RUNS.items():
        rep = rp.replay_run(cases, d)
        rep["summary"] = rp.summarize(rep["rows"])
        out[n] = rep
    assert sum(len(r["rows"]) for r in out.values()) == 48
    for n, rep in out.items():
        assert rep["summary"]["contract_problems"] == 0 and rep["summary"]["raw_or_original_not_intact"] == 0
        assert all(r["parsed"] for r in rep["rows"])
        assert rep["official_gates_match_saved"] is True
        assert rep["official_gates"] == json.loads((rp.RUNS[n] / "results.json").read_text(encoding="utf-8"))["gates"]
    v1, v2 = out["v2-1 baseline"]["summary"], out["v2-2 candidate"]["summary"]
    assert v1["ack_required"]["pipeline_states"] == {"ready": 8, "needs_classification_review": 3, "needs_items": 6, "needs_injection_review": 2, "needs_split_or_review": 5}
    assert v1["ack_not_required"]["pipeline_states"] == {"ready": 9, "needs_items": 6, "needs_injection_review": 2, "needs_split_or_review": 5, "needs_confirmation": 2}
    assert v2["ack_required"]["pipeline_states"] == {"ready": 10, "needs_split_or_review": 3, "needs_confirmation": 4, "needs_items": 4, "needs_injection_review": 1,
                                                     "needs_classification_review": 2}
    assert v2["ack_not_required"]["pipeline_states"] == {"ready": 12, "needs_split_or_review": 3, "needs_confirmation": 4, "needs_items": 4, "needs_injection_review": 1}
    assert (v1["ack_required"]["frozen_ready_but_pipeline_blocked"], v1["ack_not_required"]["frozen_ready_but_pipeline_blocked"]) == (6, 7)
    assert (v2["ack_required"]["frozen_ready_but_pipeline_blocked"], v2["ack_not_required"]["frozen_ready_but_pipeline_blocked"]) == (2, 4)
    # the official scores did not move: the readiness above is NOT part of the gates
    assert [k.split()[0] for k, v in out["v2-2 candidate"]["official_gates"].items() if v is False] == ["G1", "G8", "G10", "G12"]
    assert [k.split()[0] for k, v in out["v2-1 baseline"]["official_gates"].items() if v is False] == ["G1", "G4", "G5", "G7", "G8", "G9", "G10", "G12"]
    md = rp.render(out)
    assert "NOT a benchmark result" in md and PIPELINE_VERSION in md and "Identical to the saved results.json: yes" in md


def test_frozen_artifacts_are_untouched():
    sys.path.insert(0, str(BACKEND / "scripts"))
    try:
        spec = importlib.util.spec_from_file_location("req_v2_run_p", BACKEND / "scripts" / "requirements_v2_extraction_run.py")
        run = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(run)
    finally:
        sys.path.remove(str(BACKEND / "scripts"))
    import subprocess
    try:
        subprocess.run(["git", "-C", str(run.REPO), "cat-file", "-e", run.FROZEN_COMMIT], check=True, capture_output=True)
    except Exception:
        pytest.skip("frozen commit not in this checkout")
    assert run.verify_frozen() == [] and run.verify_candidate() == []
