"""Gates / knockout criteria, offline: the decision rules, the document rules, the suggestion cues, the CV-evidence outcome rules, the
answer evaluation and the typed edit operations. Nothing here touches a database."""
from __future__ import annotations

import copy
import itertools
import uuid

import pytest

from services import eligibility_service as G
from services import requirements_edit as E
from services.requirements_v2 import contract as C
from services.requirements_v2 import gates as R
from services.requirements_v2.extraction.gate_cues import suggest_gate
from services.requirements_v2.validation import validate_final, validate_structure

USER = "11111111-1111-4111-8111-111111111111"
OTHER_USER = "44444444-4444-4444-8444-444444444444"
NOW = "2026-10-17T12:00:00+00:00"
JD = "We need Python and SQL. Five years of backend work is preferred."
Q1 = str(uuid.uuid4())
Q2 = str(uuid.uuid4())


def _item(item_id, text, importance, weight=None, **kw):
    return C.make_item(text, importance, item_id=item_id, weight=weight, origin=C.ORIGIN_STATED, source_text=text, **kw)


def _base() -> dict:
    doc = C.empty_requirements()
    doc["categories"]["skills"] = {"weight": 100, "items": [
        _item("req_py", "Python", C.IMPORTANCE_REQUIRED, 60),
        _item("req_sql", "SQL", C.IMPORTANCE_REQUIRED, 40),
        _item("req_go", "Go", C.IMPORTANCE_PREFERRED),
    ]}
    return doc


def _plan(body, stored=None):
    stored = _base() if stored is None else stored
    return E.plan_operations(stored, copy.deepcopy(_base()), [], E.parse_operations(body), user_id=USER, now=NOW, jd_text=JD)


def _gate(doc, item_id, **kw):
    args = dict(verification_method="cv_evidence", linked_question_id=None, user_id=USER, confirmed_at=NOW)
    args.update(kw)
    return R.set_gate(doc, item_id, **args)


# ── the decision ────────────────────────────────────────────────────────────────────────────────────────────────────

S = G.GateState


def test_no_gates_means_not_applicable():
    assert G.decide([]) == G.NOT_APPLICABLE


def test_clear_evidence_of_non_compliance_makes_the_candidate_not_eligible_whatever_else_passed():
    assert G.decide([S(G.PASSED), S(G.FAILED), S(G.PASSED)]) == G.NOT_ELIGIBLE


def test_a_confirmed_failure_wins_over_an_unresolved_gate():
    assert G.decide([S(G.NEEDS_VERIFICATION), S(G.FAILED)]) == G.NOT_ELIGIBLE
    assert G.decide([S(G.NOT_ASSESSED), S(G.FAILED)]) == G.NOT_ELIGIBLE


@pytest.mark.parametrize("state", [G.NEEDS_VERIFICATION, G.NOT_ASSESSED])
def test_missing_unclear_or_unassessed_evidence_gives_review_required_never_not_eligible(state):
    assert G.decide([S(G.PASSED), S(state)]) == G.REVIEW_REQUIRED
    assert G.decide([S(state)]) == G.REVIEW_REQUIRED


def test_every_gate_passed_or_overridden_is_eligible():
    assert G.decide([S(G.PASSED), S(G.OVERRIDDEN), S(G.PASSED)]) == G.ELIGIBLE


def test_an_overridden_gate_no_longer_counts_as_a_failure():
    assert G.decide([S(G.OVERRIDDEN)]) == G.ELIGIBLE


def test_decide_is_order_independent_and_only_a_failed_result_makes_a_candidate_not_eligible():
    for combo in itertools.combinations_with_replacement([S(r) for r in G.RESULTS], 3):
        outcome = G.decide(combo)
        assert (outcome == G.NOT_ELIGIBLE) == any(s.result == G.FAILED for s in combo)
        assert G.decide(list(reversed(combo))) == outcome


def test_a_gate_state_carries_no_failure_action():
    assert [f for f in S.__dataclass_fields__] == ["result"]


def test_the_score_is_not_an_input_of_the_decision():
    import inspect
    assert list(inspect.signature(G.decide).parameters) == ["gates"]


def test_effective_decision_lets_a_failed_gate_win_over_a_high_score_decision():
    assert G.effective_decision(G.NOT_ELIGIBLE, "strong_match") == G.NOT_ELIGIBLE
    assert G.effective_decision(G.REVIEW_REQUIRED, "strong_match") == G.REVIEW_REQUIRED


