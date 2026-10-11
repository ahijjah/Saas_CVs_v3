"""Requirements v2 (stage 1): edit operations (no auto-redistribution) and original-vs-current comparison."""
from __future__ import annotations

import copy

import pytest

from services.requirements_v2 import (
    CATEGORIES, add_item, compute_readiness, edited_categories, edited_category_names, empty_requirements,
    equalize_category, make_item, original_digest, remove_item, set_category_weight, set_importance,
    set_item_weight, set_text, snapshot_original, validate_final,
)


def original_doc():
    """A processed AI analysis: skills 70% (34/33/33), experience 30% (one structured item), preferred cert."""
    d = empty_requirements()
    d["categories"]["skills"] = {"weight": 70, "items": [
        make_item("ATS", "required", item_id="req_s1", weight=34, source_text="ATS tools"),
        make_item("LinkedIn Recruiter", "required", item_id="req_s2", weight=33),
        make_item("Excel", "required", item_id="req_s3", weight=33),
        make_item("Workday", "preferred", item_id="req_s4"),
    ]}
    d["categories"]["experience"] = {"weight": 30, "items": [
        make_item("5 years of recruitment experience", "required", item_id="req_x1", weight=100,
                  source_text="five years of recruitment experience",
                  experience={"subject": "recruitment", "min_years": 5}),
    ]}
    d["categories"]["education"]["items"] = [
        make_item("BSc in HR or Business Administration", "preferred", item_id="req_e1",
                  alternatives=["Human Resources", "Business Administration"]),
    ]
    return d


def test_fixture_is_valid():
    assert validate_final(original_doc()).ok


# ── edits never redistribute ──────────────────────────────────────────────────

def test_edit_operations_do_not_mutate_their_input():
    d = original_doc()
    before = copy.deepcopy(d)
    add_item(d, "skills", "SQL", "required")
    remove_item(d, "req_s1")
    set_importance(d, "req_s1", "preferred")
    set_text(d, "req_s1", "new")
    set_item_weight(d, "req_s1", 10)
    set_category_weight(d, "skills", 10)
    assert d == before


def test_adding_a_required_item_changes_no_existing_weight_and_blocks_saving_until_fixed():
    d, new_id = add_item(original_doc(), "skills", "SQL", "required")
    assert [i["weight"] for i in d["categories"]["skills"]["items"]] == [34, 33, 33, None, None]
    assert d["categories"]["skills"]["weight"] == 70
    r = validate_final(d)
    assert not r.ok and "required_weight_invalid" in r.codes()
    fixed = equalize_category(d, "skills")                       # explicit Equalize, 4 items: 25 each
    assert [i["weight"] for i in fixed["categories"]["skills"]["items"] if i["importance"] == "required"] == [25] * 4
    assert validate_final(fixed).ok
    assert new_id in [i["id"] for i in fixed["categories"]["skills"]["items"]]


def test_new_item_ids_are_code_generated_unique_and_valid():
    d = original_doc()
    ids = set()
    for n in range(50):
        d, iid = add_item(d, "other_requirements", f"x{n}", "preferred")
        assert iid not in ids
        ids.add(iid)
    assert validate_final(d).ok


def test_adding_the_first_required_item_to_a_zero_weight_category_needs_an_explicit_weight():
    d, iid = add_item(original_doc(), "certifications", "CIPD", "required", weight=100)
    assert d["categories"]["certifications"]["weight"] == 0
    assert "category_weight_not_positive" in validate_final(d).codes()
    assert "category_weights_total" not in validate_final(d).codes()          # others untouched: still 100
    d = set_category_weight(d, "certifications", 10)
    assert "category_weights_total" in validate_final(d).codes()               # recruiter must rebalance by hand
    d = set_category_weight(set_category_weight(d, "skills", 60), "experience", 30)
    assert validate_final(d).ok


def test_removing_the_last_required_item_sets_its_category_weight_to_zero_without_moving_the_points():
    d = remove_item(original_doc(), "req_x1")
    assert d["categories"]["experience"]["weight"] == 0
    assert d["categories"]["skills"]["weight"] == 70                           # freed 30 points NOT redistributed
    assert "category_weights_total" in validate_final(d).codes()
    d = set_category_weight(d, "skills", 100)
    assert validate_final(d).ok


