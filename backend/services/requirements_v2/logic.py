"""How one criterion's wording combines: alternatives, parts that must all be met, examples, review-only alternatives and flagged ambiguity.

An item may carry an optional `logic` block next to its wording (pure: plain dicts, no I/O, no model; nothing here names a tool, a
qualification, an industry or a language):

    logic = {
      "options":   None | [OPTION, ...],   # >= 2. ANY ONE direct option satisfies the criterion (OR)
      "examples":  None | [str, ...],      # illustrative only ("such as ..."): never required, never an alternative, never scored
      "ambiguity": None | {"note": str},   # a meaningful ambiguity the AI flagged for the recruiter; it is never resolved by guessing
    }
    OPTION = {
      "id": "o1",                          # code-generated, unique inside the item
      "text": str,                         # the option as the recruiter reads it
      "acceptance": "direct" | "review",   # direct: meeting it satisfies the criterion. review: it is accepted ONLY after a person decides
      "all_of": None | [str, ...],         # >= 2 parts that must ALL be met for THIS option (AND inside one option)
    }

Reading it: the criterion is satisfied when one DIRECT option is satisfied; an option is satisfied when all its parts are. A REVIEW option that
is satisfied never satisfies the criterion on its own: it routes the case to a person. Independent obligations are separate items, not options.
A criterion has ONE weight however many options it lists: meeting several options earns nothing extra.

`alternatives` (the older field) mirrors the option texts, so everything that already reads it keeps working; items without `logic` are
unchanged. An item that has no options keeps whatever `alternatives` it had.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

ACCEPT_DIRECT = "direct"
ACCEPT_REVIEW = "review"
ACCEPTANCES = (ACCEPT_DIRECT, ACCEPT_REVIEW)

LOGIC_KEYS = frozenset({"options", "examples", "ambiguity"})
OPTION_KEYS = frozenset({"id", "text", "acceptance", "all_of"})
MAX_OPTIONS = 12
MAX_PARTS = 8
MAX_EXAMPLES = 12
MAX_TEXT = 300
MAX_NOTE = 500
OPTION_ID_PREFIX = "o"


def option_id(position: int) -> str:
    return f"{OPTION_ID_PREFIX}{position + 1}"


def _text(value: Any, limit: int = MAX_TEXT) -> str | None:
    return " ".join(value.split())[:limit] if isinstance(value, str) and value.strip() else None


def normalize(raw: Any) -> tuple[dict | None, list[str]]:
    """(a well-formed logic block or None, notes about what was dropped). Used for the AI's output and for a recruiter's edit: ids are
    assigned here, in order; anything unusable is dropped and reported, never repaired by guessing."""
    notes: list[str] = []
    if raw is None:
        return None, notes
    if not isinstance(raw, dict):
        return None, ["logic is not an object; ignored"]
    extra = sorted(set(raw) - LOGIC_KEYS)
    if extra:
        notes.append(f"unexpected logic fields ignored: {extra}")

    options: list[dict] | None = None
    raw_options = raw.get("options")
    if raw_options is not None:
        built: list[dict] = []
        if isinstance(raw_options, list):
            for entry in raw_options[:MAX_OPTIONS]:
                if isinstance(entry, str):
                    entry = {"text": entry}
                text = _text(entry.get("text")) if isinstance(entry, dict) else None
                if text is None:
                    notes.append("an option without text was dropped")
                    continue
                acceptance = entry.get("acceptance", ACCEPT_DIRECT)
                if acceptance not in ACCEPTANCES:
                    notes.append(f"option acceptance {acceptance!r} is not valid; it needs a person's review")
                    acceptance = ACCEPT_REVIEW              # an unreadable acceptance never becomes automatic
                parts = entry.get("all_of")
                clean_parts: list[str] | None = None
                if parts is not None:
                    cleaned = [p for p in (_text(x) for x in parts[:MAX_PARTS]) if p] if isinstance(parts, list) else []
                    if len(cleaned) >= 2:
                        clean_parts = cleaned
                    else:
                        notes.append("all_of needs at least two parts; the option was kept as one condition")
                built.append({"text": text, "acceptance": acceptance, "all_of": clean_parts})
        if len(built) >= 2:
            options = [{"id": option_id(i), **o} for i, o in enumerate(built)]
            if not any(o["acceptance"] == ACCEPT_DIRECT for o in options):
                # Nothing could ever be met automatically: the options were probably all marked review by mistake. Keep them as direct
                # and say so, rather than a criterion nobody can satisfy.
                for o in options:
                    o["acceptance"] = ACCEPT_DIRECT
                notes.append("no option was direct; all options were set to direct for the recruiter to review")
        elif raw_options:
            notes.append("options need at least two entries; the wording stays in the item text")

    examples: list[str] | None = None
    raw_examples = raw.get("examples")
    if raw_examples is not None:
        cleaned = [e for e in (_text(x) for x in raw_examples[:MAX_EXAMPLES]) if e] if isinstance(raw_examples, list) else []
        examples = cleaned or None

    ambiguity = None
    raw_ambiguity = raw.get("ambiguity")
    if raw_ambiguity is not None:
        note = _text(raw_ambiguity.get("note") if isinstance(raw_ambiguity, dict) else raw_ambiguity, MAX_NOTE)
        ambiguity = {"note": note} if note else None

    if options is None and examples is None and ambiguity is None:
        return None, notes
    return {"options": options, "examples": examples, "ambiguity": ambiguity}, notes


def shape_problems(logic: Any, item: dict) -> list[tuple[str, str]]:
    """(code, message) for a stored logic block that is malformed or disagrees with the item's `alternatives`. [] when fine or absent."""
    if logic is None:
        return []
    if not isinstance(logic, dict) or set(logic) != LOGIC_KEYS:
        return [("bad_logic", f"logic must be an object with exactly {sorted(LOGIC_KEYS)}.")]
    out: list[tuple[str, str]] = []
    options, examples, ambiguity = logic["options"], logic["examples"], logic["ambiguity"]
    if options is None and examples is None and ambiguity is None:
        out.append(("bad_logic", "logic must carry options, examples or an ambiguity note."))
    if options is not None:
        if not isinstance(options, list) or not 2 <= len(options) <= MAX_OPTIONS:
            return out + [("bad_logic_options", f"options must be null or a list of 2 to {MAX_OPTIONS} options.")]
        seen: set[str] = set()
        for o in options:
            ok = (isinstance(o, dict) and set(o) == OPTION_KEYS and isinstance(o["id"], str) and o["id"] not in seen
                  and isinstance(o["text"], str) and o["text"].strip() and len(o["text"]) <= MAX_TEXT and o["acceptance"] in ACCEPTANCES
                  and (o["all_of"] is None or (isinstance(o["all_of"], list) and 2 <= len(o["all_of"]) <= MAX_PARTS
                                              and all(isinstance(p, str) and p.strip() and len(p) <= MAX_TEXT for p in o["all_of"]))))
            if not ok:
                return out + [("bad_logic_options", "an option is malformed (id, text, acceptance or all_of).")]
            seen.add(o["id"])
        if not any(o["acceptance"] == ACCEPT_DIRECT for o in options):
            out.append(("bad_logic_options", "at least one option must be direct, or nothing could ever satisfy the criterion."))
        if item.get("alternatives") != [o["text"] for o in options]:
            out.append(("logic_alternatives_mismatch", "alternatives must list the option texts, in order."))
    if examples is not None and (not isinstance(examples, list) or not 1 <= len(examples) <= MAX_EXAMPLES
                                 or not all(isinstance(e, str) and e.strip() and len(e) <= MAX_TEXT for e in examples)):
        out.append(("bad_logic_examples", f"examples must be null or 1 to {MAX_EXAMPLES} non-empty texts."))
    if ambiguity is not None and (not isinstance(ambiguity, dict) or set(ambiguity) != {"note"}
                                  or not isinstance(ambiguity["note"], str) or not ambiguity["note"].strip()):
        out.append(("bad_logic_ambiguity", "ambiguity must be null or {'note': text}."))
    return out


