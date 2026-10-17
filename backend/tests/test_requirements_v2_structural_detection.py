"""Requirements-v2 structural detection driven by an explicit, validated expectation block (offline, pure). No model call, no parser
change, no database, no network, and no structure inferred from AND/OR wording: a finding needs a supplied expectation.

Expectation kinds: alternatives_together (alternatives split across items) and independent_items (independent items merged)."""
from __future__ import annotations

import copy
import hashlib
import json
import pathlib

import pytest

from services.requirements_v2 import contract as C
from services.requirements_v2.extraction import parse_response
from services.requirements_v2.readiness import compute_readiness
from services.requirements_v2.structural_detection import attach, detect
from services.requirements_v2.structural_expectations import ExpectationError, validate_expectations
from services.requirements_v2.structural_review import structural_status

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
B02 = json.loads((FIXTURES / "requirements_v2_foundations" / "B02_en_data_analyst.json").read_text(encoding="utf-8"))["jd"]
B12 = json.loads((FIXTURES / "requirements_v2_foundations" / "B12_ar_injection.json").read_text(encoding="utf-8"))["jd"]
B02_EXPECTATIONS = json.loads((FIXTURES / "requirements_v2_structural_expectations" / "B02_en_data_analyst.expectations.json")
                              .read_text(encoding="utf-8"))
B12_EXPECTATIONS = json.loads((FIXTURES / "requirements_v2_structural_expectations" / "B12_ar_injection.expectations.json")
                              .read_text(encoding="utf-8"))

PYTHON_OR_R = "Proficiency in Python or R"
CSS_HTML = "معرفة بـ CSS وHTML"
ENGLISH_JD = "Analyst role\n\nRequirements\n- Experience with Scala or Julia.\n- SQL.\n"
ENGLISH_SOURCE = "Experience with Scala or Julia"
ARABIC_JD = "مطور خلفي\n\nالمتطلبات:\n- معرفة بـ Docker وKubernetes.\n"
ARABIC_SOURCE = "معرفة بـ Docker وKubernetes"
CONNECTED_JD = "Strong communication, coordination and teamwork skills are expected."
CONNECTED_SOURCE = "communication, coordination and teamwork skills"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _block(jd: str, kind: str, source: str, tokens, eid: str = "exp-1", **overrides) -> dict:
    """Test-only helper: builds an expectation block for `source` in `jd` (the detector itself never searches)."""
    start = jd.index(source)
    exp = {"id": eid, "kind": kind, "source_text": source, "source_start": start, "source_end": start + len(source),
           "tokens": list(tokens)}
    exp.update(overrides)
    return {"schema_version": 1, "jd_sha256": _sha(jd), "expectations": [exp]}


def _draft(jd: str, items: list[dict]):
    raw = json.dumps({"categories": {"skills": items}, "category_weights": {"skills": 100}, "scoring_confirmation": None},
                     ensure_ascii=False)
    res = parse_response(raw, jd)
    assert res.status == "draft", res.errors
    return res.requirements


def _ids(doc) -> list[str]:
    return [i["id"] for i in doc["categories"]["skills"]["items"]]


SPLIT_ENGLISH = [{"text": "Scala", "importance": "required", "source_text": ENGLISH_SOURCE},
                 {"text": "Julia", "importance": "required", "source_text": ENGLISH_SOURCE}]
TOGETHER_ENGLISH = [{"text": "Scala or Julia", "importance": "required", "source_text": ENGLISH_SOURCE,
                     "alternatives": ["Scala", "Julia"]}]
MERGED_ARABIC = [{"text": "Docker and Kubernetes", "importance": "required", "source_text": ARABIC_SOURCE}]
SEPARATE_ARABIC = [{"text": "Docker", "importance": "required", "source_text": ARABIC_SOURCE},
                   {"text": "Kubernetes", "importance": "required", "source_text": ARABIC_SOURCE}]


# ---- supplied expectations: new English and Arabic JDs ----

def test_a_supplied_alternatives_expectation_detects_a_split_english_jd():
    doc = _draft(ENGLISH_JD, SPLIT_ENGLISH)
    findings = detect(doc, ENGLISH_JD, _block(ENGLISH_JD, "alternatives_together", ENGLISH_SOURCE, ["Scala", "Julia"]))["findings"]
    assert len(findings) == 1
    f = findings[0]
    assert f["code"] == "alternatives_split_across_items" and f["status"] == "open" and f["resolution"] is None
    assert f["item_ids"] == _ids(doc)
    assert f["source_start"] == ENGLISH_JD.index(ENGLISH_SOURCE) and f["source_end"] == f["source_start"] + len(ENGLISH_SOURCE)


