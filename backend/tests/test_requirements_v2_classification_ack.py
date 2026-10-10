"""Requirements-v2: classification-warning acknowledgment (isolated, pure). Covers the Yes/No policy, multiple warnings,
correcting vs accepting, invalidation, server ownership (forged acknowledgments) and the invariant that Preferred items
never gain weight. English and Arabic where the extraction is involved. No model call, no database."""
from __future__ import annotations

import copy

import pytest

from services.requirements_v2 import (
    CATEGORIES, AcknowledgmentError, acknowledge_classification_warning, add_item, carry_classification_review,
    carry_confirmation, carry_server_owned, classification_status, compute_readiness, confirm_no_numeric_score,
    edited_category_names, parse_acknowledgment_policy, reconcile_classification_review, remove_item, set_importance,
    set_text, validate_draft, validate_final,
)
from services.requirements_v2 import acknowledgment as ack_module
from services.requirements_v2.extraction import process_ai_output

NOW = "2026-01-01T10:00:00Z"


def raw_item(text, source, importance="required", cue=None, category="skills"):
    return category, {"text": text, "importance": importance, "importance_cue": cue, "source_text": source,
                      "origin": "stated", "alternatives": None, "experience": None}


def extract(jd, *entries, policy=True):
    cats = {c: [] for c in CATEGORIES}
    for category, raw in entries:
        cats[category].append(raw)
    ai = {"scoreability": {"status": "scoreable", "reason": ""}, "categories": cats,
          "category_weights": {"skills": 100}, "non_scoreable_requirements": [], "post_hiring_conditions": [],
          "informational_items": [], "warnings": []}
    return process_ai_output(ai, jd, require_classification_acknowledgment=policy)


JD = "Requirements:\n- Python is required\n- SQL is a plus\n- Docker\n- Kubernetes\n"


def flagged_job(policy=True):
    """Python (required, fine), Docker (preferred, NO cue), Kubernetes (preferred, cue of another item), SQL (fine)."""
    return extract(
        JD,
        raw_item("Python", "Python is required"),
        raw_item("SQL", "SQL is a plus", "preferred", "is a plus"),                       # established inline
        raw_item("Docker", "Docker", "preferred", None),                                  # preferred_cue_missing
        raw_item("Kubernetes", "Kubernetes", "preferred", "is a plus"),                   # cue belongs to SQL
        policy=policy)


def ids_by_text(doc):
    return {i["text"]: i["id"] for c in CATEGORIES for i in doc["categories"][c]["items"]}


def wid(doc, text, code):
    return f"{ids_by_text(doc)[text]}:{code}"


def weights(doc):
    return {i["text"]: i["weight"] for c in CATEGORIES for i in doc["categories"][c]["items"]}


# ══ the policy setting ════════════════════════════════════════════════════════

class TestPolicySetting:

    @pytest.mark.parametrize("value", ["true", "TRUE", " yes ", "1", "on", True])
    def test_explicit_yes(self, value):
        assert parse_acknowledgment_policy(value) is True

    @pytest.mark.parametrize("value", ["false", "FALSE", " no ", "0", "off", False])
    def test_explicit_no(self, value):
        assert parse_acknowledgment_policy(value) is False

    @pytest.mark.parametrize("value", [None, "", "  ", "maybe", "null", 1, 0, [], {}])
    def test_missing_or_unrecognised_means_yes(self, value):
        assert parse_acknowledgment_policy(value) is True

    def test_the_configuration_key_is_wired_only_where_the_api_stage_put_it(self):
        """Wired by the editing-API stage: seeded by migration 107, audited when super_admin changes it
        (routers/platform_config.py) and read by services/requirements_api.py (through POLICY_KEY). Nowhere else."""
        import pathlib
        assert ack_module.POLICY_KEY == "job_analysis.require_classification_acknowledgment"
        backend = pathlib.Path(__file__).resolve().parent.parent
        # workers/requirements_v2_extraction_worker.py passes the approved extraction contract a constant True (extraction always requires
        # acknowledgment for classification flags); it does not read the platform setting (reviewed: requirements-v2 extraction)
        allowed = {"routers/platform_config.py", "db/migrations/107_requirements_v2_revision.sql", "workers/requirements_v2_extraction_worker.py"}
        found = set()
        for sub in ("routers", "workers", "db"):
            for path in (backend / sub).rglob("*"):
                if path.suffix in (".py", ".sql") and "require_classification_acknowledgment" in path.read_text(encoding="utf-8"):
                    found.add(path.relative_to(backend).as_posix())
        assert found == allowed, found


class TestOnlyClassificationWarnings:

    @pytest.mark.parametrize("code", ["source_text_missing", "source_text_not_found", "category_weights_unusable",
                                      "too_many_required_items", "importance_missing_defaulted_required", "other", ""])
    def test_no_other_extraction_warning_can_become_a_classification_warning(self, code):
        with pytest.raises(ValueError, match="not a classification warning"):
            ack_module.build_warning(code=code, item_id="req_a", category="skills", cue=None, source_text=None, message="m")

    def test_the_three_classification_codes_build_valid_open_warnings(self):
        for code in ack_module.CLASSIFICATION_WARNING_CODES:
            w = ack_module.build_warning(code=code, item_id="req_a", category="skills", cue="c", source_text="s", message="m")
            assert ack_module.review_block_issues({"warnings": [w], "acknowledgments": []}) == []
            assert w["id"] == f"req_a:{code}" and w["status"] == "open"

    def test_a_non_string_cue_is_stored_as_none(self):
        w = ack_module.build_warning(code="preferred_cue_missing", item_id="req_a", category="skills", cue=["x"],
                                     source_text=None, message="m")
        assert w["evidence"]["cue"] is None


# ══ extraction produces the warnings, readiness reflects the policy ═══════════

