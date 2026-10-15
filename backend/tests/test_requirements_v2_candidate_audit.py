"""Offline audit of the completed v2-3 versus v2-5 comparison. No network, no paid call.

They prove: the stored evidence is the uploaded bytes; every row's inputs, model, cost and spend match the manifest; the saved
report is reproduced exactly by the unchanged scorers; the diagnostic trace accounts for every frozen flag; the classification
separates matching limitations from genuine defects on synthetic answers; and the specific facts stated in AUDIT.md hold.
"""
import copy
import hashlib
import json
import pathlib

import pytest

import scripts.requirements_v2_candidate_audit as A
import scripts.requirements_v2_candidate_eval as E
import scripts.requirements_v2_tc20_compare as cmp
import scripts.requirements_v2_tc20_eval as ev

RUN = A.EVIDENCE
SHA = {
    "calls.jsonl": "7a176b8ba6b78f1285187c1b72713b93aea76d2250f6b22e9b53e985c5df6f73",
    "manifest.json": "156924f9c21fa804c9cbdb9e9bc648a808132475d15f68156f0cbf9fe34dfea5",
    "scored.json": "a9fd75f6b7baacb0bcbbb50fb95cbfd4e9f7b6aaff39085bec91a3512c8d3458",
}

def sha(b):
    return hashlib.sha256(b).hexdigest()


@pytest.fixture(scope="module")
def rows():
    return [json.loads(x) for x in (RUN / "calls.jsonl").read_text(encoding="utf-8").splitlines()]


