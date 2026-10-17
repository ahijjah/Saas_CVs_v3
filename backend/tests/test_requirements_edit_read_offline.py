"""Read-only edit view: the exact whitelist, no stored free-form text, fixed messages, confirmation states, review-block handling,
and no mutation of inputs. Pure: no database, route or model."""
from __future__ import annotations

import copy
import dataclasses
import json

from services import requirements_edit as E
from services.requirements_v2 import contract as C
from services.requirements_v2.acknowledgment import (
    acknowledge_classification_warning, build_warning, empty_review,
)
from services.requirements_v2.readiness import Readiness, confirm_no_numeric_score
from services.requirements_v2.contract import Issue
from services.requirements_v2.structural_review import CODE_ALTERNATIVES_SPLIT, READINESS_INVALID

JD = "We need Python and SQL. Five years of backend work is preferred."
ORIGINAL_JD_TEXT = "Python and SQL"
STAMP = "2026-10-17T12:00:00+00:00"
CANARY = "CANARY"
ITEM_KEYS = set(E.ITEM_FIELDS) | {"experience"}


def _item(item_id, text, importance, weight=None, **kw):
    return C.make_item(text, importance, item_id=item_id, weight=weight, origin=kw.pop("origin", C.ORIGIN_STATED),
                       source_text=kw.pop("source_text", text), **kw)


def _doc() -> dict:
    doc = C.empty_requirements()
    doc["categories"]["skills"] = {"weight": 100, "items": [
        _item("req_py", "Python", C.IMPORTANCE_REQUIRED, 60, alternatives=["Python", "Python 3"]),
        _item("req_sql", "SQL", C.IMPORTANCE_REQUIRED, 40),
    ]}
    doc["categories"]["experience"] = {"weight": 0, "items": [
        _item("req_exp", "Backend work", C.IMPORTANCE_PREFERRED, source_text="Five years of backend work",
              experience={"subject": "backend work", "min_years": 5}),
    ]}
    return doc


def _preferred_only() -> dict:
    doc = C.empty_requirements()
    doc["categories"]["skills"] = {"weight": 0, "items": [_item("req_sql", "SQL", C.IMPORTANCE_PREFERRED)]}
    return doc


def _finding(item_ids, *, status="open", resolution=None, text=ORIGINAL_JD_TEXT, fid="sf_1", message="Check together."):
    start = JD.index(text)
    return {"id": fid, "code": CODE_ALTERNATIVES_SPLIT, "item_ids": list(item_ids), "source_text": text,
            "source_start": start, "source_end": start + len(text), "message": message, "status": status,
            "resolution": resolution}


def _block(*findings) -> dict:
    return {"schema_version": 1, "findings": list(findings)}


def _view(doc, *, original="same", jd=JD):
    base = copy.deepcopy(doc) if original == "same" else original
    return E.build_edit_view(doc, job_id="job-1", revision=3, schema_version=2, original=base, jd_text=jd)


def _serialized(view) -> str:
    return json.dumps(dataclasses.asdict(view), ensure_ascii=False, default=list)


# ---- the exact whitelist ----

def test_the_document_is_exactly_the_whitelisted_fields():
    view = _view(_doc())
    assert set(view.document) == {"schema_version", "categories"}
    assert set(view.document["categories"]) == set(C.CATEGORIES)
    for category in view.document["categories"].values():
        assert set(category) == {"weight", "items"}
        for item in category["items"]:
            assert set(item) == ITEM_KEYS
    experience = view.document["categories"]["experience"]["items"][0]["experience"]
    assert set(experience) == {"subject", "min_years"}


def test_item_source_text_and_alternatives_remain_visible():
    items = {i["id"]: i for c in C.CATEGORIES for i in _view(_doc()).document["categories"][c]["items"]}
    assert items["req_py"]["source_text"] == "Python"
    assert items["req_py"]["alternatives"] == ["Python", "Python 3"]