def test_effective_decision_leaves_the_score_decision_alone_otherwise():
    for status in (G.ELIGIBLE, G.NOT_APPLICABLE, None):
        assert G.effective_decision(status, "strong_match") == "strong_match"
    assert G.effective_decision(G.NOT_ELIGIBLE, None) == G.NOT_ELIGIBLE


# ── an answer against the passing criteria ──────────────────────────────────────────────────────────────────────────

def test_an_explicit_failing_answer_fails_and_a_passing_one_passes():
    crit = {"passing_answers": ["yes"]}
    assert G.evaluate_answer("yes_no", crit, None, "No") == G.FAILED
    assert G.evaluate_answer("yes_no", crit, None, "yes") == G.PASSED
    assert G.evaluate_answer("single_choice", {"passing_answers": ["A"]}, ["A", "B"], "B") == G.FAILED
    assert G.evaluate_answer("number", {"operator": ">=", "value": 3}, None, "2") == G.FAILED
    assert G.evaluate_answer("number", {"operator": ">=", "value": 3}, None, "3") == G.PASSED


@pytest.mark.parametrize("args", [
    ("yes_no", {"passing_answers": ["yes"]}, None, ""),
    ("yes_no", {"passing_answers": ["yes"]}, None, None),
    ("yes_no", {"passing_answers": ["yes"]}, None, "maybe"),
    ("yes_no", {"passing_answers": []}, None, "yes"),
    ("yes_no", None, None, "yes"),
    ("single_choice", {"passing_answers": ["A"]}, ["A", "B"], "C"),
    ("number", {"operator": ">=", "value": 3}, None, "many"),
    ("number", {"operator": "~", "value": 3}, None, "5"),
    ("number", {"operator": ">=", "value": True}, None, "5"),
])
def test_an_answer_that_cannot_be_read_is_needs_verification_never_failed(args):
    assert G.evaluate_answer(*args) == G.NEEDS_VERIFICATION


# ── the gates of a job ──────────────────────────────────────────────────────────────────────────────────────────────

def _q(qid, gate=True, legacy_action=None, text="Do you hold a valid licence?"):
    return {"question_id": qid, "question_text": text, "is_eligibility_gate": gate, "gate_failure_action": legacy_action}


def test_existing_questions_stay_advisory_unless_explicitly_marked_as_gates():
    assert G.gate_defs(_base(), [_q(Q1, gate=False)]) == []
    assert G.gate_defs(None, [_q(Q1, gate=False), _q(Q2, gate=False)]) == []


def test_a_standalone_gate_question_is_a_gate_and_a_legacy_failure_action_is_ignored():
    defs = G.gate_defs(None, [_q(Q1, legacy_action="hold_for_review"), _q(Q2, legacy_action=None)])
    assert [(d.key, d.method) for d in defs] == [(f"q:{Q1}", "candidate_question"), (f"q:{Q2}", "candidate_question")]
    assert not hasattr(defs[0], "action")


def test_a_suggestion_is_not_a_gate():
    doc = _base()
    doc["categories"]["skills"]["items"][0]["gate_suggestion"] = {"suggested": True, "cue": "must", "source_text": None}
    assert G.gate_defs(doc, []) == []


def test_a_confirmed_requirement_gate_is_enforced_in_document_order_before_questions():
    doc = _gate(_base(), "req_sql")
    defs = G.gate_defs(doc, [_q(Q1)])
    assert [d.key for d in defs] == ["req:req_sql", f"q:{Q1}"]
    assert defs[0].method == "cv_evidence"


def test_a_question_that_verifies_a_requirement_is_evaluated_once_as_that_requirement():
    doc = _gate(_base(), "req_py", verification_method="candidate_question", linked_question_id=Q1)
    defs = G.gate_defs(doc, [_q(Q1), _q(Q2)])
    assert [d.key for d in defs] == ["req:req_py", f"q:{Q2}"]
    assert defs[0].question_id == Q1


def test_a_linked_question_that_is_not_an_eligibility_gate_is_not_used_to_judge_the_requirement():
    doc = _gate(_base(), "req_py", verification_method="candidate_question", linked_question_id=Q1)
    [only] = G.gate_defs(doc, [_q(Q1, gate=False)])
    assert only.question_id is None


