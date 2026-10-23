"""
Mandatory Gates (eligibility criteria) on Requirements v2 items.

A gate is a Required item that a candidate must satisfy to be eligible, whatever the numeric score is. It is NOT a second requirements
system: the item keeps its category, weight and wording and stays in normal required-item scoring. The gate only adds how eligibility
is verified and what happens when the check fails.

    item["gate_suggestion"]  SERVER-OWNED. Set only by the extraction when the job description's own wording is explicit
                             (see extraction/gate_cues.py). It enforces nothing and is kept unchanged in the stored original, so the
                             suggestion can be compared with what a person confirmed.
    item["gate"]             The confirmed gate. Created only by set_gate() for an authenticated admin or HR manager; the user and the
                             time are taken on the server. Only Required items can carry one.

Gate rules (validate_final):
  gate_on_preferred_item                a Preferred item cannot be a gate
  gate_without_verification_method      a gate needs one of cv_evidence, candidate_question, assessment, manual
  gate_question_link_missing            a candidate_question gate names the knockout question that verifies it
  gate_contradictory_configuration      a question is linked to a gate whose method is not candidate_question
  gate_question_linked_twice            one question may verify only one requirement
Rules that need the database (the question exists, belongs to this job, has valid passing criteria and is itself an eligibility gate)
are checked at save time by verify_question_links() in services/requirements_edit.py.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Iterable

from services.requirements_v2.contract import CATEGORIES, IMPORTANCE_PREFERRED, IMPORTANCE_REQUIRED, Issue

GATE_KEY = "gate"
SUGGESTION_KEY = "gate_suggestion"

VERIFICATION_METHODS = ("cv_evidence", "candidate_question", "assessment", "manual")
# The failure action is a LEGACY field. The outcome of a gate is decided from the evidence alone (services/eligibility_service.py), so a
# stored value is never read for a decision, never returned by the API and never offered in the UI. It stays valid in a stored document so
# that documents written before this rule remain readable, and new gates keep writing the canonical value so older code can still read them.
LEGACY_FAILURE_ACTIONS = ("not_eligible", "hold_for_review")
DEFAULT_VERIFICATION_METHOD = "cv_evidence"
DEFAULT_FAILURE_ACTION = "not_eligible"

GATE_REQUIRED_FIELDS = frozenset({"mandatory", "verification_method", "linked_question_id", "confirmed_by", "confirmed_at"})
GATE_OPTIONAL_FIELDS = frozenset({"failure_action"})
GATE_FIELDS = GATE_REQUIRED_FIELDS | GATE_OPTIONAL_FIELDS
SUGGESTION_FIELDS = frozenset({"suggested", "cue", "source_text"})
SUGGESTION_CUE_LIMIT = 200

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def is_uuid(value: Any) -> bool:
    return isinstance(value, str) and bool(_UUID_RE.match(value))


def shape_issues(item: dict, category: str, iid: str | None) -> list[Issue]:
    """Malformed gate or suggestion blocks on one item (structure level: only the shape and the enumerations)."""
    out: list[Issue] = []

    def bad(code: str, message: str) -> None:
        out.append(Issue(code, message, category=category, item_id=iid))

    if SUGGESTION_KEY in item and item[SUGGESTION_KEY] is not None:
        s = item[SUGGESTION_KEY]
        if (not isinstance(s, dict) or set(s) != SUGGESTION_FIELDS or s.get("suggested") is not True
                or not isinstance(s.get("cue"), str) or not s["cue"].strip() or len(s["cue"]) > SUGGESTION_CUE_LIMIT
                or (s.get("source_text") is not None and not isinstance(s.get("source_text"), str))):
            bad("bad_gate_suggestion", "gate_suggestion must be {suggested: true, cue, source_text}.")
    if GATE_KEY in item and item[GATE_KEY] is not None:
        g = item[GATE_KEY]
        if not isinstance(g, dict) or not GATE_REQUIRED_FIELDS <= set(g) <= GATE_FIELDS:
            bad("bad_gate", f"gate must be an object with these fields: {sorted(GATE_REQUIRED_FIELDS)} (and optionally a legacy failure_action).")
            return out
        if g["mandatory"] is not True:
            bad("bad_gate", "a stored gate is always mandatory; clear it instead.")
        method = g["verification_method"]
        if method is not None and method not in VERIFICATION_METHODS:
            bad("bad_gate_verification_method", f"verification_method must be one of {list(VERIFICATION_METHODS)}.")
        if "failure_action" in g and g["failure_action"] not in LEGACY_FAILURE_ACTIONS:
            bad("bad_gate_failure_action", f"failure_action must be one of {list(LEGACY_FAILURE_ACTIONS)} when present.")
        if g["linked_question_id"] is not None and not is_uuid(g["linked_question_id"]):
            bad("bad_gate_question_link", "linked_question_id must be a question id or null.")
        if not all(isinstance(g[k], str) and g[k] for k in ("confirmed_by", "confirmed_at")):
            bad("bad_gate", "a gate carries the user and the time that confirmed it.")
    return out


def rule_issues(doc: dict) -> list[Issue]:
    """Gate rules that need only the document. Requires a structurally valid document."""
    out: list[Issue] = []
    linked: dict[str, str] = {}
    for category in CATEGORIES:
        for item in doc["categories"][category]["items"]:
            gate = item.get(GATE_KEY)
            if gate is None:
                continue
            iid = item["id"]

            def bad(code: str, message: str) -> None:
                out.append(Issue(code, message, category=category, item_id=iid))

            if item["importance"] == IMPORTANCE_PREFERRED:
                bad("gate_on_preferred_item", "Only a Required item can be a Mandatory Gate; a Preferred item cannot.")
            method, link = gate["verification_method"], gate["linked_question_id"]
            if method is None:
                bad("gate_without_verification_method", "A Mandatory Gate needs a verification method.")
            elif method == "candidate_question" and link is None:
                bad("gate_question_link_missing", "A candidate-question gate must name the knockout question that verifies it.")
            elif method != "candidate_question" and link is not None:
                bad("gate_contradictory_configuration",
                    "A knockout question is linked, but the verification method is not 'candidate question'.")
            if link is not None:
                key = link.lower()
                if key in linked and linked[key] != iid:
                    bad("gate_question_linked_twice", "One knockout question can verify only one requirement.")
                linked.setdefault(key, iid)
    return out


def iter_items(doc: dict) -> Iterable[tuple[str, dict]]:
    for category in CATEGORIES:
        for item in doc["categories"][category]["items"]:
            yield category, item


def confirmed_gates(doc: dict) -> list[dict]:
    """The enforced gates of a document: [{item_id, category, text, verification_method, linked_question_id}]. A
    suggestion is never included, and a gate on a Preferred item (which validate_final refuses) is never included either."""
    out = []
    for category, item in iter_items(doc):
        gate = item.get(GATE_KEY)
        if gate is None or item["importance"] != IMPORTANCE_REQUIRED or gate.get("verification_method") is None:
            continue
        out.append({"item_id": item["id"], "category": category, "text": item["text"],
                    "verification_method": gate["verification_method"],
                    "linked_question_id": gate["linked_question_id"]})
    return out


def linked_question_ids(doc: dict) -> dict[str, str]:
    """{question id (lower case): item id} for every gate that names a question."""
    return {g["linked_question_id"].lower(): g["item_id"] for g in confirmed_gates(doc) if g["linked_question_id"]}


def set_gate(doc: dict, item_id: str, *, verification_method: str | None,
             linked_question_id: str | None, user_id: str, confirmed_at: str) -> dict:
    """A new document in which the item is a confirmed Mandatory Gate. The user and the time are the caller's (server) values. Raises
    KeyError for an unknown item and ValueError for a Preferred item or an unusable value. The document-level rules (a missing method
    or link, a contradiction) are left to validate_final, so a save reports them with their stable codes."""
    if verification_method is not None and verification_method not in VERIFICATION_METHODS:
        raise ValueError(f"verification_method must be one of {list(VERIFICATION_METHODS)}")
    if linked_question_id is not None and not is_uuid(linked_question_id):
        raise ValueError("linked_question_id must be a question id")
    out = copy.deepcopy(doc)
    for _, item in iter_items(out):
        if item["id"] == item_id:
            if item["importance"] != IMPORTANCE_REQUIRED:
                raise ValueError("only a Required item can be a Mandatory Gate")
            previous = item.get(GATE_KEY) or {}
            item[GATE_KEY] = {"mandatory": True, "verification_method": verification_method,
                              # a legacy value already stored is kept as it was; it is never read for a decision
                              "failure_action": previous.get("failure_action", DEFAULT_FAILURE_ACTION),
                              "linked_question_id": linked_question_id.lower() if linked_question_id else None,
                              "confirmed_by": user_id, "confirmed_at": confirmed_at}
            return out
    raise KeyError(item_id)


def clear_gate(doc: dict, item_id: str) -> dict:
    out = copy.deepcopy(doc)
    for _, item in iter_items(out):
        if item["id"] == item_id:
            item.pop(GATE_KEY, None)
            return out
    raise KeyError(item_id)


def drop_gate_if_not_required(item: dict) -> None:
    """Used when an item stops being Required: a Preferred item cannot keep a gate."""
    if item.get("importance") != IMPORTANCE_REQUIRED:
        item.pop(GATE_KEY, None)


def gate_summary(item: dict) -> tuple | None:
    """What counts as a change to a gate for the audit trail (never who or when)."""
    g = item.get(GATE_KEY)
    return None if g is None else (g["verification_method"], g["linked_question_id"])
