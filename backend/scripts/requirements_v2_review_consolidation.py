#!/usr/bin/env python3
"""Consolidates the CONFIRMED reviewer submissions for the starter sheet (offline, no model call, no accuracy calculated).

It reads the blank starter and every reviews/CONFIRMED_*.csv submission (the UNCONFIRMED example file is excluded), checks that each
starter row has exactly one confirmed judgment, and writes reviews/CONSOLIDATED_judgments_S001-S012.csv. Emitted warnings (checker_warning
rows) and unflagged probes (checker_silent_probe and location_probe rows) are counted separately. A correct probe is never a dismissed warning.
"""
from __future__ import annotations

import collections
import csv
import io
import pathlib
import sys

PKG = pathlib.Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "requirements_v2_review_package_v2"
STARTER = PKG / "starter_review_sheet_v2.csv"
REVIEWS = PKG / "reviews"
OUT = REVIEWS / "CONSOLIDATED_judgments_S001-S012.csv"
COLUMNS = ["review_id", "status", "class", "row_kind", "warning_type", "case_id", "jd_statement_exact", "reviewer_choice", "reviewer_note", "submission"]


def disputed_ids() -> set[str]:
    """Review ids whose confirmed judgment is disputed pending confirmation: excluded from resolved counts, original kept."""
    out = set()
    for f in sorted(REVIEWS.glob("AMENDMENT_*.csv")):
        for r in csv.DictReader(f.read_text(encoding="utf-8").splitlines()):
            if r["status"].startswith("DISPUTED"):
                out.add(r["review_id"])
    return out


def consolidate() -> list[dict]:
    starter = {r["review_id"]: r for r in csv.DictReader(STARTER.read_text(encoding="utf-8").splitlines())}
    disputed = disputed_ids()
    judgments: dict[str, dict] = {}
    for f in sorted(REVIEWS.glob("CONFIRMED_*.csv")):
        for r in csv.DictReader(f.read_text(encoding="utf-8").splitlines()):
            if not r["reviewer_choice"]:
                continue
            if r["review_id"] in judgments:
                raise SystemExit(f"duplicate confirmed judgment for {r['review_id']}")
            base = starter[r["review_id"]]
            judgments[r["review_id"]] = {"review_id": r["review_id"],
                                         "status": "disputed" if r["review_id"] in disputed else "resolved",
                                         "class": "emitted_warning" if base["row_kind"] == "checker_warning" else "unflagged_probe",
                                         "row_kind": base["row_kind"], "warning_type": base["warning_type"], "case_id": base["case_id"],
                                         "jd_statement_exact": base["jd_statement_exact"], "reviewer_choice": r["reviewer_choice"],
                                         "reviewer_note": r["reviewer_note"], "submission": f.name}
    missing = sorted(set(starter) - set(judgments))
    if missing:
        raise SystemExit(f"no confirmed judgment for {missing}")
    return [judgments[k] for k in sorted(judgments)]


def counts(rows: list[dict]) -> dict:
    """Resolved judgments only. Disputed judgments are excluded and reported separately."""
    out = collections.Counter((r["class"], r["reviewer_choice"]) for r in rows if r["status"] == "resolved")
    result = {f"{c}: {ch}": n for (c, ch), n in sorted(out.items())}
    result["disputed (excluded)"] = sum(1 for r in rows if r["status"] == "disputed")
    return result


def main() -> int:
    rows = consolidate()
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    OUT.write_text(buf.getvalue(), encoding="utf-8")
    print(counts(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