def test_a_together_english_item_under_the_alternatives_expectation_has_no_finding():
    doc = _draft(ENGLISH_JD, TOGETHER_ENGLISH)
    assert detect(doc, ENGLISH_JD, _block(ENGLISH_JD, "alternatives_together", ENGLISH_SOURCE, ["Scala", "Julia"]))["findings"] == []


def test_a_supplied_independent_items_expectation_detects_a_merged_arabic_item():
    doc = _draft(ARABIC_JD, MERGED_ARABIC)
    findings = detect(doc, ARABIC_JD, _block(ARABIC_JD, "independent_items", ARABIC_SOURCE, ["Docker", "Kubernetes"]))["findings"]
    assert len(findings) == 1
    f = findings[0]
    assert f["code"] == "independent_items_merged" and f["item_ids"] == [_ids(doc)[0]]
    assert f["source_start"] == ARABIC_JD.index(ARABIC_SOURCE) and ARABIC_JD[f["source_start"]:f["source_end"]] == ARABIC_SOURCE


def test_separate_arabic_items_under_the_independent_expectation_have_no_finding():
    doc = _draft(ARABIC_JD, SEPARATE_ARABIC)
    assert detect(doc, ARABIC_JD, _block(ARABIC_JD, "independent_items", ARABIC_SOURCE, ["Docker", "Kubernetes"]))["findings"] == []


def test_the_same_jds_without_expectations_produce_no_findings():
    for jd, items in ((ENGLISH_JD, SPLIT_ENGLISH), (ARABIC_JD, MERGED_ARABIC)):
        doc = _draft(jd, items)
        assert detect(doc, jd, None)["findings"] == []
        assert detect(doc, jd, {"schema_version": 1, "jd_sha256": _sha(jd), "expectations": []})["findings"] == []


def test_connected_communication_and_teamwork_text_has_no_finding_without_an_explicit_expectation():
    doc = _draft(CONNECTED_JD, [{"text": "Communication, coordination and teamwork", "importance": "required",
                                 "source_text": CONNECTED_SOURCE}])
    assert detect(doc, CONNECTED_JD, None)["findings"] == []
    assert detect(doc, CONNECTED_JD, {"schema_version": 1, "jd_sha256": _sha(CONNECTED_JD), "expectations": []})["findings"] == []


# ---- the reviewed fixtures, supplied as expectations (not hardcoded in the detector) ----

def test_b02_fixture_expectation_still_produces_the_split_finding():
    assert B02_EXPECTATIONS["jd_sha256"] == _sha(B02)
    doc = _draft(B02, [{"text": "Python", "importance": "required", "source_text": PYTHON_OR_R},
                       {"text": "R", "importance": "required", "source_text": PYTHON_OR_R}])
    findings = detect(doc, B02, B02_EXPECTATIONS)["findings"]
    assert [f["code"] for f in findings] == ["alternatives_split_across_items"]
    assert findings[0]["source_start"] == 142 and findings[0]["source_end"] == 168


def test_b12_fixture_expectation_still_produces_the_merged_finding():
    assert B12_EXPECTATIONS["jd_sha256"] == _sha(B12)
    doc = _draft(B12, [{"text": "CSS and HTML", "importance": "required", "source_text": CSS_HTML}])
    findings = detect(doc, B12, B12_EXPECTATIONS)["findings"]
    assert [f["code"] for f in findings] == ["independent_items_merged"]
    assert findings[0]["source_start"] == 103 and findings[0]["source_end"] == 121


def test_findings_pass_the_merged_contract_and_cause_needs_structural_review():
    doc = _draft(B12, [{"text": "CSS and HTML", "importance": "required", "source_text": CSS_HTML}])
    server = attach(doc, B12, B12_EXPECTATIONS)
    assert [i for i in structural_status(server, B12).issues if i.code == "structural_review_invalid"] == []
    assert compute_readiness(server, jd_text=B12).state == C.NEEDS_STRUCTURAL_REVIEW


# ---- fail closed: malformed, mismatched or unsupported expectation blocks ----

