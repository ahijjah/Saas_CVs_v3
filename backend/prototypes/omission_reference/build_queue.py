"""Builds the human-review queue from reviewer_sheet.csv. Read-only with respect to every candidate annotation, the sheet's decision column
and the manifest: it writes REVIEW_QUEUE.md, review_queue.csv and nothing else. No model is called and no decision is applied.

Priorities (a row appears once, under the highest priority that applies):
  P1  proposed background or uncertain units; proposed headings that contain requirement language (routing aid, see HEADING_TERMS);
  P2  rows connected to S001-S012 judgments that are not already P1; scope (whole unit or sentence within unit) is kept as recorded;
  P3  quality-control sample: two proposed requirement rows per JD, chosen by the lowest SHA256 of their unit IDs (deterministic).
Batches hold at most BATCH_SIZE rows, in queue order.
"""
from __future__ import annotations

import csv
import hashlib
import json
import pathlib
import re
from collections import Counter

HERE = pathlib.Path(__file__).resolve().parent
SHEET = HERE / "reviewer_sheet.csv"
MANIFEST = HERE / "manifest.json"
BATCH_SIZE = 5
JD_ORDER = ["TC20", "B02_en_data_analyst", "B03_en_warehouse_supervisor", "B07_ar_accountant", "B08_ar_hr_specialist", "B12_ar_injection"]
TYPE_RANK = {"background": 0, "uncertain": 1, "heading": 2, "requirement": 3}
# Routing aid only: a heading containing one of these terms is queued for review. It does not decide the type; the reviewer does.
HEADING_TERMS = ["required", "requirement", "must", "need", "qualif", "experience", "skill", "knowledge", "ability", "proficien", "licen",
                 "certif", "degree", "competenc", "duties", "responsibilit", "years", "متطلب", "المهام", "المؤهلات", "الخبرة", "المهارات",
                 "يفضل", "مطلوب", "خبرة", "إجادة", "معرفة", "القدرة", "شهادة", "مؤهل"]
CHOICES = "requirement | heading | background | uncertain"