class TestExtractionIntegration:

    def test_only_classification_warnings_enter_the_review_block(self):
        r = flagged_job()
        block = r.requirements["classification_review"]
        assert sorted(w["code"] for w in block["warnings"]) == ["preferred_cue_missing", "preferred_cue_not_linked_to_item"]
        assert block["acknowledgments"] == []
        assert all(w["status"] == "open" and w["resolution"] is None for w in block["warnings"])
        # other extraction warnings (here: a proposal for a category without a required item) stay out of it
        assert all(w["code"] in ack_module.CLASSIFICATION_WARNING_CODES for w in block["warnings"])

    def test_warning_identity_and_evidence(self):
        r = flagged_job()
        docs = r.requirements
        w = next(w for w in docs["classification_review"]["warnings"] if w["code"] == "preferred_cue_not_linked_to_item")
        assert w["id"] == f"{ids_by_text(docs)['Kubernetes']}:preferred_cue_not_linked_to_item" and w["category"] == "skills"
        assert w["evidence"] == {"code": w["code"], "cue": "is a plus", "source_text": "Kubernetes"}
        assert w["message"].startswith("Needs review: ")

    def test_default_policy_blocks_readiness_and_lists_every_unresolved_item(self):
        r = flagged_job()
        assert r.readiness.state == "needs_classification_review" and not r.readiness.can_proceed
        assert sorted(i.item_id for i in r.readiness.reasons) == sorted([ids_by_text(r.requirements)["Docker"],
                                                                        ids_by_text(r.requirements)["Kubernetes"]])
        assert all(i.code == "classification_warning_unresolved" for i in r.readiness.reasons)
        assert len(r.readiness.unresolved_warning_ids) == len(r.readiness.open_warning_ids) == 2

    def test_policy_no_keeps_the_warnings_visible_but_does_not_block(self):
        r = flagged_job(policy=False)
        assert r.readiness.state == "ready" and r.readiness.can_proceed and r.readiness.scoring_mode == "weighted"
        assert len(r.readiness.open_warning_ids) == 2 and len(r.readiness.unresolved_warning_ids) == 2
        assert len(r.requirements["classification_review"]["warnings"]) == 2          # still in the document
        assert len(r.item_review) == 2                                                 # and in the per-item review

    def test_no_classification_warning_means_nothing_changes(self):
        r = extract(JD, raw_item("Python", "Python is required"), raw_item("SQL", "SQL is a plus", "preferred", "is a plus"))
        assert "classification_review" not in r.requirements and r.readiness.state == "ready"
        assert r.readiness.open_warning_ids == ()

    def test_the_extraction_draft_is_valid_and_the_original_snapshot_keeps_the_warnings(self):
        r = flagged_job()
        assert validate_draft(r.requirements).ok and validate_final(r.requirements).ok
        assert r.original["classification_review"] == r.requirements["classification_review"]
        assert r.analysis["requirements"]["classification_review"]["warnings"]

    def test_arabic_extraction_flags_and_blocks_the_same_way(self):
        jd = "المتطلبات: بايثون مطلوبة. يفضل معرفة SQL. Docker."
        r = extract(jd, raw_item("بايثون", "بايثون مطلوبة"), raw_item("SQL", "يفضل معرفة SQL", "preferred", "يفضل"),
                    raw_item("Docker", "Docker", "preferred", "يفضل"))
        assert [w["code"] for w in r.requirements["classification_review"]["warnings"]] == ["preferred_cue_not_linked_to_item"]
        assert r.readiness.state == "needs_classification_review"
        assert extract(jd, raw_item("Docker", "Docker", "preferred", "يفضل"), policy=False).readiness.state == "needs_confirmation"

    def test_other_extraction_warnings_never_affect_readiness(self):
        # an unquoted item (source_text_missing) and an ignored proposal are warnings, not classification warnings
        raw = raw_item("Python", "Python is required")[1]
        del raw["source_text"]
        r = extract(JD, ("skills", raw), raw_item("SQL", "SQL is a plus", "preferred", "is a plus"))
        assert "source_text_missing" in [i.code for i in r.review] and r.readiness.state == "ready"

    def test_raw_ai_output_and_preferred_classification_are_preserved(self):
        r = flagged_job()
        by_text = {i["text"]: i for i in r.requirements["categories"]["skills"]["items"]}
        assert by_text["Docker"]["importance"] == "preferred" and by_text["Kubernetes"]["importance"] == "preferred"
        assert r.raw_ai_output["categories"]["skills"][2]["importance_cue"] is None


# ══ acknowledging ═════════════════════════════════════════════════════════════

