#!/usr/bin/env python3
"""Offline audit of the completed v2-3 versus v2-5 comparison (no network, no paid call, no change to the frozen scorer).

  python3 scripts/requirements_v2_candidate_audit.py --audit RUN_DIR      rebuild the diagnostics from a run directory
  python3 scripts/requirements_v2_candidate_audit.py                     the same, on the stored evidence in the repo

Three things are produced, each labelled as what it is:
  1. the reproduction: the frozen report recomputed from calls.jsonl with the unchanged scorers, compared field by field with scored.json;
  2. the frozen-scorer flags, each traced to the rule that raised it (exact containment of source_text in the JD, and of the
     expected source in the output source), with quotations;
  3. DIAGNOSTIC classifications of every unmatched entry and every flagged item: missing content, a matching limitation (terminal
     punctuation, a shorter verbatim quote), an AND that was not split, a wrong list or category, and a genuine invention.

Diagnostics are never replacement scores. The frozen scorer (scripts/requirements_v2_candidate_eval.py score_expected) and the TC20
labels are not changed; the diagnostics only explain the flags it raised.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
from scripts import requirements_v2_candidate_eval as E  # noqa: E402  (the frozen expected-output scorer, unchanged)
from scripts import requirements_v2_tc20_eval as ev  # noqa: E402  (the frozen TC20 labels and scorer, unchanged)
from scripts import requirements_v2_tc20_compare as C  # noqa: E402  (schema and weight facts, unchanged)

TERMINAL = ".،؛:!?؟…"
EVIDENCE = BACKEND / "tests" / "fixtures" / "requirements_v2_candidate_v25" / "audit" / "run_v2-3_v2-5_20261010"
UPLOAD_NAMES = {"calls": "calls.jsonl", "manifest": "manifest.json", "scored": "scored.json"}


def norm(s) -> str:
    """Collapse whitespace and drop trailing terminal punctuation only. Used for DIAGNOSIS, never for the frozen scores."""
    return " ".join(str(s).split()).rstrip(TERMINAL + " ")


def jd_text(unit_id: str) -> str:
    if unit_id == "tc20":
        return ev.load_inputs()[0]
    return (E.EVAL_DIR / "jds" / f"{unit_id}.txt").read_text(encoding="utf-8").rstrip("\n")


def spec_of(unit_id: str) -> dict:
    return json.loads((E.EVAL_DIR / "expected" / f"{unit_id}.json").read_text(encoding="utf-8"))


def _items(obj):
    return [(c, it) for c, lst in (obj.get("categories") or {}).items() if isinstance(lst, list) for it in lst if isinstance(it, dict)]


def _blob(it) -> str:
    return " ".join([str(it.get("text", "")), *[str(a) for a in (it.get("alternatives") or [])]]).casefold()


def _quote(s: str, n: int = 160) -> str:
    return s if len(s) <= n else s[:n] + "…"


def frozen_hit(e, items, used):
    """The frozen rule, reproduced exactly: the first unused item whose source_text contains the expected source and whose
    text or alternatives contain the key."""
    for idx, (_, it) in enumerate(items):
        if idx in used or e["source"] not in str(it.get("source_text", "")):
            continue
        if e["key"].casefold() in _blob(it):
            return idx
    return None


def diagnose_entries(obj: dict, jd: str, spec: dict) -> dict:
    """Every expected entry, traced: the frozen match if there is one; otherwise a diagnosis of why it was not matched."""
    items = _items(obj)
    used: set[int] = set()
    rows = []
    frozen_used = {}
    # pass 1: the frozen assignment, in order (so the diagnostics agree with the frozen flags)
    for e in spec["items"]:
        idx = frozen_hit(e, items, used)
        if idx is not None:
            used.add(idx)
            frozen_used[e["key"]] = idx
    diag_used = set(used)
    # pass 2: diagnose the entries the frozen rule did not match
    for e in spec["items"]:
        row = {"key": e["key"], "expected_category": e["category"], "expected_importance": e["importance"],
               "expected_source": e["source"], "expected_alternatives": e["alternatives"], "group": e["group"]}
        if e["key"] in frozen_used:
            idx = frozen_used[e["key"]]
            c, it = items[idx]
            row.update({"status": "frozen_match", "output_category": c, "output_importance": it.get("importance"),
                        "output_source": it.get("source_text"), "output_text": it.get("text"), "output_alternatives": it.get("alternatives")})
            rows.append(row)
            continue
        cands = [(idx, c, it) for idx, (c, it) in enumerate(items) if e["key"].casefold() in _blob(it)]
        free = [(i, c, it) for i, c, it in cands if i not in diag_used]
        punct = [(i, c, it) for i, c, it in free if norm(e["source"]) in norm(it.get("source_text", ""))]
        partial = [(i, c, it) for i, c, it in free if norm(it.get("source_text", "")) and
                   norm(it.get("source_text", "")) in norm(e["source"]) and str(it.get("source_text", "")) in jd]
        if punct:
            i, c, it = punct[0]
            status = "matching_limitation_terminal_punctuation"
        elif partial:
            i, c, it = partial[0]
            status = "matching_limitation_shorter_verbatim_quote"
        elif cands:
            i, c, it = cands[0]
            status = "and_not_split_or_shared_item" if e["group"] == "AND" else "shared_item"
        else:
            i = c = it = None
            status = "genuinely_missing"
        if i is not None and status.startswith("matching") :
            diag_used.add(i)
        if i is not None and status.startswith("matching"):
            # the frozen scorer never checks category, importance or alternatives for an entry it did not match: check them here, as
            # DIAGNOSTIC-ONLY flags, so a category error is not hidden by a terminal-punctuation difference
            extra = []
            if c != e["category"]:
                extra.append({"group": "categories", "expected": e["category"], "output": c})
            if it.get("importance") != e["importance"]:
                extra.append({"group": "importance", "expected": e["importance"], "output": it.get("importance")})
            alts = it.get("alternatives")
            if e["alternatives"] and sorted(E._norm(a) for a in (alts or [])) != sorted(E._norm(a) for a in e["alternatives"]):
                extra.append({"group": "or_and", "expected": e["alternatives"], "output": alts})
            if not e["alternatives"] and alts is not None:
                extra.append({"group": "or_and", "expected": None, "output": alts})
            row.update({"status": status, "output_category": c, "output_importance": it.get("importance"),
                        "output_source": it.get("source_text"), "output_text": it.get("text"), "output_alternatives": it.get("alternatives"),
                        "quote_expected": _quote(e["source"]), "quote_output": _quote(str(it.get("source_text", ""))),
                        "diagnostic_only_flags": extra})
        else:
            row.update({"status": status, "output_category": c, "output_text": it.get("text") if it else None,
                        "output_source": it.get("source_text") if it else None})
        rows.append(row)
    return {"entries": rows, "diag_used": sorted(diag_used)}


def classify_items(obj: dict, jd: str, spec: dict, diag_used: set[int]) -> list[dict]:
    """Every answer item the frozen scorer did not use, classified by its source wording."""
    items = _items(obj)
    expected_duties = {e["source"] for e in spec["items"] if e["origin"] == "from_responsibilities"}
    out = []
    for idx, (c, it) in enumerate(items):
        if idx in diag_used:
            continue
        src = str(it.get("source_text", ""))
        if src in jd:
            verb = "verbatim"
        elif norm(src) and norm(src) in jd:
            verb = "verbatim_except_terminal_punctuation"
        else:
            verb = "not_verbatim"
        if it.get("origin") == "from_responsibilities" and src not in expected_duties:
            role = "duty_not_in_expected_list"
        else:
            role = "extra_item"
        out.append({"category": c, "text": it.get("text"), "source_text": src, "wording": verb, "role": role})
    return out


def classify_conditions(obj: dict, jd: str, spec: dict) -> list[dict]:
    """Every expected condition traced, and every answer condition classified by wording."""
    rows = []
    for ce in spec["conditions"]:
        candidates = [(lst, row) for lst in E.CONDITION_LISTS for row in (obj.get(lst) or [])
                      if isinstance(row, dict) and ce["key"].casefold() in str(row.get("text", "")).casefold()]
        frozen_ok = [(lst, row) for lst, row in candidates if ce["source"] in str(row.get("source_text", ""))]
        punct = [(lst, row) for lst, row in candidates if norm(ce["source"]) in norm(row.get("source_text", ""))]
        shorter = [(lst, row) for lst, row in candidates if norm(row.get("source_text", "")) and
                   str(row.get("source_text", "")) in jd and norm(row.get("source_text", "")) in norm(ce["source"])]
        if frozen_ok and frozen_ok[0][0] == ce["list"]:
            status, (lst, row) = "frozen_match", frozen_ok[0]
        elif frozen_ok:
            status, (lst, row) = "wrong_list", frozen_ok[0]
        elif punct:
            lst, row = punct[0]
            status = "matching_limitation_terminal_punctuation" if lst == ce["list"] else "wrong_list"
        elif shorter:
            lst, row = shorter[0]
            status = "matching_limitation_shorter_verbatim_quote" if lst == ce["list"] else "wrong_list"
        elif candidates:
            lst, row = candidates[0]
            status = "wrong_list"
        else:
            status, lst, row = "genuinely_missing", None, None
        rows.append({"key": ce["key"], "expected_list": ce["list"], "expected_category": ce["category"], "expected_source": ce["source"],
                     "status": status, "output_list": lst, "output_source": (row or {}).get("source_text"),
                     "output_category": (row or {}).get("category"),
                     "wording": ("verbatim" if (row and str((row or {}).get("source_text", "")) in jd) else None)})
    for lst in E.CONDITION_LISTS:
        for row in obj.get(lst) or []:
            if not isinstance(row, dict):
                continue
            src = str(row.get("source_text", ""))
            wording = "verbatim" if src in jd else ("verbatim_except_terminal_punctuation" if norm(src) and norm(src) in jd else "not_verbatim")
            rows.append({"answer_condition": True, "list": lst, "category": row.get("category"), "source_text": src, "wording": wording,
                         "text": row.get("text")})
    return rows


def frozen_flag_trace(obj: dict, jd: str, spec: dict, entries: list[dict], conditions: list[dict], diag_used: set[int]) -> list[dict]:
    """Every flag the frozen scorer raises, one row each, with the rule that raised it and the diagnosis. The counts per group must
    equal the frozen report (checked by a test), so the trace is complete."""
    items = _items(obj)
    frozen_used = {}
    used: set[int] = set()
    for e in spec["items"]:
        idx = frozen_hit(e, items, used)
        if idx is not None:
            used.add(idx)
            frozen_used[e["key"]] = idx
    expected_duties = {e["source"] for e in spec["items"] if e["origin"] == "from_responsibilities"}
    flags = []
    by_key = {r["key"]: r for r in entries}
    for e in spec["items"]:
        r = by_key[e["key"]]
        if e["key"] not in frozen_used:
            flags.append({"group": "omissions", "key": e["key"], "expected_source": e["source"], "diagnosis": r["status"],
                          "quote_output": r.get("output_source")})
            continue
        c, it = items[frozen_used[e["key"]]]
        if c != e["category"]:
            flags.append({"group": "categories", "key": e["key"], "expected": e["category"], "output": c, "output_text": it.get("text"),
                          "output_source": it.get("source_text"), "rule": "the category of the matched item differs from the expected category"})
        if it.get("importance") != e["importance"]:
            flags.append({"group": "importance", "key": e["key"], "expected": e["importance"], "output": it.get("importance")})
        alts = it.get("alternatives")
        if e["alternatives"]:
            if sorted(E._norm(a) for a in (alts or [])) != sorted(E._norm(a) for a in e["alternatives"]):
                flags.append({"group": "or_and", "key": e["key"], "expected": e["alternatives"], "output": alts,
                              "rule": "alternatives must equal the expected option list (order-insensitive, exact strings)"})
        elif alts is not None:
            flags.append({"group": "or_and", "key": e["key"], "expected": None, "output": alts,
                          "rule": "a requirement without an expected OR list carries alternatives"})
    for idx, (c, it) in enumerate(items):
        src = str(it.get("source_text", ""))
        if src not in jd:
            flags.append({"group": "invented", "rule": "source_text is not a substring of the JD (exact)", "category": c,
                          "output_source": src, "wording": "not_verbatim" if not norm(src) or norm(src) not in jd else
                          "verbatim_except_terminal_punctuation", "text": it.get("text")})
        elif idx not in used:
            if it.get("origin") == "from_responsibilities" and src not in expected_duties:
                matched_dut = any(norm(src) == norm(d) for d in expected_duties)
                flags.append({"group": "invented", "rule": "duty source is not EXACTLY one of the expected duty sources (set equality, terminal punctuation included)",
                              "category": c, "output_source": src, "diagnosis": "matching_limitation_terminal_punctuation" if matched_dut
                              else "unexpected_duty", "text": it.get("text")})
            else:
                flags.append({"group": "extra_items", "rule": "unused verbatim item", "category": c, "output_source": src, "text": it.get("text"),
                              "diagnosis": "matching_limitation_terminal_punctuation" if idx in diag_used else "unmatched_verbatim_item"})
    for row in conditions:
        if row.get("answer_condition") or row.get("status") == "frozen_match":
            continue
        flags.append({"group": "routing", "key": row["key"], "expected_list": row["expected_list"], "diagnosis": row["status"],
                      "output_list": row.get("output_list"), "quote_output": row.get("output_source")})
    return flags


CLASS_OF = {"matching_limitation_terminal_punctuation": "matching_limitation", "matching_limitation_shorter_verbatim_quote": "matching_limitation",
            "and_not_split_or_shared_item": "genuine_and_not_split", "genuinely_missing": "genuine_missing", "shared_item": "genuine_shared_item",
            "wrong_list": "genuine_wrong_list", "wrong_list_partial_quote": "genuine_wrong_list", "unexpected_duty": "genuine_unexpected_duty",
            "unmatched_verbatim_item": "genuine_unmatched_verbatim_item", "not_verbatim": "genuine_invention",
            "verbatim_except_terminal_punctuation": "matching_limitation"}


CONTESTABLE_KEYS = {"remote monitoring", "المراقبة عن بعد"}


def _class_of(f: dict, jd: str) -> str:
    """One class per flag. Matching limitations are never counted as content defects."""
    g = f["group"]
    if g == "omissions" or g == "routing" or g == "extra_items":
        return CLASS_OF.get(f.get("diagnosis"), "unclassified")
    if g == "invented":
        return CLASS_OF.get(f.get("diagnosis") or f.get("wording"), "unclassified")
    if g == "categories":
        # "experience with remote monitoring tools" fits experience (kind of work) and skills (tools, systems) under rule 5:
        # the expected label is contestable, so a placement there is reported apart from genuine category errors.
        return "contestable_expected_label" if f.get("key") in CONTESTABLE_KEYS else "genuine_category_error"
    if g == "importance":
        return "genuine_importance_error"
    if g == "or_and":
        alts = f.get("output") or []
        if any(a not in jd for a in alts):
            return "genuine_altered_alternative_wording"
        return "genuine_or_alternatives"
    return "unclassified"


def diagnose_answer(raw: str, jd: str, spec: dict) -> dict:
    """One answer: the frozen flags, the diagnostics of every entry, and the classification of every unmatched item and condition."""
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return {"valid_json": False, "frozen_groups": E.score_expected(raw, jd, spec)["groups"]}
    frozen = E.score_expected(raw, jd, spec)["groups"]
    d = diagnose_entries(obj, jd, spec)
    conditions = classify_conditions(obj, jd, spec)
    trace = frozen_flag_trace(obj, jd, spec, d["entries"], conditions, set(d["diag_used"]))
    for f in trace:
        f["class"] = _class_of(f, jd)
    counts = {g: sum(1 for f in trace if f["group"] == g) for g in E.GROUPS}
    return {"valid_json": True, "frozen_groups": frozen, "trace_counts_equal_frozen": counts == frozen, "frozen_flags": trace,
            "entries": d["entries"],
            "unmatched_items": classify_items(obj, jd, spec, set(d["diag_used"])),
            "conditions": conditions,
            "schema_problems": C.schema_problems(obj), "weights": C.weight_validity(obj),
            "education_alternatives": [it.get("alternatives") for c, it in _items(obj) if "بكالوريوس" in str(it.get("source_text", ""))
                                       or "Bachelor" in str(it.get("source_text", ""))]}


def tc20_structure(raw: str, jd: str, labels: dict) -> dict:
    """TC20: the official checks plus the structural facts behind each check (where each line landed)."""
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return {"valid_json": False}
    items = _items(obj)
    ev_vals = labels["expected_values"]
    def where(phrase):
        return sorted({c for c, it in items if phrase in str(it.get("source_text", ""))})
    reporting = [(lst, r.get("category"), r["source_text"][:90]) for lst in E.CONDITION_LISTS for r in (obj.get(lst) or [])
                 if isinstance(r, dict) and ev_vals["reporting_marker"] in str(r.get("source_text", ""))]
    location = [(lst, r["source_text"][:90]) for lst in E.CONDITION_LISTS for r in (obj.get(lst) or [])
                if isinstance(r, dict) and ev_vals["location_marker"] in str(r.get("source_text", ""))]
    return {"valid_json": True, "checks": ev.score_answer(raw, jd, labels)["checks"],
            "duty_lines_found": sum(1 for line in ev_vals["duty_lines"] if any(line in str(it.get("source_text", "")) and it.get("origin") ==
                                                                               "from_responsibilities" for _, it in items)),
            "duty_lines_expected": len(ev_vals["duty_lines"]),
            "competency_lines_where": {line: where(line) for line in ev_vals["competency_lines"]},
            "ict_or": [(c, it.get("importance"), it.get("alternatives")) for c, it in items if "ICT systems support" in str(it.get("source_text", ""))],
            "familiarity_or": [(c, it.get("importance"), it.get("alternatives")) for c, it in items if "Familiarity with business process" in str(it.get("source_text", ""))],
            "communication_item": [(c, it["text"]) for c, it in items if "Strong communication, coordination" in str(it.get("source_text", ""))],
            "reporting_statement": reporting, "location_statement": location,
            "informational_items": len(obj.get("informational_items") or []),
            "company_statement_in_informational": any("It is expected" in str(r.get("source_text", "")) for r in obj.get("informational_items") or []),
            "schema_problems": C.schema_problems(obj), "weights": C.weight_validity(obj)}


def load_run(run_dir: pathlib.Path):
    rows = [json.loads(x) for x in (run_dir / UPLOAD_NAMES["calls"]).read_text(encoding="utf-8").splitlines()]
    manifest = json.loads((run_dir / UPLOAD_NAMES["manifest"]).read_text(encoding="utf-8"))
    scored = json.loads((run_dir / UPLOAD_NAMES["scored"]).read_text(encoding="utf-8"))
    return rows, manifest, scored


def audit(run_dir: pathlib.Path) -> dict:
    rows, manifest, scored = load_run(run_dir)
    labels = ev.load_inputs()[1]
    reproduced = E.report(rows)
    fields = []
    for arm in reproduced:
        for unit in reproduced[arm]:
            for k in reproduced[arm][unit]:
                if reproduced[arm][unit][k] != scored["report"][arm][unit].get(k):
                    fields.append((arm, unit, k))
    per_call = []
    for r in rows:
        unit = r["jd"]
        jd = jd_text(unit)
        entry = {"jd": unit, "arm": r["arm"], "call": r["call"]}
        if unit == "tc20":
            entry["tc20"] = tc20_structure(r["raw"], jd, labels)
        else:
            entry["diagnosis"] = diagnose_answer(r["raw"], jd, spec_of(unit))
        per_call.append(entry)
    return {"provenance": {"calls_sha256": E.sha256_bytes((run_dir / UPLOAD_NAMES["calls"]).read_bytes()),
                           "manifest_sha256": E.sha256_bytes((run_dir / UPLOAD_NAMES["manifest"]).read_bytes()),
                           "scored_sha256": E.sha256_bytes((run_dir / UPLOAD_NAMES["scored"]).read_bytes())},
            "reproduction": {"report_equal": reproduced == scored["report"], "fields_differing": fields},
            "per_call": per_call}


def reference_answer(spec: dict) -> str:
    """A synthetic answer written from one frozen expected-output file (for the classifier tests only; not evidence of anything)."""
    cats = {c: [] for c in ("skills", "experience", "education", "certifications", "soft_skills", "domain_knowledge", "other_requirements")}
    for it in spec["items"]:
        cats[it["category"]].append({"text": it["key"], "importance": it["importance"], "importance_cue": it.get("importance_cue"),
                                     "source_text": it["source"], "origin": it["origin"], "alternatives": it["alternatives"], "experience": None})
    conds = {n: [] for n in E.CONDITION_LISTS}
    for c in spec["conditions"]:
        conds[c["list"]].append({"text": c["key"], "category": c["category"], "reason": "r", "source_text": c["source"]})
    return json.dumps({"scoreability": {"status": "scoreable", "reason": ""}, "categories": cats,
                       "category_weights": {c: 0 for c in cats}, **conds, "warnings": []}, ensure_ascii=False)


def summary(out: dict) -> dict:
    """Per JD and arm: the frozen group totals, the class of every frozen flag, and the schema, weight and verbatim facts."""
    rows, _, _ = load_run(EVIDENCE)
    by = {}
    for r in rows:
        key = f"{r['jd']}|{r['arm']}"
        by.setdefault(key, {"calls": 0, "schema_compliant": 0, "weights_usable": 0, "weights_total": [], "sources": 0,
                            "sources_verbatim": 0, "frozen_flag_classes": {}})
        o = json.loads(r["raw"])
        jd = jd_text(r["jd"])
        b = by[key]
        b["calls"] += 1
        b["schema_compliant"] += not C.schema_problems(o)
        w = C.weight_validity(o)
        b["weights_usable"] += w["usable"]
        b["weights_total"].append(w["total"])
        srcs = [it.get("source_text", "") for _, it in _items(o)] + [x.get("source_text", "") for lst in E.CONDITION_LISTS for x in o.get(lst) or [] if isinstance(x, dict)]
        b["sources"] += len(srcs)
        b["sources_verbatim"] += sum(1 for x in srcs if x in jd)
    for pc in out["per_call"]:
        if "diagnosis" not in pc:
            continue
        key = f"{pc['jd']}|{pc['arm']}"
        for f in pc["diagnosis"]["frozen_flags"]:
            cls = f["class"]
            d = by[key]["frozen_flag_classes"]
            d[cls] = d.get(cls, 0) + 1
        for e in pc["diagnosis"]["entries"]:
            for f in e.get("diagnostic_only_flags", []):
                cls = _class_of({**f, "key": e["key"]}, jd_text(pc["jd"]))
                d = by[key].setdefault("diagnostic_only_flag_classes", {})
                d[cls] = d.get(cls, 0) + 1
    return by


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", type=pathlib.Path, default=EVIDENCE)
    ap.add_argument("--write", action="store_true", help="write audit.json next to the evidence (diagnostic, not replacement scores)")
    args = ap.parse_args(argv)
    out = audit(args.audit)
    if args.write:
        (args.audit / "audit.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        (args.audit / "summary.json").write_text(json.dumps(summary(out), ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"report_equal": out["reproduction"]["report_equal"], "fields_differing": out["reproduction"]["fields_differing"],
                      "calls": len(out["per_call"])}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
