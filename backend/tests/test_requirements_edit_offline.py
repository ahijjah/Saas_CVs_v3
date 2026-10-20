"""Offline tests for the requirements-v2 editing service: operations, planning and validation order. No database, route or model.

The service must refuse every role except admin and hr_manager (every tenant role may read), parse strictly, apply only trusted operations through
editing.py, never mutate its inputs, keep server-owned blocks from the TRUSTED stored document, record structured-field
edits, refuse documents that fail validate_final, and fail closed on a structural review that does not match the
trusted job description."""
from __future__ import annotations

import copy

import pytest

from services import requirements_edit as E
from services.requirements_v2 import contract as C
from services.requirements_v2 import structure as S
from services.requirements_v2.readiness import compute_readiness
from services.requirements_v2.structural_review import CODE_ALTERNATIVES_SPLIT, READINESS_INVALID

USER = "11111111-1111-4111-8111-111111111111"
NOW = "2026-10-17T12:00:00+00:00"
JD = "We need Python and SQL. Five years of backend work is preferred."


def _item(item_id, text, importance, weight=None, **kw):
    return C.make_item(text, importance, item_id=item_id, weight=weight, origin=kw.pop("origin", C.ORIGIN_STATED),
                       source_text=kw.pop("source_text", text), **kw)


def _base() -> dict:
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


def _block(item_ids, *, start=None, text="Python and SQL", jd=JD) -> dict:
    start = jd.index(text) if start is None else start
    return {"schema_version": 1, "findings": [{
        "id": "sf_1", "code": CODE_ALTERNATIVES_SPLIT, "item_ids": list(item_ids), "source_text": text,
        "source_start": start, "source_end": start + len(text), "message": "Check these together.",
        "status": "open", "resolution": None}]}


def _with_block(doc: dict, block: dict) -> dict:
    out = copy.deepcopy(doc)
    out["structural_review"] = block
    return out


def _ops(body):
    return E.parse_operations(body)


def _plan(body, *, stored=None, original="same", retired=(), jd=JD):
    base = _base() if stored is None else stored
    original_doc = copy.deepcopy(base) if original == "same" else original
    return E.plan_operations(base, original_doc, list(retired), _ops(body), user_id=USER, now=NOW, jd_text=jd)


def _item_of(doc: dict, item_id: str) -> dict:
    return next(i for c in C.CATEGORIES for i in doc["categories"][c]["items"] if i["id"] == item_id)


# ---- who may edit ----

@pytest.mark.parametrize("role", ["admin", "hr_manager"])
def test_admin_and_hr_manager_may_edit(role):
    E.check_editor(role)


@pytest.mark.parametrize("role", ["recruiter", "viewer", "super_admin", "", "owner", "Admin", None])
def test_every_other_role_is_refused(role):
    with pytest.raises(E.PermissionDenied):
        E.check_editor(role)


@pytest.mark.parametrize("role", ["admin", "hr_manager", "recruiter", "viewer"])
def test_every_tenant_role_may_read(role):
    E.check_reader(role)


@pytest.mark.parametrize("role", ["super_admin", "", "owner", "Admin", None])
def test_any_other_role_may_not_read(role):
    with pytest.raises(E.PermissionDenied):
        E.check_reader(role)


# ---- strict parsing ----

def test_every_operation_kind_parses():
    ops = _ops([
        {"op": "set_text", "item_id": "req_py", "text": "Python"},
        {"op": "set_importance", "item_id": "req_py", "importance": "preferred"},
        {"op": "set_item_weight", "item_id": "req_py", "weight": 50},
        {"op": "set_alternatives", "item_id": "req_py", "alternatives": ["Python", "Py"]},
        {"op": "set_alternatives", "item_id": "req_py", "alternatives": None},
        {"op": "set_experience", "item_id": "req_exp", "experience": {"subject": "backend", "min_years": 3}},
        {"op": "set_experience", "item_id": "req_exp", "experience": None},
        {"op": "set_category_weight", "category": "skills", "weight": 100},
        {"op": "add_item", "category": "skills", "text": "Docker", "importance": "preferred", "weight": None,
         "alternatives": None, "experience": None},
        {"op": "remove_item", "item_id": "req_sql"},
    ])
    assert len(ops) == 10