# ── the document rules ──────────────────────────────────────────────────────────────────────────────────────────────

def test_a_gate_on_a_required_item_validates():
    assert not validate_structure(_gate(_base(), "req_py"))
    assert not [i for i in validate_final(_gate(_base(), "req_py")).errors if "gate" in i.code]


def test_a_preferred_item_cannot_be_made_a_gate():
    with pytest.raises(ValueError):
        _gate(_base(), "req_go")


def test_a_gate_stored_on_a_preferred_item_is_refused_by_the_rules():
    doc = _gate(_base(), "req_py")
    doc["categories"]["skills"]["items"][0]["importance"] = C.IMPORTANCE_PREFERRED
    doc["categories"]["skills"]["items"][0]["weight"] = None
    codes = {i.code for i in R.rule_issues(doc)}
    assert "gate_on_preferred_item" in codes
    assert R.confirmed_gates(doc) == []                              # and it is never enforced


def test_a_gate_without_a_verification_method_is_refused():
    doc = _gate(_base(), "req_py", verification_method=None)
    assert "gate_without_verification_method" in {i.code for i in R.rule_issues(doc)}
    assert R.confirmed_gates(doc) == []


def test_a_candidate_question_gate_must_name_its_question():
    doc = _gate(_base(), "req_py", verification_method="candidate_question")
    assert "gate_question_link_missing" in {i.code for i in R.rule_issues(doc)}


def test_a_linked_question_with_another_method_is_contradictory():
    doc = _gate(_base(), "req_py", verification_method="manual", linked_question_id=Q1)
    assert "gate_contradictory_configuration" in {i.code for i in R.rule_issues(doc)}


def test_one_question_cannot_verify_two_requirements():
    doc = _gate(_base(), "req_py", verification_method="candidate_question", linked_question_id=Q1)
    doc = _gate(doc, "req_sql", verification_method="candidate_question", linked_question_id=Q1.upper())
    assert "gate_question_linked_twice" in {i.code for i in R.rule_issues(doc)}


def test_the_question_link_is_stored_lower_case_so_comparisons_are_stable():
    doc = _gate(_base(), "req_py", verification_method="candidate_question", linked_question_id=Q1.upper())
    assert R.linked_question_ids(doc) == {Q1.lower(): "req_py"}


@pytest.mark.parametrize("bad", ["", "nope", "123", " "])
def test_a_malformed_question_id_is_refused(bad):
    with pytest.raises(ValueError):
        _gate(_base(), "req_py", verification_method="candidate_question", linked_question_id=bad)


def test_unknown_vocabulary_is_refused():
    with pytest.raises(ValueError):
        _gate(_base(), "req_py", verification_method="telepathy")


def test_an_unknown_item_is_a_key_error():
    with pytest.raises(KeyError):
        _gate(_base(), "req_missing")


def test_the_stored_gate_shape_is_exact_and_carries_the_confirming_user_and_time():
    gate = _gate(_base(), "req_py")["categories"]["skills"]["items"][0]["gate"]
    assert set(gate) == R.GATE_FIELDS and gate["confirmed_by"] == USER and gate["confirmed_at"] == NOW and gate["mandatory"] is True


def test_a_gate_with_an_unexpected_field_or_missing_confirmation_is_malformed():
    doc = _gate(_base(), "req_py")
    doc["categories"]["skills"]["items"][0]["gate"]["extra"] = 1
    assert any(i.code == "bad_gate" for i in validate_structure(doc))
    doc = _gate(_base(), "req_py")
    doc["categories"]["skills"]["items"][0]["gate"]["confirmed_by"] = ""
    assert any(i.code == "bad_gate" for i in validate_structure(doc))


def test_set_and_clear_do_not_mutate_their_input():
    base = _base()
    snapshot = copy.deepcopy(base)
    with_gate = _gate(base, "req_py")
    R.clear_gate(with_gate, "req_py")
    assert base == snapshot


# ── the typed edit operations ───────────────────────────────────────────────────────────────────────────────────────

def test_set_gate_through_the_edit_service_is_stamped_by_the_server_not_the_client():
    out = _plan([{"op": "set_gate", "item_id": "req_py", "verification_method": "cv_evidence", "linked_question_id": None}])
    gate = out.document["categories"]["skills"]["items"][0]["gate"]
    assert gate["confirmed_by"] == USER and gate["confirmed_at"] == NOW


