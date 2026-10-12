#!/usr/bin/env python3
"""Versioned offline human-review package for the staged-validation checker, v3 (no model call, no network).

v3 supersedes v2 (tests/fixtures/requirements_v2_review_package_v2, kept unchanged with its reviews; its builder and checker copy are in
scripts/historical/requirements_v2_review_package_v2.py and requirements_v2_staged_check_v2.py). v1 is kept the same way. v3 differs from v2
in two fixes: the OR-silent probe now looks at conditions as well as items, and quotations that jointly cover a statement are recognised
as a split rather than a partial quotation. Every case used here has been inspected during development, so all of them are DEVELOPMENT
evidence. None is an independent or held-out validation set.

It writes into tests/fixtures/requirements_v2_review_package_v3/:
  review_sheet_v2.csv         every checker warning and probe for the stored answers, with blank reviewer cells
  starter_review_sheet_v2.csv a 12-row EN/AR starter sheet chosen by fixed rules (real warnings, candidate-type warnings,
                              unflagged probes)
  seeded_defect_results.csv   seeded mutations, detection by construction, kept apart from the real-answer sheet
  INSTRUCTIONS.md             reviewer instructions
  MANIFEST.json               sources, hashes, selection rules, counts and disclosures

Reviewer choice cells are blank by construction. No precision, recall or readiness is computed from human judgments here.
"""
from __future__ import annotations

import collections
import csv
import hashlib
import io
import json
import pathlib
import re
import sys

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))
import requirements_v2_staged_check as P  # noqa: E402
import requirements_v2_tc20_eval as ev  # noqa: E402

PKG = BACKEND / "tests" / "fixtures" / "requirements_v2_review_package_v3"
V1_SRC = BACKEND / "tests" / "fixtures" / "requirements_v2_review_package" / "sources"
SRC = PKG / "sources"
BENCH = BACKEND / "tests" / "fixtures" / "requirements_v2_benchmark" / "cases"
EVAL = BACKEND / "tests" / "fixtures" / "requirements_v2_candidate_v25" / "eval_set"
ROUTING = BACKEND / "tests" / "fixtures" / "requirements_v2_routing_experiment" / "run" / "calls.jsonl"
SCOPE = BACKEND / "tests" / "fixtures" / "requirements_v2_scope_experiment" / "run" / "calls.jsonl"
OR_RE = re.compile(r"\bor\b|\beither\b|(?<!\S)أو(?!\S)", re.I)
WORD_WAW = re.compile(r"(?<!\S)و[^\s.,،؛:!?؟()]{2,}")           # any word that starts with و (a conjunction or not)
CHOICES = ["genuine defect", "false alarm", "ambiguous"]
COLUMNS = ["review_id", "source_group", "case_id", "language", "answer_label", "row_kind", "warning_type", "jd_statement_exact",
           "matched_item", "matched_source_text", "warning_text", "rationale", "mechanical_note", "reviewer_choice", "reviewer_note"]
STARTER_COLUMNS = COLUMNS + ["selection_stratum", "selection_rule"]
CHECKER_WARNING_TYPES = {
    "uncovered_statement", "partial_quote", "source_not_verbatim", "ambiguous_quote", "source_is_heading", "whitespace_differs",
    "multi_statement_span", "wrong_list", "unclassified_other", "unknown_category", "possible_lost_or", "possible_or_split",
    "possible_bundled_and", "possible_attached_and", "alternatives_too_short", "ambiguous_heading_line"}
TRACE_TYPES = {"wrong_list", "unclassified_other", "unknown_category", "possible_lost_or", "possible_bundled_and", "possible_attached_and",
               "alternatives_too_short", "ambiguous_quote", "source_is_heading", "whitespace_differs", "multi_statement_span",
               "source_not_verbatim"}
