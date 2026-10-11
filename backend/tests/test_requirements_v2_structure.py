"""Requirements-v2 structured-requirement review (pure module): OR alternatives and experience subject / duration.
English and Arabic. No database, no model. Nothing here verifies that wording and structure agree -- that is a
person's statement, and these tests pin when it is required, recorded and invalidated."""
from __future__ import annotations

import copy

import pytest

from services.requirements_v2 import (
    CATEGORIES, NEEDS_STRUCTURE_REVIEW, ConfirmationError, StructureError, add_item, carry_server_owned, carry_structure_review,
    compute_readiness, confirm_no_numeric_score, confirm_structure, reconcile_structure_review,
    record_structure_edits, remove_item, set_structure, set_text, structure_status, validate_draft, validate_final,
)
from services.requirements_v2.extraction import process_ai_output
from services.requirements_v2.structure import STRUCTURE_KEY, basis_hash, basis_of

NOW = "2026-03-03T09:00:00Z"
LANG = {
    "english": dict(jd="Requirements:\n- 4 years as a Maintenance Planner\n- SQL or PostgreSQL\n",
                    exp="4 years as a Maintenance Planner", subject="Maintenance Planner", alt="SQL or PostgreSQL",
                    alts=["SQL", "PostgreSQL"], edited="7 years as a Maintenance Planner"),
    "arabic": dict(jd="المتطلبات:\n- خبرة 4 سنوات كمخطط صيانة\n- لغة SQL أو PostgreSQL\n",
                   exp="خبرة 4 سنوات كمخطط صيانة", subject="مخطط صيانة", alt="لغة SQL أو PostgreSQL",
                   alts=["SQL", "PostgreSQL"], edited="خبرة 7 سنوات كمخطط صيانة"),
}
LANGUAGES = pytest.mark.parametrize("lang", list(LANG))


def raw(text, importance="required", alternatives=None, experience=None, cue=None):
    return {"text": text, "importance": importance, "importance_cue": cue, "source_text": text, "origin": "stated",
            "alternatives": alternatives, "experience": experience}


def job(lang):
    L = LANG[lang]
    cats = {c: [] for c in CATEGORIES}
    cats["experience"].append(raw(L["exp"], experience={"subject": L["subject"], "min_years": 4}))
    cats["skills"].append(raw(L["alt"], alternatives=L["alts"]))
    ai = {"scoreability": {"status": "scoreable", "reason": ""}, "categories": cats,
          "category_weights": {"skills": 50, "experience": 50}, "non_scoreable_requirements": [],
          "post_hiring_conditions": [], "informational_items": [], "warnings": []}
    r = process_ai_output(ai, L["jd"])
    assert r.requirements, r.errors
    return r.requirements, r.original, L


def ids(doc):
    return {i["text"]: i["id"] for c in CATEGORIES for i in doc["categories"][c]["items"]}


def save_like(stored, incoming, original, user="u-1"):
    """What the API does for a save: carry server-owned state from STORED, then record this save's structure edits."""
    carried = carry_server_owned(stored, incoming)
    return record_structure_edits(stored, carried, original, user_id=user, recorded_at=NOW)


class TestStatus:

    @LANGUAGES
    def test_unedited_structure_is_settled_by_the_original_baseline(self, lang):
        doc, original, L = job(lang)
        st = structure_status(doc, original)
        assert set(st.original) == set(ids(doc).values()) and st.needs_review == ()
        assert compute_readiness(doc, original=original).state == "ready"

    @LANGUAGES
    def test_a_wording_only_edit_needs_review_and_changes_nothing_else(self, lang):
        doc, original, L = job(lang)
        eid = ids(doc)[L["exp"]]
        edited = set_text(doc, eid, L["edited"])
        assert structure_status(edited, original).needs_review == (eid,)
        r = compute_readiness(edited, original=original)
        assert r.state == NEEDS_STRUCTURE_REVIEW and not r.can_proceed and r.structure_review_item_ids == (eid,)
        assert r.reasons[0].code == "structure_review_pending" and r.reasons[0].item_id == eid
        item = next(i for i in edited["categories"]["experience"]["items"])
        assert item["experience"] == doc["categories"]["experience"]["items"][0]["experience"]
        assert (item["importance"], item["weight"], item["source_text"]) == ("required", 100, L["exp"])

    @LANGUAGES
    def test_restoring_the_original_wording_exactly_settles_it_again(self, lang):
        doc, original, L = job(lang)
        eid = ids(doc)[L["exp"]]
        back = set_text(set_text(doc, eid, L["edited"]), eid, L["exp"])
        assert compute_readiness(back, original=original).state == "ready"

    def test_the_structure_review_is_skipped_unless_an_original_is_passed(self):
        doc, original, L = job("english")
        edited = set_text(doc, ids(doc)[L["exp"]], L["edited"])
        assert compute_readiness(edited).state == "ready"                      # not evaluated (extraction drafts)
        assert compute_readiness(edited, original=None).state == NEEDS_STRUCTURE_REVIEW      # no baseline: fail closed
        assert compute_readiness(doc, original=None).state == NEEDS_STRUCTURE_REVIEW

    def test_items_without_structure_never_need_review(self):
        doc, original, L = job("english")
        plain, pid = add_item(doc, "domain_knowledge", "Cement", "preferred")
        assert structure_status(plain, original).needs_review == ()
        assert compute_readiness(set_text(plain, pid, "Cement industry"), original=original).state == "ready"

    def test_structure_review_never_touches_required_preferred_or_weights(self):
        doc, original, L = job("english")
        edited = set_text(doc, ids(doc)[L["exp"]], L["edited"])
        for c in CATEGORIES:
            assert edited["categories"][c]["weight"] == doc["categories"][c]["weight"]
        assert [i["importance"] for c in CATEGORIES for i in edited["categories"][c]["items"]] == \
               [i["importance"] for c in CATEGORIES for i in doc["categories"][c]["items"]]