@pytest.fixture(scope="module")
def manifest():
    return json.loads((RUN / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def scored():
    return json.loads((RUN / "scored.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def audit_out():
    return A.audit(RUN)


@pytest.fixture(scope="module")
def summary_out():
    return json.loads((RUN / "summary.json").read_text(encoding="utf-8"))


# ── 1. provenance ────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_stored_files_are_the_uploaded_bytes():
    for name, digest in SHA.items():
        assert sha((RUN / name).read_bytes()) == digest, name


def test_the_manifest_pins_equal_the_files_this_audit_uses(manifest):
    p = manifest["pins"]
    assert p["candidate_sha256"] == E.CANDIDATE_SHA256
    assert p["control_sha256"] == E.CONTROL_SHA256
    assert p["labels_sha256"] == sha(ev.LABELS.read_bytes())
    assert p["eval_set_manifest_sha256"] == sha(E.EVAL_MANIFEST.read_bytes())
    assert p["script_sha256"] == sha(pathlib.Path(E.__file__).read_bytes())
    assert p["tc20_eval_script_sha256"] == sha(pathlib.Path(ev.__file__).read_bytes())
    assert p["compare_script_sha256"] == sha(pathlib.Path(cmp.__file__).read_bytes())
    assert p["git_commit"] == "1e73231bda9e1061d5d78be29eb68bf3c1154e4b"


def test_every_row_matches_its_recorded_inputs_model_cost_and_finish(rows, manifest):
    units = {u["id"]: u for u in E.jd_sets()}
    prompts = {"v2-3": E.CONTROL_SHA256, "v2-5": E.CANDIDATE_SHA256}
    for r in rows:
        msgs = E.messages(r["arm"], units[r["jd"]])
        assert r["input"]["system_sha256"] == sha(msgs[0]["content"].encode()) == prompts[r["arm"]]
        assert r["input"]["user_sha256"] == sha(msgs[1]["content"].encode())
        assert r["model"] == r["model_returned"] == E.MODEL and r["finish_reason"] == "stop" and r["error"] is None
        expected_cost = r["usage"]["prompt_tokens"] * E.PRICE_IN + r["usage"]["completion_tokens"] * E.PRICE_OUT
        assert r["cost_usd"] == pytest.approx(expected_cost, abs=2e-6)


def test_the_spend_is_the_sum_of_the_rows_and_within_the_cap(rows, scored):
    assert scored["run"] == {"spent_usd": 0.625308, "calls_attempted": 22, "successful_calls": 22, "stopped": None}
    assert sum(r["cost_usd"] for r in rows) == pytest.approx(0.625308, abs=2e-5)
    assert scored["run"]["spent_usd"] <= E.CAP_USD


def test_the_plan_recorded_in_the_manifest_is_the_planned_one(manifest):
    assert manifest["plan"]["call_count"] == 22 and manifest["plan"]["retries"] == 0
    assert manifest["plan"]["worst_case_total_usd"] == pytest.approx(1.706582, abs=1e-6)


# ── 2. the reproduction, field by field ───────────────────────────────────────────────────────────────────────────────

def test_the_frozen_scorer_reproduces_scored_json_exactly(rows, scored):
    assert E.report(rows) == scored["report"]


def test_the_audit_reproduces_the_report_with_no_differing_field(audit_out):
    assert audit_out["reproduction"] == {"report_equal": True, "fields_differing": []}


def test_the_diagnostic_trace_accounts_for_every_frozen_flag(audit_out):
    checked = [pc for pc in audit_out["per_call"] if "diagnosis" in pc]
    assert len(checked) == 12                                  # the expected-output answers: 2 JDs x 2 arms x 3 calls
    assert all(pc["diagnosis"]["trace_counts_equal_frozen"] for pc in checked)


# ── 3. the classified evidence (the numbers stated in AUDIT.md) ────────────────────────────────────────────────────────

def test_arabic_v23_frozen_flags_are_matching_limitations_except_three_real_defects(summary_out):
    s = summary_out["eval_ar_facilities_01|v2-3"]["frozen_flag_classes"]
    assert s == {"matching_limitation": 46, "genuine_and_not_split": 2, "genuine_category_error": 1}


def test_arabic_v23_soft_skill_placements_are_visible_only_to_the_diagnostic(summary_out):
    d = summary_out["eval_ar_facilities_01|v2-3"]["diagnostic_only_flag_classes"]
    assert d == {"genuine_category_error": 3, "contestable_expected_label": 2}


def test_arabic_v25_has_no_and_or_soft_skill_defect_but_drops_the_company_statement(summary_out):
    s = summary_out["eval_ar_facilities_01|v2-5"]["frozen_flag_classes"]
    assert s == {"contestable_expected_label": 3, "genuine_missing": 3}


def test_english_v23_has_three_and_splits_and_two_altered_alternatives(summary_out):
    s = summary_out["eval_en_field_service_01|v2-3"]["frozen_flag_classes"]
    assert s == {"genuine_category_error": 5, "genuine_and_not_split": 3, "genuine_altered_alternative_wording": 2}


def test_english_v25_keeps_the_and_split_and_the_certificate_alternatives(summary_out):
    s = summary_out["eval_en_field_service_01|v2-5"]["frozen_flag_classes"]
    assert s == {"contestable_expected_label": 3, "genuine_category_error": 3}


def test_no_answer_invents_content_or_breaks_the_schema_or_weights(summary_out):
    for key, b in summary_out.items():
        assert b["sources_verbatim"] == b["sources"], key            # every source span is an exact substring of its JD
        assert b["schema_compliant"] == b["calls"], key
        assert b["weights_usable"] == b["calls"], key
    assert all(t in (85, 90, 100, 110) for key, b in summary_out.items() for t in b["weights_total"])


def test_the_arabic_company_statement_is_absent_from_every_v25_answer_and_present_in_v23(rows):
    for r in rows:
        if r["jd"] != "eval_ar_facilities_01":
            continue
        in_info = any("تدير الشركة" in str(x.get("source_text", "")) for x in json.loads(r["raw"]).get("informational_items") or [])
        anywhere = "تدير الشركة" in r["raw"]
        if r["arm"] == "v2-5":
            assert not in_info and not anywhere, r["call"]
        elif r["call"] <= 3:
            assert in_info, r["call"]


def test_the_english_certificate_alternatives_are_correct_in_v25_and_altered_in_v23(rows):
    for r in rows:
        if r["jd"] != "eval_en_field_service_01":
            continue
        cert = [it for lst in json.loads(r["raw"])["categories"].values() for it in lst if "Certified welder" in it["source_text"]]
        assert len(cert) == 1
        if r["arm"] == "v2-5":
            assert cert[0]["alternatives"] == ["Certified welder", "boilermaker certificate"]
        elif r["call"] >= 2:
            assert cert[0]["alternatives"] == ["Certified welder certificate", "boilermaker certificate"]


# ── 4. the TC20 facts ─────────────────────────────────────────────────────────────────────────────────────────────────

def _tc20(rows, arm):
    return [A.tc20_structure(r["raw"], A.jd_text("tc20"), A.ev.load_inputs()[1]) for r in rows if r["jd"] == "tc20" and r["arm"] == arm]


def test_tc20_duties_are_complete_in_all_v25_calls_and_missing_in_v23_call_1(rows):
    v23 = _tc20(rows, "v2-3")
    v25 = _tc20(rows, "v2-5")
    assert [s["duty_lines_found"] for s in v25] == [16] * 5
    assert [s["duty_lines_found"] for s in v23] == [0, 16, 16, 16, 16]


def test_tc20_the_ict_and_familiarity_or_lists_lose_their_alternatives_in_every_call(rows):
    for arm in ("v2-3", "v2-5"):
        for s in _tc20(rows, arm):
            assert all(alts is None for _, _, alts in s["ict_or"]) and s["ict_or"]
            assert all(alts is None for _, _, alts in s["familiarity_or"]) and s["familiarity_or"]


def test_tc20_the_communication_coordination_teamwork_line_is_never_split(rows):
    for arm in ("v2-3", "v2-5"):
        for s in _tc20(rows, arm):
            assert len(s["communication_item"]) == 1


def test_tc20_soft_skills_moved_to_soft_skills_in_every_v25_call_and_not_in_v23(rows):
    for s in _tc20(rows, "v2-5"):
        assert s["communication_item"][0][0] == "soft_skills"
    for s in _tc20(rows, "v2-3"):
        assert s["communication_item"][0][0] == "skills"


def test_tc20_reporting_statement_is_missing_from_the_whole_answer_in_v25_calls_two_to_five(rows):
    raws = [r["raw"] for r in rows if r["jd"] == "tc20" and r["arm"] == "v2-5"]
    assert ["deliverables" in raw for raw in raws] == [True, False, False, False, False]
    assert [s["informational_items"] for s in _tc20(rows, "v2-5")] == [2, 0, 0, 0, 0]


def test_tc20_v23_call_1_lacks_the_reporting_statement_and_call_5_mislists_the_location(rows):
    v23 = _tc20(rows, "v2-3")
    assert v23[0]["reporting_statement"] == [] and "deliverables" not in [r for r in rows if r["jd"] == "tc20"][0]["raw"]
    assert v23[4]["location_statement"][0][0] == "informational_items"        # the work location is in company_description


def test_tc20_location_is_in_non_scoreable_in_all_v25_calls(rows):
    for s in _tc20(rows, "v2-5"):
        assert s["location_statement"] and s["location_statement"][0][0] == "non_scoreable_requirements"


# ── 5. the diagnostic classifier on synthetic answers ──────────────────────────────────────────────────────────────────

def _ref(name):
    spec = A.spec_of(name)
    jd = A.jd_text(name)
    return spec, jd, json.loads(A.reference_answer(spec))


@pytest.mark.parametrize("name", ["eval_en_field_service_01", "eval_ar_facilities_01"])
def test_the_reference_answer_has_no_flag_in_any_group_or_class(name):
    spec, jd, ref = _ref(name)
    d = A.diagnose_answer(json.dumps(ref, ensure_ascii=False), jd, spec)
    assert d["frozen_flags"] == [] and d["trace_counts_equal_frozen"] is True


def test_a_missing_terminal_full_stop_is_a_matching_limitation_not_an_invention():
    spec, jd, ref = _ref("eval_en_field_service_01")
    ref["categories"]["experience"][0]["source_text"] = ref["categories"]["experience"][0]["source_text"].rstrip(".")
    d = A.diagnose_answer(json.dumps(ref, ensure_ascii=False), jd, spec)
    classes = [f["class"] for f in d["frozen_flags"]]
    assert classes and set(classes) == {"matching_limitation"}
    assert d["trace_counts_equal_frozen"] is True


def test_a_source_that_is_not_in_the_jd_is_a_genuine_invention():
    spec, jd, ref = _ref("eval_en_field_service_01")
    ref["categories"]["skills"][0]["source_text"] = "Able to repair pumps by hand."
    d = A.diagnose_answer(json.dumps(ref, ensure_ascii=False), jd, spec)
    inv = [f for f in d["frozen_flags"] if f["group"] == "invented"]
    assert inv and inv[0]["class"] == "genuine_invention" and d["trace_counts_equal_frozen"] is True


def test_an_and_pair_merged_into_one_item_is_a_genuine_and_not_split():
    spec, jd, ref = _ref("eval_en_field_service_01")
    soft = ref["categories"]["soft_skills"]
    ref["categories"]["soft_skills"] = [{**soft[0], "text": "Communication and teamwork"}]
    d = A.diagnose_answer(json.dumps(ref, ensure_ascii=False), jd, spec)
    assert [f["class"] for f in d["frozen_flags"] if f["group"] == "omissions"] == ["genuine_and_not_split"]


def test_a_shorter_verbatim_condition_quote_in_the_right_list_is_a_matching_limitation():
    spec, jd, ref = _ref("eval_ar_facilities_01")
    row = ref["post_hiring_conditions"][0]
    row["source_text"] = "فحص طبي قبل المباشرة."
    assert row["source_text"] in jd
    d = A.diagnose_answer(json.dumps(ref, ensure_ascii=False), jd, spec)
    routing = [f for f in d["frozen_flags"] if f["group"] == "routing"]
    assert [f["diagnosis"] for f in routing] == ["matching_limitation_shorter_verbatim_quote"]
    assert [f["class"] for f in routing] == ["matching_limitation"]


def test_a_condition_in_the_wrong_list_is_a_genuine_wrong_list():
    spec, jd, ref = _ref("eval_en_field_service_01")
    loc = ref["non_scoreable_requirements"].pop(0)
    ref["informational_items"].append(loc)
    d = A.diagnose_answer(json.dumps(ref, ensure_ascii=False), jd, spec)
    routing = [f for f in d["frozen_flags"] if f["group"] == "routing"]
    assert [f["class"] for f in routing] == ["genuine_wrong_list"] and d["trace_counts_equal_frozen"] is True


def test_an_altered_alternative_is_a_genuine_or_defect():
    spec, jd, ref = _ref("eval_en_field_service_01")
    cert = [it for it in ref["categories"]["certifications"]][0]
    cert["alternatives"] = ["Certified welder certificate", "boilermaker certificate"]
    d = A.diagnose_answer(json.dumps(ref, ensure_ascii=False), jd, spec)
    assert [f["class"] for f in d["frozen_flags"]] == ["genuine_altered_alternative_wording"]


def test_a_remote_monitoring_placement_in_experience_is_contestable_not_genuine():
    spec, jd, ref = _ref("eval_en_field_service_01")
    item = [it for it in ref["categories"]["skills"] if "remote monitoring" in it["source_text"]
            or it["source_text"].startswith("Experience with remote")]
    if not item:
        item = [it for it in ref["categories"]["skills"] if "monitoring" in it["text"].lower() or "remote" in it["text"].lower()]
    assert item
    moved = item[0]
    ref["categories"]["skills"].remove(moved)
    ref["categories"]["experience"].append(moved)
    d = A.diagnose_answer(json.dumps(ref, ensure_ascii=False), jd, spec)
    assert [f["class"] for f in d["frozen_flags"] if f["group"] == "categories"] == ["contestable_expected_label"]


def test_a_category_error_hidden_by_a_missing_full_stop_is_reported_as_diagnostic_only():
    spec, jd, ref = _ref("eval_en_field_service_01")
    comm = [it for it in ref["categories"]["soft_skills"] if it["text"] == "Communication"][0]
    ref["categories"]["soft_skills"].remove(comm)
    comm["source_text"] = comm["source_text"].rstrip(".")
    ref["categories"]["skills"].append(comm)
    d = A.diagnose_answer(json.dumps(ref, ensure_ascii=False), jd, spec)
    entry = [e for e in d["entries"] if e["key"] == "Communication"][0]
    assert entry["status"] == "matching_limitation_terminal_punctuation"
    assert [f["group"] for f in entry["diagnostic_only_flags"]] == ["categories"]
    assert all(f["group"] != "categories" or f.get("key") != "Communication" for f in d["frozen_flags"])


def test_the_classifier_never_uses_fuzzy_matching_for_a_genuinely_missing_entry():
    spec, jd, ref = _ref("eval_en_field_service_01")
    ref["categories"]["experience"] = [it for it in ref["categories"]["experience"] if "Inspect pumps" not in it["source_text"]]
    d = A.diagnose_answer(json.dumps(ref, ensure_ascii=False), jd, spec)
    missing = [e for e in d["entries"] if e["status"] == "genuinely_missing"]
    assert [e["key"] for e in missing] == ["Inspect pumps"]