RATIONALE = {
    "uncovered_statement": "No quotation covers this statement's substantive text. It may be background text, a requirement or duty the model did not extract, or an omission.",
    "partial_quote": "A quotation covers only part of this statement's substantive text, so it is a shortened quotation.",
    "source_not_verbatim": "The source_text does not occur in the job description, character for character or after whitespace.",
    "ambiguous_quote": "The source_text occurs more than once in the job description, so its span is not certain.",
    "source_is_heading": "The quotation matches a heading-shaped line. Review whether the line is a requirement or a label.",
    "whitespace_differs": "The source_text matches the job description only after whitespace changes.",
    "multi_statement_span": "One quotation covers more than one statement (for example several reporting sentences in one item).",
    "wrong_list": "A condition sits in a list other than the one rule 9 assigns to its category (deterministic check against the rule 9 table).",
    "unclassified_other": "The condition has category 'other'. Rule 9 uses 'other' only when no label fits.",
    "unknown_category": "The category is not a rule 9 label (or not one of the seven categories), so the checker cannot route it.",
    "possible_lost_or": "The statement joins options with 'or' but the item that covers it keeps no alternatives. Candidate only: rule 4 decides.",
    "possible_or_split": "One statement with 'or' is covered by two or more items. Rule 4 keeps the options as ONE item. Candidate only; nothing is merged.",
    "possible_bundled_and": "A stated statement joins independent requirements with 'and' or a comma but one item carries them all. Candidate only: rule 5 decides.",
    "possible_attached_and": "A word begins with the conjunction و attached to the definite article or a Latin word. Candidate only: the checker does not decide conjunction versus word-beginning.",
    "alternatives_too_short": "An alternatives list holds fewer than two options.",
    "ambiguous_heading_line": "A line with no terminal punctuation that resembles a heading, and no quotation covers it. It may be a title or section label, or a requirement the answer did not extract. It is not counted as an omission.",
    "location_probe": "Rule 9 assigns the location category to non_scoreable_requirements. This row shows where the answer placed the location condition.",
    "or_silent_probe": "The statement contains 'or' and no item quotes any part of it, so the checker's OR test cannot run on it.",
    "attached_waw_limitation_probe": "A word begins with و and the conservative pattern did not match it. The checker does not check other attached conjunctions, so a reviewer decides whether a split is needed.",
}
ROW_KIND = {"location_probe": "location_probe", "or_silent_probe": "checker_silent_probe", "attached_waw_limitation_probe": "checker_silent_probe"}
SOURCE_GROUP = {"benchmark": "benchmark cases B01 to B12 (development)", "tc20": "TC20 (development)", "eval": "EN and AR eval JDs (development)"}


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha_file(p: pathlib.Path) -> str:
    return sha_bytes(p.read_bytes())


def annotate_partial(stmt: str, quotes: list[str]) -> str:
    core = stmt.rstrip(P.TERMINAL + " ")
    notes = set()
    for q in quotes:
        qc = q.rstrip(P.TERMINAL + " ")
        if qc == core:
            notes.add("quote equals the statement without its terminal punctuation")
        elif ":" in stmt and qc == stmt.split(":", 1)[1].strip().rstrip(P.TERMINAL):
            notes.add("quote omits the label before the colon")
        else:
            notes.add("quote covers only part of the statement")
    return "; ".join(sorted(notes))


def describe_entry(ent: dict | None) -> str:
    if ent is None:
        return "none"
    e = ent["entry"]
    where = ent["category"] if ent["kind"] == "item" else f"{ent['list']} / category {ent['category']}"
    return f"{ent['path']} | {where} | {e.get('text', '')}"


class Rows:
    def __init__(self):
        self.rows: list[dict] = []

    def add(self, group, case_id, lang, label, kind, wtype, stmt, matched, source, warning, mech=""):
        self.rows.append({"review_id": "", "source_group": group, "case_id": case_id, "language": lang, "answer_label": label,
                          "row_kind": kind, "warning_type": wtype, "jd_statement_exact": stmt, "matched_item": matched,
                          "matched_source_text": source, "warning_text": warning, "rationale": RATIONALE.get(wtype, ""),
                          "mechanical_note": mech, "reviewer_choice": "", "reviewer_note": ""})

    def merged(self) -> list[dict]:
        """Probe rows that repeat the same JD statement across answers of one case appear once; the answer labels are joined."""
        out, seen = [], {}
        for r in self.rows:
            if r["row_kind"] == "checker_warning":
                out.append(r)
                continue
            key = (r["case_id"], r["jd_statement_exact"], r["warning_type"], r["matched_item"])
            if key in seen:
                seen[key]["answer_label"] += "; " + r["answer_label"]
                continue
            seen[key] = dict(r)
            out.append(seen[key])
        return out