class TestAcknowledging:

    def test_acknowledging_every_warning_makes_the_job_ready(self):
        doc = flagged_job().requirements
        for text, code in (("Docker", "preferred_cue_missing"), ("Kubernetes", "preferred_cue_not_linked_to_item")):
            doc = acknowledge_classification_warning(doc, wid(doc, text, code), user_id="u-1", acknowledged_at=NOW)
        status = classification_status(doc)
        assert status.unresolved == () and len(status.acknowledged) == 2 and len(status.open) == 2
        ready = compute_readiness(doc)
        assert ready.state == "ready" and ready.can_proceed and ready.scoring_mode == "weighted"
        assert validate_final(doc).ok

    def test_partial_acknowledgment_keeps_blocking_and_names_only_what_is_left(self):
        doc = flagged_job().requirements
        doc = acknowledge_classification_warning(doc, wid(doc, "Docker", "preferred_cue_missing"), user_id="u-1", acknowledged_at=NOW)
        state = compute_readiness(doc)
        assert state.state == "needs_classification_review"
        assert [i.item_id for i in state.reasons] == [ids_by_text(doc)["Kubernetes"]]
        assert len(state.open_warning_ids) == 2 and len(state.unresolved_warning_ids) == 1

    def test_the_acknowledgment_records_user_time_warning_and_item_state(self):
        doc = flagged_job().requirements
        warning = wid(doc, "Docker", "preferred_cue_missing")
        doc = acknowledge_classification_warning(doc, warning, user_id="user-42", acknowledged_at=NOW)
        (a,) = doc["classification_review"]["acknowledgments"]
        assert (a["warning_id"], a["code"], a["item_id"]) == (warning, "preferred_cue_missing", ids_by_text(doc)["Docker"])
        assert a["user_id"] == "user-42" and a["acknowledged_at"] == NOW
        assert a["item_state"] == {"id": ids_by_text(doc)["Docker"], "category": "skills", "text": "Docker",
                                   "importance": "preferred", "source_text": "Docker", "alternatives": None,
                                   "experience": None}
        assert len(a["item_state_hash"]) == len(a["evidence_hash"]) == 64

    def test_acknowledging_never_changes_the_items_or_gives_a_preferred_item_weight(self):
        before = flagged_job().requirements
        doc = before
        for text, code in (("Docker", "preferred_cue_missing"), ("Kubernetes", "preferred_cue_not_linked_to_item")):
            doc = acknowledge_classification_warning(doc, wid(doc, text, code), user_id="u", acknowledged_at=NOW)
        assert doc["categories"] == before["categories"]                       # items, weights, categories: identical
        w = weights(doc)
        assert w["Docker"] is None and w["Kubernetes"] is None and w["SQL"] is None and w["Python"] == 100
        assert edited_category_names(before, doc) == []

    def test_acknowledgment_does_not_mutate_its_input(self):
        doc = flagged_job().requirements
        snapshot = copy.deepcopy(doc)
        acknowledge_classification_warning(doc, wid(doc, "Docker", "preferred_cue_missing"), user_id="u", acknowledged_at=NOW)
        assert doc == snapshot

    @pytest.mark.parametrize("kwargs", [{"user_id": "", "acknowledged_at": NOW}, {"user_id": "u", "acknowledged_at": ""},
                                        {"user_id": None, "acknowledged_at": NOW}, {"user_id": "u", "acknowledged_at": 5}])
    def test_user_and_time_are_required(self, kwargs):
        doc = flagged_job().requirements
        with pytest.raises(AcknowledgmentError):
            acknowledge_classification_warning(doc, wid(doc, "Docker", "preferred_cue_missing"), **kwargs)

    def test_unknown_resolved_and_repeated_acknowledgments_are_refused(self):
        doc = flagged_job().requirements
        with pytest.raises(AcknowledgmentError, match="unknown"):
            acknowledge_classification_warning(doc, "req_nope:preferred_cue_missing", user_id="u", acknowledged_at=NOW)
        with pytest.raises(AcknowledgmentError, match="unknown"):
            acknowledge_classification_warning({"categories": doc["categories"]}, "x", user_id="u", acknowledged_at=NOW)
        warning = wid(doc, "Docker", "preferred_cue_missing")
        acked = acknowledge_classification_warning(doc, warning, user_id="u", acknowledged_at=NOW)
        with pytest.raises(AcknowledgmentError, match="already"):
            acknowledge_classification_warning(acked, warning, user_id="other", acknowledged_at=NOW)
        corrected = set_importance(doc, ids_by_text(doc)["Docker"], "required")
        with pytest.raises(AcknowledgmentError, match="no longer applies"):
            acknowledge_classification_warning(corrected, warning, user_id="u", acknowledged_at=NOW)

    def test_acknowledging_is_possible_under_either_policy_and_policy_no_never_needs_it(self):
        doc = flagged_job(policy=False).requirements
        assert compute_readiness(doc, require_classification_acknowledgment=False).state == "ready"
        acked = acknowledge_classification_warning(doc, wid(doc, "Docker", "preferred_cue_missing"), user_id="u", acknowledged_at=NOW)
        r = compute_readiness(acked, require_classification_acknowledgment=False)
        assert r.state == "ready" and len(r.unresolved_warning_ids) == 1


# ══ correcting the classification ═════════════════════════════════════════════

class TestCorrecting:

    def test_reclassifying_to_required_makes_the_warning_inactive(self):
        doc = flagged_job().requirements
        from services.requirements_v2 import equalize_category
        doc = equalize_category(set_importance(doc, ids_by_text(doc)["Docker"], "required"), "skills")
        status = classification_status(doc)
        assert wid(doc, "Docker", "preferred_cue_missing") in status.inactive and status.resolved == ()
        assert len(status.unresolved) == 1 and compute_readiness(doc).state == "needs_classification_review"

    def test_correcting_every_flagged_item_makes_the_job_ready_after_equalizing(self):
        from services.requirements_v2 import equalize_category
        doc = flagged_job().requirements
        for text in ("Docker", "Kubernetes"):
            doc = set_importance(doc, ids_by_text(doc)[text], "required")
        doc = equalize_category(doc, "skills")
        state = compute_readiness(doc)
        assert state.state == "ready" and state.open_warning_ids == () and state.unresolved_warning_ids == ()
        assert [weights(doc)[t] for t in ("Python", "Docker", "Kubernetes")] == [34, 33, 33] and weights(doc)["SQL"] is None

    def test_removing_a_flagged_item_resolves_its_warning(self):
        doc = flagged_job().requirements
        docker = ids_by_text(doc)["Docker"]
        doc = remove_item(doc, docker)
        status = classification_status(doc)
        assert f"{docker}:preferred_cue_missing" in status.resolved and len(status.unresolved) == 1

    def test_a_mix_of_correcting_and_accepting_settles_all_warnings(self):
        doc = flagged_job().requirements
        doc = set_importance(doc, ids_by_text(doc)["Docker"], "required")
        from services.requirements_v2 import equalize_category
        doc = equalize_category(doc, "skills")
        doc = acknowledge_classification_warning(doc, wid(doc, "Kubernetes", "preferred_cue_not_linked_to_item"),
                                                 user_id="u", acknowledged_at=NOW)
        assert compute_readiness(doc).state == "ready"

    def test_reconcile_only_resolves_a_warning_permanently_when_its_item_is_removed(self):
        doc = flagged_job().requirements
        docker = ids_by_text(doc)["Docker"]
        corrected = reconcile_classification_review(set_importance(doc, docker, "required"))
        assert corrected.resolved == ()                                          # reclassified: inactive, not resolved
        stored = next(w for w in corrected.doc["classification_review"]["warnings"] if w["item_id"] == docker)
        assert stored["status"] == "open" and stored["resolution"] is None       # kept, so it can reopen
        raw = copy.deepcopy(doc)                                                   # item deleted outside remove_item
        raw["categories"]["skills"]["items"] = [i for i in raw["categories"]["skills"]["items"] if i["id"] != docker]
        removed = reconcile_classification_review(raw)
        assert removed.resolved == ((f"{docker}:preferred_cue_missing", "item_removed"),)
        assert classification_status(remove_item(doc, docker)).resolved == (f"{docker}:preferred_cue_missing",)
        assert classification_status(removed.doc).resolved == (f"{docker}:preferred_cue_missing",)


