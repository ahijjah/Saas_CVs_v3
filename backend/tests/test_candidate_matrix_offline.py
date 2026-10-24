"""Final Candidate Matrix, pure parts: the column registry, layouts and precedence, the status mapping (Preferred never shows partial
compliance), flag severity, SQL construction (client values are binds, never SQL text), and the CSV export."""
from __future__ import annotations

import csv
import io
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from services.matrix import config as C
from services.matrix import export as X
from services.matrix import labels as L
from services.matrix import query as Q
from services.matrix import registry as R
from services.matrix import summaries as S

DOC = {"categories": {
    "skills": {"weight": 60, "items": [{"id": "req_a", "importance": "required", "text": "SQL troubleshooting"},
                                       {"id": "req_p", "importance": "preferred", "text": "Kubernetes"}]},
    "experience": {"weight": 40, "items": [{"id": "req_b", "importance": "required", "text": "Backend experience"}]}}}
GATES = [SimpleNamespace(key="req:req_a", label="SQL troubleshooting"), SimpleNamespace(key="q:11111111-1111-4111-8111-111111111111", label="Licence?")]


def registry():
    return {c.id: c for c in R.job_columns(DOC, GATES)}


# ── registry ────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_static_columns_are_the_documented_set_and_age_is_not_one():
    ids = set(R.STATIC_BY_ID)
    assert {"candidate.name", "decision.eligibility", "decision.score", "decision.scoring", "mandatory.summary", "preferred.summary",
            "gates.summary", "decision.flags", "candidate.applied_at", "candidate.source", "workflow.status", "candidate.assigned_to",
            "candidate.tags", "candidate.position", "candidate.location"} == ids
    assert not any("age" in i.split(".")[-1].split("_") or "birth" in i for i in ids)


def test_position_and_location_are_registered_but_unavailable_because_they_are_not_stored():
    for cid in ("candidate.position", "candidate.location"):
        column = R.STATIC_BY_ID[cid]
        assert column.available is False and "not stored" in column.unavailable_reason
        assert cid not in R.DEFAULT_COLUMNS


def test_dynamic_columns_use_immutable_ids_not_labels():
    reg = registry()
    assert {"mandatory:req_a", "mandatory:req_b", "preferred:req_p", "gate:req:req_a", "gate:q:11111111-1111-4111-8111-111111111111"} <= set(reg)
    assert reg["mandatory:req_a"].ref == "req_a" and reg["mandatory:req_a"].label_en == "SQL troubleshooting"
    assert reg["preferred:req_p"].kind == "preferred" and reg["gate:req:req_a"].kind == "gate"


@pytest.mark.parametrize("bad", ["mandatory:", "mandatory:a b", "mandatory:a;drop", "gate:other:x", "gate:req:", "x:req_a", "candidate.name; DROP", "", "mandatory:" + "a" * 65])
def test_malformed_ids_are_not_columns(bad):
    assert R.well_formed(bad) is False


def test_a_document_that_is_not_requirements_v2_yields_no_item_columns():
    for document in (None, {}, {"categories": []}, {"categories": {"skills": {"items": "x"}}}, "text"):
        assert [c.id for c in R.job_columns(document, [])] == [c.id for c in R.STATIC]


def test_partition_marks_a_removed_requirement_unavailable_without_failing():
    usable, unavailable = R.partition(["candidate.name", "mandatory:req_gone", "mandatory:req_a", "candidate.location"], registry())
    assert usable == ["candidate.name", "mandatory:req_a"] and unavailable == ["mandatory:req_gone", "candidate.location"]


# ── layouts ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def layout(**over):
    base = {"columns": ["candidate.name", "decision.score", "mandatory:req_a"], "sort": {"column": "decision.score", "direction": "desc"}}
    return C.Layout.model_validate({**base, **over})


def test_a_layout_is_strict_about_unknown_fields_and_shape():
    for bad in ({"columns": ["candidate.name"], "sql": "1"}, {"columns": []}, {"columns": ["decision.score"]},
                {"columns": ["candidate.name", "candidate.name"]}, {"columns": ["candidate.name", "bad id"]},
                {"columns": ["candidate.name"], "page_size": 5}, {"columns": ["candidate.name"], "filters": {"nope": 1}},
                {"columns": ["candidate.name"], "filters": {"score_min": 90, "score_max": 10}},
                {"columns": ["candidate.name"], "filters": {"eligibility": ["maybe"]}},
                {"columns": ["candidate.name"], "filters": {"preferred": [{"item_id": "req_p", "results": ["partially_met"]}]}}):
        with pytest.raises(ValidationError):
            C.Layout.model_validate(bad)


