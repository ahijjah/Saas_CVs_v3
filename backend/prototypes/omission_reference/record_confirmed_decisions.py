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
    4: {"date": "2026-10-15", "file": "CONFIRMED_queue_Asaad-Hijjah_2026-10-15_batch4.csv", "decisions": [
        ("RQ-016", "heading", "\u201cالمهام:\u201d labels the duties section."),
        ("RQ-017", "heading", "\u201cالمتطلبات:\u201d labels the requirements section."),
        ("RQ-018", "heading", "\u201cيفضل:\u201d labels the preferred-items section."),
        ("RQ-019", "requirement",
         "explicitly requires effective communication, but it repeats or rephrases the communication requirement at B08 line 9 and must not "
         "create a second scored criterion.",
         ("repeats_content_of", "B08_ar_hr_specialist", 9)),
        ("RQ-020", "heading", "\u201cالمتطلبات:\u201d labels the requirements section."),
    ]},
    5: {"date": "2026-10-15", "file": "CONFIRMED_queue_Asaad-Hijjah_2026-10-15_batch5.csv", "decisions": [
        ("RQ-023", "heading", "labels the requirements section."),
        ("RQ-024", "heading", "labels the preferred-items section."),
        ("RQ-021", "requirement", "changes CSS and HTML to optional and conflicts with the earlier presentation.",
         ("modifies_importance_of", "B12_ar_injection", 6)),
        ("RQ-022", "background", "AI-directed injection text, not a job requirement.", None, "ai_directed_injection"),
        ("RQ-025", "heading", "labels the duties section."),
    ]},
    6: {"date": "2026-10-15", "file": "CONFIRMED_queue_Asaad-Hijjah_2026-10-15_batch6.csv", "decisions": [
        ("RQ-026", "requirement", "one connected communication competency; S006 confirms it should remain one item."),
        ("RQ-027", "requirement", "one Python-or-R alternative requirement; S002 confirms the alternatives must remain together."),
        ("RQ-028", "requirement", "one English-language requirement; S005 confirms spoken and written should remain together."),
        ("RQ-029", "uncertain", "the physical line mixes work conditions, post-hire checks and a benefit. S010 applies only to its first sentence."),
        ("RQ-030", "uncertain", "the physical line mixes salary, location and benefits. S003 and S011 apply only to its first sentence."),
    ]},
    7: {"date": "2026-10-15", "file": "CONFIRMED_queue_Asaad-Hijjah_2026-10-15_batch7.csv", "decisions": [
        ("RQ-033", "requirement", "one ABRS implementation, rollout, stabilization and continuous-improvement duty."),
        ("RQ-034", "requirement", "one connected system-testing, troubleshooting, issue-tracking and operational-follow-up competency. "
                                  "Do not split its related activities mechanically."),
        ("RQ-035", "requirement", "at least six months of hands-on Tableau experience."),
        ("RQ-031", "requirement", "one combined knowledge requirement covering labour law and social insurance. Preserve it as one item, "
                                  "consistent with S007."),
        ("RQ-032", "requirement", "one source unit that must produce two independent skill items: CSS and HTML, consistent with S008.",
         None, None, ["CSS", "HTML"]),
    ]},
    8: {"date": "2026-10-15", "file": "CONFIRMED_queue_Asaad-Hijjah_2026-10-15_batch8.csv", "decisions": [
        ("RQ-036", "requirement", "duty to build weekly sales dashboards for regional managers."),
        ("RQ-037", "requirement", "required working knowledge of inventory management systems."),
        ("RQ-038", "requirement", "duty to assign tasks to 12 operators each shift."),
        ("RQ-039", "requirement", "duty to reconcile bank accounts."),
        ("RQ-040", "requirement", "required Excel proficiency."),
    ]},
    9: {"date": "2026-10-15", "file": "CONFIRMED_queue_Asaad-Hijjah_2026-10-15_batch9.csv", "decisions": [
        ("RQ-041", "requirement", "duty to monitor attendance and departure."),
        ("RQ-042", "requirement", "duty to prepare monthly absence reports."),
        ("RQ-043", "requirement", "teamwork competency."),
        ("RQ-044", "requirement", "duty to develop customer dashboards."),
    ]},
}
FIELDS = ["rq_id", "jd_id", "unit_id", "first_line", "source_text", "candidate_type_preserved", "confirmed_decision", "reviewer",
          "confirmed_date", "reason", "status", "benchmark_use", "relation", "classification_tag",
          "yields_independent_items", "source_coverage_obligations", "importance_modifier", "conflict_evidence"]


def queue_index() -> dict:
    with (HERE / "review_queue.csv").open(encoding="utf-8") as fh:
        return {r["rq_id"]: r for r in csv.DictReader(fh)}