def test_removing_a_non_last_required_item_leaves_weights_alone():
    d = remove_item(original_doc(), "req_s1")
    assert d["categories"]["skills"]["weight"] == 70
    assert [i["weight"] for i in d["categories"]["skills"]["items"] if i["importance"] == "required"] == [33, 33]
    assert "required_weights_total" in validate_final(d).codes()


def test_reclassifying_required_to_preferred_clears_weight_and_zeroes_a_now_empty_category():
    d = set_importance(original_doc(), "req_x1", "preferred")
    item = d["categories"]["experience"]["items"][0]
    assert item["importance"] == "preferred" and item["weight"] is None
    assert d["categories"]["experience"]["weight"] == 0
    assert d["categories"]["skills"]["weight"] == 70


def test_reclassifying_preferred_to_required_starts_unweighted():
    d = set_importance(original_doc(), "req_s4", "required")
    item = [i for i in d["categories"]["skills"]["items"] if i["id"] == "req_s4"][0]
    assert item["importance"] == "required" and item["weight"] is None
    assert [i["weight"] for i in d["categories"]["skills"]["items"] if i["id"] != "req_s4"][:3] == [34, 33, 33, None][:3]


def test_set_importance_to_the_same_value_changes_nothing():
    d = original_doc()
    assert set_importance(d, "req_s1", "required") == d


def test_preferred_items_cannot_be_given_a_weight():
    with pytest.raises(ValueError):
        set_item_weight(original_doc(), "req_s4", 10)
    with pytest.raises(ValueError):
        add_item(original_doc(), "skills", "x", "preferred", weight=5)
    assert set_item_weight(original_doc(), "req_s4", None) == original_doc()


def test_edit_helpers_reject_bad_input():
    d = original_doc()
    for call in (lambda: set_text(d, "req_missing", "x"), lambda: remove_item(d, "req_missing"),
                 lambda: add_item(d, "hobbies", "x", "required"), lambda: add_item(d, "skills", "x", "maybe"),
                 lambda: set_category_weight(d, "skills", 10.5), lambda: set_category_weight(d, "hobbies", 10),
                 lambda: set_item_weight(d, "req_s1", 3.5), lambda: set_importance(d, "req_s1", "maybe")):
        with pytest.raises((KeyError, ValueError)):
            call()


# ── text edits keep structure and provenance ──────────────────────────────────

def test_text_edit_keeps_structured_experience_alternatives_source_and_origin():
    d = set_text(original_doc(), "req_x1", "Five years working in recruitment")
    item = d["categories"]["experience"]["items"][0]
    assert item["text"] == "Five years working in recruitment"
    assert item["experience"] == {"subject": "recruitment", "min_years": 5}
    assert item["source_text"] == "five years of recruitment experience" and item["origin"] == "stated"
    assert item["weight"] == 100 and item["id"] == "req_x1"

    d = set_text(d, "req_e1", "Degree in HR")
    assert d["categories"]["education"]["items"][0]["alternatives"] == ["Human Resources", "Business Administration"]


def test_responsibility_provenance_survives_edits():
    d, iid = add_item(original_doc(), "experience", "Manage end-to-end hiring", "required",
                      origin="from_responsibilities", source_text="You will manage end-to-end hiring")
    d = set_importance(set_text(d, iid, "Manage hiring"), iid, "preferred")
    item = [i for i in d["categories"]["experience"]["items"] if i["id"] == iid][0]
    assert item["origin"] == "from_responsibilities" and item["source_text"] == "You will manage end-to-end hiring"


# ── original vs current ───────────────────────────────────────────────────────

def test_unchanged_current_has_no_edited_flags():
    o = original_doc()
    assert edited_categories(o, copy.deepcopy(o)) == {c: False for c in CATEGORIES}
    assert edited_category_names(o, o) == []


@pytest.mark.parametrize("edit,category", [
    (lambda d: set_text(d, "req_s1", "ATS systems"), "skills"),
    (lambda d: set_importance(d, "req_s4", "required"), "skills"),
    (lambda d: set_item_weight(d, "req_s2", 34), "skills"),
    (lambda d: set_category_weight(d, "skills", 71), "skills"),
    (lambda d: add_item(d, "other_requirements", "Driving licence", "preferred")[0], "other_requirements"),
    (lambda d: remove_item(d, "req_e1"), "education"),
    (lambda d: set_text(d, "req_e1", "Degree"), "education"),
])
def test_any_item_wording_importance_or_weight_difference_flags_only_that_category(edit, category):
    o = original_doc()
    flags = edited_categories(o, edit(o))
    assert [c for c in CATEGORIES if flags[c]] == [category]


