"""Deterministic child review units for uncertain parent rows (RQ-029, RQ-030). Offline; reads the package and the batch-6 submission only.

Split rule (fixed, not semantic judgment): a boundary is placed after terminal punctuation followed by whitespace (sentence), and after a
comma, Arabic comma or semicolon followed by whitespace (clause). Every boundary keeps its trailing whitespace with the child before it, so
the children partition the parent exactly: concatenating the children reproduces the parent text character for character. " or " is never
a boundary, so OR alternatives such as "valid visa or be willing to relocate" stay in one child.

Proposed semantic roles are Claude-generated candidates. They are recorded for review and never applied. Each child's judgment applicability
is computed from exact offsets: a judgment applies to a child only if the child lies inside the judgment's sentence.
"""
from __future__ import annotations

import csv
import hashlib
import json
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent
PARENTS = ["RQ-029", "RQ-030"]
BOUNDARY = re.compile(r"(?:[.!?؟۔][ \t]+)|(?:[,،;][ \t]+)")
ROLES = ["scoreable requirement", "non-scoreable condition", "post-hiring condition", "informational benefit", "background", "uncertain"]

# Claude-generated candidate roles, keyed by parent and child index (1-based). Not decisions.
PROPOSED = {
    "RQ-029": [
        ("non-scoreable condition", "work schedule (rotating night shifts) is a non-scoreable condition"),
        ("non-scoreable condition", "travel is a non-scoreable condition"),
        ("non-scoreable condition", "work authorisation or residency (visa) with a relocation alternative; the OR is kept in one child"),
        ("post-hiring condition", "a background check and references requested after appointment"),
        ("informational benefit", "a transport allowance is a benefit, not a requirement"),
    ],
    "RQ-030": [
        ("non-scoreable condition", "salary is a non-scoreable condition"),
        ("non-scoreable condition", "work location and schedule are non-scoreable conditions"),
        ("informational benefit", "medical cover and annual leave are benefits, not requirements"),
    ],
}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_children(jd: str, start: int, end: int) -> list[tuple[int, int]]:
    """Partition [start, end) of the JD into child spans at the fixed boundaries. Returns exact absolute offsets."""
    text = jd[start:end]
    cuts = sorted({m.end() for m in BOUNDARY.finditer(text) if 0 < m.end() < len(text)})
    bounds = [0] + cuts + [len(text)]
    return [(start + a, start + b) for a, b in zip(bounds, bounds[1:])]


CONSOLIDATED = HERE.parent.parent / "tests" / "fixtures" / "requirements_v2_review_package_v2" / "reviews" / "CONSOLIDATED_judgments_S001-S012.csv"


def judgment_spans(jd: str, parent_start: int, parent_end: int, parent_judgments: list[str]) -> list[tuple[str, int, int]]:
    """Sentence-level confirmed judgments of this parent. The full statement is taken from the consolidated judgments (the sheet truncates it),
    and must occur exactly once inside the parent text. Returns (review id, absolute start, absolute end) of the statement."""
    statements = {}
    with CONSOLIDATED.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            statements[r["review_id"]] = r["jd_statement_exact"]
    parent_text = jd[parent_start:parent_end]
    out = []
    for rid in parent_judgments:
        statement = statements[rid]
        if parent_text.count(statement) != 1:
            raise SystemExit(f"{rid}: statement does not occur exactly once in the parent unit")
        k = parent_text.index(statement)
        out.append((rid, parent_start + k, parent_start + k + len(statement)))
    return out


def main() -> int:
    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    with (HERE / "review_queue.csv").open(encoding="utf-8") as fh:
        queue = {r["rq_id"]: r for r in csv.DictReader(fh)}
    sheet = list(csv.DictReader((HERE / "reviewer_sheet.csv").open(encoding="utf-8")))
    sheet_by_unit = {r["unit_id"]: r for r in sheet}
    rows = []
    for rq in PARENTS:
        parent = queue[rq]
        jd_id = parent["jd_id"]
        jd = (HERE / "jd" / f"{jd_id}.txt").read_bytes().decode("utf-8")
        srow = sheet_by_unit[parent["unit_id"]]
        start, end = int(srow["start"]), int(srow["end"])
        if jd[start:end] != parent["source_text"]:
            raise SystemExit(f"{rq}: parent offsets do not reproduce its source text")
        spans = split_children(jd, start, end)
        if "".join(jd[a:b] for a, b in spans) != jd[start:end]:
            raise SystemExit(f"{rq}: children do not reproduce the parent exactly")
        if len(spans) != len(PROPOSED[rq]):
            raise SystemExit(f"{rq}: {len(spans)} children but {len(PROPOSED[rq])} proposed roles")
        sentence_ids = sorted(set(re.findall(r"S\d{3}", " ".join(
            r["existing_confirmed_judgment"] for r in sheet_by_unit.values() if r["unit_id"] == parent["unit_id"]))))
        scoped = [rid for rid in sentence_ids if "sentence within unit" in srow["existing_confirmed_judgment"]]
        js = judgment_spans(jd, start, end, scoped)
        for n, ((a, b), (role, reason)) in enumerate(zip(spans, PROPOSED[rq]), 1):
            raw = jd[a:b]
            ca, cb = a + (len(raw) - len(raw.lstrip())), b - (len(raw) - len(raw.rstrip()))   # content extent, without separators
            applies = [f"{rid} (sentence-level: child content inside its statement)" for rid, sa, sb in js if sa <= ca and cb <= sb]
            rows.append({"child_id": f"{rq}/C{n:02d}", "parent_rq_id": rq, "parent_unit_id": parent["unit_id"], "jd_id": jd_id,
                         "language": parent["language"], "child_index": n, "start": a, "end": b,
                         "content_start": ca, "content_end": cb,
                         "child_text": jd[a:b].strip(), "child_text_with_separator": jd[a:b],
                         "proposed_role_candidate": role, "candidate_reason": reason,
                         "existing_judgment_applies": "; ".join(applies),
                         "semantic_role_choices": " | ".join(ROLES), "semantic_role_decision": ""})
    with (HERE / "review_children.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} child units for {', '.join(PARENTS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