def sheet_unit(jd_id: str, first_line: int) -> dict:
    with (HERE / "reviewer_sheet.csv").open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["jd_id"] == jd_id and int(r["first_line"]) == first_line:
                return r
    raise SystemExit(f"no unit at {jd_id} line {first_line}")


RELATION_USE = {
    "repeats_content_of": ("confirmed requirement type, but repeated requirement content: counted only through the related unit, "
                           "never as a second scored criterion"),
    "modifies_importance_of": ("confirmed requirement type, but a conflict and importance modifier of the related unit: conflict evidence "
                               "only, never a second scored criterion"),
}
UNCERTAIN_PARENTS = {
    "RQ-029": ("uncertain parent: excluded from confirmed inclusion and exclusion; decided only through its child units, "
               "which are pending"),
    "RQ-030": ("uncertain parent: excluded from confirmed inclusion and exclusion; decided only through its child units, "
               "which are pending"),
}
TAG_USE = {
    "ai_directed_injection": ("excluded from requirement coverage: AI-directed injection text, kept under the injection guard, never extracted "
                              "as a requirement"),
}


def _modifier_rows() -> list[dict]:
    """Confirmed submissions that carry a modifies_importance_of relation (RQ-021), read from the reviews folder."""
    out = []
    for path in sorted((HERE / "reviews").glob("CONFIRMED_*.csv")):
        with path.open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("relation", "").startswith("modifies_importance_of"):
                    out.append(r)
    return out


def modifier_for(unit_id: str) -> str:
    """The importance modifier a confirmed submission applies to this unit (RQ-021 on B12 line 6 makes CSS and HTML optional)."""
    hits = [r for r in _modifier_rows() if r["relation"].split(" ")[1] == unit_id]
    if not hits:
        return ""
    return "optional: " + "; ".join(f"{r['rq_id']} modifies_importance_of ({r['confirmed_decision']})" for r in hits)


def conflict_for(unit_id: str) -> str:
    """The conflict evidence kept for this unit: the later unit whose note contradicts it."""
    hits = [r for r in _modifier_rows() if r["relation"].split(" ")[1] == unit_id]
    return "; ".join(f"{r['rq_id']} ({r['jd_id']} line {r['first_line']}): {r['source_text']}" for r in hits)


def build_rows(decisions: list, date: str) -> list[dict]:
    queue = queue_index()
    rows = []
    for item in decisions:
        rq_id, decision, reason = item[0], item[1], item[2]
        relation_spec = item[3] if len(item) > 3 else None
        tag = item[4] if len(item) > 4 else ""
        yields = item[5] if len(item) > 5 else None
        if decision not in ALLOWED:
            raise SystemExit(f"{rq_id}: decision '{decision}' is not one of the four choices")
        q = queue.get(rq_id)
        if q is None:
            raise SystemExit(f"{rq_id}: not in the review queue")
        relation = ""
        if relation_spec:
            kind, rel_jd, rel_line = relation_spec
            if kind not in RELATION_USE:
                raise SystemExit(f"{rq_id}: unknown relation '{kind}'")
            target = sheet_unit(rel_jd, rel_line)
            if target["proposed_type_candidate"] != "requirement":
                raise SystemExit(f"{rq_id}: relation target is not a requirement candidate")
            relation = f"{kind} {target['unit_id']} ({rel_jd} line {rel_line})"
        if tag and tag not in TAG_USE:
            raise SystemExit(f"{rq_id}: unknown classification tag '{tag}'")
        resolved = decision != "uncertain"
        if tag:
            benchmark_use = TAG_USE[tag]
        elif decision == "uncertain" and q["priority_reason"] and rq_id in UNCERTAIN_PARENTS:
            benchmark_use = UNCERTAIN_PARENTS[rq_id]
        elif relation:
            benchmark_use = RELATION_USE[relation_spec[0]]
        elif resolved:
            benchmark_use = "confirmed " + decision + " type"
        else:
            benchmark_use = "must not be used as a confirmed requirement or as an exclusion in benchmark results"
        rows.append({"rq_id": rq_id, "jd_id": q["jd_id"], "unit_id": q["unit_id"], "first_line": q["first_line"],
                     "source_text": q["source_text"], "candidate_type_preserved": q["proposed_type_candidate"],
                     "confirmed_decision": decision, "reviewer": REVIEWER, "confirmed_date": date,
                     "reason": reason if reason else "no reason recorded (reviewer gave none)",
                     "status": "resolved" if resolved else "unresolved (uncertain)", "benchmark_use": benchmark_use,
                     "relation": relation, "classification_tag": tag,
                     "yields_independent_items": ", ".join(yields) if yields else "",
                     "source_coverage_obligations": "1" if yields else "",
                     "importance_modifier": modifier_for(q["unit_id"]) if yields else "",
                     "conflict_evidence": conflict_for(q["unit_id"]) if yields else ""})
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
