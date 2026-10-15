"""Requirements-v2 structural detection (offline, pure). The detector looks only at the reviewed source units in its server-owned
table, and only at the two structural codes it implements. No model call, no parser change, no database, no network.
Source-unit omissions, conflicts and connected competencies are not detected here."""
from __future__ import annotations

import json
import pathlib

from services.requirements_v2 import contract as C
from services.requirements_v2.extraction import parse_response
from services.requirements_v2.readiness import compute_readiness
from services.requirements_v2.structural_detection import attach, detect
from services.requirements_v2.structural_review import structural_status

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "requirements_v2_foundations"
B02 = json.loads((FIXTURES / "B02_en_data_analyst.json").read_text(encoding="utf-8"))["jd"]
B12 = json.loads((FIXTURES / "B12_ar_injection.json").read_text(encoding="utf-8"))["jd"]

PYTHON_OR_R = "Proficiency in Python or R"               # B02 line 6: alternatives unit
CSS_HTML = "معرفة بـ CSS وHTML"                            # B12 line 6: independent-items unit


def _draft(jd: str, items: list[dict]):
    raw = json.dumps({"categories": {"skills": items}, "category_weights": {"skills": 100}, "scoring_confirmation": None},
                     ensure_ascii=False)
    res = parse_response(raw, jd)
    assert res.status == "draft", res.errors
    return res.requirements


def _ids(doc) -> list[str]:
    return [i["id"] for i in doc["categories"]["skills"]["items"]]


def _assert_no_block(doc):
    assert "structural_review" not in doc


# ---- B02: Python or R ----

def test_correct_python_or_r_item_with_both_alternatives_has_no_finding():
    doc = _draft(B02, [{"text": "Python or R proficiency", "importance": "required", "source_text": PYTHON_OR_R,
                        "alternatives": ["Python", "R"]}])
    assert detect(doc, B02)["findings"] == []


def test_python_and_r_split_into_two_items_yield_one_open_alternatives_finding():
    doc = _draft(B02, [{"text": "Python", "importance": "required", "source_text": PYTHON_OR_R},
                       {"text": "R", "importance": "required", "source_text": PYTHON_OR_R}])
    findings = detect(doc, B02)["findings"]
    assert len(findings) == 1
    f = findings[0]
    assert f["code"] == "alternatives_split_across_items" and f["status"] == "open" and f["resolution"] is None
    assert f["item_ids"] == _ids(doc)                       # both affected items, in document order
    assert f["source_start"] == B02.index(PYTHON_OR_R) and f["source_end"] == f["source_start"] + len(PYTHON_OR_R)
    assert B02[f["source_start"]:f["source_end"]] == f["source_text"] == PYTHON_OR_R


def test_an_incomplete_alternative_is_not_misclassified_as_split():
    doc = _draft(B02, [{"text": "Python", "importance": "required", "source_text": PYTHON_OR_R}])
    assert detect(doc, B02)["findings"] == []


def test_a_missing_alternative_item_is_not_misclassified_as_split():
    doc = _draft(B02, [{"text": "SQL", "importance": "required", "source_text": "SQL and Power BI."}])
    assert detect(doc, B02)["findings"] == []


# ---- B12: CSS and HTML ----

def test_correct_css_and_html_as_two_items_has_no_finding():
    doc = _draft(B12, [{"text": "CSS", "importance": "required", "source_text": CSS_HTML},
                       {"text": "HTML", "importance": "required", "source_text": CSS_HTML}])
    assert detect(doc, B12)["findings"] == []


def test_css_and_html_merged_into_one_item_yield_one_open_merged_finding():
    doc = _draft(B12, [{"text": "CSS and HTML", "importance": "required", "source_text": CSS_HTML}])
    findings = detect(doc, B12)["findings"]
    assert len(findings) == 1
    f = findings[0]
    assert f["code"] == "independent_items_merged" and f["status"] == "open" and f["resolution"] is None
    assert f["item_ids"] == [_ids(doc)[0]]                  # the merged item
    assert f["source_start"] == B12.index(CSS_HTML) and f["source_end"] == f["source_start"] + len(CSS_HTML)
    assert B12[f["source_start"]:f["source_end"]] == CSS_HTML


def test_a_single_css_item_without_html_is_not_reported_as_merged():
    doc = _draft(B12, [{"text": "CSS", "importance": "required", "source_text": CSS_HTML}])
    assert detect(doc, B12)["findings"] == []


# ---- scope: no finding for connected competencies ----

def test_a_connected_and_competency_is_not_structural_evidence():
    jd = "Strong communication, coordination and teamwork skills are expected."
    doc = _draft(jd, [{"text": "Communication, coordination and teamwork", "importance": "required",
                       "source_text": "communication, coordination and teamwork skills"}])
    assert detect(doc, jd)["findings"] == []


# ---- contract, determinism and ownership ----

def test_findings_pass_the_merged_contract_and_cause_needs_structural_review():
    doc = _draft(B12, [{"text": "CSS and HTML", "importance": "required", "source_text": CSS_HTML}])
    server = attach(doc, B12)
    status = structural_status(server, B12)
    assert [i for i in status.issues if i.code == "structural_review_invalid"] == []
    assert len(status.open_ids) == 1
    assert compute_readiness(server, jd_text=B12).state == C.NEEDS_STRUCTURAL_REVIEW


def test_detection_is_deterministic_and_duplicate_free():
    doc = _draft(B12, [{"text": "CSS and HTML", "importance": "required", "source_text": CSS_HTML}])
    first = detect(doc, B12)
    assert first == detect(doc, B12)
    ids = [f["id"] for f in first["findings"]]
    assert len(ids) == len(set(ids))


def test_the_parser_output_never_contains_a_structural_review_block():
    raw = json.dumps({"categories": {"skills": [{"text": "CSS and HTML", "importance": "required", "source_text": CSS_HTML},
                                                {"text": "Python", "importance": "required", "source_text": PYTHON_OR_R},
                                                {"text": "R", "importance": "required", "source_text": PYTHON_OR_R}]},
                      "category_weights": {"skills": 100}, "scoring_confirmation": None}, ensure_ascii=False)
    _assert_no_block(parse_response(raw, B12).requirements)


def test_detection_runs_only_on_a_valid_draft():
    doc = _draft(B12, [{"text": "CSS and HTML", "importance": "required", "source_text": CSS_HTML}])
    doc_with_block = attach(doc, B12)
    try:
        detect(doc_with_block, B12)
    except ValueError:
        pass
    else:
        raise AssertionError("a document that already carries a structural block is not a draft")


def test_attach_adds_nothing_when_there_are_no_findings():
    doc = _draft(B12, [{"text": "CSS", "importance": "required", "source_text": CSS_HTML},
                       {"text": "HTML", "importance": "required", "source_text": CSS_HTML}])
    _assert_no_block(attach(doc, B12))


def test_the_split_finding_also_passes_the_contract_and_causes_needs_structural_review():
    doc = _draft(B02, [{"text": "Python", "importance": "required", "source_text": PYTHON_OR_R},
                       {"text": "R", "importance": "required", "source_text": PYTHON_OR_R}])
    server = attach(doc, B02)
    assert [i for i in structural_status(server, B02).issues if i.code == "structural_review_invalid"] == []
    assert compute_readiness(server, jd_text=B02).state == C.NEEDS_STRUCTURAL_REVIEW
