"""Server-side three-way decisions for applying a re-analysis candidate by merge. PURE: no database, no model, no pipeline import.

The draft (what the recruiter has, possibly edited) and the candidate (the new analysis) are compared item by item. Items are matched by their
normalised wording within a category; when a wording occurs more than once, the n-th occurrence on each side is paired with the n-th on the other.
Item ids differ between two extractions, so a matched pair is one logical requirement. Every difference needs an explicit decision, and the merged
document is built here, on the server, from those decisions. The client never supplies the merged document.

Matched items that differ (importance, weight, alternatives or experience) are conflicts: the recruiter chooses 'draft' or 'candidate'. Draft items
the candidate does not contain are 'keep' or 'drop'. Candidate items the draft does not contain are 'add' or 'skip'. A category weight that differs is
'draft' or 'candidate'. A merged document is only built when every decision is present and valid.

Merged items keep the CANDIDATE's item id whenever the item is one the candidate contains, so the candidate's review findings stay attached to the
same items. A draft-only item that is kept keeps its draft id: the candidate's findings never referred to it.
"""
from __future__ import annotations

import copy
import json
from typing import Any

CATEGORIES = ("skills", "experience", "education", "certifications", "soft_skills", "domain_knowledge", "other_requirements")
FIELDS = ("importance", "weight", "alternatives", "experience")
CONFLICT_CHOICES = ("draft", "candidate")
DRAFT_ONLY_CHOICES = ("keep", "drop")
CANDIDATE_ONLY_CHOICES = ("add", "skip")
WEIGHT_CHOICES = ("draft", "candidate")


class MergeError(ValueError):
    """Decisions missing or invalid. `missing` lists the keys that need a decision; `invalid` lists the keys with an unknown value."""

    def __init__(self, missing: list[str], invalid: list[str]):
        super().__init__("merge decisions are incomplete")
        self.missing, self.invalid = missing, invalid


def norm(text: Any) -> str:
    return " ".join(str(text or "").casefold().split())


def _same(a: dict, b: dict) -> bool:
    return all(json.dumps(a.get(f), sort_keys=True) == json.dumps(b.get(f), sort_keys=True) for f in FIELDS)


def _differing(a: dict, b: dict) -> list[str]:
    return [f for f in FIELDS if json.dumps(a.get(f), sort_keys=True) != json.dumps(b.get(f), sort_keys=True)]


def _pairs(draft: dict, candidate: dict) -> list[dict]:
    """Every decision point of one category, in draft order, then candidate-only items. Each carries its key and what kind it is."""
    out: list[dict] = []
    seen: dict[str, int] = {}
    cand_by: dict[str, list[dict]] = {}
    for item in candidate.get("items", []):
        cand_by.setdefault(norm(item.get("text")), []).append(item)
    used: dict[str, int] = {}
    for item in draft.get("items", []):
        k = norm(item.get("text"))
        n = seen.get(k, 0)
        seen[k] = n + 1
        peers = cand_by.get(k, [])
        if n < len(peers):
            used[k] = max(used.get(k, 0), n + 1)
            out.append({"kind": "matched", "key_base": k, "occurrence": n, "draft": item, "candidate": peers[n]})
        else:
            out.append({"kind": "draft_only", "key_base": k, "occurrence": n, "draft": item, "candidate": None})
    for k, peers in cand_by.items():
        for n in range(used.get(k, 0), len(peers)):
            out.append({"kind": "candidate_only", "key_base": k, "occurrence": n, "draft": None, "candidate": peers[n]})
    return out


def _key(category: str, point: dict) -> str:
    return f"{point['kind']}:{category}:{point['key_base']}:{point['occurrence']}"


def plan(draft: dict, candidate: dict) -> dict:
    """What the recruiter must decide. Pure and deterministic: the same two documents always give the same keys."""
    conflicts, draft_only, candidate_only, weights = [], [], [], []
    for c in CATEGORIES:
        dc, cc = draft["categories"][c], candidate["categories"][c]
        if dc.get("weight") != cc.get("weight"):
            weights.append({"key": f"weight:{c}", "category": c, "draft": dc.get("weight"), "candidate": cc.get("weight")})
        for point in _pairs(dc, cc):
            if point["kind"] == "matched":
                if not _same(point["draft"], point["candidate"]):
                    conflicts.append({"key": _key(c, point), "category": c, "text": point["candidate"].get("text"),
                                      "fields": _differing(point["draft"], point["candidate"]),
                                      "draft": {f: point["draft"].get(f) for f in FIELDS}, "candidate": {f: point["candidate"].get(f) for f in FIELDS}})
            elif point["kind"] == "draft_only":
                draft_only.append({"key": _key(c, point), "category": c, "text": point["draft"].get("text"),
                                   "origin": point["draft"].get("origin")})
            else:
                candidate_only.append({"key": _key(c, point), "category": c, "text": point["candidate"].get("text"),
                                       "origin": point["candidate"].get("origin")})
    return {"conflicts": conflicts, "draft_only": draft_only, "candidate_only": candidate_only, "weights": weights}


def build(draft: dict, candidate: dict, resolutions: dict[str, str]) -> tuple[dict, list[str]]:
    """The merged document for the given decisions, and the wording of every draft item that is kept (for the server's own checks).
    Raises MergeError when a decision is missing or unknown."""
    decided = plan(draft, candidate)
    allowed = {**{x["key"]: CONFLICT_CHOICES for x in decided["conflicts"]},
               **{x["key"]: DRAFT_ONLY_CHOICES for x in decided["draft_only"]},
               **{x["key"]: CANDIDATE_ONLY_CHOICES for x in decided["candidate_only"]},
               **{x["key"]: WEIGHT_CHOICES for x in decided["weights"]}}
    missing = [k for k in allowed if k not in resolutions]
    invalid = [k for k, v in resolutions.items() if k not in allowed or v not in allowed[k]]
    if missing or invalid:
        raise MergeError(sorted(missing), sorted(invalid))
    merged = copy.deepcopy(candidate)
    kept_wording: list[str] = []
    for c in CATEGORIES:
        dc, cc = draft["categories"][c], candidate["categories"][c]
        if f"weight:{c}" in resolutions:
            merged["categories"][c]["weight"] = dc.get("weight") if resolutions[f"weight:{c}"] == "draft" else cc.get("weight")
        items: list[dict] = []
        for point in _pairs(dc, cc):
            if point["kind"] == "matched":
                cand_item = copy.deepcopy(point["candidate"])
                if _same(point["draft"], point["candidate"]) or resolutions[_key(c, point)] == "candidate":
                    items.append(cand_item)
                else:                                                   # the draft's content, under the candidate's id
                    item = copy.deepcopy(point["draft"])
                    item["id"] = point["candidate"]["id"]
                    items.append(item)
            elif point["kind"] == "draft_only":
                if resolutions[_key(c, point)] == "keep":
                    items.append(copy.deepcopy(point["draft"]))
                    kept_wording.append(point["draft"].get("text") or "")
            else:
                if resolutions[_key(c, point)] == "add":
                    items.append(copy.deepcopy(point["candidate"]))
        merged["categories"][c]["items"] = items
    return merged, kept_wording