class TestConfirmation:

    @LANGUAGES
    def test_confirming_records_user_time_wording_and_structure(self, lang):
        doc, original, L = job(lang)
        eid = ids(doc)[L["exp"]]
        edited = set_text(doc, eid, L["edited"])
        out = confirm_structure(edited, eid, user_id="u-9", confirmed_at=NOW, original=original)
        (rec,) = out[STRUCTURE_KEY]["records"]
        assert (rec["item_id"], rec["kind"], rec["user_id"], rec["recorded_at"]) == (eid, "confirmed", "u-9", NOW)
        assert rec["basis"] == {"text": L["edited"], "alternatives": None,
                                "experience": {"subject": L["subject"], "min_years": 4}}
        assert rec["basis_hash"] == basis_hash(rec["basis"])
        assert structure_status(out, original).confirmed == (eid,) and compute_readiness(out, original=original).state == "ready"
        assert validate_final(out).ok
        assert STRUCTURE_KEY not in edited                                                      # input not mutated

    def test_confirmation_is_refused_when_there_is_nothing_to_confirm(self):
        doc, original, L = job("english")
        eid = ids(doc)[L["exp"]]
        for args, code in (((doc, "req_nope"), "unknown_item"), ((doc, eid), "already_settled")):
            with pytest.raises(StructureError) as ei:
                confirm_structure(*args, user_id="u", confirmed_at=NOW, original=original)
            assert ei.value.code == code
        plain, pid = add_item(doc, "domain_knowledge", "Cement", "preferred")
        with pytest.raises(StructureError) as ei:
            confirm_structure(plain, pid, user_id="u", confirmed_at=NOW, original=original)
        assert ei.value.code == "no_structure"
        edited = set_text(doc, eid, L["edited"])
        for bad in ({"user_id": "", "confirmed_at": NOW}, {"user_id": "u", "confirmed_at": ""}, {"user_id": None, "confirmed_at": NOW}):
            with pytest.raises(StructureError):
                confirm_structure(edited, eid, original=original, **bad)

    def test_later_wording_or_structure_changes_invalidate_with_a_reason(self):
        doc, original, L = job("english")
        eid = ids(doc)[L["exp"]]
        out = confirm_structure(set_text(doc, eid, L["edited"]), eid, user_id="u", confirmed_at=NOW, original=original)
        for changed in (set_text(out, eid, "8 years"), set_structure(out, eid, experience={"subject": "X", "min_years": 1})):
            rec = reconcile_structure_review(changed)
            assert rec.invalidated == ((eid, "item_changed"),) and rec.doc[STRUCTURE_KEY]["records"] == []
            assert structure_status(changed, original).confirmed == ()          # even before pruning, never counted
        removed = reconcile_structure_review(remove_item(out, eid))
        assert removed.invalidated == ((eid, "item_removed"),)

    def test_a_pruned_confirmation_does_not_come_back_when_the_wording_returns(self):
        doc, original, L = job("english")
        eid = ids(doc)[L["exp"]]
        out = confirm_structure(set_text(doc, eid, L["edited"]), eid, user_id="u", confirmed_at=NOW, original=original)
        stored = out
        away, _ = save_like(stored, set_text(out, eid, "something else"), original)
        assert away[STRUCTURE_KEY]["records"] == []
        back, _ = save_like(away, set_text(away, eid, L["edited"]), original)
        assert structure_status(back, original).needs_review == (eid,)