def test_the_top_level_view_has_only_the_approved_sections():
    view = _view(_doc())
    assert {f.name for f in dataclasses.fields(view)} == {
        "job_id", "requirements_revision", "schema_version", "document", "readiness", "confirmation",
        "classification", "structure", "structural"}
    assert {f.name for f in dataclasses.fields(view.readiness)} == {"state", "can_proceed", "scoring_mode", "reasons"}
    assert {f.name for f in dataclasses.fields(view.confirmation)} == {"status", "confirmed_at"}
    assert {f.name for f in dataclasses.fields(view.classification)} == {"acknowledgment_required", "open_warnings"}
    assert {f.name for f in dataclasses.fields(view.structure)} == {"pending_item_ids"}
    assert {f.name for f in dataclasses.fields(view.structural)} == {"status", "open_findings"}


# ---- nothing free-form, secret or server-internal leaks ----

def test_stored_free_form_text_identities_and_hashes_never_reach_the_view():
    doc = _doc()
    doc["categories"]["skills"]["items"].append(_item("req_docker", "Docker", C.IMPORTANCE_PREFERRED))
    doc["categories"]["skills"]["items"][-1]["id"] = "req_docker"
    review = empty_review()
    review["warnings"].append(build_warning(code="preferred_cue_missing", item_id="req_docker", category="skills",
                                            cue=None, source_text=f"{CANARY}-EVIDENCE", message=f"{CANARY}-WARNING-MESSAGE"))
    doc["classification_review"] = review
    doc["classification_review"] = acknowledge_classification_warning(
        doc, "req_docker:preferred_cue_missing", user_id=f"{CANARY}-ACKNOWLEDGER", acknowledged_at=STAMP)["classification_review"]
    doc["structural_review"] = _block(
        _finding(["req_py", "req_sql"], message=f"{CANARY}-FINDING-MESSAGE"),
        _finding(["req_py"], status="resolved", text="Five years of backend work", fid="sf_2",
                 resolution={"decision": "accepted_as_is", "reviewer_user_id": f"{CANARY}-REVIEWER",
                             "resolved_at": STAMP, "note": f"{CANARY}-RESOLUTION-NOTE", "evidence_item_ids": None}))
    doc["scoring_confirmation"] = None
    doc["requirements_pipeline"] = {"prompt": f"{CANARY}-PIPELINE"}
    text = _serialized(_view(doc, jd=JD + " " + CANARY.lower()))
    for leak in (f"{CANARY}-WARNING-MESSAGE", f"{CANARY}-EVIDENCE", f"{CANARY}-ACKNOWLEDGER", f"{CANARY}-FINDING-MESSAGE",
                 f"{CANARY}-REVIEWER", f"{CANARY}-RESOLUTION-NOTE", f"{CANARY}-PIPELINE"):
        assert leak not in text, leak
    for forbidden in ("basis_hash", "item_state_hash", "evidence_hash", "user_id", "reviewer_user_id", "requirements_retired_item_ids",
                      "requirements_pipeline", "classification_review", "structure_review", "structural_review", "kind"):
        assert f'"{forbidden}"' not in text, forbidden        # a JSON key, not a substring of a reason code


def test_the_stored_finding_message_is_replaced_by_the_fixed_message():
    doc = _doc()
    doc["structural_review"] = _block(_finding(["req_py", "req_sql"], message="stored text that is never returned"))
    open_finding = _view(doc).structural.open_findings[0]
    assert open_finding.message == E.STRUCTURAL_MESSAGES[CODE_ALTERNATIVES_SPLIT]
    assert "stored text" not in _serialized(_view(doc))


def test_an_unknown_readiness_code_gets_the_neutral_message_and_no_stored_text(monkeypatch):
    fake = Readiness("needs_review", None, (Issue("made_up_code", f"{CANARY}-STORED", category="skills", item_id="req_py"),))
    monkeypatch.setattr(E, "compute_readiness", lambda *a, **k: fake)
    reason = _view(_doc()).readiness.reasons[0]
    assert reason.code == "made_up_code"
    assert reason.message == E.NEUTRAL_MESSAGE
    assert (reason.category, reason.item_id) == ("skills", "req_py")
    assert CANARY not in _serialized(_view(_doc()))


