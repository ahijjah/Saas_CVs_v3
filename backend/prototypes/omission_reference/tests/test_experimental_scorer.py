"""Offline tests for the EXPERIMENTAL scorer v1. Replays stored answers and synthetic answers only; no model call."""
import hashlib
import json
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent / "omission_inventory"), str(HERE.parent.parent / "scripts"), str(HERE.parent.parent)]
import experimental_scorer_v1 as X  # noqa: E402
import replay_ref as RR  # noqa: E402

B02 = "B02_en_data_analyst"
B12 = "B12_ar_injection"
B02_UNIT = "Proficiency in Python or R."
MANIFEST = json.loads((HERE / "reference_set_v1_manifest.json").read_text(encoding="utf-8"))


def answer(*items):
    return json.dumps({"categories": {"requirements": list(items)}}, ensure_ascii=False)


def item(text, source, alternatives=None, importance="required"):
    return {"text": text, "source_text": source, "alternatives": alternatives or [], "importance": importance}


# ---- alternative structure (S002, RQ-027) ----

def test_python_or_r_held_in_one_item_with_both_alternatives_is_together():
    raw = answer(item("Python or R proficiency", "Proficiency in Python or R", ["Python", "R"]))
    s = X.alternative_structure(B02, B02_UNIT, 6, raw)
    assert s["structure"] == "together"
    assert s["alternatives_recorded"] == [{"text": "Python or R proficiency", "alternatives": ["Python", "R"]}]


def test_one_item_holding_both_names_but_recording_one_alternative_is_incomplete_together():
    raw = answer(item("Python or R proficiency", "Proficiency in Python or R", ["Python"]))
    assert X.alternative_structure(B02, B02_UNIT, 6, raw)["structure"] == "together_alternatives_incomplete"


def test_python_and_r_in_separate_items_each_recording_both_is_split():
    raw = answer(item("Python", "Proficiency in Python", ["Python", "R"]),
                 item("R", "Proficiency in Python or R", ["Python", "R"]))
    assert X.alternative_structure(B02, B02_UNIT, 6, raw)["structure"] == "split"


def test_only_one_alternative_present_is_incomplete():
    raw = answer(item("Python", "Proficiency in Python or R", ["R"]))
    assert X.alternative_structure(B02, B02_UNIT, 6, raw)["structure"] == "incomplete"


def test_no_item_for_the_unit_is_missing():
    raw = answer(item("SQL", "SQL skills", []))
    assert X.alternative_structure(B02, B02_UNIT, 6, raw)["structure"] == "missing"


def test_r_does_not_match_inside_a_longer_word():
    raw = answer(item("Proficiency in Python or Rust", "Proficiency in Python or R", ["Python"]))
    assert X.alternative_structure(B02, B02_UNIT, 6, raw)["structure"] == "incomplete"


def test_a_unit_without_a_declared_alternative_structure_returns_none():
    assert X.alternative_structure(B02, "anything", 99, answer()) is None


# ---- item attribution (S008 sources) ----

def test_an_item_quoted_from_the_conflict_note_is_attributed_to_the_conflict_not_the_required_line():
    raw = answer(item("CSS", "CSS", importance="preferred"), item("HTML", "HTML"))
    raw2 = answer(item("CSS", "معرفة CSS وHTML اختيارية"))
    src2 = X.item_sources(raw2, ["CSS"], "معرفة بـ CSS وHTML.", "معرفة CSS وHTML اختيارية لهذه الوظيفة.")
    assert src2["CSS"] == ["conflict_note"]


def test_an_item_quoted_from_the_required_line_is_attributed_to_the_required_unit():
    raw = answer(item("CSS", "معرفة بـ CSS وHTML"))
    src = X.item_sources(raw, ["CSS"], "معرفة بـ CSS وHTML.", "معرفة CSS وHTML اختيارية لهذه الوظيفة.")
    assert src["CSS"] == ["required_unit"]


# ---- importance (CSS/HTML), Arabic cue, schema findings ----

def test_the_arabic_optional_cue_maps_to_preferred():
    assert X.CUE_MAP["اختيارية"] == "preferred"


def test_optional_is_a_schema_finding_and_not_an_accepted_importance():
    raw = answer(item("CSS", "معرفة بـ CSS وHTML", importance="optional"), item("HTML", "HTML"))
    imp = X.importance_checks(raw, [])
    assert {"token": "CSS", "importance": "optional"} in imp["schema_findings"]
    assert imp["within_accepted"] is False


def test_required_and_preferred_are_accepted_for_both_conflicting_items():
    raw = answer(item("CSS", "CSS", importance="required"), item("HTML", "HTML", importance="preferred"))
    imp = X.importance_checks(raw, [])
    assert imp["schema_findings"] == [] and imp["within_accepted"] is True


def test_a_merged_css_and_html_item_is_still_checked_for_importance():
    raw = answer(item("CSS and HTML", "CSS وHTML", importance="preferred"))
    imp = X.importance_checks(raw, [])
    assert imp["items"]["CSS"] == ["preferred"] and imp["items"]["HTML"] == ["preferred"]