class TestSavesRecordOnlyRealStructureEdits:

    @LANGUAGES
    def test_saving_the_same_structured_values_is_not_a_confirmation(self, lang):
        doc, original, L = job(lang)
        eid = ids(doc)[L["exp"]]
        incoming = set_text(doc, eid, L["edited"])
        incoming = set_structure(incoming, eid, experience={"subject": L["subject"], "min_years": 4})   # same values again
        out, recorded = save_like(doc, incoming, original)
        assert recorded == () and STRUCTURE_KEY not in out
        assert compute_readiness(out, original=original).state == NEEDS_STRUCTURE_REVIEW

    @LANGUAGES
    def test_changing_the_experience_is_a_correction(self, lang):
        doc, original, L = job(lang)
        eid = ids(doc)[L["exp"]]
        incoming = set_structure(set_text(doc, eid, L["edited"]), eid, experience={"subject": L["subject"], "min_years": 7})
        out, recorded = save_like(doc, incoming, original, user="u-7")
        assert recorded == ((eid, "corrected"),)
        (rec,) = out[STRUCTURE_KEY]["records"]
        assert (rec["user_id"], rec["recorded_at"]) == ("u-7", NOW) and rec["basis"]["experience"]["min_years"] == 7
        assert compute_readiness(out, original=original).state == "ready"

    @LANGUAGES
    def test_changing_or_alternatives_is_a_correction(self, lang):
        doc, original, L = job(lang)
        aid = ids(doc)[L["alt"]]
        out, recorded = save_like(doc, set_structure(doc, aid, alternatives=L["alts"] + ["Oracle"]), original)
        assert recorded == ((aid, "corrected"),) and compute_readiness(out, original=original).state == "ready"
        # adding alternatives to an item that had none
        plain, pid = add_item(doc, "domain_knowledge", "Cement", "preferred")
        out2, rec2 = save_like(plain, set_structure(plain, pid, alternatives=["a", "b"]), original)
        assert rec2 == ((pid, "corrected"),)

    def test_clearing_the_structure_leaves_nothing_to_review_and_prunes_the_record(self):
        doc, original, L = job("english")
        aid = ids(doc)[L["alt"]]
        corrected, _ = save_like(doc, set_structure(doc, aid, alternatives=["SQL", "Oracle"]), original)
        cleared, recorded = save_like(corrected, set_structure(corrected, aid, alternatives=None), original)
        assert recorded == () and cleared[STRUCTURE_KEY]["records"] == []
        assert compute_readiness(cleared, original=original).state == "ready"

    def test_new_structured_items_are_entered_and_unstructured_ones_record_nothing(self):
        doc, original, L = job("english")
        incoming, nid = add_item(doc, "skills", "Docker or Podman", "preferred", alternatives=["Docker", "Podman"])
        incoming, eid = add_item(incoming, "experience", "2 years QA", "required", weight=None,
                                 experience={"subject": "QA", "min_years": 2})
        incoming, pid = add_item(incoming, "skills", "Plain", "preferred")
        out, recorded = save_like(doc, incoming, original)
        assert dict(recorded) == {nid: "entered", eid: "entered"} and pid not in dict(recorded)
        assert {r["item_id"] for r in out[STRUCTURE_KEY]["records"]} == {nid, eid}

    def test_reverting_a_correction_to_the_original_values_records_nothing(self):
        doc, original, L = job("english")
        aid = ids(doc)[L["alt"]]
        corrected, _ = save_like(doc, set_structure(doc, aid, alternatives=["SQL", "Oracle"]), original)
        back, recorded = save_like(corrected, set_structure(corrected, aid, alternatives=L["alts"]), original)
        assert recorded == ()
        assert aid in structure_status(back, original).original and back[STRUCTURE_KEY]["records"] == []