@pytest.mark.parametrize("extra", [{"confirmed_by": OTHER_USER}, {"confirmed_at": "2000-01-01T00:00:00+00:00"}, {"mandatory": False}])
def test_a_client_cannot_supply_the_confirmation_fields(extra):
    body = {"op": "set_gate", "item_id": "req_py", "verification_method": "cv_evidence", "linked_question_id": None, **extra}
    with pytest.raises(E.InvalidOperation):
        E.parse_operations([body])


def test_set_gate_on_a_preferred_item_is_refused_with_a_stable_code():
    with pytest.raises(E.InvalidOperation) as caught:
        _plan([{"op": "set_gate", "item_id": "req_go", "verification_method": "cv_evidence", "linked_question_id": None}])
    assert "prefer" in str(caught.value).lower() or caught.value.code


def test_making_a_gated_item_preferred_removes_its_gate():
    gated = _gate(_base(), "req_py")
    out = _plan([{"op": "set_importance", "item_id": "req_py", "importance": "preferred"},
                 {"op": "set_item_weight", "item_id": "req_sql", "weight": 100}], stored=gated)
    item = out.document["categories"]["skills"]["items"][0]
    assert item["importance"] == "preferred" and "gate" not in item


def test_clear_gate_removes_it():
    gated = _gate(_base(), "req_py")
    out = _plan([{"op": "clear_gate", "item_id": "req_py"}], stored=gated)
    assert "gate" not in out.document["categories"]["skills"]["items"][0]


def test_the_audit_records_gate_changes_as_typed_ids_and_enums_only():
    out = _plan([{"op": "set_gate", "item_id": "req_py", "verification_method": "manual", "linked_question_id": None}])
    assert out.audit.gate_changes == (("req_py", "confirmed"),)
    assert "Python" not in repr(out.audit)                           # no requirement text in the audit


def test_the_original_suggestion_is_preserved_when_the_gate_is_confirmed():
    doc = _base()
    doc["categories"]["skills"]["items"][0]["gate_suggestion"] = {"suggested": True, "cue": "must", "source_text": None}
    out = _plan([{"op": "set_gate", "item_id": "req_py", "verification_method": "cv_evidence", "linked_question_id": None}], stored=doc)
    item = out.document["categories"]["skills"]["items"][0]
    assert item["gate_suggestion"]["cue"] == "must" and item["gate"]["mandatory"] is True


def test_a_client_cannot_write_a_suggestion():
    with pytest.raises(E.InvalidOperation):
        E.parse_operations([{"op": "add_item", "category": "skills", "text": "Rust", "importance": "required", "weight": None,
                             "alternatives": None, "experience": None, "gate_suggestion": {"suggested": True, "cue": "must"}}])


# ── AI suggestion cues ──────────────────────────────────────────────────────────────────────────────────────────────

def _span(jd, phrase):
    start = jd.index(phrase)
    return (start, start + len(phrase))


@pytest.mark.parametrize("jd,phrase,cue", [
    ("Candidates must hold a valid driving licence.", "valid driving licence", "must"),
    ("A bachelor degree is required.", "bachelor degree", "required"),
    ("Minimum 5 years of Java experience.", "Java experience", "Minimum"),
])
def test_explicit_wording_produces_a_suggestion_with_the_job_description_own_words(jd, phrase, cue):
    suggestion = suggest_gate(jd, _span(jd, phrase))
    assert suggestion == {"suggested": True, "cue": cue, "source_text": None}


@pytest.mark.parametrize("jd,phrase", [
    ("Knowledge of Python is a plus.", "Python"),
    ("Experience with Docker is preferred and nice to have.", "Docker"),
    ("We offer a friendly team and Python tooling.", "Python tooling"),
    ("The role is not required to travel but uses Excel.", "Excel"),
])
def test_ordinary_optional_or_unbound_wording_produces_no_suggestion(jd, phrase):
    assert suggest_gate(jd, _span(jd, phrase)) is None


def test_no_span_or_no_text_is_no_suggestion():
    assert suggest_gate("must", None) is None
    assert suggest_gate(None, (0, 1)) is None


# ── CV evidence ─────────────────────────────────────────────────────────────────────────────────────────────────────

CV = "Worked at Acme from 2015. Holds a valid driving licence issued in Jordan."