def test_the_cue_mapping_is_reported_for_the_reference_cue():
    cues = X.cue_units()
    assert any(c["jd_id"] == B12 and c["cue"] == "اختيارية" for c in cues)
    imp = X.importance_checks(answer(), [c for c in cues if c["jd_id"] == B12])
    assert imp["cue_mapping"] == {"اختيارية": "preferred"}


def test_importance_does_not_read_conflict_evidence():
    import inspect
    assert list(inspect.signature(X.importance_checks).parameters) == ["raw", "cue_units"]


# ---- conflict evidence is kept apart from importance ----

def test_conflict_evidence_is_labelled_as_review_evidence_and_carries_the_exact_statement():
    ev = X.conflict_evidence()
    assert ev, "the confirmed RQ-021 conflict must be present"
    assert all(e["role"] == "review evidence, not a finding" for e in ev)
    assert any("اختيارية" in e["statement"] for e in ev)


def test_score_answer_keeps_conflict_evidence_out_of_the_importance_result():
    raw = answer(item("CSS", "CSS", importance="preferred"), item("HTML", "HTML", importance="preferred"))
    res = X.score_answer(B12, raw, "stop")
    assert set(res["importance"]) == {"schema_findings", "cue_mapping", "items", "within_accepted"}
    assert res["conflict_evidence"] == X.conflict_evidence()


# ---- stored answers: every check runs, nothing is a model call ----

def stored_answers():
    out = []
    for src in RR.STORED:
        for line in src["path"].read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec.get("error") or not rec.get("raw"):
                continue
            if src["kind"] == "tc20":
                continue
            if rec["case"] in RR.PACKAGE_JDS:
                out.append((rec["case"], rec["raw"], rec.get("finish_reason")))
    return out


@pytest.mark.parametrize("jd_id,raw,finish", stored_answers())
def test_every_stored_answer_produces_every_check(jd_id, raw, finish):
    res = X.score_answer(jd_id, raw, finish)
    for key in ("source_unit_omissions", "alternative_structure", "independent_item_structure", "child_routing", "importance", "conflict_evidence"):
        assert key in res
    assert res["version"] == X.VERSION


def test_child_routing_statuses_are_only_the_four_defined_values():
    allowed = {"met", "partial", "misrouted", "missing"}
    for jd_id, raw, finish in stored_answers():
        for c in X.score_answer(jd_id, raw, finish)["child_routing"]:
            assert c["status"] in allowed, c


def test_the_committed_results_are_the_replay_of_the_stored_answers():
    committed = json.loads((HERE / "results" / "experimental_scorer_v1.json").read_text(encoding="utf-8"))
    assert committed["version"] == X.VERSION and committed["frozen_scorer_unchanged"] is True
    assert len(committed["answers"]) == 40


# ---- isolation ----

def test_the_frozen_benchmark_scorer_is_unchanged():
    frozen = HERE.parent.parent / "scripts" / "requirements_v2_extraction_eval.py"
    assert hashlib.sha256(frozen.read_bytes()).hexdigest() == MANIFEST["frozen_benchmark_scorer_sha256"]


def test_the_experimental_scorer_makes_no_model_call_and_imports_no_client():
    src = (HERE / "experimental_scorer_v1.py").read_text(encoding="utf-8")
    for banned in ("openai", "anthropic", "requests", "httpx", "urllib.request"):
        assert f"import {banned}" not in src and f"from {banned}" not in src


def test_production_code_does_not_reference_the_omission_reference_or_this_scorer():
    backend = HERE.parent.parent
    for folder in ("services", "workers", "routers"):
        for path in (backend / folder).rglob("*.py") if (backend / folder).exists() else []:
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "omission_reference" not in text and "experimental_scorer" not in text, path


# ---- regression: child-routing parents are RQ-029 (B03) and RQ-030 (B07); RQ-027 is the Python-or-R row, not a child parent ----

def test_child_routing_parents_are_rq_029_for_b03_and_rq_030_for_b07():
    assert X.CHILD_PARENTS == {"B03_en_warehouse_supervisor": "RQ-029", "B07_ar_accountant": "RQ-030"}
    for jd_id, parent in X.CHILD_PARENTS.items():
        for raw in [r for case, r, _ in stored_answers() if case == jd_id][:1]:
            routed = X.score_answer(jd_id, raw, None)["child_routing"]
            assert routed and {c["child_id"].split("/")[0] for c in routed} == {parent}, jd_id


def test_child_parents_match_the_reviewed_child_units_and_are_not_the_python_or_r_row():
    import csv
    children = list(csv.DictReader((HERE / "review_children.csv").open(encoding="utf-8")))
    parents = {(c["parent_rq_id"], c["jd_id"]) for c in children}
    assert ("RQ-029", "B03_en_warehouse_supervisor") in parents
    assert ("RQ-030", "B07_ar_accountant") in parents
    assert all(p != "RQ-027" for p, _ in parents)
    assert X.ALTERNATIVE_UNITS[("B02_en_data_analyst", 6)]["rq"] == "RQ-027"