@pytest.mark.parametrize("body", [
    None,                                                                   # not a list
    {"op": "remove_item", "item_id": "req_sql"},                            # an object, not a list
    [],                                                                     # nothing to save
    [{"op": "set_text", "item_id": "req_py", "text": "x"}] * (E.MAX_OPERATIONS + 1),
    [{"op": "rename_item", "item_id": "req_py"}],                           # unknown operation
    [{"op": "set_text", "item_id": "req_py", "text": "x", "origin": "recruiter_added"}],   # protected field
    [{"op": "set_text", "item_id": "req_py", "text": "x", "source_text": "forged"}],       # protected field
    [{"op": "set_text", "item_id": "req_py", "text": "x", "structural_review": {"findings": []}}],
    [{"op": "set_text", "item_id": "req_py", "text": "x", "id": "req_other"}],
    [{"op": "set_text", "item_id": "req_py"}],                              # missing field
    [{"op": "set_text", "item_id": "req_py", "text": 5}],                   # wrong type
    [{"op": "set_text", "item_id": "bad id", "text": "x"}],                 # malformed id
    [{"op": "set_item_weight", "item_id": "req_py", "weight": True}],       # bool is not a weight
    [{"op": "set_item_weight", "item_id": "req_py", "weight": "50"}],
    [{"op": "set_importance", "item_id": "req_py", "importance": "mandatory"}],
    [{"op": "set_category_weight", "category": "hobbies", "weight": 10}],
    [{"op": "add_item", "category": "skills", "text": "Docker", "importance": "preferred", "weight": None,
      "alternatives": None}],                                               # add_item missing experience
    [{"op": "add_item", "category": "skills", "text": "Docker", "importance": "preferred", "weight": None,
      "alternatives": None, "experience": None, "origin": "stated"}],
    [{"op": "set_experience", "item_id": "req_exp", "experience": {"subject": "x"}}],
    [{"op": "set_experience", "item_id": "req_exp", "experience": {"subject": "x", "min_years": 1, "extra": 1}}],
    [{"op": "set_alternatives", "item_id": "req_py", "alternatives": "Python"}],
    [{"op": "remove_item", "item_id": "req_sql", "reason": "duplicate"}],
])
def test_malformed_bodies_are_refused_as_invalid_operations(body):
    with pytest.raises(E.InvalidOperation):
        E.parse_operations(body)


# ---- editing through the trusted operations ----

def test_editing_wording_keeps_origin_and_source_text():
    stored = _base()
    planned = _plan([{"op": "set_text", "item_id": "req_sql", "text": "SQL (PostgreSQL)"}], stored=stored)
    after = _item_of(planned.document, "req_sql")
    assert after["text"] == "SQL (PostgreSQL)"
    assert (after["origin"], after["source_text"]) == ("stated", "SQL")


def test_a_recruiter_added_item_has_fixed_provenance():
    planned = _plan([{"op": "add_item", "category": "skills", "text": "Docker", "importance": "preferred",
                      "weight": None, "alternatives": None, "experience": None}])
    added = planned.document["categories"]["skills"]["items"][-1]
    assert (added["text"], added["origin"], added["source_text"]) == ("Docker", C.ORIGIN_RECRUITER_ADDED, None)
    assert added["id"] not in {"req_py", "req_sql"}


def test_adding_a_preferred_item_with_a_weight_is_refused_by_editing():
    with pytest.raises(E.InvalidOperation):
        _plan([{"op": "add_item", "category": "skills", "text": "Docker", "importance": "preferred",
                "weight": 5, "alternatives": None, "experience": None}])


def test_removing_an_item_retires_its_id_and_the_weights_must_still_balance():
    planned = _plan([{"op": "remove_item", "item_id": "req_sql"},
                     {"op": "set_item_weight", "item_id": "req_py", "weight": 100}])
    assert planned.retired_item_ids == ["req_sql"]
    assert planned.weights["weight_skills"] == 100


def test_removing_an_item_without_rebalancing_is_refused():
    with pytest.raises(E.InvalidRequirements) as caught:
        _plan([{"op": "remove_item", "item_id": "req_sql"}])
    assert "required_weights_total" in [i.code for i in caught.value.issues]


def test_an_unknown_item_id_is_refused_with_its_own_code():
    with pytest.raises(E.InvalidOperation) as caught:
        _plan([{"op": "set_text", "item_id": "req_nope", "text": "x"}])
    assert caught.value.code == "unknown_item_id"


def test_an_invalid_weight_total_is_refused_as_invalid_requirements():
    with pytest.raises(E.InvalidRequirements) as caught:
        _plan([{"op": "set_item_weight", "item_id": "req_py", "weight": 70}])
    assert "required_weights_total" in [i.code for i in caught.value.issues]


def test_a_category_weight_without_required_items_is_refused():
    with pytest.raises(E.InvalidRequirements) as caught:
        _plan([{"op": "set_category_weight", "category": "experience", "weight": 10}])
    assert "category_weight_without_required_items" in [i.code for i in caught.value.issues]


def test_the_weights_mapping_has_one_column_per_category():
    planned = _plan([{"op": "set_text", "item_id": "req_sql", "text": "SQL"}])
    assert planned.weights == {"weight_skills": 100, "weight_experience": 0, "weight_education": 0,
                               "weight_certifications": 0, "weight_soft_skills": 0,
                               "weight_domain_knowledge": 0, "weight_other": 0}