def answer_rows(out: Rows, group: str, case_id: str, lang: str, label: str, jd: str, raw: str) -> dict:
    st, hd = P.segment(jd)
    byid = {s["id"]: s for s in st}
    answer = json.loads(raw)
    ents = {e["path"]: e for e in P.entries_of(answer)}
    rep = P.validate(jd, raw)
    trace = {t["path"]: t for t in rep["trace"]}
    heads = {h["id"]: h for h in rep["headings"]}
    counts = collections.Counter()
    partial_classes = collections.Counter()
    for x in rep["review_queue"]:
        t = x["type"]
        if t == "uncovered_statement":
            s = byid[x["statement"]]
            out.add(group, case_id, lang, label, "checker_warning", t, s["text"], "none", "", x["detail"], "no quotation covers the substantive text")
        elif t == "partial_quote":
            s = byid[x["statement"]]
            quotes = [trace[p]["source_text"] for p in x["paths"]]
            mech = annotate_partial(s["text"], quotes)
            partial_classes[mech] += 1
            out.add(group, case_id, lang, label, "checker_warning", t, s["text"], describe_entry(ents[x["paths"][0]]), quotes[0], x["detail"], mech)
        elif t == "ambiguous_heading_line":
            h = heads[x["heading"]]
            out.add(group, case_id, lang, label, "checker_warning", t, h["text"], "none", "", x["detail"],
                    "first line of the job description" if h["start"] == 0 else "not the first line")
        elif t == "possible_or_split":
            s = byid[x["statement"]]
            matched = " ;; ".join(describe_entry(ents[p]) for p in x["paths"])
            out.add(group, case_id, lang, label, "checker_warning", t, s["text"], matched, trace[x["paths"][0]]["source_text"], x["detail"],
                    "paths: " + ", ".join(x["paths"]))
        elif t in TRACE_TYPES:
            p = x["path"]
            tr = trace.get(p)
            span = tr["span"] if tr else None
            stmts = [s["text"] for s in st if span and s["start"] < span[1] and span[0] < s["end"]] if span else []
            out.add(group, case_id, lang, label, "checker_warning", t, " | ".join(stmts) or "(no span in the job description)",
                    describe_entry(ents.get(p)), (tr or {}).get("source_text") or "", x["detail"], f"path {p}")
        counts[t] += 1
    # covered-with-notes: quotations that omit terminal punctuation or a label and are still treated as covering
    notes_count = collections.Counter(n.split(": ", 1)[1] for t in rep["trace"] for n in t.get("notes", []))
    # location probes: every condition in the location category, with the list it was placed in
    for ent in P.entries_of(answer):
        if ent["kind"] == "condition" and ent["category"] == "location":
            src = ent["entry"].get("source_text") or ""
            loc = P.locate(jd, src) if src else None
            stmts = [s["text"] for s in st if loc and s["start"] < loc["end"] and loc["start"] < s["end"]]
            out.add(group, case_id, lang, label, "location_probe", "location_probe", " | ".join(stmts) or "(no span)", describe_entry(ent),
                    src, "placement of a location condition", f"placed in {ent['list']}")
            counts["location_probe"] += 1
    # silent probes: OR statements that no item or condition quotes at all; Arabic words beginning with و that the conservative rule does not match
    for s in st:
        overlapping = [t for t in rep["trace"] if t["span"] and s["start"] < t["span"][1] and t["span"][0] < s["end"]]
        items_overlapping = [t for t in overlapping if t["kind"] == "item"]
        if OR_RE.search(s["text"]) and not overlapping:
            out.add(group, case_id, lang, label, "checker_silent_probe", "or_silent_probe", s["text"], "none", "",
                    "no item or condition quotes any part of this statement", "no item or condition quotes any part")
            counts["or_silent_probe"] += 1
        words = sorted(set(m.group(0) for m in WORD_WAW.finditer(s["text"]))) if lang == "ar" else []
        if words and not P.ATTACHED_CONJ.search(s["text"]) and items_overlapping:
            stated = [t for t in items_overlapping if t.get("origin") == "stated"]
            if stated:
                out.add(group, case_id, lang, label, "checker_silent_probe", "attached_waw_limitation_probe", s["text"],
                        describe_entry(ents.get(stated[0]["path"])), stated[0]["source_text"],
                        "words starting with و: " + ", ".join(words), "not matched by the conservative pattern")
                counts["attached_waw_limitation_probe"] += 1
    return {"coverage": rep["coverage"], "warnings_total": len(rep["review_queue"]), "by_type": dict(counts),
            "partial_quote_mechanical_classes": dict(partial_classes), "covered_with_notes": dict(notes_count)}


def number_rows(rows: list[dict], prefix: str) -> None:
    for i, r in enumerate(rows, 1):
        r["review_id"] = f"{prefix}{i:03d}"


def csv_bytes(rows: list[dict], columns: list[str]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in columns})
    return buf.getvalue().encode("utf-8")


# ── sources (every case is development evidence) ─────────────────────────────────────────────────────────────────────────