class TestServerOwnership:

    def test_the_client_block_is_discarded_even_with_a_matching_hash(self):
        doc, original, L = job("english")
        eid = ids(doc)[L["exp"]]
        edited = set_text(doc, eid, L["edited"])
        forged = copy.deepcopy(edited)
        basis = basis_of(next(i for i in forged["categories"]["experience"]["items"]))
        forged[STRUCTURE_KEY] = {"records": [{"item_id": eid, "kind": "confirmed", "user_id": "mallory",
                                              "recorded_at": "2020-01-01T00:00:00Z", "basis": basis,
                                              "basis_hash": basis_hash(basis)}]}
        carried = carry_structure_review(doc, forged)
        assert STRUCTURE_KEY not in carried
        assert compute_readiness(carry_server_owned(doc, forged), original=original).state == NEEDS_STRUCTURE_REVIEW

    def test_the_stored_block_survives_a_save_only_while_valid(self):
        doc, original, L = job("english")
        eid = ids(doc)[L["exp"]]
        stored = confirm_structure(set_text(doc, eid, L["edited"]), eid, user_id="u", confirmed_at=NOW, original=original)
        same = carry_server_owned(stored, copy.deepcopy(stored))
        assert same[STRUCTURE_KEY] == stored[STRUCTURE_KEY]
        client_sent = copy.deepcopy(stored); client_sent[STRUCTURE_KEY] = {"records": []}      # client tries to delete it
        assert carry_server_owned(stored, client_sent)[STRUCTURE_KEY] == stored[STRUCTURE_KEY]
        changed = set_text(stored, eid, "other")
        assert carry_server_owned(stored, changed)[STRUCTURE_KEY]["records"] == []

    def test_a_malformed_stored_block_is_not_carried(self):
        doc, original, L = job("english")
        bad = copy.deepcopy(doc); bad[STRUCTURE_KEY] = {"records": [{"item_id": "x"}]}
        assert STRUCTURE_KEY not in carry_structure_review(bad, copy.deepcopy(doc))


class TestValidation:

    def good(self):
        doc, original, L = job("english")
        eid = ids(doc)[L["exp"]]
        return confirm_structure(set_text(doc, eid, L["edited"]), eid, user_id="u", confirmed_at=NOW, original=original), eid

    def test_a_valid_block_passes_final_validation(self):
        doc, _ = self.good()
        assert validate_final(doc).ok

    @pytest.mark.parametrize("mutate", [
        lambda d, e: d.__setitem__(STRUCTURE_KEY, []),
        lambda d, e: d.__setitem__(STRUCTURE_KEY, {"records": [], "extra": 1}),
        lambda d, e: d[STRUCTURE_KEY]["records"][0].__setitem__("kind", "approved"),
        lambda d, e: d[STRUCTURE_KEY]["records"][0].__setitem__("basis_hash", "0" * 64),
        lambda d, e: d[STRUCTURE_KEY]["records"][0].__setitem__("user_id", ""),
        lambda d, e: d[STRUCTURE_KEY]["records"][0].pop("recorded_at"),
        lambda d, e: d[STRUCTURE_KEY]["records"].append(copy.deepcopy(d[STRUCTURE_KEY]["records"][0])),
    ])
    def test_malformed_blocks_are_rejected(self, mutate):
        doc, eid = self.good()
        bad = copy.deepcopy(doc)
        mutate(bad, eid)
        assert not validate_final(bad).ok

    def test_an_extraction_draft_cannot_carry_a_structure_record(self):
        doc, eid = self.good()
        assert validate_draft(doc).errors[0].code == "structure_record_not_allowed_in_draft"

    def test_the_preferred_only_confirmation_is_invalidated_by_a_structure_edit(self):
        cats = {c: [] for c in CATEGORIES}
        cats["skills"].append(raw("Docker or Podman is a plus", "preferred", ["Docker", "Podman"], cue="is a plus"))
        ai = {"scoreability": {"status": "scoreable", "reason": ""}, "categories": cats, "category_weights": {"skills": 100},
              "non_scoreable_requirements": [], "post_hiring_conditions": [], "informational_items": [], "warnings": []}
        r = process_ai_output(ai, "Requirements:\n- Docker or Podman is a plus\n")
        doc, original = r.requirements, r.original
        aid = ids(doc)["Docker or Podman is a plus"]
        confirmed = confirm_no_numeric_score(doc, user_id="u", confirmed_at=NOW, original=original)
        out, _ = save_like(confirmed, set_structure(confirmed, aid, alternatives=["Docker", "Podman", "LXC"]), original)
        assert out["scoring_confirmation"] is None
        assert compute_readiness(out, original=original).state == "needs_confirmation"
        # wording-only: the confirmation goes and the structure review must be settled first
        out2, _ = save_like(confirmed, set_text(confirmed, aid, "Docker or Podman or LXC"), original)
        assert out2["scoring_confirmation"] is None
        assert compute_readiness(out2, original=original).state == NEEDS_STRUCTURE_REVIEW
        with pytest.raises(ConfirmationError):
            confirm_no_numeric_score(out2, user_id="u", confirmed_at=NOW, original=original)
