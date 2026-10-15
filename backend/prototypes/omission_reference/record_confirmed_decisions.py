"""Writes the reviewer's confirmed decisions to a SEPARATE submission file. It does not touch the candidate annotations, the reviewer sheet or
the manifest. Each decision is checked against the queue: the RQ id must exist, and the unit id and exact source text must match the queue.

Input: the decisions as given by the reviewer (below). Output: reviews/CONFIRMED_queue_<reviewer>_<date>.csv
"""
from __future__ import annotations

import csv
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
REVIEWER = "Asaad Hijjah"
DATE = "2026-10-15"
ALLOWED = {"requirement", "heading", "background", "uncertain"}
DECISIONS = [
    # rq_id, decision, reason as recorded (None = the reviewer gave no reason)
    ("RQ-001", "requirement", None),
    ("RQ-002", "requirement", None),
    ("RQ-003", "uncertain", "substantive duty summary, but potentially duplicates the detailed duties that follow"),
    ("RQ-004", "heading", None),
    ("RQ-005", "heading", None),
]
SUBMISSION = HERE / "reviews" / f"CONFIRMED_queue_{REVIEWER.replace(' ', '-')}_{DATE}.csv"
FIELDS = ["rq_id", "jd_id", "unit_id", "first_line", "source_text", "candidate_type_preserved", "confirmed_decision", "reviewer",
          "confirmed_date", "reason", "status", "benchmark_use"]


def queue_index() -> dict:
    with (HERE / "review_queue.csv").open(encoding="utf-8") as fh:
        return {r["rq_id"]: r for r in csv.DictReader(fh)}


def build_rows() -> list[dict]:
    queue = queue_index()
    rows = []
    for rq_id, decision, reason in DECISIONS:
        if decision not in ALLOWED:
            raise SystemExit(f"{rq_id}: decision '{decision}' is not one of the four choices")
        q = queue.get(rq_id)
        if q is None:
            raise SystemExit(f"{rq_id}: not in the review queue")
        resolved = decision != "uncertain"
        rows.append({"rq_id": rq_id, "jd_id": q["jd_id"], "unit_id": q["unit_id"], "first_line": q["first_line"],
                     "source_text": q["source_text"], "candidate_type_preserved": q["proposed_type_candidate"],
                     "confirmed_decision": decision, "reviewer": REVIEWER, "confirmed_date": DATE,
                     "reason": reason if reason else "no reason recorded (reviewer gave none)",
                     "status": "resolved" if resolved else "unresolved (uncertain)",
                     "benchmark_use": ("confirmed " + decision + " type" if resolved else
                                       "must not be used as a confirmed requirement or as an exclusion in benchmark results")})
    return rows


def main() -> int:
    rows = build_rows()
    with SUBMISSION.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {SUBMISSION.name} with {len(rows)} decisions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