def sources() -> list[dict]:
    out = []
    bench_rows = [json.loads(x) for x in (SRC / "benchmark_v2-2_run1_calls.jsonl").read_text(encoding="utf-8").splitlines()]
    for f in sorted(BENCH.glob("*.json")):
        case = json.loads(f.read_text(encoding="utf-8"))
        raw = [r for r in bench_rows if r["case"] == case["id"]]
        assert len(raw) == 1 and raw[0]["error"] is None, case["id"]
        out.append({"group": "benchmark", "case_id": case["id"], "language": case["language"], "jd": case["jd"], "raw": raw[0]["raw"],
                    "label": f"v2-2 run1 ({raw[0]['returned_model']})", "source_file": f.name})
    jd_tc20, _ = ev.load_inputs()
    routing = [json.loads(x) for x in ROUTING.read_text(encoding="utf-8").splitlines()]
    scope = [json.loads(x) for x in SCOPE.read_text(encoding="utf-8").splitlines()]
    out.append({"group": "tc20", "case_id": "TC20", "language": "en", "jd": jd_tc20,
                "raw": [r for r in routing if r["order"] == 1][0]["raw"], "label": "routing run, CONTROL call 1 (gpt-4.1)",
                "source_file": "routing run calls.jsonl"})
    out.append({"group": "tc20", "case_id": "TC20", "language": "en", "jd": jd_tc20,
                "raw": [r for r in scope if r["order"] == 4][0]["raw"], "label": "scope run, CLARIFIED call 2 (gpt-4.1)",
                "source_file": "scope run calls.jsonl"})
    evals = [json.loads(x) for x in (SRC / "eval_gpt-4.1_calls.jsonl").read_text(encoding="utf-8").splitlines()]
    for name, lang in (("eval_en_field_service_01", "en"), ("eval_ar_facilities_01", "ar")):
        jd = (EVAL / "jds" / f"{name}.txt").read_text(encoding="utf-8")
        for arm in ("v2-3", "v2-5"):
            r = [x for x in evals if x["jd"] == name and x["arm"] == arm and x["call"] == 1][0]
            out.append({"group": "eval", "case_id": name, "language": lang, "jd": jd, "raw": r["raw"],
                        "label": f"{arm} call 1 (gpt-4.1)", "source_file": "eval_gpt-4.1_calls.jsonl"})
    return out


# ── starter selection (fixed rules, applied to the sorted sheet) ─────────────────────────────────────────────────────────

CANDIDATE_PRIORITY = ("possible_bundled_and", "possible_attached_and", "ambiguous_heading_line", "source_is_heading")
STARTER_STRATA = [
    ("real_warning", "warnings a reviewer is most likely to confirm: a wrong list, an unknown category, a lost or split OR, an uncovered or shortened statement",
     lambda r: r["row_kind"] == "checker_warning" and r["warning_type"] in
     ("wrong_list", "unknown_category", "possible_lost_or", "possible_or_split", "uncovered_statement", "partial_quote")),
    ("candidate_warning", "candidate-type warnings that may be false alarms: bundled or attached AND, ambiguous heading lines, source is a heading",
     lambda r: r["row_kind"] == "checker_warning" and r["warning_type"] in CANDIDATE_PRIORITY),
    ("unflagged_probe", "unflagged cases that may hide a missed defect: OR statements no item quotes, attached و the pattern does not match, location placement",
     lambda r: r["row_kind"] in ("checker_silent_probe", "location_probe")),
]


def starter(rows: list[dict]) -> list[dict]:
    chosen = []
    for name, rule, pick in STARTER_STRATA:
        cands = [r for r in rows if pick(r)]
        if name == "candidate_warning":                    # AND and attached-و candidates first, then heading-shaped lines
            cands.sort(key=lambda r: CANDIDATE_PRIORITY.index(r["warning_type"]))
        for lang in ("en", "ar"):
            sel = [r for r in cands if r["language"] == lang][:2]
            for r in sel:
                chosen.append({**r, "selection_stratum": name, "selection_rule": rule})
    return chosen


# ── seeded defects (kept apart; detection by construction) ───────────────────────────────────────────────────────────────

def _reference(name: str):
    jd = (EVAL / "jds" / f"{name}.txt").read_text(encoding="utf-8")
    d = json.loads((EVAL / "expected" / f"{name}.json").read_text(encoding="utf-8"))
    ans = {"categories": {c: [] for c in P.CATEGORIES}}
    for it in d["items"]:
        ans["categories"][it["category"]].append({"text": it["key"], "importance": it["importance"], "importance_cue": None,
                                                  "source_text": it["source"], "origin": it["origin"],
                                                  "alternatives": it["alternatives"], "experience": None})
    for n in P.CONDITION_LISTS:
        ans[n] = []
    for c in d["conditions"]:
        ans[c["list"]].append({"text": c["key"], "category": c["category"], "reason": "", "source_text": c["source"]})
    return jd, ans


