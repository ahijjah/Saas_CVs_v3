"""Requirements-v2 server-owned structural-review contract (offline). Readiness and validation only: no detection logic, no parser
change, no prompt, no model call, no database. A structural finding is separate from classification warnings, from injection
review and from scoring confirmation.

Finding codes in this contract: alternatives_split_across_items, independent_items_merged, importance_conflict_unresolved.
Source-unit omissions are NOT part of this contract."""
from __future__ import annotations

import copy
import json
import pathlib

from services.requirements_v2 import contract as C
from services.requirements_v2.extraction import parse_response
from services.requirements_v2.readiness import compute_readiness
from services.requirements_v2.validation import validate_draft

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "requirements_v2_foundations"
B12 = json.loads((FIXTURES / "B12_ar_injection.json").read_text(encoding="utf-8"))["jd"]
CSS_HTML_REQUIRED = "معرفة بـ CSS وHTML"          # B12 line 6
CODES = ("alternatives_split_across_items", "independent_items_merged", "importance_conflict_unresolved")
RESOLUTION = {"decision": "accepted_as_is", "reviewer_user_id": "reviewer-1", "resolved_at": "2026-10-15T19:00:00+00:00",
              "note": None, "evidence_item_ids": None}


def _base_doc() -> dict:
    """A valid, weighted draft: two required items (CSS, HTML) quoting the same B12 line. Readiness is READY without a block."""
    raw = json.dumps({"categories": {"skills": [
        {"text": "CSS", "importance": "required", "source_text": CSS_HTML_REQUIRED},
        {"text": "HTML", "importance": "required", "source_text": CSS_HTML_REQUIRED},
    ]}, "category_weights": {"skills": 100}, "scoring_confirmation": None}, ensure_ascii=False)
    return parse_response(raw, B12).requirements


def _ids(doc: dict) -> list[str]:
    return [i["id"] for i in doc["categories"]["skills"]["items"]]


def _finding(doc: dict, *, fid: str = "f1", code: str = "independent_items_merged", item_ids=None, text: str = CSS_HTML_REQUIRED,
             start: int | None = None, end: int | None = None, status: str = "open", resolution=None, message: str = "CSS and HTML were returned as one requirement") -> dict:
    start = (B12.find(text) if B12.find(text) >= 0 else 0) if start is None else start
    end = start + len(text) if end is None else end
    return {"id": fid, "code": code, "item_ids": _ids(doc) if item_ids is None else item_ids, "source_text": text,
            "source_start": start, "source_end": end, "message": message, "status": status, "resolution": resolution}


def _with_block(doc: dict, findings: list[dict]) -> dict:
    out = copy.deepcopy(doc)
    out["structural_review"] = {"schema_version": 1, "findings": findings}
    return out


def _state(doc: dict):
    return compute_readiness(doc, require_classification_acknowledgment=True, jd_text=B12)


def _codes(readiness) -> list[str]:
    return [i.code for i in readiness.reasons]


# 1-2. open findings block readiness

def test_an_open_finding_produces_needs_structural_review():
    doc = _base_doc()
    res = _state(_with_block(doc, [_finding(doc)]))
    assert res.state == "needs_structural_review"
    assert _codes(res) == ["structural_review_open"]


def test_multiple_open_findings_still_produce_needs_structural_review_and_each_is_reported():
    doc = _base_doc()
    res = _state(_with_block(doc, [_finding(doc, fid="f1"), _finding(doc, fid="f2", code="alternatives_split_across_items")]))
    assert res.state == "needs_structural_review"
    assert _codes(res) == ["structural_review_open", "structural_review_open"]


# 3-4. resolved findings and legacy documents

def test_all_findings_resolved_removes_the_structural_blocker():
    doc = _base_doc()
    res = _state(_with_block(doc, [_finding(doc, status="resolved", resolution=dict(RESOLUTION))]))
    assert res.state == C.READY


def test_a_document_without_a_structural_review_block_keeps_legacy_behaviour():
    doc = _base_doc()
    assert "structural_review" not in doc
    assert _state(doc).state == C.READY


# 5-10. fail closed

def _invalid(doc: dict, finding: dict | None = None, *, block=None) -> None:
    out = copy.deepcopy(doc)
    out["structural_review"] = block if block is not None else {"schema_version": 1, "findings": [finding]}
    res = _state(out)
    assert res.state == "needs_structural_review"
    assert "structural_review_invalid" in _codes(res), _codes(res)


def test_a_malformed_block_fails_closed():
    doc = _base_doc()
    _invalid(doc, block={"schema_version": 2, "findings": []})
    _invalid(doc, block={"schema_version": 1, "findings": "not-a-list"})
    _invalid(doc, block={"schema_version": 1, "findings": [], "extra": True})


def test_an_unknown_finding_code_fails_closed():
    doc = _base_doc()
    _invalid(doc, _finding(doc, code="source_unit_omitted"))           # omissions are a separate design, not this contract


def test_invalid_or_duplicate_item_ids_fail_closed():
    doc = _base_doc()
    _invalid(doc, _finding(doc, item_ids=["no-such-item"]))
    a = _ids(doc)[0]
    _invalid(doc, _finding(doc, item_ids=[a, a]))
    _invalid(doc, _finding(doc, item_ids=[]))