def test_a_preferred_filter_cannot_name_partial_compliance():
    with pytest.raises(ValidationError):
        C.PreferredFilter(item_id="req_p", results=["partially_met"])
    assert C.PreferredFilter(item_id="req_p", results=["yes", "unclear", "not_assessed"])


def test_validate_layout_checks_every_reference_against_the_job_registry():
    C.validate_layout(layout(), registry(), scope="job")
    for bad, code in ((layout(columns=["candidate.name", "mandatory:req_gone"]), "unknown_column"),
                      (layout(columns=["candidate.name", "candidate.location"]), "column_unavailable"),
                      (layout(sort={"column": "candidate.source", "direction": "asc"}), "sort_not_allowed"),
                      (layout(filters={"mandatory": [{"item_id": "req_p", "results": ["met"]}]}), "unknown_filter_target"),
                      (layout(filters={"gates": [{"gate_key": "req:nope", "results": ["failed"]}]}), "unknown_filter_target")):
        with pytest.raises(C.LayoutProblem) as caught:
            C.validate_layout(bad, registry(), scope="job")
        assert caught.value.code == code


def test_the_tenant_default_can_use_only_standard_columns_and_filters():
    static = {c.id: c for c in R.STATIC}
    C.validate_layout(layout(columns=["candidate.name", "decision.score"]), static, scope="tenant")
    with pytest.raises(C.LayoutProblem):
        C.validate_layout(layout(columns=["candidate.name", "mandatory:req_a"]), registry(), scope="tenant")
    with pytest.raises(C.LayoutProblem):
        C.validate_layout(layout(columns=["candidate.name"], filters={"mandatory": [{"item_id": "req_a", "results": ["met"]}]}), registry(), scope="tenant")


def test_precedence_is_personal_then_job_then_tenant_then_system():
    def lay(name):
        return C.Layout(columns=["candidate.name", name])
    layers = {"personal": {"layout": lay("decision.score")}, "job": {"layout": lay("decision.flags")}, "tenant": {"layout": lay("workflow.status")}}
    assert C.effective_layout(layers) == (lay("decision.score"), "personal")
    layers["personal"] = None
    assert C.effective_layout(layers) == (lay("decision.flags"), "job")
    layers["job"] = None
    assert C.effective_layout(layers) == (lay("workflow.status"), "tenant")
    layers["tenant"] = None
    assert C.effective_layout(layers) == (C.SYSTEM_DEFAULT, "system")


def test_an_unreadable_stored_layer_is_skipped_not_fatal():
    layers = {"personal": {"layout": None}, "job": {"layout": C.Layout(columns=["candidate.name", "decision.flags"])}, "tenant": None}
    assert C.effective_layout(layers)[1] == "job"


def test_resolve_columns_hides_unavailable_ones_but_keeps_the_identity_column():
    shown, unavailable = C.resolve_columns(layout(columns=["candidate.name", "mandatory:req_gone", "decision.score"],
                                                  sort={"column": "mandatory:req_gone", "direction": "asc"}), registry())
    assert shown.columns == ["candidate.name", "decision.score"] and unavailable == ["mandatory:req_gone"] and shown.sort is None


def test_the_system_default_shows_the_decision_elements_side_by_side_and_sorts_by_application_date():
    assert C.SYSTEM_DEFAULT.columns[0] == "candidate.name" and C.SYSTEM_DEFAULT.frozen_candidate is True
    assert {"decision.eligibility", "decision.score", "mandatory.summary", "preferred.summary", "gates.summary", "decision.flags"} <= set(C.SYSTEM_DEFAULT.columns)
    assert C.SYSTEM_DEFAULT.sort.column == "candidate.applied_at"


# ── status mapping ──────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("stored,shown", [("yes", "yes"), ("no", "no"), ("not_clear", "unclear"), (None, "not_assessed"),
                                          ("partially_met", "unclear"), ("met", "unclear"), ("garbage", "unclear")])
def test_a_preferred_item_never_exposes_partial_compliance(stored, shown):
    assert S.preferred_status(stored) == shown
    assert S.preferred_status(stored) in R.PREFERRED_RESULTS and "partially_met" not in R.PREFERRED_RESULTS