def seeded_results() -> list[dict]:
    import copy
    results = []

    def record(lang, kind, detail, expect, flags, detected, known_gap):
        results.append({"seed_set": "reference (development)", "language": lang, "defect": kind, "seed": detail,
                        "expected_flag": expect, "flags_observed": ";".join(sorted(flags)), "detected": detected,
                        "known_gap": known_gap})

    for name, lang in (("eval_en_field_service_01", "en"), ("eval_ar_facilities_01", "ar")):
        jd, ref = _reference(name)
        heads = {h["text"]: h for h in P.segment(jd)[1]}

        def run(kind, mutate, expect):
            m = copy.deepcopy(ref)
            detail = mutate(m)
            flags = {x["type"] for x in P.validate(jd, json.dumps(m, ensure_ascii=False))["review_queue"]}
            record(lang, kind, detail, expect, flags, expect in flags, "no")

        run("wrong_list", lambda m: _move_location(m), "wrong_list")
        run("lost_or", lambda m: _null_alts(m, "education"), "possible_lost_or")
        run("shorter_verbatim_quote", lambda m: _shorten(m, "experience"), "partial_quote")
        run("non_verbatim_quote", lambda m: _unverbatim(m), "source_not_verbatim")
        run("ambiguous_category_other", lambda m: _other_category(m), "unclassified_other")
        run("unknown_category", lambda m: _unknown_category(m), "unknown_category")
        sec = ("Requirements:", "Preferred:") if lang == "en" else ("المتطلبات:", "يفضل:")
        run("omitted_section", lambda m: _omit_section(m, jd, heads, *sec), "uncovered_statement")
        if lang == "en":
            run("bundled_and", lambda m: _merge_soft(m), "possible_bundled_and")
            run("or_split", lambda m: _split_or(m), "possible_or_split")
    # bare requirement line: quoted (detected as a heading-shaped quotation) and omitted (silent by construction)
    jd_en, ref_en = _reference("eval_en_field_service_01")
    bullet = "- Communication and teamwork skills."
    jd_bare = jd_en.replace(bullet, "Communication and teamwork skills")
    m = copy.deepcopy(ref_en)
    for it in m["categories"]["soft_skills"]:
        if "Communication and teamwork" in it["source_text"]:
            it["source_text"] = "Communication and teamwork skills"
    flags = {x["type"] for x in P.validate(jd_bare, json.dumps(m, ensure_ascii=False))["review_queue"]}
    record("en", "unpunctuated_requirement_quoted", "bare line quoted by the answer", "source_is_heading", flags,
           "source_is_heading" in flags, "no")
    m2 = copy.deepcopy(ref_en)
    m2["categories"]["soft_skills"] = [it for it in m2["categories"]["soft_skills"] if "Communication and teamwork" not in it["source_text"]]
    flags2 = {x["type"] for x in P.validate(jd_bare, json.dumps(m2, ensure_ascii=False))["review_queue"]}
    record("en", "unpunctuated_requirement_omitted", "bare line not quoted by the answer: surfaced as an ambiguous heading line, not as an omission",
           "ambiguous_heading_line", flags2, "ambiguous_heading_line" in flags2, "no")
    # Arabic attached و before the article: now a candidate (conservative rule); the Arabic AND pair seed
    jd_ar, ref_ar = _reference("eval_ar_facilities_01")
    m3 = copy.deepcopy(ref_ar)
    soft = m3["categories"]["soft_skills"]
    m3["categories"]["soft_skills"] = [dict(soft[0], text="merged")]
    flags3 = {x["type"] for x in P.validate(jd_ar, json.dumps(m3, ensure_ascii=False))["review_queue"]}
    record("ar", "bundled_and_attached_waw", "two items for 'مهارات التواصل والعمل ضمن فريق' merged; the second removed",
           "possible_attached_and", flags3, "possible_attached_and" in flags3, "no")
    # the documented limitation: an attached و before a verb is not matched
    jd_lim = "نبحث عن مهندس، وتحتاج الشركة إلى خبرة.\n"
    lim = _one_item_answer("x", "نبحث عن مهندس، وتحتاج الشركة إلى خبرة")
    flags4 = {x["type"] for x in P.validate(jd_lim, json.dumps(lim, ensure_ascii=False))["review_queue"]}
    record("ar", "attached_waw_before_verb_limitation", "an attached و before a verb (وتحتاج)", "possible_attached_and", flags4,
           "possible_attached_and" in flags4, "yes")
    return results


