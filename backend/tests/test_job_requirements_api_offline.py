"""Authenticated requirements-v2 API, offline: strict request models, the error-to-HTTP mapping, the exact read response built from
EditView, and the wiring in main.py. No database, route or model."""
from __future__ import annotations

import copy
import pathlib

import pytest
from pydantic import ValidationError

from routers import job_requirements as R
from services import requirements_edit as E
from services.requirements_v2 import contract as C
from services.requirements_v2.contract import Issue

JD = "We need Python and SQL. Five years of backend work is preferred."
BACKEND = pathlib.Path(__file__).resolve().parent.parent
CANARY = "CANARY"


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


VALID_BODY = {"client_revision": 3, "operations": [{"op": "set_text", "item_id": "req_sql", "text": "SQL"}]}


# ---- strict request model ----

def test_a_valid_request_parses():
    body = R.SaveEditsRequest.model_validate(VALID_BODY)
    assert body.client_revision == 3 and len(body.operations) == 1


@pytest.mark.parametrize("field", ["document", "review", "readiness", "scoring_confirmation", "structural_review",
                                   "requirements_retired_item_ids", "classification_review", "structure_review"])
def test_a_read_only_or_server_owned_top_level_field_is_refused(field):
    with pytest.raises(ValidationError):
        R.SaveEditsRequest.model_validate({**VALID_BODY, field: {}})


@pytest.mark.parametrize("revision", [True, False, 3.0, "3", None, -1])
def test_client_revision_must_be_a_strict_non_negative_integer(revision):
    with pytest.raises(ValidationError):
        R.SaveEditsRequest.model_validate({**VALID_BODY, "client_revision": revision})


def test_zero_is_a_valid_revision():
    assert R.SaveEditsRequest.model_validate({**VALID_BODY, "client_revision": 0}).client_revision == 0


@pytest.mark.parametrize("operations", [[], [{"op": "set_text"}] * 101, "set_text", None, ["set_text"]])
def test_operations_must_be_a_list_of_one_to_one_hundred_objects(operations):
    with pytest.raises(ValidationError):
        R.SaveEditsRequest.model_validate({**VALID_BODY, "operations": operations})


def test_one_hundred_operations_is_the_maximum():
    body = {**VALID_BODY, "operations": [{"op": "set_text", "item_id": "req_sql", "text": "SQL"}] * 100}
    assert len(R.SaveEditsRequest.model_validate(body).operations) == 100


@pytest.mark.parametrize("raw", [
    {"op": "rename_item", "item_id": "req_sql"},                              # unknown operation
    {"op": "set_text", "item_id": "req_sql", "text": "x", "origin": "stated"},  # protected field
    {"op": "set_text", "item_id": "req_sql", "text": "x", "structural_review": {}},
    {"op": "remove_item", "item_id": "req_sql", "reason": "duplicate"},
])
def test_operations_are_parsed_by_the_service_and_refuse_unknown_and_protected_fields(raw):
    with pytest.raises(E.InvalidOperation):
        E.parse_operations([raw])


# ---- error mapping: every service error to one stable code ----

@pytest.mark.parametrize("exc, http_status, code", [
    (E.PermissionDenied("x"), 403, "permission_denied"),
    (E.JobNotFound("x"), 404, "job_not_found"),
    (E.NotRequirementsV2("x"), 409, "not_requirements_v2"),
    (E.NoRequirementsDocument("x"), 409, "no_requirements_document"),
    (E.StoredDocumentInvalid("x"), 500, "stored_document_invalid"),
    (E.AuditWriteError("x"), 500, "audit_failed"),
    (E.RequirementsEditError("x", code="write_failed"), 500, "write_failed"),
    (E.RequirementsEditError("x"), 500, "write_failed"),
])
def test_each_service_error_maps_to_its_status_and_code(exc, http_status, code):
    mapped = R._service_error(exc)
    assert mapped.status_code == http_status
    assert mapped.detail["code"] == code and isinstance(mapped.detail["message"], str)


def test_a_revision_conflict_carries_the_current_revision():
    mapped = R._service_error(E.RevisionConflict("x", current_revision=7))
    assert (mapped.status_code, mapped.detail["code"], mapped.detail["current_revision"]) == (409, "revision_conflict", 7)


def test_invalid_requirements_return_sanitized_issues_only():
    issue = Issue("required_weights_total", f"{CANARY}-STORED-TEXT", category="skills", item_id="req_py")
    mapped = R._service_error(E.InvalidRequirements("x", issues=(issue,)))
    assert mapped.status_code == 422 and mapped.detail["code"] == "invalid_requirements"
    assert mapped.detail["issues"] == [{"code": "required_weights_total", "category": "skills", "item_id": "req_py"}]
    assert CANARY not in repr(mapped.detail)