@pytest.mark.parametrize("stored,shown", [("met", "met"), ("partially_met", "partially_met"), ("not_met", "not_met"), ("unclear", "unclear"),
                                          (None, "not_assessed"), ("yes", "not_assessed")])
def test_a_mandatory_result_keeps_its_own_word_and_unclear_is_not_converted(stored, shown):
    assert S.mandatory_status(stored) == shown


@pytest.mark.parametrize("stored,shown", [("passed", "passed"), ("overridden", "passed"), ("failed", "failed"), ("needs_verification", "review_required"),
                                          ("not_assessed", "not_assessed"), (None, "not_assessed")])
def test_a_gate_result_maps_to_the_four_gate_statuses(stored, shown):
    assert S.gate_status(stored) == shown


def test_summaries_are_not_assessed_without_results_and_exact_with_them():
    assert S.mandatory_summary({"m_total": None})["assessed"] is False and S.preferred_summary({"p_total": None})["assessed"] is False
    assert S.gate_summary({"g_total": 0})["assessed"] is False
    m = S.mandatory_summary({"m_total": 7, "m_met": 4, "m_partial": 1, "m_not_met": 1, "m_unclear": 1})
    assert (m["met"], m["partially_met"], m["not_met"], m["unclear"], m["total"]) == (4, 1, 1, 1, 7)
    assert S.preferred_summary({"p_total": 3, "p_yes": 1, "p_no": 1, "p_unclear": 1})["unclear"] == 1
    g = S.gate_summary({"g_total": 4, "g_passed": 1, "g_failed": 1, "g_review": 1, "g_na": 1})
    assert (g["passed"], g["failed"], g["review_required"], g["not_assessed"]) == (1, 1, 1, 1)


def test_flag_severity_and_targets():
    row = {"application_id": "app", "fl_items": 4, "g_review": 1, "fl_proc": 1, "review_gate_targets": [{"type": "gate", "id": "req:req_a"}]}
    flagged = [{"item_id": "req_a", "importance": "required", "result": "unclear", "review_reason": None, "gate_linked": True},
               {"item_id": "req_b", "importance": "required", "result": "unclear", "review_reason": "no_verified_evidence", "gate_linked": False},
               {"item_id": "req_p", "importance": "preferred", "result": "not_clear", "review_reason": "low_confidence", "gate_linked": False},
               {"item_id": "req_c", "importance": "required", "result": "unclear", "review_reason": "conflicting_evidence", "gate_linked": False}]
    out = S.flags(row, flagged)
    by_kind = {e["kind"]: e for e in out["items"]}
    assert out["total"] == 6
    assert by_kind["unclear_criteria"]["severity"] == "high" and by_kind["unclear_criteria"]["count"] == 4
    assert by_kind["evidence_not_found"]["targets"] == [{"type": "item", "id": "req_b"}]
    assert by_kind["low_confidence"]["severity"] == "low" and by_kind["conflicting_evidence"]["severity"] == "medium"
    assert by_kind["processing_error"]["severity"] == "high" and by_kind["unresolved_review"]["count"] == 4
    assert all(set(e) == {"kind", "severity", "count", "targets"} for e in out["items"])         # no opaque risk score
    assert "score" not in out and "risk" not in str(out)


def test_an_unclear_preferred_only_is_low_severity():
    out = S.flags({"application_id": "a", "fl_items": 1}, [{"item_id": "p", "importance": "preferred", "result": "not_clear", "review_reason": None, "gate_linked": False}])
    assert out["items"][0]["severity"] == "low" and "unresolved_review" not in {e["kind"] for e in out["items"]}


# ── SQL construction ────────────────────────────────────────────────────────────────────────────────────────────────

def test_client_values_are_bound_never_spliced_into_sql():
    hostile = "x'; DROP TABLE applications; --"
    f = C.Filters.model_construct(search=hostile, eligibility=["eligible"], scoring_status=None, score_min=None, score_max=None, workflow_status=None,
                                  source=None, flags=None, applied_after=None, applied_before=None, assigned_to=None,
                                  mandatory=[C.ItemFilter.model_construct(item_id=hostile, results=["met"])], preferred=[], gates=[])
    where, params = Q._filters(f, "user")
    assert hostile not in " ".join(where) and hostile in params.values()