def test_incorrect_source_offsets_or_text_fail_closed():
    doc = _base_doc()
    start = B12.index(CSS_HTML_REQUIRED)
    _invalid(doc, _finding(doc, start=start + 1, end=start + 1 + len(CSS_HTML_REQUIRED)))   # offsets shifted: text differs
    _invalid(doc, _finding(doc, start=10, end=5))                                            # start not before end
    _invalid(doc, _finding(doc, text="not in the description"))                              # text not a JD substring


# 9-10. resolution rules

def test_an_open_finding_with_a_resolution_fails_closed():
    doc = _base_doc()
    _invalid(doc, _finding(doc, status="open", resolution=dict(RESOLUTION)))


def test_a_resolved_finding_without_a_complete_resolution_fails_closed():
    doc = _base_doc()
    _invalid(doc, _finding(doc, status="resolved", resolution=None))
    _invalid(doc, _finding(doc, status="resolved", resolution={**RESOLUTION, "decision": "parser_fixed"}))
    _invalid(doc, _finding(doc, status="resolved", resolution={**RESOLUTION, "reviewer_user_id": ""}))
    _invalid(doc, _finding(doc, status="resolved", resolution={**RESOLUTION, "resolved_at": "yesterday"}))
    _invalid(doc, _finding(doc, status="resolved", resolution={**RESOLUTION, "resolved_at": "2026-10-15T19:00:00"}))  # no zone
    _invalid(doc, _finding(doc, status="resolved", resolution={**RESOLUTION, "evidence_item_ids": ["no-such-item"]}))


def test_a_finding_whose_status_is_unknown_fails_closed():
    doc = _base_doc()
    _invalid(doc, _finding(doc, status="closed"))


# 11. separate from classification, injection review and scoring confirmation

def test_structural_findings_are_not_classification_warnings_and_do_not_change_them():
    doc = _base_doc()
    doc = _with_block(doc, [_finding(doc)])
    res = _state(doc)
    assert res.open_warning_ids == () and res.unresolved_warning_ids == ()
    assert "classification_review" not in doc, "a structural finding never creates a classification block"


def test_a_classification_blocker_keeps_its_precedence_over_a_structural_finding():
    """A Preferred item with no cue yields a real classification warning from the parser; that blocker outranks the structure."""
    raw = json.dumps({"categories": {"skills": [{"text": "CSS", "importance": "preferred", "source_text": CSS_HTML_REQUIRED},
                                                {"text": "HTML", "importance": "required", "source_text": CSS_HTML_REQUIRED}]},
                      "category_weights": {"skills": 100}, "scoring_confirmation": None}, ensure_ascii=False)
    doc = parse_response(raw, B12).requirements
    assert _state(doc).state == "needs_classification_review"
    doc = _with_block(doc, [_finding(doc, item_ids=[_ids(doc)[1]])])
    res = _state(doc)
    assert res.state == "needs_classification_review"
    assert "structural_review_open" not in _codes(res)


def test_a_structural_block_is_not_a_scoring_confirmation():
    base = _base_doc()
    doc = _with_block(base, [_finding(base)])
    assert doc.get("scoring_confirmation") is None
    assert _state(doc).state == "needs_structural_review"


# draft and ownership rules

def test_a_draft_may_carry_only_open_findings():
    doc = _base_doc()
    draft = _with_block(doc, [_finding(doc, status="resolved", resolution=dict(RESOLUTION))])
    assert [e.code for e in validate_draft(draft).errors] == ["structural_resolution_not_allowed_in_draft"]
    assert validate_draft(_with_block(doc, [_finding(doc)])).errors == ()


def test_the_parser_never_writes_a_structural_review_block():
    raw = json.dumps({"categories": {"skills": [{"text": "CSS", "importance": "required", "source_text": CSS_HTML_REQUIRED}]},
                      "category_weights": {"skills": 100}, "scoring_confirmation": None}, ensure_ascii=False)
    assert "structural_review" not in parse_response(raw, B12).requirements


def test_no_production_module_imports_the_structural_review_contract():
    import re
    backend = pathlib.Path(__file__).resolve().parent.parent
    pattern = re.compile(r"structural_review\b|services\.requirements_v2\.structural_review")
    offenders = []
    for rel in ("routers", "workers", "auth", "main.py", "database.py", "config.py"):
        p = backend / rel
        files = [p] if p.is_file() else list(p.rglob("*.py")) if p.exists() else []
        offenders += [str(f) for f in files if pattern.search(f.read_text(encoding="utf-8", errors="ignore"))]
    assert offenders == []


def test_a_complete_resolution_with_evidence_items_is_accepted():
    doc = _base_doc()
    evidence = [_ids(doc)[0]]
    res = _state(_with_block(doc, [_finding(doc, status="resolved", resolution={**RESOLUTION, "decision": "fixed", "note": "split",
                                                                                 "evidence_item_ids": evidence})]))
    assert res.state == C.READY


def test_carry_keeps_the_stored_block_and_discards_a_client_block():
    from services.requirements_v2.structural_review import carry_structural_review
    doc = _base_doc()
    stored = _with_block(doc, [_finding(doc)])
    forged = _with_block(doc, [])                      # the client tries to clear the finding
    out = carry_structural_review(stored, forged)
    assert out["structural_review"] == stored["structural_review"]
    assert _state(out).state == "needs_structural_review"
    assert "structural_review" not in carry_structural_review(None, forged), "no stored block: none is created from the client"