# ══ invalidation ══════════════════════════════════════════════════════════════

def acked_job():
    doc = flagged_job().requirements
    for text, code in (("Docker", "preferred_cue_missing"), ("Kubernetes", "preferred_cue_not_linked_to_item")):
        doc = acknowledge_classification_warning(doc, wid(doc, text, code), user_id="u-1", acknowledged_at=NOW)
    assert compute_readiness(doc).state == "ready"
    return doc


class TestInvalidation:

    def test_changing_the_wording_invalidates_only_that_acknowledgment(self):
        doc = acked_job()
        doc = set_text(doc, ids_by_text(doc)["Docker"], "Docker and Compose")
        state = compute_readiness(doc)
        assert state.state == "needs_classification_review"
        assert [i.item_id for i in state.reasons] == [ids_by_text(doc)["Docker and Compose"]]
        assert len(classification_status(doc).acknowledged) == 1

    def test_a_whitespace_only_wording_change_also_invalidates(self):
        doc = acked_job()
        doc = set_text(doc, ids_by_text(doc)["Docker"], "Docker ")
        assert compute_readiness(doc).state == "needs_classification_review"

    def test_changing_the_classification_drops_the_acceptance(self):
        doc = acked_job()
        docker = ids_by_text(doc)["Docker"]
        changed = set_importance(doc, docker, "required")
        assert [a["item_id"] for a in changed["classification_review"]["acknowledgments"]] == [ids_by_text(doc)["Kubernetes"]]
        assert f"{docker}:preferred_cue_missing" in classification_status(changed).inactive

    def test_changing_structured_data_or_category_invalidates(self):
        doc = acked_job()
        edited = copy.deepcopy(doc)
        next(x for x in edited["categories"]["skills"]["items"] if x["text"] == "Docker")["alternatives"] = ["Docker", "Podman"]
        assert wid(edited, "Docker", "preferred_cue_missing") in classification_status(edited).unresolved
        moved = copy.deepcopy(doc)
        item = next(x for x in moved["categories"]["skills"]["items"] if x["text"] == "Docker")
        moved["categories"]["skills"]["items"].remove(item)
        moved["categories"]["other_requirements"]["items"].append(item)
        assert wid(moved, "Docker", "preferred_cue_missing") in classification_status(moved).unresolved

    def test_changing_the_warning_evidence_invalidates(self):
        doc = acked_job()
        for w in doc["classification_review"]["warnings"]:
            if w["item_id"] == ids_by_text(doc)["Docker"]:
                w["evidence"]["cue"] = "a different cue"
        assert wid(doc, "Docker", "preferred_cue_missing") in classification_status(doc).unresolved
        assert compute_readiness(doc).state == "needs_classification_review"

    def test_unrelated_edits_do_not_invalidate(self):
        doc = acked_job()
        doc = set_text(doc, ids_by_text(doc)["Python"], "Python 3")
        from services.requirements_v2 import set_category_weight
        doc = set_category_weight(doc, "skills", 100)
        assert compute_readiness(doc).state == "ready"
        doc, new_id = add_item(doc, "domain_knowledge", "Cloud", "preferred")
        assert compute_readiness(doc).state == "ready"

    def test_reconcile_prunes_stale_acknowledgments_and_reports_them_for_audit(self):
        doc = acked_job()
        docker = ids_by_text(doc)["Docker"]
        edited = set_text(doc, docker, "Docker and Compose")
        result = reconcile_classification_review(edited)
        assert result.invalidated == ((f"{docker}:preferred_cue_missing", "item_or_evidence_changed"),)
        assert [a["item_id"] for a in result.doc["classification_review"]["acknowledgments"]] == [ids_by_text(doc)["Kubernetes"]]
        again = reconcile_classification_review(result.doc)
        assert again.invalidated == () and again.resolved == () and again.doc == result.doc

    def test_reconcile_reports_why_an_acknowledgment_was_dropped(self):
        doc = acked_job()
        docker = ids_by_text(doc)["Docker"]
        raw_required = copy.deepcopy(doc)                                         # reclassified without the helper
        next(i for i in raw_required["categories"]["skills"]["items"] if i["id"] == docker)["importance"] = "required"
        assert (f"{docker}:preferred_cue_missing", "warning_inactive") in reconcile_classification_review(raw_required).invalidated
        raw_removed = copy.deepcopy(doc)
        raw_removed["categories"]["skills"]["items"] = [i for i in raw_removed["categories"]["skills"]["items"] if i["id"] != docker]
        assert (f"{docker}:preferred_cue_missing", "warning_resolved") in reconcile_classification_review(raw_removed).invalidated

    def test_stale_acknowledgments_never_count_even_without_reconcile(self):
        doc = acked_job()
        docker = ids_by_text(doc)["Docker"]
        doc = set_text(doc, docker, "Docker, Compose")
        assert classification_status(doc).stale_acknowledgments == (f"{docker}:preferred_cue_missing",)
        assert compute_readiness(doc).state == "needs_classification_review"

    def test_reacknowledging_after_an_edit_replaces_the_old_record(self):
        doc = acked_job()
        docker = ids_by_text(doc)["Docker"]
        edited = set_text(doc, docker, "Docker and Compose")
        redone = acknowledge_classification_warning(edited, f"{docker}:preferred_cue_missing", user_id="u-2",
                                                    acknowledged_at="2026-02-02T00:00:00Z")
        mine = [a for a in redone["classification_review"]["acknowledgments"] if a["item_id"] == docker]
        assert len(mine) == 1 and mine[0]["user_id"] == "u-2" and mine[0]["item_state"]["text"] == "Docker and Compose"
        assert compute_readiness(redone).state == "ready"