def sha_file(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def judgment_scope(judgment: str) -> str:
    if not judgment:
        return ""
    scopes = sorted({"whole unit" if "whole unit" in part else "sentence within unit" for part in judgment.split("; ")})
    return " + ".join(scopes)


def assign(rows: list[dict]) -> list[dict]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for jd_id, meta in manifest["jds"].items():
        cand_path = HERE / "annotations" / f"{jd_id}.candidate.json"
        if sha_file(cand_path) != meta["annotation_sha256"]:
            raise SystemExit(f"{jd_id}: candidate annotation no longer matches the manifest; refusing to build the queue")
    p1_reason, p2_reason = {}, {}
    for r in rows:
        t = r["proposed_type_candidate"]
        text = r["source_text"].lower()
        if t in ("background", "uncertain"):
            p1_reason[r["unit_id"]] = f"proposed {t}: {r['candidate_reason']}"
        elif t == "heading" and any(term in text for term in HEADING_TERMS):
            matched = [term for term in HEADING_TERMS if term in text]
            p1_reason[r["unit_id"]] = f"heading with requirement term ({', '.join(matched[:3])}): check whether the section holds requirements"
        elif r["existing_confirmed_judgment"]:
            p2_reason[r["unit_id"]] = "connected to a confirmed S-judgment"
    p3_ids = set()
    for jd_id in JD_ORDER:
        eligible = [r for r in rows if r["jd_id"] == jd_id and r["proposed_type_candidate"] == "requirement"
                    and r["unit_id"] not in p1_reason and r["unit_id"] not in p2_reason]
        eligible.sort(key=lambda r: hashlib.sha256(r["unit_id"].encode("utf-8")).hexdigest())
        p3_ids |= {r["unit_id"] for r in eligible[:2]}

    queued = []
    for r in rows:
        if r["unit_id"] in p1_reason:
            prio, why = 1, p1_reason[r["unit_id"]]
        elif r["unit_id"] in p2_reason:
            prio, why = 2, p2_reason[r["unit_id"]]
        elif r["unit_id"] in p3_ids:
            prio, why = 3, "quality-control sample (two proposed requirement rows per JD, deterministic)"
        else:
            continue
        queued.append({**r, "priority": prio, "priority_reason": why})

    def key(q):
        return (q["priority"], JD_ORDER.index(q["jd_id"]), TYPE_RANK[q["proposed_type_candidate"]], int(q["first_line"]))
    queued.sort(key=key)
    for n, q in enumerate(queued, 1):
        q["rq_id"] = f"RQ-{n:03d}"
        q["batch"] = f"B{(n - 1) // BATCH_SIZE + 1:02d}"
        q["judgment_scope"] = judgment_scope(q["existing_confirmed_judgment"])
        q["choices"] = CHOICES
        q["reviewer_decision"] = ""
    return queued


CSV_FIELDS = ["rq_id", "batch", "priority", "priority_reason", "jd_id", "language", "unit_id", "first_line", "source_text",
              "proposed_type_candidate", "candidate_reason", "existing_confirmed_judgment", "judgment_scope", "choices", "reviewer_decision",
              "review_status", "confirmed_by", "confirmed_date", "benchmark_use"]


def load_submissions() -> dict:
    """Confirmed decisions from reviews/CONFIRMED_*.csv, keyed by unit id. A submission row must match the sheet's unit id and exact text."""
    sheet = {r["unit_id"]: r["source_text"] for r in csv.DictReader(SHEET.open(encoding="utf-8"))}
    out = {}
    for path in sorted((HERE / "reviews").glob("CONFIRMED_*.csv")):
        with path.open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if sheet.get(r["unit_id"]) != r["source_text"]:
                    raise SystemExit(f"{path.name}: {r['rq_id']} does not match the reviewer sheet; refusing to apply")
                out[r["unit_id"]] = {**r, "submission": path.name}
    return out


def apply_submissions(queue: list[dict], subs: dict) -> None:
    for q in queue:
        sub = subs.get(q["unit_id"])
        if sub is None:
            q.update({"review_status": "open", "confirmed_by": "", "confirmed_date": "", "benchmark_use": "not decided"})
            continue
        q["reviewer_decision"] = sub["confirmed_decision"]
        q["review_status"] = sub["status"]
        q["confirmed_by"] = sub["reviewer"]
        q["confirmed_date"] = sub["confirmed_date"]
        q["benchmark_use"] = sub["benchmark_use"]


def write_csv(queue: list[dict]) -> None:
    with (HERE / "review_queue.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(queue)


def summary(queue: list[dict], rows: list[dict]) -> dict:
    by_jd = Counter(q["jd_id"] for q in queue)
    by_prio = Counter(q["priority"] for q in queue)
    by_type = Counter(q["proposed_type_candidate"] for q in queue)
    jd_prio = Counter((q["jd_id"], q["priority"]) for q in queue)
    jd_type = Counter((q["jd_id"], q["proposed_type_candidate"]) for q in queue)
    return {"sheet_rows": len(rows), "queued_rows": len(queue), "batches": len({q["batch"] for q in queue}),
            "review_status": dict(Counter(q["review_status"] for q in queue)),
            "by_jd": {j: by_jd.get(j, 0) for j in JD_ORDER}, "by_priority": {p: by_prio.get(p, 0) for p in (1, 2, 3)},
            "by_type": dict(by_type), "jd_by_priority": {f"{j}|P{p}": n for (j, p), n in jd_prio.items()},
            "jd_by_type": {f"{j}|{t}": n for (j, t), n in jd_type.items()},
            "sheet_sha256": sha_file(SHEET)}


def write_md(queue: list[dict], summ: dict) -> None:
    out = ["# Human-review queue: omission-reference package", "",
           "Built by `build_queue.py` from `reviewer_sheet.csv` (sha256 `" + summ["sheet_sha256"][:16] + "…`). Read-only: no candidate annotation,",
           "reviewer decision or manifest entry was changed, and no model was called. The `reviewer_decision` column is blank for every row.",
           "A reviewer chooses one of the four types in the **choices** column, or records a decision in `reviewer_decision`. Each row's",
           "**judgment scope** says whether a confirmed judgment covers the whole unit or only one sentence inside it.", "",
           f"Rows queued: **{summ['queued_rows']}** of {summ['sheet_rows']} in the sheet, in **{summ['batches']}** batches of at most {BATCH_SIZE}.", "",
           "## Counts", "", "| JD | P1 | P2 | P3 | total | requirement | heading | background | uncertain |", "|---|---|---|---|---|---|---|---|---|"]
    for j in JD_ORDER:
        cells = [summ["jd_by_priority"].get(f"{j}|P{p}", 0) for p in (1, 2, 3)]
        t = {k: summ["jd_by_type"].get(f"{j}|{k}", 0) for k in ("requirement", "heading", "background", "uncertain")}
        out.append(f"| {j} | {cells[0]} | {cells[1]} | {cells[2]} | {summ['by_jd'][j]} | {t['requirement']} | {t['heading']} | {t['background']} | {t['uncertain']} |")
    out += ["", f"By priority: P1 {summ['by_priority'][1]}, P2 {summ['by_priority'][2]}, P3 {summ['by_priority'][3]}.", ""]
    out += ["Review status: " + ", ".join(f"{k} {v}" for k, v in sorted(summ["review_status"].items())) + ". Confirmed decisions come from "
            "`reviews/CONFIRMED_*.csv`; candidate classifications and the reviewer sheet are never changed. An unresolved (uncertain) row is not "
            "a confirmed requirement or exclusion in any benchmark result.", ""]
    out += ["## Batches", ""]
    for b in sorted({q["batch"] for q in queue}):
        members = [q for q in queue if q["batch"] == b]
        out.append(f"### {b} (P{members[0]['priority']}{'–P' + str(members[-1]['priority']) if members[-1]['priority'] != members[0]['priority'] else ''}, {len(members)} rows)")
        out.append("")
        for q in members:
            scope = f" · judgment: {q['existing_confirmed_judgment']} · scope: {q['judgment_scope']}" if q["existing_confirmed_judgment"] else " · judgment: none"
            out += [f"**{q['rq_id']}** · {q['jd_id']} ({q['language']}) · line {q['first_line']} · unit `{q['unit_id']}`",
                    f"- Source text: {q['source_text']}",
                    f"- Proposed type: **{q['proposed_type_candidate']}** — {q['candidate_reason']}",
                    f"- Priority: P{q['priority']} — {q['priority_reason']}",
                    f"- Existing confirmed judgment:{scope}",
                    f"- Choices: {CHOICES} · Status: **{q['review_status']}**"
                    + (f" · Confirmed decision: **{q['reviewer_decision']}** ({q['confirmed_by']}, {q['confirmed_date']})" if q["reviewer_decision"] else " · Reviewer decision: _(blank)_"),
                    f"- Benchmark use: {q['benchmark_use']}", ""]
    p1_tc20 = [q for q in queue if q["jd_id"] == "TC20" and q["priority"] == 1][:5]
    out += ["## Summary by JD, priority and proposed type", "", "| JD | priority | proposed type | rows |", "|---|---|---|---|"]
    for (j, p, t), n in sorted(Counter((q["jd_id"], q["priority"], q["proposed_type_candidate"]) for q in queue).items(),
                               key=lambda kv: (JD_ORDER.index(kv[0][0]), kv[0][1], kv[0][2])):
        out.append(f"| {j} | P{p} | {t} | {n} |")
    out.append("")
    (HERE / "REVIEW_QUEUE.md").write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> int:
    with SHEET.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    queue = assign(rows)
    apply_submissions(queue, load_submissions())
    write_csv(queue)
    summ = summary(queue, rows)
    write_md(queue, summ)
    (HERE / "results" / "review_queue_summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"queued {summ['queued_rows']} rows in {summ['batches']} batches; P1 {summ['by_priority'][1]}, P2 {summ['by_priority'][2]}, P3 {summ['by_priority'][3]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
