"""Writes the version-1 reference-set manifest: what was reviewed, what was not, and the hashes of every input. Offline; reads files only.

Version 1 = the 44 reviewed parent rows (42 resolved, 2 resolved by children) and the 8 decided child units. The remaining 70 rows of the
114-row candidate sheet are UNREVIEWED candidates and are listed explicitly. Nothing here implies that all 114 rows were validated.
"""
from __future__ import annotations

import csv
import hashlib
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
VERSION = "omission-reference-set-v1"


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    sheet = list(csv.DictReader((HERE / "reviewer_sheet.csv").open(encoding="utf-8")))
    queue = list(csv.DictReader((HERE / "review_queue.csv").open(encoding="utf-8")))
    children = list(csv.DictReader((HERE / "review_children.csv").open(encoding="utf-8")))
    queued_units = {q["unit_id"] for q in queue}
    unreviewed = [r for r in sheet if r["unit_id"] not in queued_units]
    parent_status = {}
    for q in queue:
        parent_status[q["rq_id"]] = q["review_status"]
    reviewed_parents = [q for q in queue if q["review_status"] in ("resolved", "resolved_by_children")]
    unresolved = [q for q in queue if q["review_status"] not in ("resolved", "resolved_by_children")]
    child_files = sorted((HERE / "reviews").glob("CHILD_CONFIRMED_*.csv"))
    decided_children = [r for f in child_files for r in csv.DictReader(f.open(encoding="utf-8"))]
    submissions = sorted((HERE / "reviews").glob("CONFIRMED_*.csv"))
    amendments = sorted((HERE / "reviews").glob("AMENDMENT_*.csv"))

    manifest = {
        "version": VERSION,
        "scope_statement": ("Version 1 labels the 44 reviewed parent rows and the 8 decided child units. The 70 candidate rows of the 114-row "
                            "sheet that are not in the queue are UNREVIEWED and are not validated. Do not describe the set as 114 validated rows."),
        "counts": {
            "candidate_sheet_rows": len(sheet),
            "reviewed_parent_rows": len(reviewed_parents),
            "reviewed_parent_rows_by_status": {s: sum(1 for q in queue if q["review_status"] == s) for s in ("resolved", "resolved_by_children")},
            "parent_rows_unresolved": len(unresolved),
            "decided_child_units": len(decided_children),
            "unreviewed_candidate_rows": len(unreviewed),
        },
        "unreviewed_candidate_rows": [{"jd_id": r["jd_id"], "first_line": int(r["first_line"]), "unit_id": r["unit_id"],
                                       "proposed_type_candidate": r["proposed_type_candidate"]} for r in unreviewed],
        "parent_rows": {q["rq_id"]: q["review_status"] for q in queue},
        "submissions": [{"file": f.name, "sha256": sha(f), "rows": sum(1 for _ in csv.DictReader(f.open(encoding="utf-8")))}
                        for f in submissions],
        "child_submissions": [{"file": f.name, "sha256": sha(f)} for f in child_files],
        "amendments": [{"file": f.name, "sha256": sha(f)} for f in amendments],
        "inputs": {
            "reviewer_sheet_sha256": sha(HERE / "reviewer_sheet.csv"),
            "review_children_sha256": sha(HERE / "review_children.csv"),
            "review_queue_sha256": sha(HERE / "review_queue.csv"),
            "annotations": {p.name: sha(p) for p in sorted((HERE / "annotations").glob("*.candidate.json"))},
            "jd_texts": {p.name: sha(p) for p in sorted((HERE / "jd").glob("*.txt"))},
            "package_manifest_sha256": sha(HERE / "manifest.json"),
        },
        "frozen_benchmark_scorer_sha256": sha(HERE.parent.parent / "scripts" / "requirements_v2_extraction_eval.py"),
    }
    (HERE / "reference_set_v1_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    c = manifest["counts"]
    md = [f"# Reference set {VERSION}", "",
          "**Scope.** Version 1 labels the reviewed parent rows and decided child units listed below. It does **not** validate the whole "
          f"114-row candidate sheet. {c['unreviewed_candidate_rows']} candidate rows are **unreviewed**; they are listed in "
          "`reference_set_v1_manifest.json` and are not part of the reference labels.", "",
          "## Counts", "",
          "| item | count |", "|---|---|",
          f"| candidate sheet rows (Claude-generated candidates) | {c['candidate_sheet_rows']} |",
          f"| reviewed parent rows (accepted as version-1 labels) | {c['reviewed_parent_rows']} |",
          f"|   resolved | {c['reviewed_parent_rows_by_status']['resolved']} |",
          f"|   resolved by children | {c['reviewed_parent_rows_by_status']['resolved_by_children']} |",
          f"| parent rows unresolved | {c['parent_rows_unresolved']} (RQ-003 was superseded by its batch-ten background decision) |",
          f"| decided child units | {c['decided_child_units']} |",
          f"| **unreviewed candidate rows (NOT validated)** | **{c['unreviewed_candidate_rows']}** |", "",
          "## Submissions and amendments (SHA-256)", ""]
    md += [f"- `{s['file']}` ({s['rows']} rows): `{s['sha256']}`" for s in manifest["submissions"]]
    md += [f"- child: `{s['file']}`: `{s['sha256']}`" for s in manifest["child_submissions"]]
    md += [f"- amendment: `{s['file']}`: `{s['sha256']}`" for s in manifest["amendments"]]
    md += ["", "## Inputs (SHA-256)", "",
           f"- reviewer sheet: `{manifest['inputs']['reviewer_sheet_sha256']}`",
           f"- child units: `{manifest['inputs']['review_children_sha256']}`",
           f"- review queue: `{manifest['inputs']['review_queue_sha256']}`",
           f"- package manifest: `{manifest['inputs']['package_manifest_sha256']}`",
           f"- frozen benchmark scorer (unchanged): `{manifest['frozen_benchmark_scorer_sha256']}`", "",
           "The machine-readable manifest (`reference_set_v1_manifest.json`) lists the 70 unreviewed rows by unit id.", ""]
    (HERE / "REFERENCE_SET_V1.md").write_text("\n".join(md), encoding="utf-8")
    print(f"reference set v1: {c['reviewed_parent_rows']} reviewed parent rows, {c['decided_child_units']} child units, "
          f"{c['unreviewed_candidate_rows']} unreviewed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