def test_every_fixed_message_is_returned_for_its_own_code_only():
    for code, message in E.READINESS_MESSAGES.items():
        assert E.readiness_message(code) == message
    assert E.readiness_message("not_a_code") == E.NEUTRAL_MESSAGE


# ---- confirmation states ----

def test_an_empty_document_is_not_required():
    assert _view(C.empty_requirements()).confirmation == E.ConfirmationView(E.CONFIRMATION_NOT_REQUIRED, None)


def test_a_document_with_required_items_is_not_required():
    assert _view(_doc()).confirmation.status == E.CONFIRMATION_NOT_REQUIRED


def test_preferred_only_without_a_confirmation_is_required():
    assert _view(_preferred_only()).confirmation == E.ConfirmationView(E.CONFIRMATION_REQUIRED, None)


def test_preferred_only_with_a_current_confirmation_is_confirmed():
    doc = _preferred_only()
    confirmed = confirm_no_numeric_score(doc, user_id=f"{CANARY}-USER", confirmed_at=STAMP, original=copy.deepcopy(doc), jd_text=JD)
    view = _view(confirmed)
    assert view.confirmation == E.ConfirmationView(E.CONFIRMATION_CONFIRMED, STAMP)
    assert CANARY not in _serialized(view)


def test_a_confirmation_that_no_longer_matches_is_stale():
    doc = _preferred_only()
    confirmed = confirm_no_numeric_score(doc, user_id="u", confirmed_at=STAMP, original=copy.deepcopy(doc), jd_text=JD)
    changed = copy.deepcopy(confirmed)
    changed["categories"]["skills"]["items"][0]["text"] = "SQL and PostgreSQL"
    assert _view(changed).confirmation == E.ConfirmationView(E.CONFIRMATION_STALE, None)


def test_a_stored_confirmation_on_a_document_that_is_not_preferred_only_is_not_required():
    doc = _preferred_only()
    confirmed = confirm_no_numeric_score(doc, user_id="u", confirmed_at=STAMP, original=copy.deepcopy(doc), jd_text=JD)
    confirmed["categories"]["skills"]["items"].append(_item("req_py", "Python", C.IMPORTANCE_REQUIRED, 100))
    confirmed["categories"]["skills"]["weight"] = 100
    assert _view(confirmed).confirmation.status == E.CONFIRMATION_NOT_REQUIRED


# ---- structural review ----

def test_no_structural_block_gives_status_none():
    assert _view(_doc()).structural == E.StructuralView(E.STRUCTURAL_NONE, ())


def test_a_valid_open_finding_is_returned_with_its_source_text_and_a_fixed_message():
    doc = _doc()
    doc["structural_review"] = _block(_finding(["req_py", "req_sql"]))
    view = _view(doc)
    assert view.structural.status == E.STRUCTURAL_VALID
    (finding,) = view.structural.open_findings
    assert (finding.id, finding.code, finding.item_ids) == ("sf_1", CODE_ALTERNATIVES_SPLIT, ("req_py", "req_sql"))
    assert finding.source_text == ORIGINAL_JD_TEXT
    assert finding.message == E.STRUCTURAL_MESSAGES[CODE_ALTERNATIVES_SPLIT]


def test_resolved_findings_and_their_notes_are_not_returned():
    doc = _doc()
    doc["structural_review"] = _block(
        _finding(["req_py", "req_sql"]),
        _finding(["req_py"], status="resolved", text="Five years of backend work", fid="sf_2",
                 resolution={"decision": "accepted_as_is", "reviewer_user_id": "r", "resolved_at": STAMP,
                             "note": "a reviewer note", "evidence_item_ids": None}))
    view = _view(doc)
    assert [f.id for f in view.structural.open_findings] == ["sf_1"]
    assert "a reviewer note" not in _serialized(view)


def test_an_invalid_structural_block_is_reported_invalid_and_hides_its_findings():
    doc = _doc()
    bad = _finding(["req_py", "req_sql"])
    bad["source_end"] += 2
    doc["structural_review"] = _block(bad)
    view = _view(doc)
    assert view.structural == E.StructuralView(E.STRUCTURAL_INVALID, ())
    assert view.readiness.state == C.NEEDS_STRUCTURAL_REVIEW
    assert [r.code for r in view.readiness.reasons] == [READINESS_INVALID]