@pytest.mark.parametrize("code", ["invalid_operation", "unknown_item_id", "invalid_revision"])
def test_invalid_operation_keeps_its_specific_code(code):
    mapped = R._service_error(E.InvalidOperation("Operation 0: refused.", code=code))
    assert (mapped.status_code, mapped.detail["code"]) == (422, code)


def test_a_stored_document_error_does_not_return_internal_issues():
    mapped = R._service_error(E.StoredDocumentInvalid("x", issues=(Issue("bad_categories", "internal detail"),)))
    assert "issues" not in mapped.detail and "internal detail" not in repr(mapped.detail)


# ---- the read response is built from the approved whitelist ----

def _view(doc, original=None):
    return E.build_edit_view(doc, job_id="job-1", revision=3, schema_version=2, original=original, jd_text=JD)


def test_the_read_response_has_exactly_the_approved_top_level_keys_and_review_wrapper():
    response = R._edit_response(_view(_doc(), original=copy.deepcopy(_doc())))
    dumped = response.model_dump()
    assert set(dumped) == {"job_id", "requirements_revision", "schema_version", "document", "review"}
    assert set(dumped["review"]) == {"readiness", "confirmation", "classification", "structure", "structural"}
    assert set(dumped["review"]["readiness"]) == {"state", "can_proceed", "scoring_mode", "reasons"}
    assert set(dumped["review"]["confirmation"]) == {"status", "confirmed_at"}
    assert set(dumped["review"]["classification"]) == {"acknowledgment_required", "open_warnings"}
    assert set(dumped["review"]["structure"]) == {"pending_item_ids"}
    assert set(dumped["review"]["structural"]) == {"status", "open_findings"}


def test_the_document_is_exactly_the_whitelisted_shape():
    dumped = R._edit_response(_view(_doc())).model_dump()
    assert set(dumped["document"]) == {"schema_version", "categories"}
    for category in dumped["document"]["categories"].values():
        assert set(category) == {"weight", "items"}
        for item in category["items"]:
            assert set(item) == {"id", "text", "importance", "weight", "origin", "source_text", "alternatives", "experience", "gate", "gate_suggestion"}


def test_no_hidden_or_server_owned_content_appears_in_the_response():
    doc = _doc()
    doc["requirements_pipeline"] = {"prompt": f"{CANARY}-PIPELINE"}
    doc["scoring_confirmation"] = {"kind": "no_numeric_score", "user_id": f"{CANARY}-USER",
                                   "confirmed_at": "2026-10-17T12:00:00+00:00", "basis_hash": "abc"}
    text = R._edit_response(_view(doc)).model_dump_json()
    assert CANARY not in text
    for hidden in ("requirements_retired_item_ids", "requirements_pipeline", "scoring_confirmation", "structure_review",
                   "classification_review", "structural_review", "user_id", "basis_hash", "evidence", "reviewer_user_id"):
        assert f'"{hidden}"' not in text, hidden


def test_the_response_model_refuses_an_extra_field():
    dumped = R._edit_response(_view(_doc())).model_dump()
    dumped["retired_item_ids"] = ["req_gone"]
    with pytest.raises(ValidationError):
        R.EditResponse.model_validate(dumped)


def test_the_save_response_has_only_the_new_revision():
    assert R.SaveResponse(requirements_revision=4).model_dump() == {"requirements_revision": 4}


def test_a_document_model_refuses_an_unknown_category():
    dumped = R._edit_response(_view(_doc())).model_dump()
    dumped["document"]["categories"]["hobbies"] = {"weight": 0, "items": []}
    with pytest.raises(ValidationError):
        R.DocumentOut.model_validate(dumped["document"])


# ---- wiring ----

def test_the_router_is_registered_in_main_and_no_other_module_imports_it():
    main = (BACKEND / "main.py").read_text(encoding="utf-8")
    assert main.count("app.include_router(job_requirements.router)") == 1
    assert "job_requirements" in main.split("from routers import (", 1)[1].split(")", 1)[0]
    offenders = []
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if rel.startswith("tests/") or rel in {"routers/job_requirements.py", "main.py"} or "__pycache__" in rel:
            continue
        if "job_requirements" in path.read_text(encoding="utf-8", errors="ignore"):
            offenders.append(rel)
    assert offenders == [], offenders