def _one_item_answer(item_text, source):
    ans = {"categories": {c: [] for c in P.CATEGORIES}}
    ans["categories"]["skills"].append({"text": item_text, "importance": "required", "importance_cue": None, "source_text": source,
                                        "origin": "stated", "alternatives": None, "experience": None})
    for n in P.CONDITION_LISTS:
        ans[n] = []
    return ans


def _move_location(m):
    c = [c for c in m["non_scoreable_requirements"] if c["category"] == "location"][0]
    m["non_scoreable_requirements"].remove(c)
    m["informational_items"].append(c)
    return f"location condition '{c['source_text'][:40]}' moved to informational_items"


def _null_alts(m, cat):
    for it in m["categories"][cat]:
        it["alternatives"] = None
    return f"alternatives set to null in category {cat}"


def _merge_soft(m):
    soft = m["categories"]["soft_skills"]
    m["categories"]["soft_skills"] = [dict(soft[0], text="Communication and teamwork skills")]
    return "the two communication and teamwork items merged into one"


def _split_or(m):
    it = m["categories"]["education"][0]
    second = dict(it, text="(second item)")
    m["categories"]["education"].append(second)
    return "the education item with alternatives duplicated as a second item"


def _shorten(m, cat):
    it = m["categories"][cat][0]
    words = it["source_text"].split(" ")
    it["source_text"] = " ".join(words[:2])
    return f"source_text shortened to its first two words: '{it['source_text']}'"


def _unverbatim(m):
    it = m["categories"]["education"][0]
    it["source_text"] = it["source_text"] + " (edited)"
    return "' (edited)' appended to a source_text"


def _other_category(m):
    c = [c for c in m["non_scoreable_requirements"] if c["category"] == "location"][0]
    c["category"] = "other"
    return "location category changed to 'other'"


def _unknown_category(m):
    c = [c for c in m["non_scoreable_requirements"] if c["category"] == "location"][0]
    c["category"] = "perks"
    return "location category changed to 'perks'"


def _omit_section(m, jd, heads, start_heading, end_heading):
    lo, hi = heads[start_heading]["end"], heads[end_heading]["start"]
    for cat in m["categories"]:
        m["categories"][cat] = [it for it in m["categories"][cat] if not (lo <= P.locate(jd, it["source_text"])["start"] < hi)]
    return "all items whose source lies in the requirements section removed"


# ── instructions and manifest ────────────────────────────────────────────────────────────────────────────────────────────