def test_a_structural_block_checked_against_a_different_description_is_invalid():
    doc = _doc()
    doc["structural_review"] = _block(_finding(["req_py", "req_sql"]))
    assert _view(doc, jd="An unrelated description.").structural.status == E.STRUCTURAL_INVALID


def test_a_finding_naming_a_removed_item_is_invalid():
    doc = _doc()
    doc["structural_review"] = _block(_finding(["req_gone"]))
    assert _view(doc).structural.status == E.STRUCTURAL_INVALID


# ---- structure review and classification ----

def test_an_item_whose_structure_changed_from_the_original_is_pending():
    doc = _doc()
    original = copy.deepcopy(doc)
    doc["categories"]["skills"]["items"][0]["alternatives"] = ["Python", "CPython"]
    view = _view(doc, original=original)
    assert view.structure.pending_item_ids == ("req_py",)
    assert view.readiness.state == C.NEEDS_STRUCTURE_REVIEW


def test_a_missing_baseline_leaves_every_structured_item_pending():
    """An experience object counts as structure, so req_exp is pending too."""
    assert _view(_doc(), original=None).structure.pending_item_ids == ("req_py", "req_exp")


def test_an_open_classification_warning_shows_its_safe_references_only():
    doc = _preferred_only()
    doc["classification_review"] = empty_review()
    doc["classification_review"]["warnings"].append(build_warning(
        code="preferred_cue_missing", item_id="req_sql", category="skills", cue=None,
        source_text=f"{CANARY}-EVIDENCE", message=f"{CANARY}-MESSAGE"))
    view = _view(doc)
    (warning,) = view.classification.open_warnings
    assert warning == E.WarningView("req_sql:preferred_cue_missing", "preferred_cue_missing", "req_sql", "skills", False)
    assert view.classification.acknowledgment_required is True
    assert view.readiness.reasons[0].message == E.READINESS_MESSAGES["classification_warning_unresolved"]
    assert CANARY not in _serialized(view)


def test_an_acknowledged_warning_is_marked_acknowledged_without_the_acknowledger():
    doc = _preferred_only()
    doc["classification_review"] = empty_review()
    doc["classification_review"]["warnings"].append(build_warning(
        code="preferred_cue_missing", item_id="req_sql", category="skills", cue=None, source_text="x", message="m"))
    doc = acknowledge_classification_warning(doc, "req_sql:preferred_cue_missing", user_id=f"{CANARY}-USER", acknowledged_at=STAMP)
    view = _view(doc)
    assert view.classification.open_warnings[0].acknowledged is True
    assert CANARY not in _serialized(view)


def test_a_warning_on_a_required_item_is_not_applicable():
    doc = _doc()
    doc["classification_review"] = empty_review()
    doc["classification_review"]["warnings"].append(build_warning(
        code="preferred_cue_missing", item_id="req_sql", category="skills", cue=None, source_text="x", message="m"))
    assert _view(doc).classification.open_warnings == ()


# ---- readiness reasons keep safe references; inputs are never mutated ----

def test_readiness_reasons_keep_their_category_and_item_references():
    doc = _doc()
    doc["categories"]["skills"]["items"][0]["weight"] = 70
    reasons = {r.code: r for r in _view(doc, original=None).readiness.reasons}
    assert reasons["required_weights_total"].category == "skills"
    assert reasons["required_weights_total"].message == E.READINESS_MESSAGES["required_weights_total"]


def test_building_the_view_never_mutates_its_input():
    doc = _doc()
    doc["classification_review"] = empty_review()
    doc["structural_review"] = _block(_finding(["req_py", "req_sql"]))
    before = copy.deepcopy(doc)
    original = copy.deepcopy(doc)
    E.build_edit_view(doc, job_id="j", revision=1, schema_version=2, original=original, jd_text=JD)
    assert doc == before
    assert original == before