def test_a_met_verdict_with_a_verbatim_quote_and_confidence_passes():
    out = G.cv_outcome({"verdict": "met", "confidence": 0.9, "evidence_quote": "valid driving licence"}, CV)
    assert out["result"] == G.PASSED and "valid driving licence" in out["summary"]


def test_a_quote_that_is_not_in_the_cv_never_passes():
    out = G.cv_outcome({"verdict": "met", "confidence": 0.99, "evidence_quote": "holds a pilot licence"}, CV)
    assert out["result"] == G.NEEDS_VERIFICATION


def test_low_confidence_met_needs_verification():
    assert G.cv_outcome({"verdict": "met", "confidence": 0.5, "evidence_quote": "valid driving licence"}, CV)["result"] == G.NEEDS_VERIFICATION


def test_missing_cv_evidence_never_fails_a_gate():
    for verdict in ("unclear", "not_met", "met", None, "garbage"):
        out = G.cv_outcome({"verdict": verdict, "confidence": 1.0, "evidence_quote": None}, CV)
        assert out["result"] == G.NEEDS_VERIFICATION


def test_not_met_without_an_explicit_contradiction_does_not_fail():
    out = G.cv_outcome({"verdict": "not_met", "confidence": 1.0, "evidence_quote": "Worked at Acme from 2015"}, CV)
    assert out["result"] == G.NEEDS_VERIFICATION


def test_not_met_below_the_fail_confidence_does_not_fail():
    out = G.cv_outcome({"verdict": "not_met", "confidence": 0.85, "explicit_contradiction": True, "evidence_quote": "Worked at Acme from 2015"}, CV)
    assert out["result"] == G.NEEDS_VERIFICATION


def test_only_a_confident_quoted_explicit_contradiction_fails():
    out = G.cv_outcome({"verdict": "not_met", "confidence": 0.95, "explicit_contradiction": True, "evidence_quote": "Worked at Acme from 2015"}, CV)
    assert out["result"] == G.FAILED


@pytest.mark.parametrize("confidence", [None, "high", float("nan"), -3, 12])
def test_a_garbled_confidence_is_never_a_decisive_one(confidence):
    out = G.cv_outcome({"verdict": "not_met", "confidence": confidence, "explicit_contradiction": True, "evidence_quote": "Worked at Acme from 2015"}, CV)
    assert out["result"] == G.NEEDS_VERIFICATION


def test_the_summary_does_not_echo_more_than_the_quote():
    out = G.cv_outcome({"verdict": "met", "confidence": 0.9, "evidence_quote": "valid driving licence"}, CV)
    assert "Acme" not in out["summary"]


# ── no configurable failure action; legacy values stay readable ─────────────────────────────────────────────────────

def _legacy(doc, action):
    doc["categories"]["skills"]["items"][0]["gate"]["failure_action"] = action
    return doc


@pytest.mark.parametrize("action", ["not_eligible", "hold_for_review"])
def test_a_stored_gate_with_an_old_failure_action_value_is_still_valid_and_enforced(action):
    doc = _legacy(_gate(_base(), "req_py"), action)
    assert not validate_structure(doc) and not R.rule_issues(doc)
    assert [g["item_id"] for g in R.confirmed_gates(doc)] == ["req_py"]
    assert [d.key for d in G.gate_defs(doc, [])] == ["req:req_py"]


def test_a_stored_gate_without_a_failure_action_is_valid_and_enforced():
    doc = _gate(_base(), "req_py")
    del doc["categories"]["skills"]["items"][0]["gate"]["failure_action"]
    assert not validate_structure(doc) and not R.rule_issues(doc)
    assert [d.key for d in G.gate_defs(doc, [])] == ["req:req_py"]


def test_a_malformed_stored_failure_action_is_still_refused():
    doc = _legacy(_gate(_base(), "req_py"), "reject_everyone")
    assert any(i.code == "bad_gate_failure_action" for i in validate_structure(doc))


def test_a_legacy_value_is_neither_used_nor_exposed_by_the_decision_or_the_projection():
    legacy = _legacy(_gate(_base(), "req_py"), "hold_for_review")
    assert [d.method for d in G.gate_defs(legacy, [])] == ["cv_evidence"]
    projected = E._project_document(legacy)["categories"]["skills"]["items"][0]["gate"]
    assert "failure_action" not in projected and set(projected) == {"mandatory", "verification_method", "linked_question_id", "confirmed_at"}
    assert "failure_action" not in R.confirmed_gates(legacy)[0]