def apply(item: dict, logic: dict | None) -> dict:
    """The item with `logic` set and `alternatives` mirroring its options (an item without options keeps its alternatives)."""
    out = copy.deepcopy(item)
    if logic is None:
        out.pop("logic", None)
        return out
    out["logic"] = copy.deepcopy(logic)
    if logic.get("options"):
        out["alternatives"] = [o["text"] for o in logic["options"]]
    return out


def has_options(item: dict) -> bool:
    logic = item.get("logic")
    return isinstance(logic, dict) and bool(logic.get("options"))


def has_ambiguity(item: dict) -> bool:
    logic = item.get("logic")
    return isinstance(logic, dict) and bool(logic.get("ambiguity"))


def leaves(logic: dict) -> list[dict]:
    """What the evaluator must assess, in order: one leaf per option, or one per part of an option that has parts.
    {"leaf_id": "o1" | "o1.2", "option_id", "part": None | int (zero-based), "text", "acceptance"}."""
    out: list[dict] = []
    for o in logic.get("options") or []:
        if o.get("all_of"):
            for index, part in enumerate(o["all_of"]):
                out.append({"leaf_id": f"{o['id']}.{index + 1}", "option_id": o["id"], "part": index, "text": part, "acceptance": o["acceptance"]})
        else:
            out.append({"leaf_id": o["id"], "option_id": o["id"], "part": None, "text": o["text"], "acceptance": o["acceptance"]})
    return out


def digest(logic: dict | None) -> str | None:
    if logic is None:
        return None
    return hashlib.sha256(json.dumps(logic, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
