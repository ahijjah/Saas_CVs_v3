"""Writes the reviewer's confirmed decisions to a SEPARATE submission file, one file per batch. It does not touch the candidate annotations, the
reviewer sheet or the manifest. Each decision is checked against the queue: the RQ id must exist and the choice must be one of the four. A
submission file is never overwritten: a batch that already has one is refused.

Usage: python record_confirmed_decisions.py --batch N
"""
from __future__ import annotations

import argparse
import csv
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
REVIEWER = "Asaad Hijjah"
ALLOWED = {"requirement", "heading", "background", "uncertain"}
BATCHES = {
    1: {"date": "2026-10-15", "file": "CONFIRMED_queue_Asaad-Hijjah_2026-10-15.csv", "decisions": [
        # rq_id, decision, reason as recorded (None = the reviewer gave no reason)
        ("RQ-001", "requirement", None),
        ("RQ-002", "requirement", None),
        ("RQ-003", "uncertain", "substantive duty summary, but potentially duplicates the detailed duties that follow"),
        ("RQ-004", "heading", None),
        ("RQ-005", "heading", None),
    ]},
    2: {"date": "2026-10-15", "file": "CONFIRMED_queue_Asaad-Hijjah_2026-10-15_batch2.csv", "decisions": [
        ("RQ-006", "heading", "“متطلبات الوظيفة” only names the job-requirements section."),
        ("RQ-007", "heading", "“Qualifications” only names a section."),
        ("RQ-008", "heading", "names the qualifications and professional-experience section and contains no requirement itself."),
        ("RQ-009", "heading", "names the skills and competencies section."),
        ("RQ-010", "requirement", "it explicitly requires clear communication of findings to non-technical colleagues."),
    ]},
    3: {"date": "2026-10-15", "file": "CONFIRMED_queue_Asaad-Hijjah_2026-10-15_batch3.csv", "decisions": [
        ("RQ-011", "heading", "\u201cRequirements\u201d only identifies the section."),
        ("RQ-012", "requirement", "it contains the Jebel Ali work location, which must be retained and routed as a non-scoreable location condition."),
        ("RQ-013", "heading", "\u201cRequired:\u201d only identifies the section."),
        ("RQ-014", "heading", "\u201cDuties:\u201d only identifies the section."),
        ("RQ-015", "background", "optional company information, consistent with confirmed whole-unit judgment S004."),
    ]},
}
FIELDS = ["rq_id", "jd_id", "unit_id", "first_line", "source_text", "candidate_type_preserved", "confirmed_decision", "reviewer",
          "confirmed_date", "reason", "status", "benchmark_use"]


def queue_index() -> dict:
    with (HERE / "review_queue.csv").open(encoding="utf-8") as fh:
        return {r["rq_id"]: r for r in csv.DictReader(fh)}


def build_rows(decisions: list, date: str) -> list[dict]:
    queue = queue_index()
    rows = []
    for rq_id, decision, reason in decisions:
        if decision not in ALLOWED:
            raise SystemExit(f"{rq_id}: decision '{decision}' is not one of the four choices")
        q = queue.get(rq_id)
        if q is None:
            raise SystemExit(f"{rq_id}: not in the review queue")
        resolved = decision != "uncertain"
        rows.append({"rq_id": rq_id, "jd_id": q["jd_id"], "unit_id": q["unit_id"], "first_line": q["first_line"],
                     "source_text": q["source_text"], "candidate_type_preserved": q["proposed_type_candidate"],
                     "confirmed_decision": decision, "reviewer": REVIEWER, "confirmed_date": date,
                     "reason": reason if reason else "no reason recorded (reviewer gave none)",
                     "status": "resolved" if resolved else "unresolved (uncertain)",
                     "benchmark_use": ("confirmed " + decision + " type" if resolved else
                                       "must not be used as a confirmed requirement or as an exclusion in benchmark results")})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--batch", type=int, required=True, choices=sorted(BATCHES))
    args = ap.parse_args(argv)
    spec = BATCHES[args.batch]
    target = HERE / "reviews" / spec["file"]
    if target.exists():
        raise SystemExit(f"{target.name} already exists; a submission is never overwritten")
    rows = build_rows(spec["decisions"], spec["date"])
    with target.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {target.name} with {len(rows)} decisions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
