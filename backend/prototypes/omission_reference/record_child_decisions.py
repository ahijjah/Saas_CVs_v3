"""Writes the reviewer's confirmed CHILD-unit decisions to a separate submission file (one per parent). Offline; touches no other file.

Each decision is validated against review_children.csv: the child id must exist, the role must be one of the six choices, and the
decision is stored with the child's exact offsets. The S010 scope claim is checked from exact offsets before anything is written: the
children named must cover the statement exactly, and no other child may lie inside it. A submission is never overwritten.

Usage: python record_child_decisions.py --parent RQ-029
"""
from __future__ import annotations

import argparse
import csv
import pathlib

import build_children as BC

HERE = pathlib.Path(__file__).resolve().parent
REVIEWER = "Asaad Hijjah"
DATE = "2026-10-15"
ROLES = ["scoreable requirement", "non-scoreable condition", "post-hiring condition", "informational benefit", "background", "uncertain"]
FIELDS = ["child_id", "parent_rq_id", "parent_unit_id", "jd_id", "start", "end", "content_start", "content_end", "child_text",
          "confirmed_role", "reviewer", "confirmed_date", "reason", "status", "obligation", "judgment_scope_confirmed"]
CHILD_BATCHES = {
    "RQ-029": {"file": "CHILD_CONFIRMED_queue_Asaad-Hijjah_2026-10-15_RQ-029.csv", "decisions": [
        ("RQ-029/C01", "non-scoreable condition", "rotating night shifts."),
        ("RQ-029/C02", "non-scoreable condition", "occasional travel to the Abu Dhabi depot."),
        ("RQ-029/C03", "non-scoreable condition", "valid UAE residence visa or willingness to relocate. Preserve the OR wording as one condition."),
        ("RQ-029/C04", "post-hiring condition", "background check and two professional references requested before the start date."),
        ("RQ-029/C05", "informational benefit", "transport allowance."),
    ], "s010_scope": {"judgment": "S010", "children": ["RQ-029/C01", "RQ-029/C02", "RQ-029/C03"]}},
}
OBLIGATION = {"non-scoreable condition": "non_scoreable_requirements", "post-hiring condition": "post_hiring_conditions",
               "informational benefit": "informational_items"}


def load_children() -> dict:
    with (HERE / "review_children.csv").open(encoding="utf-8") as fh:
        return {r["child_id"]: r for r in csv.DictReader(fh)}


def check_s010_scope(children: dict, parent_rq: str, named: list[str]) -> None:
    """S010 must apply to exactly the named children: their content covers the statement, and no other child lies inside it."""
    jd_id = children[named[0]]["jd_id"]
    parent = [c for c in children.values() if c["parent_rq_id"] == parent_rq]
    jd = (HERE / "jd" / f"{jd_id}.txt").read_bytes().decode("utf-8")
    start, end = int(parent[0]["start"]), int(parent[-1]["end"])
    [(rid, a, b)] = BC.judgment_spans(jd, start, end, ["S010"])
    inside = [c["child_id"] for c in parent if int(c["content_start"]) >= a and int(c["content_end"]) <= b]
    if sorted(inside) != sorted(named):
        raise SystemExit(f"S010 applies to {inside}, not to {named}")
    lo = min(int(children[n]["content_start"]) for n in named)
    hi = max(int(children[n]["content_end"]) for n in named)
    if (lo, hi) != (a, b):
        raise SystemExit(f"S010 statement is {(a, b)} but the named children cover {(lo, hi)}")


def build_rows(parent_rq: str, spec: dict) -> list[dict]:
    children = load_children()
    rows = []
    scope_ok = None
    if spec.get("s010_scope"):
        check_s010_scope(children, parent_rq, spec["s010_scope"]["children"])
        scope_ok = "S010 applies exactly to " + ", ".join(c.split("/")[1] for c in spec["s010_scope"]["children"]) + " (checked from exact offsets)"
    for child_id, role, reason in spec["decisions"]:
        if child_id not in children:
            raise SystemExit(f"{child_id}: not a child unit")
        if role not in ROLES:
            raise SystemExit(f"{child_id}: role '{role}' is not one of the six choices")
        c = children[child_id]
        obligation = OBLIGATION.get(role, "")
        rows.append({"child_id": child_id, "parent_rq_id": c["parent_rq_id"], "parent_unit_id": c["parent_unit_id"], "jd_id": c["jd_id"],
                     "start": c["start"], "end": c["end"], "content_start": c["content_start"], "content_end": c["content_end"],
                     "child_text": c["child_text"], "confirmed_role": role, "reviewer": REVIEWER, "confirmed_date": DATE,
                     "reason": reason, "status": "resolved",
                     "obligation": (f"extraction-coverage obligation in {obligation}; not a scored criterion" if obligation
                                    else "not an extraction obligation; not a scored criterion"),
                     "judgment_scope_confirmed": scope_ok or ""})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--parent", required=True, choices=sorted(CHILD_BATCHES))
    args = ap.parse_args(argv)
    spec = CHILD_BATCHES[args.parent]
    target = HERE / "reviews" / spec["file"]
    if target.exists():
        raise SystemExit(f"{target.name} already exists; a submission is never overwritten")
    rows = build_rows(args.parent, spec)
    with target.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {target.name} with {len(rows)} child decisions for {args.parent} (parent: resolved_by_children)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