# ---- structured-field edits are recorded by the server ----

def test_changing_alternatives_records_a_corrected_structure_edit():
    planned = _plan([{"op": "set_alternatives", "item_id": "req_py", "alternatives": ["Python", "CPython"]}])
    assert planned.recorded_structure_edits == (("req_py", "corrected"),)
    record = planned.document[S.STRUCTURE_KEY]["records"][0]
    assert (record["item_id"], record["kind"], record["user_id"], record["recorded_at"]) == ("req_py", "corrected", USER, NOW)


def test_a_wording_only_edit_records_nothing_and_needs_review():
    planned = _plan([{"op": "set_text", "item_id": "req_py", "text": "Python programming"}])
    assert planned.recorded_structure_edits == ()
    assert planned.document.get(S.STRUCTURE_KEY) is None
    readiness = compute_readiness(planned.document, original=_base(), jd_text=JD)
    assert readiness.state == C.NEEDS_STRUCTURE_REVIEW and "req_py" in readiness.structure_review_item_ids


def test_a_new_item_with_structure_records_an_entered_edit():
    planned = _plan([{"op": "add_item", "category": "skills", "text": "Docker", "importance": "preferred",
                      "weight": None, "alternatives": ["Docker", "Containers"], "experience": None}])
    assert [kind for _, kind in planned.recorded_structure_edits] == ["entered"]


def test_setting_structured_fields_to_the_original_values_records_nothing():
    planned = _plan([{"op": "set_alternatives", "item_id": "req_py", "alternatives": ["Python", "Python 3"]}])
    assert planned.recorded_structure_edits == ()


# ---- server-owned state is rebuilt from the trusted stored document ----

def test_a_stored_structural_review_survives_an_ordinary_edit():
    stored = _with_block(_base(), _block(["req_py"]))
    planned = _plan([{"op": "set_text", "item_id": "req_sql", "text": "SQL"}], stored=stored)
    assert planned.document["structural_review"] == stored["structural_review"]
    assert planned.open_structural_finding_ids == ("sf_1",)


def test_an_open_structural_finding_is_allowed_but_is_reported():
    stored = _with_block(_base(), _block(["req_py", "req_sql"]))
    planned = _plan([{"op": "set_text", "item_id": "req_sql", "text": "SQL"}], stored=stored)
    readiness = compute_readiness(planned.document, original=_base(), jd_text=JD)
    assert readiness.state == C.NEEDS_STRUCTURAL_REVIEW
    assert readiness.structural_finding_ids == ("sf_1",)


def test_a_structural_review_that_does_not_match_the_trusted_description_is_refused():
    stored = _with_block(_base(), _block(["req_py"]))
    with pytest.raises(E.InvalidRequirements) as caught:
        _plan([{"op": "set_text", "item_id": "req_sql", "text": "SQL"}], stored=stored,
              jd="A different job description entirely.")
    assert [i.code for i in caught.value.issues] == [READINESS_INVALID]


def test_a_structural_review_with_wrong_offsets_is_refused():
    block = _block(["req_py"])
    block["findings"][0]["source_end"] += 1
    with pytest.raises(E.InvalidRequirements) as caught:
        _plan([{"op": "set_text", "item_id": "req_sql", "text": "SQL"}], stored=_with_block(_base(), block))
    assert caught.value.issues[0].code == READINESS_INVALID


def test_a_structural_finding_naming_a_removed_item_is_refused():
    stored = _with_block(_base(), _block(["req_sql"]))
    with pytest.raises(E.InvalidRequirements) as caught:
        _plan([{"op": "remove_item", "item_id": "req_sql"},
               {"op": "set_item_weight", "item_id": "req_py", "weight": 100}], stored=stored)
    assert caught.value.issues[0].code == READINESS_INVALID


def test_an_invalid_stored_document_is_refused_before_anything_is_applied():
    stored = _base()
    del stored["categories"]["skills"]
    with pytest.raises(E.StoredDocumentInvalid):
        _plan([{"op": "set_text", "item_id": "req_py", "text": "x"}], stored=stored)


# ---- retired ids ----

def test_retired_ids_only_grow_and_stay_sorted():
    stored = _base()
    after = copy.deepcopy(stored)
    del after["categories"]["skills"]["items"][1]
    assert E.plan_retired_ids(stored, after, ["req_zzz"]) == ["req_sql", "req_zzz"]
    assert E.plan_retired_ids(stored, stored, ["req_old"]) == ["req_old"]


def test_an_id_that_was_retired_before_is_kept():
    planned = _plan([{"op": "set_text", "item_id": "req_py", "text": "Python"}], retired=["req_old"])
    assert planned.retired_item_ids == ["req_old"]