def test_a_block_bound_to_another_jd_hash_fails_closed():
    doc = _draft(ENGLISH_JD, SPLIT_ENGLISH)
    block = _block(ENGLISH_JD, "alternatives_together", ENGLISH_SOURCE, ["Scala", "Julia"])
    block["jd_sha256"] = _sha(ENGLISH_JD + " ")
    with pytest.raises(ExpectationError):
        detect(doc, ENGLISH_JD, block)


def test_wrong_offsets_fail_closed():
    block = _block(ENGLISH_JD, "alternatives_together", ENGLISH_SOURCE, ["Scala", "Julia"])
    start = block["expectations"][0]["source_start"]
    block["expectations"][0]["source_start"] = start + 1
    block["expectations"][0]["source_end"] = start + 1 + len(ENGLISH_SOURCE)
    with pytest.raises(ExpectationError):
        validate_expectations(block, ENGLISH_JD)


def test_source_text_that_is_not_at_the_offsets_fails_closed():
    block = _block(ENGLISH_JD, "alternatives_together", ENGLISH_SOURCE, ["Scala", "Julia"], source_text="Experience with Scala or Julia!")
    with pytest.raises(ExpectationError):
        validate_expectations(block, ENGLISH_JD)


def test_duplicate_expectation_ids_fail_closed():
    block = _block(ENGLISH_JD, "alternatives_together", ENGLISH_SOURCE, ["Scala", "Julia"])
    second = {**block["expectations"][0], "source_text": "SQL", "source_start": ENGLISH_JD.index("SQL"),
              "source_end": ENGLISH_JD.index("SQL") + 3, "tokens": ["SQL", "Julia"]}
    block["expectations"].append(second)                            # same id, different span
    with pytest.raises(ExpectationError):
        validate_expectations(block, ENGLISH_JD)


def test_duplicate_tokens_fail_closed():
    block = _block(ENGLISH_JD, "alternatives_together", ENGLISH_SOURCE, ["Scala", "Scala"])
    with pytest.raises(ExpectationError):
        validate_expectations(block, ENGLISH_JD)


def test_fewer_than_two_tokens_or_a_token_absent_from_the_wording_fail_closed():
    with pytest.raises(ExpectationError):
        validate_expectations(_block(ENGLISH_JD, "alternatives_together", ENGLISH_SOURCE, ["Scala"]), ENGLISH_JD)
    with pytest.raises(ExpectationError):
        validate_expectations(_block(ENGLISH_JD, "alternatives_together", ENGLISH_SOURCE, ["Scala", "Rust"]), ENGLISH_JD)


def test_an_unknown_kind_fails_closed():
    with pytest.raises(ExpectationError):
        validate_expectations(_block(ENGLISH_JD, "omission", ENGLISH_SOURCE, ["Scala", "Julia"]), ENGLISH_JD)


def test_an_exact_duplicate_signature_fails_closed_even_with_another_id():
    block = _block(ENGLISH_JD, "alternatives_together", ENGLISH_SOURCE, ["Scala", "Julia"])
    block["expectations"].append({**block["expectations"][0], "id": "exp-2", "tokens": ["Julia", "Scala"]})   # same kind, span, tokens
    with pytest.raises(ExpectationError):
        validate_expectations(block, ENGLISH_JD)


def test_distinct_expectations_may_share_one_sentence_span():
    block = _block(ENGLISH_JD, "alternatives_together", ENGLISH_SOURCE, ["Scala", "Julia"], eid="exp-alt")
    block["expectations"].append({**block["expectations"][0], "id": "exp-ind", "kind": "independent_items"})
    assert [e.kind for e in validate_expectations(block, ENGLISH_JD)] == ["alternatives_together", "independent_items"]
    doc = _draft(ENGLISH_JD, SPLIT_ENGLISH)
    kinds = [f["id"] for f in detect(doc, ENGLISH_JD, block)["findings"]]
    assert len(kinds) == 1                                           # only the alternatives expectation applies to these items


def test_case_variants_of_one_token_are_duplicates_and_fail_closed():
    with pytest.raises(ExpectationError):
        validate_expectations(_block(B12, "independent_items", CSS_HTML, ["CSS", "css"]), B12)
    with pytest.raises(ExpectationError):
        validate_expectations(_block(ENGLISH_JD, "alternatives_together", ENGLISH_SOURCE, ["Scala", "SCALA"]), ENGLISH_JD)