INSTRUCTIONS = """# Human review of the staged-validation checker, v2: reviewer instructions

Status: **offline, no judgments filled.** Every reviewer choice cell is blank. This package reports no precision, recall, agreement or
production readiness. Unit tests and the starter sample do not establish any of these.

## 1. What this review asks

The checker reads a job description (JD) and one stored answer in the existing output contract. It traces each item and condition to a
verbatim quotation, lists statements the answer does not cover, checks each condition's list against the rule 9 table, and flags candidate
OR and AND defects and candidate attached Arabic conjunctions. It decides none of these. Each row asks: **does the answer have the defect
the row names?** Each row shows the exact JD statement, the matched item (if any), the checker's warning and its rationale.

## 2. Status of the cases: all development evidence

Every case in this package has been inspected during development: the twelve benchmark cases (B01 to B12, six English and six Arabic), TC20,
and the English and Arabic eval JDs. **None is an independent, held-out or unseen validation set.** The benchmark answers come from the v2-2
prompt run with gpt-4o-mini-2024-07-18; TC20 and the eval answers come from gpt-4.1 runs. Run 2 of the benchmark cases is not included.

## 3. The two sheets

- `review_sheet_v2.csv`: every checker warning and probe for the stored answers.
- `starter_review_sheet_v2.csv`: twelve rows, chosen by the fixed rules in section 7 (two English and two Arabic per stratum). The starter is a
  sample to test the instructions and the choices. It is not a basis for any measure.

## 4. Row kinds

| row_kind | Meaning |
|---|---|
| checker_warning | A warning the checker raised. Includes uncovered statements, shortened quotations, wrong lists, unknown or other categories, possible lost OR, possible OR split, possible bundled AND, possible attached AND, source-is-heading and ambiguous heading lines. |
| location_probe | A location-category condition, with the list the answer placed it in. |
| checker_silent_probe | Something the checker does not warn about: an OR statement that no item quotes at all, or an Arabic word beginning with و that the conservative pattern does not match. |

Heading-shaped lines (no terminal punctuation, not bullets) that no quotation covers appear as `ambiguous_heading_line` warnings. They are
**not** counted as omissions.

## 5. Columns

`review_id`, `source_group`, `case_id`, `language`, `answer_label`, `row_kind`, `warning_type`: identify the row. `jd_statement_exact`: the JD
statement, copied exactly. `matched_item`: the path, category or list and text of the linked item or condition, or `none`.
`matched_source_text`: the quotation as written in the answer. `warning_text`: the checker's message. `rationale`: what the type means (fixed).
`mechanical_note`: a **fact** about the row, not a judgment (for example, "quote omits the label before the colon" or "partly quoting item keeps
no alternatives"). `reviewer_choice`: fill with one of `genuine defect`, `false alarm`, `ambiguous`. `reviewer_note`: optional, one line.

## 6. How to choose

For **checker warnings**:
- *genuine defect*: the answer has the defect the warning names. The warning is correct about the answer.
- *false alarm*: the answer does not have that defect. The warning is wrong.
- *ambiguous*: the JD or the rule allows both readings, so the text cannot settle it.

For **probes**, the choice describes the **answer**: *genuine defect* if the answer has the defect the row names (a location in the wrong list,
an OR list that should have been kept, a missed Arabic conjunction), *false alarm* if the answer is fine on that point, *ambiguous* if both readings
are defensible.

For **ambiguous heading lines**: *genuine defect* if the line is a requirement, duty or condition the answer did not extract; *false alarm* if it is
a title or section label that is correctly not extracted; *ambiguous* if it could be either.

## 7. Starter sheet: selection rules (fixed before the sample was drawn)

The sheet is sorted by case, then by review identifier. From that order, each stratum takes its first two English rows and its first two Arabic
rows.

1. **real_warning**: checker warnings of type wrong_list, unknown_category, possible_lost_or, possible_or_split, uncovered_statement or
   partial_quote. These are the warnings a reviewer is most likely to confirm.
2. **candidate_warning**: candidate-type warnings that may be false alarms, in the priority possible_bundled_and, possible_attached_and,
   ambiguous_heading_line, source_is_heading, then sheet order.
3. **unflagged_probe**: checker_silent_probe and location_probe rows, which may hide a defect the checker did not warn about.

## 8. What the checker cannot detect (not in any sheet)

- **Item category** (for example experience versus skills): not judged.
- **Importance** (Required or Preferred) beyond the cue check: not checked.
- **Experience fields** beyond the presence of the object: not checked.
- **Alternatives content**: only that alternatives hold two or more entries and that OR text has alternatives.
- **Whether a statement is a requirement, a duty, a condition or context**: an uncovered statement may be any of these.
- **Attached Arabic conjunctions** other than the conservative pattern (before ال or a Latin letter): not checked. Examples: وتحتاج, وإجازة.
- **The AND rule for duty lines**: AND candidates are raised only for stated requirements, not for duty lines (origin from_responsibilities), to avoid
  flagging lists of objects. A duty that joins two tasks with و is not raised.
- **Weights and warnings** in the answer.

## 9. Checker limitations (mechanical)

- A quotation that omits only the final punctuation, or a label before a colon, covers its statement. The mechanical note says so.
- Quotations from separate items that together cover a statement's substantive text, with only separators, whitespace or the conjunction
  و or "and" between them, are recorded as a split across items. They are not a partial quotation.
- A quotation, or a set of quotations, that leaves a substantive word unquoted remains a partial quotation, and the warning names the unquoted text.
- An OR statement quoted by two or more items is a candidate, and nothing is merged.
- Attached Arabic و is matched only before ال or a Latin letter. This is conservative: some conjunctions are missed (section 8).

## 10. Returning the sheets

Fill `reviewer_choice` and optionally `reviewer_note`. Do not change other columns. Keep the file names. Two reviewers should work
independently if possible. Agreement is computed only after both have filled the sheets, and not from this repository.
"""