def test_a_new_item_that_reuses_a_retired_id_is_refused(monkeypatch):
    original_add = E.add_item

    def reusing_add(doc, *args, **kwargs):
        out, _ = original_add(doc, *args, **kwargs)
        out["categories"]["skills"]["items"][-1]["id"] = "req_old"
        return out, "req_old"

    monkeypatch.setattr(E, "add_item", reusing_add)
    with pytest.raises(E.InvalidRequirements) as caught:
        _plan([{"op": "add_item", "category": "skills", "text": "Docker", "importance": "preferred",
                "weight": None, "alternatives": None, "experience": None}], retired=["req_old"])
    assert caught.value.issues[0].code == "retired_item_id_reused"


# ---- inputs are never mutated ----

def test_planning_never_mutates_its_inputs():
    stored = _with_block(_base(), _block(["req_py"]))
    original = _base()
    body = [{"op": "set_alternatives", "item_id": "req_py", "alternatives": ["Python", "CPython"]}]
    before = copy.deepcopy((stored, original, body))
    E.plan_operations(stored, original, [], E.parse_operations(body), user_id=USER, now=NOW, jd_text=JD)
    assert (stored, original, body) == before


# ---- stored retired ids are validated, never converted ----

@pytest.mark.parametrize("value", [
    None,                       # NULL: not read as an empty list
    "req_py",                   # a string: refused, and never iterated character by character
    "",
    {"req_1": True},            # an object
    7,                          # a number
    0,
    True,
    [1],                        # a non-string entry
    ["req_py", 2],
    [None],
    ["bad id"],                 # not the requirement-id format
    ["req_a\n"],               # a trailing newline is not part of an id ($ alone would accept it)
    ["req_py", "req_py "],
    ["req_1", "req_1"],         # a duplicate
])
def test_malformed_stored_retired_ids_are_refused(value):
    with pytest.raises(E.StoredDocumentInvalid):
        E.validated_retired_ids(value)


@pytest.mark.parametrize("value", [None, "req_old", {}, 7, [1], ["req_1", "req_1"]])
def test_a_malformed_retired_list_refuses_planning_before_anything_is_applied(value):
    body = [{"op": "set_text", "item_id": "req_py", "text": "x"}]
    with pytest.raises(E.StoredDocumentInvalid):
        E.plan_operations(_base(), _base(), value, E.parse_operations(body), user_id=USER, now=NOW, jd_text=JD)
    with pytest.raises(E.StoredDocumentInvalid):
        E.plan_retired_ids(_base(), _base(), value)


def test_an_empty_stored_list_is_valid():
    assert E.validated_retired_ids([]) == []


def test_valid_unsorted_ids_are_normalised_to_sorted_order():
    assert E.validated_retired_ids(["req_z", "req_a", "req_m"]) == ["req_a", "req_m", "req_z"]


def test_validation_does_not_mutate_the_stored_list():
    stored = ["req_z", "req_a"]
    E.validated_retired_ids(stored)
    assert stored == ["req_z", "req_a"]


def test_unsorted_stored_ids_normalise_the_same_way_through_planning():
    body = [{"op": "remove_item", "item_id": "req_sql"}, {"op": "set_item_weight", "item_id": "req_py", "weight": 100}]
    unsorted = _plan(body, retired=["req_z", "req_old"]).retired_item_ids
    sorted_store = _plan(body, retired=["req_old", "req_z"]).retired_item_ids
    assert unsorted == sorted_store == ["req_old", "req_sql", "req_z"]


# ---- reachability: the service is imported by nothing but its own tests ----

APPROVED_EDIT_SERVICE_IMPORTERS = {"services/requirements_edit.py", "routers/job_requirements.py"}


def test_only_the_approved_modules_import_the_editing_service():
    """Routers, workers, auth, startup, configuration and every other module must not import the editing service. The only
    approved importers are the service itself and the requirements router, which is the sole route to it."""
    import pathlib
    import re

    backend = pathlib.Path(__file__).resolve().parent.parent
    pattern = re.compile(r"services\.requirements_edit\b|from services import requirements_edit\b")
    offenders = []
    for path in backend.rglob("*.py"):
        rel = path.relative_to(backend).as_posix()
        if rel.startswith(("tests/", ".", "node_modules")) or "__pycache__" in rel:
            continue
        if pattern.search(path.read_text(encoding="utf-8", errors="ignore")) and rel not in APPROVED_EDIT_SERVICE_IMPORTERS:
            offenders.append(rel)
    assert offenders == [], offenders
    assert pattern.search((backend / "routers/job_requirements.py").read_text(encoding="utf-8")), "the router is the approved route"
