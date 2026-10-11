"""Requirements v2 (stage 1 robustness): controlled carry_confirmation, strict apply_category_weights, reserved item
ids, and confirmation invalidation for alternatives and experience changes. Pure functions only."""
from __future__ import annotations

import copy

import pytest

from services.requirements_v2 import (
    CATEGORIES, add_item, apply_category_weights, basis_hash, carry_confirmation, collect_item_ids,
    compute_readiness, confirm_no_numeric_score, empty_requirements, make_item, remove_item, validate_final,
)
from services.requirements_v2 import contract


def _pref(text, iid, **kw):
    return make_item(text, "preferred", item_id=iid, **kw)


def _confirmed(doc, user="u1"):
    return confirm_no_numeric_score(doc, user_id=user, confirmed_at="2026-01-01T00:00:00Z")


def preferred_only():
    d = empty_requirements()
    d["categories"]["education"]["items"] = [
        _pref("BSc in HR or Business", "req_e1", alternatives=["Human Resources", "Business"])]
    d["categories"]["experience"]["items"] = [
        _pref("3 years of recruitment", "req_x1", experience={"subject": "recruitment", "min_years": 3})]
    return d


# ── 1. carry_confirmation: controlled behavior for non-object input ──────────

@pytest.mark.parametrize("incoming", ["text", ["a"], 42, 4.5, True, None])
def test_carry_confirmation_returns_non_object_input_unchanged_without_raising(incoming):
    stored = _confirmed(preferred_only())
    out = carry_confirmation(stored, incoming)
    assert out == incoming and type(out) is type(incoming)
    assert validate_final(out).codes() == ["not_an_object"]         # validation, not a crash, rejects it


def test_carry_confirmation_non_object_list_is_a_copy_not_the_same_object():
    incoming = [1, 2]
    out = carry_confirmation(_confirmed(preferred_only()), incoming)
    assert out == incoming and out is not incoming


@pytest.mark.parametrize("stored", [None, "x", [], 7, {}, {"scoring_confirmation": "x"}, {"scoring_confirmation": []},
                                    {"scoring_confirmation": 5}])
def test_carry_confirmation_treats_unusable_stored_state_as_no_confirmation(stored):
    incoming = preferred_only()
    out = carry_confirmation(stored, incoming)
    assert out["scoring_confirmation"] is None


def test_carry_confirmation_with_malformed_stored_confirmation_dict_does_not_raise():
    incoming = preferred_only()
    out = carry_confirmation({"scoring_confirmation": {"kind": "no_numeric_score"}}, incoming)   # no basis_hash
    assert out["scoring_confirmation"] is None


def test_carry_confirmation_never_mutates_its_inputs():
    stored = _confirmed(preferred_only())
    incoming = copy.deepcopy(stored)
    incoming["scoring_confirmation"]["user_id"] = "tampered"
    s0, i0 = copy.deepcopy(stored), copy.deepcopy(incoming)
    carry_confirmation(stored, incoming)
    assert stored == s0 and incoming == i0


def test_a_matching_hash_is_not_authorization():
    """The hash is computable by anyone: only the stored (server-created) confirmation can survive a save."""
    content = preferred_only()
    forged = copy.deepcopy(content)
    forged["scoring_confirmation"] = {"kind": "no_numeric_score", "user_id": "attacker",
                                      "confirmed_at": "t", "basis_hash": basis_hash(content)}
    assert carry_confirmation(None, forged)["scoring_confirmation"] is None
    assert carry_confirmation(content, forged)["scoring_confirmation"] is None       # nothing stored -> nothing kept
    stored = _confirmed(content, user="real-user")
    kept = carry_confirmation(stored, forged)["scoring_confirmation"]
    assert kept == stored["scoring_confirmation"] and kept["user_id"] == "real-user"  # the server's, not the client's


# ── 2. apply_category_weights: exactly seven keys, whole numbers 0..100, never truncate ─

def _weights(**kw):
    w = {c: 0 for c in CATEGORIES}
    w.update(kw)
    return w