# ══ server ownership ══════════════════════════════════════════════════════════

class TestServerOwnership:

    def test_a_client_supplied_acknowledgment_is_discarded(self):
        stored = flagged_job().requirements
        forged = acked_job()                                                   # a client sending its own acknowledgments
        forged_for_stored = copy.deepcopy(stored)
        forged_for_stored["classification_review"] = copy.deepcopy(forged["classification_review"])
        saved = carry_classification_review(stored, forged_for_stored)
        assert saved["classification_review"]["acknowledgments"] == []
        assert compute_readiness(saved).state == "needs_classification_review"

    def test_a_client_cannot_alter_an_existing_acknowledgment(self):
        stored = acked_job()
        tampered = copy.deepcopy(stored)
        for a in tampered["classification_review"]["acknowledgments"]:
            a["user_id"], a["acknowledged_at"] = "someone-else", "1999-01-01T00:00:00Z"
        saved = carry_classification_review(stored, tampered)
        assert {a["user_id"] for a in saved["classification_review"]["acknowledgments"]} == {"u-1"}

    def test_a_client_cannot_remove_a_warning_or_the_whole_block(self):
        stored = flagged_job().requirements
        for mutate in (lambda d: d["classification_review"]["warnings"].clear(), lambda d: d.pop("classification_review"),
                       lambda d: d.update(classification_review=None), lambda d: d.update(classification_review="resolved")):
            incoming = copy.deepcopy(stored)
            mutate(incoming)
            saved = carry_classification_review(stored, incoming)
            assert len(saved["classification_review"]["warnings"]) == 2
            assert compute_readiness(saved).state == "needs_classification_review"

    def test_a_client_cannot_mark_a_warning_resolved_or_edit_its_evidence(self):
        stored = flagged_job().requirements
        incoming = copy.deepcopy(stored)
        for w in incoming["classification_review"]["warnings"]:
            w["status"], w["resolution"] = "resolved", "reclassified"
            w["evidence"]["cue"] = "forged"
        saved = carry_classification_review(stored, incoming)
        assert all(w["status"] == "open" and w["evidence"]["cue"] != "forged" for w in saved["classification_review"]["warnings"])

    def test_a_client_cannot_invent_a_review_block_when_nothing_is_stored(self):
        stored = extract(JD, raw_item("Python", "Python is required")).requirements
        incoming = copy.deepcopy(stored)
        incoming["classification_review"] = copy.deepcopy(acked_job()["classification_review"])
        assert "classification_review" not in carry_classification_review(stored, incoming)
        assert "classification_review" not in carry_classification_review(None, incoming)

    def test_the_stored_acknowledgment_survives_a_save_when_the_items_are_unchanged(self):
        stored = acked_job()
        saved = carry_classification_review(stored, copy.deepcopy(stored))
        assert saved["classification_review"] == stored["classification_review"] and compute_readiness(saved).state == "ready"

    def test_the_stored_acknowledgment_is_dropped_when_the_client_edits_the_item(self):
        stored = acked_job()
        incoming = set_text(stored, ids_by_text(stored)["Docker"], "Docker!")
        saved = carry_classification_review(stored, incoming)
        assert len(saved["classification_review"]["acknowledgments"]) == 1
        assert compute_readiness(saved).state == "needs_classification_review"

    def test_matching_hashes_in_client_input_prove_nothing(self):
        stored = flagged_job().requirements
        forged = acknowledge_classification_warning(copy.deepcopy(stored), wid(stored, "Docker", "preferred_cue_missing"),
                                                    user_id="attacker", acknowledged_at=NOW)       # perfectly valid hashes
        saved = carry_classification_review(stored, forged)
        assert saved["classification_review"]["acknowledgments"] == []

    @pytest.mark.parametrize("bad", ["text", [1], None, 5])
    def test_non_object_input_is_returned_unchanged_without_raising(self, bad):
        assert carry_classification_review(flagged_job().requirements, bad) == bad

    @pytest.mark.parametrize("stored", [None, "x", {}, {"classification_review": "x"}, {"classification_review": {"warnings": 3}}])
    def test_unusable_stored_state_means_no_block(self, stored):
        incoming = flagged_job().requirements
        assert "classification_review" not in carry_classification_review(stored, incoming)

    def test_carry_server_owned_rebuilds_both_confirmation_and_review(self):
        stored = acked_job()
        forged = copy.deepcopy(stored)
        forged["classification_review"]["acknowledgments"].clear()
        forged["scoring_confirmation"] = {"kind": "no_numeric_score", "user_id": "x", "confirmed_at": "t", "basis_hash": "h" * 64}
        saved = carry_server_owned(stored, forged)
        assert saved["scoring_confirmation"] is None and len(saved["classification_review"]["acknowledgments"]) == 2
        assert saved["classification_review"] == stored["classification_review"]

    def test_the_extraction_draft_must_not_carry_acknowledgments(self):
        assert [e.code for e in validate_draft(acked_job()).errors] == ["acknowledgment_not_allowed_in_draft"]
        assert validate_draft(flagged_job().requirements).ok

    @pytest.mark.parametrize("mutate,code", [
        (lambda b: b["warnings"][0].update(code="other"), "bad_classification_warning"),
        (lambda b: b["warnings"][0].update(id="wrong"), "bad_classification_warning"),
        (lambda b: b["warnings"][0].update(status="open", resolution="reclassified"), "bad_classification_warning"),
        (lambda b: b["warnings"].append(copy.deepcopy(b["warnings"][0])), "duplicate_classification_warning"),
        (lambda b: b.update(extra=1), "bad_classification_review"),
        (lambda b: b["acknowledgments"][0].update(warning_id="req_zz:preferred_cue_missing"), "bad_classification_acknowledgment"),
        (lambda b: b["acknowledgments"][0].update(user_id=""), "bad_classification_acknowledgment"),
        (lambda b: b["acknowledgments"].append(copy.deepcopy(b["acknowledgments"][0])), "duplicate_classification_acknowledgment"),
    ])
    def test_a_malformed_review_block_is_rejected(self, mutate, code):
        doc = acked_job()
        mutate(doc["classification_review"])
        assert code in validate_final(doc).codes()
        assert compute_readiness(doc).state == "needs_review"