def test_confirming_a_gate_needs_no_failure_action_and_new_gates_keep_the_canonical_legacy_value_for_older_code():
    out = _plan([{"op": "set_gate", "item_id": "req_py", "verification_method": "cv_evidence", "linked_question_id": None}])
    gate = out.document["categories"]["skills"]["items"][0]["gate"]
    assert gate["failure_action"] == "not_eligible" and gate["mandatory"] is True


def test_reconfirming_an_old_gate_keeps_its_stored_legacy_value_and_is_not_a_change():
    stored = _legacy(_gate(_base(), "req_py"), "hold_for_review")
    out = _plan([{"op": "set_gate", "item_id": "req_py", "verification_method": "cv_evidence", "linked_question_id": None}], stored=stored)
    assert out.document["categories"]["skills"]["items"][0]["gate"]["failure_action"] == "hold_for_review"
    assert out.audit.gate_changes == ()


@pytest.mark.parametrize("sent", ["not_eligible", "hold_for_review"])
def test_a_stale_client_may_still_send_a_valid_failure_action_which_changes_nothing(sent):
    with_sent = _plan([{"op": "set_gate", "item_id": "req_py", "verification_method": "cv_evidence", "failure_action": sent,
                        "linked_question_id": None}])
    without = _plan([{"op": "set_gate", "item_id": "req_py", "verification_method": "cv_evidence", "linked_question_id": None}])
    assert with_sent.document == without.document


@pytest.mark.parametrize("bad", ["reject_everyone", None, 5, ""])
def test_a_malformed_failure_action_in_a_request_is_refused(bad):
    with pytest.raises(E.InvalidOperation):
        E.parse_operations([{"op": "set_gate", "item_id": "req_py", "verification_method": "cv_evidence", "failure_action": bad,
                             "linked_question_id": None}])


def test_set_gate_still_refuses_other_unknown_fields():
    with pytest.raises(E.InvalidOperation):
        E.parse_operations([{"op": "set_gate", "item_id": "req_py", "verification_method": "cv_evidence", "linked_question_id": None,
                             "outcome": "not_eligible"}])
    with pytest.raises(E.InvalidOperation):
        E.parse_operations([{"op": "set_gate", "item_id": "req_py", "linked_question_id": None}])


# ── outcomes follow the evidence ────────────────────────────────────────────────────────────────────────────────────

def test_a_satisfied_requirement_passes_and_the_candidate_is_eligible():
    out = G.cv_outcome({"verdict": "met", "confidence": 0.9, "evidence_quote": "valid driving licence"}, CV)
    assert out["result"] == G.PASSED
    assert G.decide([S(out["result"])]) == G.ELIGIBLE


def test_a_confirmed_contradiction_is_the_only_cv_outcome_that_makes_a_candidate_not_eligible():
    out = G.cv_outcome({"verdict": "not_met", "confidence": 0.97, "explicit_contradiction": True,
                        "evidence_quote": "Worked at Acme from 2015"}, CV)
    assert out["result"] == G.FAILED and G.decide([S(out["result"])]) == G.NOT_ELIGIBLE


@pytest.mark.parametrize("verdict", ["unclear", "conflicting", "mixed", "partial", "outdated", None, 7])
def test_ambiguous_conflicting_or_incomplete_evidence_is_review_required_not_not_eligible(verdict):
    out = G.cv_outcome({"verdict": verdict, "confidence": 1.0, "evidence_quote": "valid driving licence", "explicit_contradiction": True}, CV)
    assert out["result"] == G.NEEDS_VERIFICATION
    assert G.decide([S(out["result"])]) == G.REVIEW_REQUIRED


def test_missing_evidence_is_review_required_not_not_eligible():
    out = G.cv_outcome({"verdict": "not_met", "confidence": 1.0, "explicit_contradiction": True, "evidence_quote": None}, CV)
    assert out["result"] == G.NEEDS_VERIFICATION and G.decide([S(out["result"])]) == G.REVIEW_REQUIRED


def test_the_model_is_told_that_conflicting_evidence_is_unclear():
    assert "conflicting" in G.GATE_EVIDENCE_SYSTEM_PROMPT