def test_apply_category_weights_sets_exactly_the_given_integers():
    doc = empty_requirements()
    w = _weights(skills=60, experience=40)
    out = apply_category_weights(doc, w)
    assert [out["categories"][c]["weight"] for c in CATEGORIES] == [60, 40, 0, 0, 0, 0, 0]
    assert all(doc["categories"][c]["weight"] == 0 for c in CATEGORIES)                 # input untouched


def test_apply_category_weights_does_not_check_the_total():
    out = apply_category_weights(empty_requirements(), _weights(skills=7))
    assert out["categories"]["skills"]["weight"] == 7                    # validate_final decides, not this function


@pytest.mark.parametrize("bad", [14.0, 14.7, 0.5, True, False, "14", None, -1, 101, [14]])
def test_apply_category_weights_rejects_non_integer_or_out_of_range_values(bad):
    doc = empty_requirements()
    before = copy.deepcopy(doc)
    with pytest.raises(ValueError, match="whole numbers from 0 to 100"):
        apply_category_weights(doc, _weights(skills=bad))
    assert doc == before


def test_apply_category_weights_never_truncates_floats():
    with pytest.raises(ValueError):
        apply_category_weights(empty_requirements(), _weights(skills=14.7))


def test_apply_category_weights_requires_exactly_the_seven_categories():
    doc = empty_requirements()
    missing = _weights(skills=100)
    del missing["education"]
    with pytest.raises(ValueError, match="missing"):
        apply_category_weights(doc, missing)
    with pytest.raises(ValueError, match="unknown"):
        apply_category_weights(doc, {**_weights(), "hobbies": 0})
    with pytest.raises(ValueError):
        apply_category_weights(doc, {})
    for not_a_mapping in (None, [1] * 7, "skills", 100):
        with pytest.raises(ValueError, match="mapping"):
            apply_category_weights(doc, not_a_mapping)


# ── 3. add_item reserved ids ──────────────────────────────────────────────────

def test_collect_item_ids_lists_every_id_in_the_document():
    d = preferred_only()
    assert collect_item_ids(d) == {"req_e1", "req_x1"}
    assert collect_item_ids(empty_requirements()) == set()


def _force_uuids(monkeypatch, hex_values):
    """Make the id generator hand out the given 12-hex-digit values in order (then fresh random ones)."""
    seq = iter(hex_values)

    class _U:
        def __init__(self, h):
            self.hex = h + "0" * 20

    real = contract.uuid.uuid4

    def fake():
        try:
            return _U(next(seq))
        except StopIteration:
            return real()

    monkeypatch.setattr(contract.uuid, "uuid4", fake)


def test_add_item_skips_reserved_ids(monkeypatch):
    _force_uuids(monkeypatch, ["aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc"])
    d, iid = add_item(empty_requirements(), "skills", "SQL", "preferred",
                      reserved_ids={"req_aaaaaaaaaaaa", "req_bbbbbbbbbbbb"})
    assert iid == "req_cccccccccccc"


def test_add_item_still_avoids_ids_already_in_the_document(monkeypatch):
    base, first = add_item(empty_requirements(), "skills", "A", "preferred")
    _force_uuids(monkeypatch, [first.removeprefix("req_"), "dddddddddddd"])
    _, second = add_item(base, "skills", "B", "preferred")
    assert second == "req_dddddddddddd" and second != first


def test_many_additions_never_reissue_reserved_original_or_deleted_ids():
    original = preferred_only()
    reserved = collect_item_ids(original)                                   # {"req_e1", "req_x1"}
    current = remove_item(original, "req_e1")                              # req_e1 is gone from the document
    ids = set()
    for n in range(200):
        current, iid = add_item(current, "other_requirements", f"x{n}", "preferred", reserved_ids=reserved)
        ids.add(iid)
    assert len(ids) == 200 and not (ids & reserved)
    assert validate_final(current).ok