# ══ interplay with the preferred-only confirmation ════════════════════════════

class TestWithPreferredOnlyConfirmation:

    def preferred_only(self):
        return extract("Docker. Kubernetes is a plus.", raw_item("Docker", "Docker", "preferred", None),
                       raw_item("Kubernetes", "Kubernetes is a plus", "preferred", "is a plus"))

    def test_classification_is_settled_before_the_no_score_confirmation(self):
        r = self.preferred_only()
        assert r.readiness.state == "needs_classification_review"
        with pytest.raises(Exception, match="Nothing to confirm"):
            confirm_no_numeric_score(r.requirements, user_id="u", confirmed_at=NOW)
        doc = acknowledge_classification_warning(r.requirements, wid(r.requirements, "Docker", "preferred_cue_missing"),
                                                 user_id="u", acknowledged_at=NOW)
        assert compute_readiness(doc).state == "needs_confirmation"
        confirmed = confirm_no_numeric_score(doc, user_id="u", confirmed_at=NOW)
        ready = compute_readiness(confirmed)
        assert ready.state == "ready" and ready.scoring_mode == "none"
        assert all(i["weight"] is None for c in CATEGORIES for i in confirmed["categories"][c]["items"])
        assert all(confirmed["categories"][c]["weight"] == 0 for c in CATEGORIES)

    def test_policy_no_allows_the_confirmation_directly_and_keeps_the_warning_visible(self):
        r = self.preferred_only()
        confirmed = confirm_no_numeric_score(r.requirements, user_id="u", confirmed_at=NOW,
                                             require_classification_acknowledgment=False)
        ready = compute_readiness(confirmed, require_classification_acknowledgment=False)
        assert ready.state == "ready" and len(ready.unresolved_warning_ids) == 1
        assert compute_readiness(confirmed).state == "needs_classification_review"      # the strict policy still blocks

    def test_invalidating_the_acknowledgment_does_not_remove_the_confirmation_but_blocks_again(self):
        r = self.preferred_only()
        doc = acknowledge_classification_warning(r.requirements, wid(r.requirements, "Docker", "preferred_cue_missing"),
                                                 user_id="u", acknowledged_at=NOW)
        confirmed = confirm_no_numeric_score(doc, user_id="u", confirmed_at=NOW)
        edited = set_text(confirmed, ids_by_text(confirmed)["Docker"], "Docker Swarm")
        assert compute_readiness(edited).state == "needs_review"                         # confirmation basis is stale too
        saved = carry_server_owned(confirmed, edited)
        assert saved["scoring_confirmation"] is None
        assert compute_readiness(saved).state == "needs_classification_review"


# ══ preferred items never gain weight ═════════════════════════════════════════

class TestPreferredStaysUnweighted:

    def test_through_every_step_preferred_weights_stay_null_and_category_weights_sane(self):
        doc = flagged_job().requirements
        steps = [doc]
        for text, code in (("Docker", "preferred_cue_missing"), ("Kubernetes", "preferred_cue_not_linked_to_item")):
            doc = acknowledge_classification_warning(doc, wid(doc, text, code), user_id="u", acknowledged_at=NOW)
            steps.append(doc)
        steps.append(reconcile_classification_review(set_text(doc, ids_by_text(doc)["Docker"], "Docker 2")).doc)
        steps.append(carry_server_owned(steps[-1], copy.deepcopy(steps[-1])))
        for d in steps:
            for c in CATEGORIES:
                for i in d["categories"][c]["items"]:
                    assert i["importance"] != "preferred" or i["weight"] is None
            assert validate_final(d).ok and sum(d["categories"][c]["weight"] for c in CATEGORIES) == 100

    def test_acknowledging_cannot_be_used_to_attach_a_weight(self):
        doc = flagged_job().requirements
        a = acknowledge_classification_warning(doc, wid(doc, "Docker", "preferred_cue_missing"), user_id="u", acknowledged_at=NOW)
        forced = copy.deepcopy(a)
        next(i for i in forced["categories"]["skills"]["items"] if i["text"] == "Docker")["weight"] = 10
        assert "preferred_item_has_weight" in validate_final(forced).codes()
        assert compute_readiness(forced).state == "needs_review"