def test_a_sort_column_outside_the_registry_falls_back_to_the_application_date():
    order, params = Q._order(C.Sort(column="candidate.source", direction="asc"), registry())
    assert order.startswith("m.applied_at ASC") and params == {}
    order, params = Q._order(C.Sort.model_construct(column="x; DROP TABLE a", direction="asc"), registry())
    assert "DROP" not in order and params == {}


def test_every_ordering_ends_with_the_deterministic_tie_break():
    for column in ("candidate.name", "decision.score", "decision.eligibility", "mandatory:req_a", "gate:req:req_a", "preferred:req_p"):
        order, _ = Q._order(C.Sort(column=column, direction="desc"), registry())
        assert order.endswith("m.applied_at DESC, m.application_id")


def test_scores_sort_with_missing_scores_last_and_no_composite_expression_exists():
    order, _ = Q._order(C.Sort(column="decision.score", direction="desc"), registry())
    assert order.startswith("m.score DESC NULLS LAST")
    assert all("+" not in expr and "*" not in expr for expr in Q._STATIC_SORT.values())          # one explicit column, never a blend


def test_the_eligibility_rule_puts_a_failed_gate_before_everything_else():
    sql = Q._ELIGIBILITY
    assert sql.index("'not_eligible'") < sql.index("'review_required'") < sql.index("'eligible'") < sql.index("'not_assessed'")
    assert "score" not in sql                                                                  # the score cannot decide eligibility


# ── export ──────────────────────────────────────────────────────────────────────────────────────────────────────────

ROW = {"application_id": "a1", "candidate_name": "=cmd|' /C calc'!A0", "applied_at": "2026-10-20T10:00:00+00:00", "source": "manual_upload",
       "workflow_status": "shortlisted", "assigned_to": {"id": "u", "name": "Hala"}, "tags": [{"id": "t", "name": "Remote"}, {"id": "t2", "name": "Senior"}],
       "eligibility": "not_eligible", "score": 91, "scoring": {"state": "scored", "method": "item_level_v2", "revision_used": 3, "stale": False},
       "mandatory": {"assessed": True, "met": 2, "partially_met": 1, "not_met": 0, "unclear": 1, "total": 4},
       "preferred": {"assessed": True, "yes": 1, "no": 0, "unclear": 1, "total": 2},
       "gates": {"assessed": True, "passed": 0, "failed": 1, "review_required": 0, "not_assessed": 0, "total": 1},
       "flags": {"total": 2, "items": [{"kind": "unclear_criteria", "severity": "medium", "count": 1, "targets": []}]},
       "cells": {"mandatory:req_a": {"status": "unclear", "confidence": 0.5, "resolved": False, "review_reason": None, "flagged": True},
                 "gate:req:req_a": {"status": "failed", "overridden": False}}}


def parse(data: bytes):
    assert data.startswith(b"\xef\xbb\xbf")
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))


def test_the_export_has_exactly_the_requested_columns_in_order_and_nothing_else():
    reg = registry()
    cols = [reg[c] for c in ("candidate.name", "decision.score", "mandatory:req_a", "decision.eligibility")]
    rows = parse(X.build_csv(lang="en", columns=cols, rows=[ROW], meta=None))
    assert rows[0] == ["Candidate", "Overall score", "SQL troubleshooting", "Eligibility"]
    assert rows[1][1:] == ["91", "Unclear", "Not Eligible"] and len(rows) == 2


def test_hidden_columns_and_evidence_never_appear():
    reg = registry()
    text = X.build_csv(lang="en", columns=[reg["candidate.name"], reg["decision.eligibility"]], rows=[ROW], meta=None).decode("utf-8")
    for leak in ("91", "Hala", "Remote", "0.5", "quote", "Preferred criteria", "shortlisted", "Shortlisted"):
        assert leak not in text


def test_a_cell_that_starts_like_a_formula_is_neutralised():
    assert X.safe("=1+1") == "'=1+1" and X.safe("+x") == "'+x" and X.safe("-x") == "'-x" and X.safe("@x") == "'@x" and X.safe("plain") == "plain"
    assert parse(X.build_csv(lang="en", columns=[registry()["candidate.name"]], rows=[ROW], meta=None))[1][0].startswith("'=")


def test_arabic_is_written_as_utf8_with_arabic_statuses():
    reg = registry()
    data = X.build_csv(lang="ar", columns=[reg[c] for c in ("candidate.name", "decision.eligibility", "mandatory.summary", "gates.summary")], rows=[ROW], meta=None)
    rows = parse(data)
    assert rows[0][1] == "الأهلية" and rows[1][1] == "غير مؤهل" and "مستوفى جزئيًا 1" in rows[1][2] and "لم يجتز 1" in rows[1][3]