def test_html_matches_inside_an_arabic_prefixed_token():
    from services.requirements_v2.structural_expectations import has_token
    assert has_token("معرفة بـ CSS وHTML", "HTML")


def test_an_arabic_token_does_not_match_inside_a_longer_arabic_word():
    from services.requirements_v2.structural_expectations import has_token
    assert not has_token("العمل الجماعي", "عمل")
    assert has_token("عمل جماعي", "عمل")


def test_a_complete_arabic_token_or_phrase_matches():
    from services.requirements_v2.structural_expectations import has_token
    assert has_token("مطور واجهات", "واجهات")
    assert has_token("معرفة بـ CSS وHTML", "معرفة بـ CSS")
    assert has_token("معرفة بـ CSS وHTML", "CSS وHTML")


def test_malformed_blocks_fail_closed():
    base = _block(ENGLISH_JD, "alternatives_together", ENGLISH_SOURCE, ["Scala", "Julia"])
    bad_version = {**base, "schema_version": 2}
    extra_key = {**base, "note": "x"}
    not_a_list = {**base, "expectations": "nope"}
    bad_sha = {**base, "jd_sha256": "ABC"}
    for block in (bad_version, extra_key, not_a_list, bad_sha):
        with pytest.raises(ExpectationError):
            validate_expectations(block, ENGLISH_JD)


# ---- binding: an expectation for one JD does not apply to another JD ----

def test_the_same_wording_in_an_unrelated_jd_without_its_expectation_has_no_finding():
    unrelated = "Engineer role.\nProficiency in Python or R is needed.\n"
    doc = _draft(unrelated, [{"text": "Python", "importance": "required", "source_text": PYTHON_OR_R},
                             {"text": "R", "importance": "required", "source_text": PYTHON_OR_R}])
    assert detect(doc, unrelated, None)["findings"] == []


def test_the_b02_expectation_applied_to_an_unrelated_jd_fails_closed():
    unrelated = "Engineer role.\nProficiency in Python or R is needed.\n"
    doc = _draft(unrelated, [{"text": "Python", "importance": "required", "source_text": PYTHON_OR_R},
                             {"text": "R", "importance": "required", "source_text": PYTHON_OR_R}])
    with pytest.raises(ExpectationError):
        detect(doc, unrelated, B02_EXPECTATIONS)


# ---- identity, determinism and ownership ----

def test_finding_ids_differ_for_expectations_with_different_identity_but_the_same_code_and_items():
    doc = _draft(ENGLISH_JD, SPLIT_ENGLISH)
    a = detect(doc, ENGLISH_JD, _block(ENGLISH_JD, "alternatives_together", ENGLISH_SOURCE, ["Scala", "Julia"], eid="reviewer-a"))
    b = detect(doc, ENGLISH_JD, _block(ENGLISH_JD, "alternatives_together", ENGLISH_SOURCE, ["Scala", "Julia"], eid="reviewer-b"))
    fa, fb = a["findings"][0], b["findings"][0]
    assert fa["code"] == fb["code"] and fa["item_ids"] == fb["item_ids"]
    assert fa["id"] != fb["id"]


def test_detection_is_deterministic_and_duplicate_free():
    doc = _draft(ENGLISH_JD, SPLIT_ENGLISH)
    block = _block(ENGLISH_JD, "alternatives_together", ENGLISH_SOURCE, ["Scala", "Julia"])
    first = detect(doc, ENGLISH_JD, block)
    assert first == detect(doc, ENGLISH_JD, block)
    ids = [f["id"] for f in first["findings"]]
    assert len(ids) == len(set(ids))


def test_the_parser_never_writes_expectations_or_structural_findings():
    raw = json.dumps({"categories": {"skills": SPLIT_ENGLISH}, "category_weights": {"skills": 100}, "scoring_confirmation": None},
                     ensure_ascii=False)
    doc = parse_response(raw, ENGLISH_JD).requirements
    assert "structural_review" not in doc and "structural_expectations" not in doc


def test_attach_does_not_mutate_the_draft():
    doc = _draft(ENGLISH_JD, SPLIT_ENGLISH)
    before = copy.deepcopy(doc)
    attach(doc, ENGLISH_JD, _block(ENGLISH_JD, "alternatives_together", ENGLISH_SOURCE, ["Scala", "Julia"]))
    assert doc == before