# ══ Preferred -> Required -> Preferred: the warning reopens, the old acknowledgment does not come back ════════

LANG = {
    "english": ("Requirements:\n- Python is required\n- Docker\n", "Python is required", "Python", "Docker"),
    "arabic": ("المتطلبات:\n- بايثون مطلوبة\n- Docker\n", "بايثون مطلوبة", "بايثون", "Docker"),
}
LANGUAGES = pytest.mark.parametrize("lang", list(LANG))
POLICIES = pytest.mark.parametrize("policy", [True, False], ids=["policy-yes", "policy-no"])


def single_warning_job(lang):
    jd, required_source, required_text, flagged = LANG[lang]
    r = extract(jd, raw_item(required_text, required_source), raw_item(flagged, flagged, "preferred", None))
    assert [w["code"] for w in r.requirements["classification_review"]["warnings"]] == ["preferred_cue_missing"]
    return r.requirements, ids_by_text(r.requirements)[flagged], flagged


def to_required(doc, item_id):
    from services.requirements_v2 import equalize_category
    return equalize_category(set_importance(doc, item_id, "required"), "skills")


def back_to_preferred(doc, item_id):
    from services.requirements_v2 import equalize_category
    return equalize_category(set_importance(doc, item_id, "preferred"), "skills")


class TestReclassificationRoundTrip:

    @LANGUAGES
    @POLICIES
    def test_without_prior_acknowledgment_the_warning_reopens(self, lang, policy):
        doc, item_id, text = single_warning_job(lang)
        warning = f"{item_id}:preferred_cue_missing"
        flagged = compute_readiness(doc, require_classification_acknowledgment=policy)
        assert flagged.state == ("needs_classification_review" if policy else "ready")

        required = to_required(doc, item_id)                                      # Preferred -> Required
        status = classification_status(required)
        assert status.inactive == (warning,) and status.open == () and status.unresolved == ()
        assert compute_readiness(required, require_classification_acknowledgment=policy).state == "ready"
        assert compute_readiness(required, require_classification_acknowledgment=policy).open_warning_ids == ()

        back = back_to_preferred(required, item_id)                               # Required -> Preferred
        status = classification_status(back)
        assert status.open == status.unresolved == (warning,) and status.inactive == ()
        state = compute_readiness(back, require_classification_acknowledgment=policy)
        if policy:
            assert state.state == "needs_classification_review" and [i.item_id for i in state.reasons] == [item_id]
        else:
            assert state.state == "ready" and state.can_proceed
            assert state.open_warning_ids == state.unresolved_warning_ids == (warning,)     # visible, not blocking

    @LANGUAGES
    @POLICIES
    def test_with_prior_acknowledgment_the_old_one_is_not_restored(self, lang, policy):
        doc, item_id, text = single_warning_job(lang)
        warning = f"{item_id}:preferred_cue_missing"
        acked = acknowledge_classification_warning(doc, warning, user_id="user-1", acknowledged_at=NOW)
        assert compute_readiness(acked, require_classification_acknowledgment=policy).state == "ready"
        assert classification_status(acked).acknowledged == (warning,)

        required = to_required(acked, item_id)                                    # Preferred -> Required
        assert required["classification_review"]["acknowledgments"] == []         # dropped at once, not parked
        assert classification_status(required).inactive == (warning,)
        assert compute_readiness(required, require_classification_acknowledgment=policy).state == "ready"

        back = back_to_preferred(required, item_id)                               # Required -> Preferred
        status = classification_status(back)
        assert status.acknowledged == () and status.unresolved == (warning,)      # NOT restored automatically
        assert back["classification_review"]["acknowledgments"] == []
        state = compute_readiness(back, require_classification_acknowledgment=policy)
        if policy:
            assert state.state == "needs_classification_review"                   # fresh acknowledgment required
        else:
            assert state.state == "ready" and state.unresolved_warning_ids == (warning,)

    @LANGUAGES
    def test_a_fresh_acknowledgment_after_the_round_trip_is_a_new_record(self, lang):
        doc, item_id, text = single_warning_job(lang)
        warning = f"{item_id}:preferred_cue_missing"
        acked = acknowledge_classification_warning(doc, warning, user_id="user-1", acknowledged_at=NOW)
        back = back_to_preferred(to_required(acked, item_id), item_id)
        assert compute_readiness(back).state == "needs_classification_review"
        fresh = acknowledge_classification_warning(back, warning, user_id="user-2", acknowledged_at="2026-03-03T00:00:00Z")
        (record,) = fresh["classification_review"]["acknowledgments"]
        assert record["user_id"] == "user-2" and record["acknowledged_at"] == "2026-03-03T00:00:00Z"
        assert record["item_state"]["importance"] == "preferred" and record["item_state"]["text"] == text
        assert compute_readiness(fresh).state == "ready"

    @LANGUAGES
    @POLICIES
    def test_the_warning_evidence_and_the_original_ai_answer_survive_the_round_trip(self, lang, policy):
        doc, item_id, text = single_warning_job(lang)
        before = copy.deepcopy(doc["classification_review"]["warnings"])
        back = back_to_preferred(to_required(doc, item_id), item_id)
        assert back["classification_review"]["warnings"] == before                # evidence and message untouched
        r = extract(*[LANG[lang][0]], raw_item(LANG[lang][2], LANG[lang][1]), raw_item(text, text, "preferred", None))
        assert r.raw_ai_output["categories"]["skills"][1]["importance"] == "preferred"
        assert r.original["categories"]["skills"]["items"][1]["importance"] == "preferred"

    @LANGUAGES
    @POLICIES
    def test_preferred_weights_stay_null_and_required_weighting_follows_the_normal_rules(self, lang, policy):
        doc, item_id, text = single_warning_job(lang)
        required = to_required(doc, item_id)
        assert sorted(i["weight"] for i in required["categories"]["skills"]["items"]) == [50, 50]
        back = back_to_preferred(required, item_id)
        assert weights(back)[text] is None
        assert sorted(w for w in weights(back).values() if w) == [100]
        assert validate_final(back).ok
        # without the explicit equalize the leftover required weight is unbalanced, and that is reported, not hidden
        raw_back = set_importance(required, item_id, "preferred")
        assert not validate_final(raw_back).ok
        assert compute_readiness(back, require_classification_acknowledgment=False).state == "ready"

    @LANGUAGES
    @POLICIES
    def test_the_round_trip_through_real_saves_behaves_the_same(self, lang, policy):
        """Every step goes through the server path: carry_server_owned(stored, incoming) as an API save would."""
        doc, item_id, text = single_warning_job(lang)
        warning = f"{item_id}:preferred_cue_missing"
        stored = acknowledge_classification_warning(doc, warning, user_id="user-1", acknowledged_at=NOW)

        # the client edits ONLY the item (it also sends back whatever block it holds -- forged or stale, it is discarded)
        edited = set_importance(copy.deepcopy(stored), item_id, "required")
        edited["classification_review"] = copy.deepcopy(stored["classification_review"])
        stored = carry_server_owned(stored, to_required(edited, item_id))
        assert stored["classification_review"]["acknowledgments"] == []
        assert classification_status(stored).inactive == (warning,)

        incoming = back_to_preferred(copy.deepcopy(stored), item_id)
        incoming["classification_review"] = copy.deepcopy(acknowledge_classification_warning(
            doc, warning, user_id="forger", acknowledged_at=NOW)["classification_review"])      # forged acknowledgment
        stored = carry_server_owned(stored, incoming)
        assert stored["classification_review"]["acknowledgments"] == []
        assert classification_status(stored).unresolved == (warning,)
        state = compute_readiness(stored, require_classification_acknowledgment=policy)
        assert state.state == ("needs_classification_review" if policy else "ready")

    @LANGUAGES
    def test_net_unchanged_state_between_two_saves_keeps_the_stored_acknowledgment(self, lang):
        """No save happened while the item was Required, so nothing changed: the stored acceptance still matches."""
        doc, item_id, text = single_warning_job(lang)
        stored = acknowledge_classification_warning(doc, f"{item_id}:preferred_cue_missing", user_id="u", acknowledged_at=NOW)
        saved = carry_server_owned(stored, copy.deepcopy(stored))
        assert saved["classification_review"] == stored["classification_review"]
        assert compute_readiness(saved).state == "ready"

    @LANGUAGES
    @POLICIES
    def test_the_cycle_can_repeat_and_each_time_needs_a_fresh_decision(self, lang, policy):
        doc, item_id, text = single_warning_job(lang)
        warning = f"{item_id}:preferred_cue_missing"
        for n in range(3):
            doc = acknowledge_classification_warning(doc, warning, user_id=f"u{n}", acknowledged_at=NOW)
            assert compute_readiness(doc, require_classification_acknowledgment=policy).state == "ready"
            doc = back_to_preferred(to_required(doc, item_id), item_id)
            assert classification_status(doc).unresolved == (warning,)
            assert (compute_readiness(doc, require_classification_acknowledgment=policy).state
                    == ("needs_classification_review" if policy else "ready"))


