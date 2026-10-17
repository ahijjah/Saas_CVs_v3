"""carry_server_owned must keep the TRUSTED stored structural_review block on every save, must never let a client-supplied block
replace it, and must leave every other server-owned block exactly as before. Offline; no database, route, worker or model."""
from __future__ import annotations

import copy
import json
import pathlib

from services.requirements_v2 import contract as C
from services.requirements_v2.acknowledgment import carry_classification_review
from services.requirements_v2.extraction import parse_response
from services.requirements_v2.readiness import carry_confirmation, carry_server_owned, compute_readiness
from services.requirements_v2.structural_detection import attach
from services.requirements_v2.structure import carry_structure_review
from services.requirements_v2.validation import validate_draft

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
B12 = json.loads((FIXTURES / "requirements_v2_foundations" / "B12_ar_injection.json").read_text(encoding="utf-8"))["jd"]
B12_EXPECTATIONS = json.loads((FIXTURES / "requirements_v2_structural_expectations" / "B12_ar_injection.expectations.json")
                              .read_text(encoding="utf-8"))
CSS_HTML = "معرفة بـ CSS وHTML"
RESOLUTION = {"decision": "accepted_as_is", "reviewer_user_id": "reviewer-1", "resolved_at": "2026-10-17T12:00:00+00:00",
              "note": "Reviewed: the employer lists both skills separately.", "evidence_item_ids": None}


def _draft(items: list[dict], *, preferred_only: bool = False) -> dict:
    raw = json.dumps({"categories": {"skills": items}, "category_weights": {"skills": 100}, "scoring_confirmation": None},
                     ensure_ascii=False)
    res = parse_response(raw, B12)
    assert res.status == "draft", res.errors
    return res.requirements


def _merged_draft() -> dict:
    return _draft([{"text": "CSS and HTML", "importance": "required", "source_text": CSS_HTML}])


def _stored_with_open_finding() -> dict:
    return attach(_merged_draft(), B12, B12_EXPECTATIONS)


def _stored_with_resolution() -> dict:
    stored = copy.deepcopy(_stored_with_open_finding())
    finding = stored["structural_review"]["findings"][0]
    finding["status"] = "resolved"
    finding["resolution"] = dict(RESOLUTION)
    return stored


def _ordinary_save_of(stored: dict) -> dict:
    """What the client sends for an ordinary save: the same items, with no structural block."""
    incoming = copy.deepcopy(stored)
    incoming.pop("structural_review", None)
    return incoming


def _forged_block() -> dict:
    return {"schema_version": 1, "findings": []}              # the client tries to clear the finding


def test_stored_structural_review_survives_an_ordinary_save():
    stored = _stored_with_open_finding()
    out = carry_server_owned(stored, _ordinary_save_of(stored))
    assert out["structural_review"] == stored["structural_review"]


def test_a_client_supplied_structural_review_cannot_overwrite_the_stored_block():
    stored = _stored_with_open_finding()
    incoming = _ordinary_save_of(stored)
    incoming["structural_review"] = _forged_block()
    out = carry_server_owned(stored, incoming)
    assert out["structural_review"] == stored["structural_review"]
    assert compute_readiness(out, jd_text=B12).state == C.NEEDS_STRUCTURAL_REVIEW


def test_a_valid_reviewer_resolution_is_preserved_by_a_save():
    stored = _stored_with_resolution()
    out = carry_server_owned(stored, _ordinary_save_of(stored))
    assert out["structural_review"] == stored["structural_review"]
    assert out["structural_review"]["findings"][0]["resolution"] == RESOLUTION
    assert compute_readiness(out, jd_text=B12).state == C.READY


def test_a_client_block_is_discarded_when_nothing_is_stored():
    incoming = _ordinary_save_of(_stored_with_open_finding())
    incoming["structural_review"] = _forged_block()
    out = carry_server_owned(None, incoming)
    assert "structural_review" not in out


def test_drafts_still_cannot_supply_a_structural_review_block():
    draft = _merged_draft()
    draft["structural_review"] = _forged_block()
    assert [e.code for e in validate_draft(draft).errors] == ["structural_review_not_allowed_in_draft"]


def test_all_other_server_owned_blocks_are_carried_exactly_as_before():
    """The composition used before this change, applied to every key except the structural block, must give the same result."""
    stored = _stored_with_open_finding()
    stored = copy.deepcopy(stored)
    # a stored classification warning, so the classification block is non-trivial
    stored_draft = _draft([{"text": "CSS", "importance": "preferred", "source_text": CSS_HTML},
                           {"text": "HTML", "importance": "required", "source_text": CSS_HTML}])
    stored_draft["structural_review"] = stored["structural_review"]
    for source in (stored, stored_draft):
        incoming = _ordinary_save_of(source)
        incoming["scoring_confirmation"] = {"forged": True}
        out = carry_server_owned(source, incoming)
        before = carry_structure_review(source, carry_classification_review(source, carry_confirmation(source, incoming)))
        assert {k: v for k, v in out.items() if k != "structural_review"} == \
               {k: v for k, v in before.items() if k != "structural_review"}