def test_reserved_ids_forced_collision_with_a_deleted_original_id(monkeypatch):
    """The generator yields the deleted original id's value first; with it reserved, it must be skipped."""
    original = empty_requirements()
    original["categories"]["skills"]["items"] = [_pref("Excel", "req_1a2b3c4d5e6f")]
    reserved = collect_item_ids(original)
    current = remove_item(original, "req_1a2b3c4d5e6f")
    _force_uuids(monkeypatch, ["1a2b3c4d5e6f", "ffffffffffff"])
    _, with_reserve = add_item(current, "skills", "New", "preferred", reserved_ids=reserved)
    assert with_reserve == "req_ffffffffffff"
    _force_uuids(monkeypatch, ["1a2b3c4d5e6f", "ffffffffffff"])
    _, without_reserve = add_item(current, "skills", "New", "preferred")      # old behavior: the deleted id returns
    assert without_reserve == "req_1a2b3c4d5e6f"


def test_add_item_reserved_ids_accepts_any_iterable():
    d, iid = add_item(empty_requirements(), "skills", "x", "preferred", reserved_ids=iter(["req_zzz"]))
    assert iid.startswith("req_")
    d, iid = add_item(d, "skills", "y", "preferred", reserved_ids=("req_a", "req_b"))
    assert validate_final(d).ok


# ── 4/5. confirmation invalidation: alternatives and experience ───────────────

def _saved(stored, edited):
    return carry_confirmation(stored, edited)


@pytest.mark.parametrize("new_alternatives", [
    ["Human Resources", "Business", "Psychology"],        # alternative added
    ["Human Resources", "Business Administration"],       # alternative reworded
    ["Business", "Human Resources"],                       # alternatives reordered (part of the structure)
    None,                                                  # structure removed
])
def test_alternatives_changes_invalidate_the_confirmation(new_alternatives):
    stored = _confirmed(preferred_only())
    edited = copy.deepcopy(stored)
    edited["categories"]["education"]["items"][0]["alternatives"] = new_alternatives
    saved = _saved(stored, edited)
    assert saved["scoring_confirmation"] is None
    assert compute_readiness(saved).state == "needs_confirmation"


def test_adding_alternatives_to_an_item_without_them_invalidates():
    base = preferred_only()
    base["categories"]["education"]["items"][0]["alternatives"] = None
    stored = _confirmed(base)
    edited = copy.deepcopy(stored)
    edited["categories"]["education"]["items"][0]["alternatives"] = ["A", "B"]
    assert _saved(stored, edited)["scoring_confirmation"] is None


@pytest.mark.parametrize("new_experience", [
    {"subject": "recruitment", "min_years": 4},            # duration changed
    {"subject": "sourcing", "min_years": 3},               # subject changed
    {"subject": "recruitment", "min_years": None},         # duration removed
    {"subject": None, "min_years": 3},                     # subject removed
    None,                                                  # structure removed
])
def test_experience_changes_invalidate_the_confirmation(new_experience):
    stored = _confirmed(preferred_only())
    edited = copy.deepcopy(stored)
    edited["categories"]["experience"]["items"][0]["experience"] = new_experience
    saved = _saved(stored, edited)
    assert saved["scoring_confirmation"] is None
    assert compute_readiness(saved).state == "needs_confirmation"


def test_unchanged_structure_keeps_the_confirmation_and_the_job_stays_ready():
    stored = _confirmed(preferred_only())
    same = copy.deepcopy(stored)
    saved = _saved(stored, same)
    assert saved["scoring_confirmation"] == stored["scoring_confirmation"]
    ready = compute_readiness(saved)
    assert ready.state == "ready" and ready.scoring_mode == "none"


def test_equal_structure_with_equal_wording_is_not_invalidated_by_identical_resave_of_fresh_copy():
    stored = _confirmed(preferred_only())
    rebuilt = preferred_only()                                                # same content, built independently
    assert _saved(stored, rebuilt)["scoring_confirmation"] == stored["scoring_confirmation"]


def test_attached_stale_confirmation_fails_final_validation_after_structure_edit():
    stored = _confirmed(preferred_only())
    edited = copy.deepcopy(stored)                                            # NOT passed through carry_confirmation
    edited["categories"]["experience"]["items"][0]["experience"]["min_years"] = 9
    assert "confirmation_stale" in validate_final(edited).codes()