class TestRemovalMakesTheWarningInactive:

    @LANGUAGES
    @POLICIES
    @pytest.mark.parametrize("acknowledged", [False, True], ids=["unacknowledged", "acknowledged"])
    def test_a_removed_item_has_no_active_warning_and_blocks_nothing(self, lang, policy, acknowledged):
        doc, item_id, text = single_warning_job(lang)
        warning = f"{item_id}:preferred_cue_missing"
        if acknowledged:
            doc = acknowledge_classification_warning(doc, warning, user_id="u", acknowledged_at=NOW)
        removed = remove_item(doc, item_id)
        assert removed["classification_review"]["acknowledgments"] == []
        status = classification_status(removed)
        assert status.resolved == (warning,) and status.open == () and status.unresolved == ()
        assert removed["classification_review"]["warnings"][0]["status"] == "resolved"
        assert removed["classification_review"]["warnings"][0]["resolution"] == "item_removed"
        state = compute_readiness(removed, require_classification_acknowledgment=policy)
        assert state.state == "ready" and state.open_warning_ids == ()

    @LANGUAGES
    def test_adding_the_item_back_creates_a_new_item_without_the_old_warning(self, lang):
        doc, item_id, text = single_warning_job(lang)
        removed = remove_item(doc, item_id)
        readded, new_id = add_item(removed, "skills", text, "preferred",
                                   reserved_ids=[item_id])
        assert new_id != item_id
        status = classification_status(readded)
        assert status.open == () and status.unresolved == () and status.resolved == (f"{item_id}:preferred_cue_missing",)
        assert compute_readiness(readded).state == "ready"

    @LANGUAGES
    def test_a_resolved_warning_cannot_be_acknowledged_or_reopened_by_the_client(self, lang):
        doc, item_id, text = single_warning_job(lang)
        warning = f"{item_id}:preferred_cue_missing"
        removed = remove_item(doc, item_id)
        with pytest.raises(AcknowledgmentError):
            acknowledge_classification_warning(removed, warning, user_id="u", acknowledged_at=NOW)
        incoming = copy.deepcopy(removed)
        incoming["classification_review"]["warnings"][0].update(status="open", resolution=None)       # client "reopens"
        saved = carry_classification_review(removed, incoming)
        assert saved["classification_review"]["warnings"][0]["status"] == "resolved"