def test_summary_cells_are_readable_words():
    reg = registry()
    rows = parse(X.build_csv(lang="en", columns=[reg["mandatory.summary"], reg["preferred.summary"], reg["gates.summary"], reg["decision.flags"]], rows=[ROW], meta=None))
    assert rows[1] == ["Met 2 · Partially Met 1 · Not Met 0 · Unclear 1 · Total 4", "Yes 1 · No 0 · Unclear 1 · Total 2",
                       "Passed 0 · Failed 1 · Review Required 0 · Not Assessed 0 · Total 1", "Unclear criteria 1"]


def test_unassessed_rows_say_not_assessed():
    legacy = {**ROW, "mandatory": {"assessed": False}, "preferred": {"assessed": False}, "gates": {"assessed": False}, "cells": {}, "score": None,
              "eligibility": "not_assessed", "scoring": {"state": "none", "method": "none", "revision_used": None, "stale": False}}
    reg = registry()
    row = parse(X.build_csv(lang="en", columns=[reg[c] for c in ("mandatory.summary", "mandatory:req_a", "decision.score", "decision.scoring")], rows=[legacy], meta=None))[1]
    assert row == ["Not Assessed", "Not Assessed", "", "Not scored"]


def test_metadata_carries_job_client_organization_filters_and_the_decision_support_note():
    rows = parse(X.build_csv(lang="en", columns=[registry()["candidate.name"]], rows=[ROW], meta={"job": "Engineer (J-1)", "client": "Acme", "organization": "Tenant",
                                                                                                  "filters": "eligibility=eligible", "sort": "decision.score desc"}))
    flat = {r[0]: (r[1] if len(r) > 1 else "") for r in rows[:9] if r}
    assert flat["Job"] == "Engineer (J-1)" and flat["Client"] == "Acme" and flat["Organization"] == "Tenant" and flat["Filters"] == "eligibility=eligible"
    assert any("not a ranking" in r[0] for r in rows[:10] if r) and rows[9] == [] and rows[10] == ["Candidate"]


def test_english_and_arabic_vocabularies_have_the_same_keys():
    for table in (L.STATUS, L.FLAG, L.SUMMARY, L.WORKFLOW, L.META):
        assert set(table["en"]) == set(table["ar"])
    from routers.applications import WORKFLOW_STATUSES
    assert set(L.WORKFLOW["en"]) == WORKFLOW_STATUSES


# ── the evaluator labels why an item needs a person ─────────────────────────────────────────────────────────────────

CV = "Five years of SQL troubleshooting and incident management."


def settle(importance, result, *, quote="Five years of SQL troubleshooting", confidence=0.9, **extra):
    from services.item_scoring import evaluator as V
    spec = V.ItemSpec("req_a", "skills", importance, "SQL", 100 if importance == "required" else None)
    return V.validate_output({"evaluations": [{"item_id": "req_a", "result": result, "evidence_quotes": [quote] if quote else [],
                                               "explanation": "x", "confidence": confidence, **extra}]}, [spec], CV)[0]


def test_an_unverified_quotation_is_labelled_evidence_not_found_and_downgraded():
    item = settle("required", "met", quote="invented text that is not in the CV")
    assert (item.result, item.review_reason) == ("unclear", "no_verified_evidence")


def test_low_confidence_is_labelled_and_downgraded():
    item = settle("required", "not_met", confidence=0.3)
    assert (item.result, item.review_reason) == ("unclear", "low_confidence")
    assert settle("preferred", "yes", confidence=0.2).review_reason == "low_confidence"
    assert settle("preferred", "yes", confidence=0.2).result == "not_clear"


def test_conflicting_evidence_is_labelled_only_on_an_unclear_result():
    assert settle("required", "unclear", quote="", evidence_conflict=True).review_reason == "conflicting_evidence"
    assert settle("preferred", "not_clear", quote="", evidence_conflict=True).review_reason == "conflicting_evidence"
    assert settle("required", "met", evidence_conflict=True).review_reason is None            # a decisive, verified result is not flagged by the model's say-so
    assert settle("required", "unclear", quote="").review_reason is None                      # a plain unclear carries no extra label


def test_a_verified_decisive_result_carries_no_label():
    assert settle("required", "met").review_reason is None and settle("preferred", "yes").review_reason is None