def test_structured_changes_flag_the_category():
    o = original_doc()
    c = copy.deepcopy(o)
    c["categories"]["experience"]["items"][0]["experience"]["min_years"] = 6
    assert edited_category_names(o, c) == ["experience"]
    c = copy.deepcopy(o)
    c["categories"]["education"]["items"][0]["alternatives"] = ["Human Resources"]  # (invalid for save, still a diff)
    assert edited_category_names(o, c) == ["education"]


def test_restoring_original_values_removes_the_flag():
    o = original_doc()
    c = set_item_weight(set_text(o, "req_s1", "changed"), "req_s2", 40)
    c = set_category_weight(c, "skills", 50)
    assert edited_category_names(o, c) == ["skills"]
    c = set_item_weight(set_text(set_category_weight(c, "skills", 70), "req_s1", "ATS"), "req_s2", 33)
    assert edited_category_names(o, c) == []


def test_removing_and_restoring_an_item_by_undo_clears_the_flag_but_a_re_add_does_not():
    o = original_doc()
    removed = remove_item(o, "req_s4")
    assert edited_category_names(o, removed) == ["skills"]
    undone = copy.deepcopy(o)                                                 # a true restore of the original object
    assert edited_category_names(o, undone) == []
    readded, _ = add_item(removed, "skills", "Workday", "preferred")          # same wording, new id: still a difference
    assert edited_category_names(o, readded) == ["skills"]


def test_changing_only_provenance_or_confirmation_does_not_flag():
    o = original_doc()
    c = copy.deepcopy(o)
    c["categories"]["skills"]["items"][0]["source_text"] = "other"
    c["categories"]["skills"]["items"][0]["origin"] = "recruiter_added"
    c["scoring_confirmation"] = None
    assert edited_category_names(o, c) == []


def test_item_order_is_part_of_the_content():
    o = original_doc()
    c = copy.deepcopy(o)
    c["categories"]["skills"]["items"].reverse()
    assert edited_category_names(o, c) == ["skills"]


def test_original_snapshot_is_a_deep_copy_unaffected_by_later_edits():
    processed = original_doc()
    snap = snapshot_original(processed)
    digest = original_digest(snap)
    current = set_text(set_category_weight(processed, "skills", 1), "req_s1", "changed")
    assert original_digest(snap) == digest                                     # edits never touch the snapshot
    assert snap["categories"]["skills"]["weight"] == 70
    assert snap["categories"]["skills"]["items"][0]["text"] == "ATS" and snap["categories"]["skills"]["items"][0]["id"] == "req_s1"
    assert edited_category_names(snap, current) == ["skills"]
    processed["categories"]["skills"]["weight"] = 5                            # even mutating the source later
    assert original_digest(snap) == digest


def test_snapshot_strips_any_confirmation_and_keeps_ids_and_initial_weights():
    d = original_doc()
    d["scoring_confirmation"] = {"kind": "no_numeric_score", "user_id": "u", "confirmed_at": "t", "basis_hash": "h"}
    snap = snapshot_original(d)
    assert snap["scoring_confirmation"] is None
    assert [i["weight"] for i in snap["categories"]["skills"]["items"]] == [34, 33, 33, None]
    assert d["scoring_confirmation"] is not None


# ── end to end: an edit session ───────────────────────────────────────────────

def test_edit_session_reaches_a_valid_state_only_through_explicit_actions():
    o = original_doc()
    cur, new_id = add_item(o, "skills", "SQL", "required")
    assert not validate_final(cur).ok
    cur = equalize_category(cur, "skills")
    assert validate_final(cur).ok and compute_readiness(cur).state == "ready"
    assert edited_category_names(o, cur) == ["skills"]
    cur = remove_item(cur, new_id)
    cur = equalize_category(cur, "skills")
    assert [i["weight"] for i in cur["categories"]["skills"]["items"] if i["importance"] == "required"] == [34, 33, 33]
    assert edited_category_names(o, cur) == []                                 # back to the original values: flag gone