def manifest_for(files: dict, summary: dict) -> dict:
    sources_hashes = {}
    for f in sorted(SRC.glob("*.jsonl")):
        sources_hashes[f"sources/{f.name}"] = sha_file(f)
    for f in sorted(BENCH.glob("*.json")):
        sources_hashes[f"benchmark cases/{f.name}"] = sha_file(f)
    for name in ("jds/eval_en_field_service_01.txt", "jds/eval_ar_facilities_01.txt", "expected/eval_en_field_service_01.json",
                 "expected/eval_ar_facilities_01.json"):
        sources_hashes[f"eval set/{name}"] = sha_file(EVAL / name)
    return {
        "package": "requirements_v2 staged-validation checker, human review, version 3 (offline)",
        "supersedes": "tests/fixtures/requirements_v2_review_package_v2 (v2, commit 27eee4d), kept unchanged with its reviews; "
                      "v1 (commit 347dc55) is kept unchanged as well",
        "v2_historical_builder": "scripts/historical/requirements_v2_review_package_v2.py with scripts/historical/requirements_v2_staged_check_v2.py",
        "v1_historical_builder": "scripts/historical/requirements_v2_review_package_v1.py with scripts/historical/requirements_v2_staged_check_v1.py",
        "builder": "scripts/requirements_v2_review_package.py",
        "builder_sha256": sha_file(pathlib.Path(__file__).resolve()),
        "checker_sha256": sha_file(BACKEND / "scripts" / "requirements_v2_staged_check.py"),
        "sources_sha256": sources_hashes,
        "source_provenance": {
            "sources/benchmark_v2-2_run1_calls.jsonl": {"derived_from": "7e3511c3-calls.jsonl (attachment)",
                                                       "derived_from_sha256": "4beac81b7aa08dfa6d12e68014acf6866b19a40417735d929396d4dae6e732f4",
                                                       "rows_kept": "the 12 rows with run == run1"},
            "sources/eval_gpt-4.1_calls.jsonl": {"derived_from": "1e1fcb97-calls.jsonl (attachment)",
                                                 "derived_from_sha256": "7a176b8ba6b78f1285187c1b72713b93aea76d2250f6b22e9b53e985c5df6f73",
                                                 "rows_kept": "the 12 rows whose jd is an eval JD"},
        },
        "outputs_sha256": {k: sha_bytes(v) for k, v in sorted(files.items())},
        "selection": {
            "answers": "benchmark run1 (12, one per case); TC20 routing CONTROL call 1 and scope CLARIFIED call 2; eval v2-3 and v2-5 call 1 (EN and AR)",
            "starter": "fixed strata and rules in section 7 of INSTRUCTIONS.md (two English and two Arabic rows per stratum)",
        },
        "summary": summary,
        "development_status": "every case is development evidence; no held-out set exists",
        "human_judgments": "none filled; every reviewer choice cell is blank",
        "precision_recall_or_readiness": "not computed: no human judgments exist and the sample is not a validation",
    }


def build() -> tuple[dict[str, bytes], dict]:
    files: dict[str, bytes] = {}
    summary: dict = {}
    out = Rows()
    per = []
    for s in sources():
        info = answer_rows(out, s["group"], s["case_id"], s["language"], s["label"], s["jd"], s["raw"])
        per.append({"case_id": s["case_id"], "answer": s["label"], "source_file": s["source_file"], "source_group": s["group"], **info})
    rows = out.merged()
    rows.sort(key=lambda r: (r["case_id"], r["answer_label"], r["row_kind"], r["warning_type"], r["jd_statement_exact"]))
    number_rows(rows, "V")
    files["review_sheet_v2.csv"] = csv_bytes(rows, COLUMNS)
    chosen = starter(rows)
    number_rows(chosen, "S")
    files["starter_review_sheet_v2.csv"] = csv_bytes(chosen, STARTER_COLUMNS)
    seeds = seeded_results()
    buf = io.StringIO()
    keys = ["seed_set", "language", "defect", "seed", "expected_flag", "flags_observed", "detected", "known_gap"]
    w = csv.DictWriter(buf, fieldnames=keys, lineterminator="\n")
    w.writeheader()
    for r in seeds:
        w.writerow({k: r[k] for k in keys})
    files["seeded_defect_results.csv"] = buf.getvalue().encode("utf-8")
    files["INSTRUCTIONS.md"] = INSTRUCTIONS.encode("utf-8")
    summary["sheet_rows"] = len(rows)
    summary["starter_rows"] = len(chosen)
    summary["starter_by_stratum"] = dict(collections.Counter(r["selection_stratum"] for r in chosen))
    summary["sheet_by_kind"] = dict(collections.Counter(r["row_kind"] for r in rows))
    summary["sheet_by_type"] = dict(collections.Counter(r["warning_type"] for r in rows))
    summary["answers"] = per
    summary["seeded"] = {"rows": len(seeds),
                         "detected_no_gap": sum(1 for r in seeds if r["detected"] is True and r["known_gap"] == "no"),
                         "known_gap_rows": sum(1 for r in seeds if r["known_gap"] == "yes")}
    return files, summary


def main() -> int:
    files, summary = build()
    PKG.mkdir(parents=True, exist_ok=True)
    files["MANIFEST.json"] = (json.dumps(manifest_for(files, summary), ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")
    for name, data in files.items():
        (PKG / name).write_bytes(data)
    print(json.dumps({k: v for k, v in summary.items() if k != "answers"}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
