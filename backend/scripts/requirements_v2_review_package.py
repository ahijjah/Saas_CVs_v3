#!/usr/bin/env python3
"""Builds the offline human-review package for the staged-validation checker (no model call, no network).

It runs scripts/requirements_v2_staged_check.py over stored answers and writes, into tests/fixtures/requirements_v2_review_package/:
  review_sheet_unseen.csv   the 12 benchmark cases (never used to develop the checker), answers stored from the v2-2 run
  review_sheet_dev.csv      TC20 and the EN and AR eval job descriptions (used while developing the checker)
  seeded_defect_results.csv seeded mutations of the EN and AR references: detection BY CONSTRUCTION, kept apart from real answers
  INSTRUCTIONS.md           reviewer instructions, the three choices, what the checker cannot detect
  MANIFEST.json             every source, sha256, selection rule and row count

Reviewer choice columns are left blank by construction. No precision or recall is computed from human judgments here.
Mechanical notes state facts about a quotation (for example "equals the statement without its final period"); they are not judgments.
"""
from __future__ import annotations

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

PKG = BACKEND / "tests" / "fixtures" / "requirements_v2_review_package"
SRC = PKG / "sources"
BENCH = BACKEND / "tests" / "fixtures" / "requirements_v2_benchmark" / "cases"
EVAL = BACKEND / "tests" / "fixtures" / "requirements_v2_candidate_v25" / "eval_set"
ROUTING = BACKEND / "tests" / "fixtures" / "requirements_v2_routing_experiment" / "run" / "calls.jsonl"
SCOPE = BACKEND / "tests" / "fixtures" / "requirements_v2_scope_experiment" / "run" / "calls.jsonl"
PAIRED = BACKEND / "tests" / "fixtures" / "requirements_v2_candidate_v25" / "audit" / "run_v2-3_v2-5_20261010" / "calls.jsonl"
TERM = P.TERMINAL
OR_RE = re.compile(r"\bor\b|\beither\b|(?<!\S)أو(?!\S)", re.I)
AND_RE = re.compile(r",\s*and\b|\band\b|(?<!\S)و(?!\S)", re.I)
ATTACHED_WAW = re.compile(r"(?<!\S)و(?=[^\s\d\W]{2,})")          # a word that starts with و (attached or not): reviewer decides
CHOICES = ["genuine defect", "false alarm", "ambiguous"]
COLUMNS = ["review_id", "split", "case_id", "language", "answer_label", "row_kind", "warning_type", "jd_statement_exact",
           "matched_item", "matched_source_text", "warning_text", "rationale", "mechanical_note", "reviewer_choice", "reviewer_note"]

RATIONALE = {
    "uncovered_statement": "No quotation in the answer covers this statement. It may be background text, a requirement or duty the model did not extract, or an omission.",
    "partial_quote": "Only part of this statement is quoted. The checker counts a quote that covers part of a statement as partial coverage.",
    "source_not_verbatim": "The source_text of this item or condition does not occur in the job description, character for character or after whitespace.",
    "ambiguous_quote": "The source_text occurs more than once in the job description, so the span it points to is not certain.",
    "source_is_heading": "The source_text is a heading line, not a requirement line.",
    "whitespace_differs": "The source_text matches the job description only after whitespace changes.",
    "multi_statement_span": "One quotation spans more than one statement. For reporting lines this means several sentences sit in one item.",
    "wrong_list": "A condition sits in a list other than the one rule 9 assigns to its category (the checker's deterministic check against the rule 9 table).",
    "unclassified_other": "The condition has category 'other'. Rule 9 uses 'other' only when no label fits.",
    "unknown_category": "The condition's category is not a rule 9 label, so the checker cannot route it.",
    "possible_lost_or": "The statement joins options with 'or' but the item that quotes it keeps no alternatives. Candidate only: rule 4 decides.",
    "possible_bundled_and": "The statement joins independent requirements with 'and' or a comma but one item carries them all. Candidate only: rule 5 decides.",
    "alternatives_too_short": "An alternatives list holds fewer than two options.",
    "heading_candidate": "Classified as a heading: the line is not a bullet, has no terminal punctuation and has at most eight words. Decide whether it is a title or section label, or a requirement the checker did not extract.",
    "location_probe": "Rule 9 assigns the location category to non_scoreable_requirements. This row shows where the answer placed the location condition, so the placement can be confirmed.",
    "or_silent_probe": "The statement contains an 'or' but no quotation covers the whole statement, so the checker's lost-alternatives test did not run on it.",
    "attached_waw_probe": "The statement contains a word starting with the conjunction و. The checker detects only a standalone و, so an attached one is not checked. Decide whether this statement needs a split.",
    "or_split_probe": "One statement that joins options with 'or' is quoted by two or more items. Rule 4 keeps the options as ONE item with alternatives. The checker does not check for this.",
}
ROW_KIND = {"uncovered_statement": "checker_warning", "partial_quote": "checker_warning", "heading_candidate": "heading_candidate",
            "location_probe": "location_probe", "or_silent_probe": "checker_silent_probe", "attached_waw_probe": "checker_silent_probe",
            "or_split_probe": "checker_silent_probe"}


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha_file(p: pathlib.Path) -> str:
    return sha_bytes(p.read_bytes())


def annotate_partial(stmt: str, quotes: list[str]) -> str:
    """Mechanical facts about a partial quotation. These are not judgments."""
    core = stmt.rstrip(TERM + " ")
    notes = []
    for q in quotes:
        qc = q.rstrip(TERM + " ")
        if qc == core:
            notes.append("quote equals the statement without its terminal punctuation")
        elif ":" in stmt and qc == stmt.split(":", 1)[1].strip().rstrip(TERM):
            notes.append("quote omits the label before the colon")
        else:
            notes.append("quote covers only part of the statement")
    return "; ".join(sorted(set(notes)))


def load_answer(raw: str) -> dict:
    return json.loads(raw)


def entry_index(answer: dict) -> dict:
    out = {}
    for ent in P.entries_of(answer):
        out[ent["path"]] = ent
    return out


def describe_entry(ent: dict | None) -> str:
    if ent is None:
        return "none"
    e = ent["entry"]
    where = ent["category"] if ent["kind"] == "item" else f"{ent['list']} / category {ent['category']}"
    return f"{ent['path']} | {where} | {e.get('text', '')}"


class Rows:
    def __init__(self, split: str):
        self.split = split
        self.rows: list[dict] = []
        self.counts = {}

    def merged(self) -> list[dict]:
        """Probe and heading rows that repeat the same JD statement across answers of one case appear once; the answers are listed."""
        out, seen = [], {}
        for r in self.rows:
            if r["row_kind"] in ("checker_warning",):
                out.append(r)
                continue
            key = (r["case_id"], r["jd_statement_exact"], r["warning_type"], r["matched_item"])
            if key in seen:
                seen[key]["answer_label"] += "; " + r["answer_label"]
                continue
            seen[key] = dict(r)
            out.append(seen[key])
        return out

    def add(self, case_id, lang, label, kind, wtype, stmt, matched, source, warning, mech=""):
        r = {"review_id": "", "split": self.split, "case_id": case_id, "language": lang, "answer_label": label,
             "row_kind": ROW_KIND.get(wtype, kind), "warning_type": wtype, "jd_statement_exact": stmt,
             "matched_item": matched, "matched_source_text": source, "warning_text": warning,
             "rationale": RATIONALE.get(wtype, ""), "mechanical_note": mech, "reviewer_choice": "", "reviewer_note": ""}
        self.rows.append(r)


def answer_rows(out: Rows, case_id: str, lang: str, label: str, jd: str, raw: str, *, drop_punct_extra: bool = True) -> dict:
    st, hd = P.segment(jd)
    byid = {s["id"]: s for s in st}
    answer = load_answer(raw)
    ents = entry_index(answer)
    rep = P.validate(jd, raw)
    trace = {t["path"]: t for t in rep["trace"]}
    counts = {"checker_warning": 0, "heading_candidate": 0, "checker_silent_probe": 0, "location_probe": 0,
              "punctuation_only_partial_not_listed": 0}
    punct_seen = False
    mech_classes = {"quote equals the statement without its terminal punctuation": 0, "quote omits the label before the colon": 0,
                    "quote covers only part of the statement": 0}
    for x in rep["review_queue"]:
        t = x["type"]
        if t == "uncovered_statement":
            s = byid[x["statement"]]
            out.add(case_id, lang, label, "checker_warning", t, s["text"], "none", "", x["detail"], "no quotation covers the statement")
            counts["checker_warning"] += 1
        elif t == "partial_quote":
            s = byid[x["statement"]]
            quotes = [trace[p]["source_text"] for p in x["paths"]]
            mech = annotate_partial(s["text"], quotes)
            mech_classes[mech] = mech_classes.get(mech, 0) + 1
            if mech == "quote equals the statement without its terminal punctuation" and drop_punct_extra:
                if punct_seen:
                    counts["punctuation_only_partial_not_listed"] += 1
                    continue
                punct_seen = True
            matched = describe_entry(ents[x["paths"][0]])
            out.add(case_id, lang, label, "checker_warning", t, s["text"], matched, quotes[0], x["detail"], mech)
            counts["checker_warning"] += 1
        elif t in ("wrong_list", "unclassified_other", "unknown_category", "possible_lost_or", "possible_bundled_and",
                   "alternatives_too_short", "ambiguous_quote", "source_is_heading", "whitespace_differs", "multi_statement_span",
                   "source_not_verbatim"):
            p = x["path"]
            tr = trace.get(p)
            span = tr["span"] if tr else None
            stmts = [s["text"] for s in st if span and s["start"] < span[1] and span[0] < s["end"]] if span else []
            stmt = " | ".join(stmts) if stmts else "(no span in the job description)"
            ent = ents.get(p)
            out.add(case_id, lang, label, "checker_warning", t, stmt, describe_entry(ent),
                    (tr or {}).get("source_text") or "", x["detail"], f"path {p}")
            counts["checker_warning"] += 1
    # heading candidates: short unpunctuated lines the segmenter classed as headings
    first = min((h["start"] for h in hd), default=None)
    for h in hd:
        if h["text"].endswith(":"):
            continue
        note = "first line of the job description (title position)" if h["start"] == 0 or (first == h["start"]) else "not the first line"
        out.add(case_id, lang, label, "heading_candidate", "heading_candidate", h["text"], "none", "", "classified as a heading", note)
        counts["heading_candidate"] += 1
    # location probes: every condition in the location category, with its list
    for ent in P.entries_of(answer):
        if ent["kind"] == "condition" and ent["category"] == "location":
            src = ent["entry"].get("source_text") or ""
            loc = P.locate(jd, src) if src else None
            stmts = [s["text"] for s in st if loc and s["start"] < loc["end"] and loc["start"] < s["end"]]
            out.add(case_id, lang, label, "location_probe", "location_probe", " | ".join(stmts) or "(no span)", describe_entry(ent),
                    src, "placement of a location condition", f"placed in {ent['list']}")
            counts["location_probe"] += 1
    # checker-silent probes: OR statements without a whole-statement quotation; attached و; duplicate items
    cover_full: dict[str, list[dict]] = {s["id"]: [] for s in st}
    for path, t in trace.items():
        if t["span"] is None:
            continue
        for s in st:
            if t["span"][0] <= s["start"] and s["end"] <= t["span"][1]:
                cover_full[s["id"]].append(t)
    for s in st:
        covering = cover_full[s["id"]]
        if OR_RE.search(s["text"]) and not covering:
            partial = [t for t in trace.values() if t["span"] and s["start"] < t["span"][1] and t["span"][0] < s["end"]]
            if partial:
                ent = ents.get(partial[0]["path"])
                alts = ent["entry"].get("alternatives") if ent and ent["kind"] == "item" else None
                mech = "partly quoting item keeps alternatives" if alts else "partly quoting item keeps no alternatives"
                matched = describe_entry(ent)
                source = partial[0]["source_text"]
            else:
                mech, matched, source = "no item quotes any part of the statement", "none", ""
            out.add(case_id, lang, label, "checker_silent_probe", "or_silent_probe", s["text"], matched, source,
                    "no quotation covers the whole statement, so the lost-alternatives test did not run", mech)
            counts["checker_silent_probe"] += 1
        if lang == "ar" and ATTACHED_WAW.search(s["text"]) and len(covering) <= 1:
            words = sorted(set(m.group(0) for m in re.finditer(r"(?<!\S)و[^\s.،,؛:!?؟]+", s["text"])))
            out.add(case_id, lang, label, "checker_silent_probe", "attached_waw_probe", s["text"],
                    describe_entry(ents.get(covering[0]["path"])) if covering else "none", "",
                    "words starting with و: " + ", ".join(words), f"{len(covering)} quoting item(s)")
            counts["checker_silent_probe"] += 1
        overlapping = [t for t in trace.values() if t["span"] and t["kind"] == "item"
                       and s["start"] < t["span"][1] and t["span"][0] < s["end"]]
        if OR_RE.search(s["text"]) and len(overlapping) >= 2:
            items = [ents[t["path"]] for t in overlapping if t["path"] in ents]
            kept = sum(1 for e in items if e["entry"].get("alternatives"))
            texts = "; ".join(str(e["entry"].get("text")) for e in items)
            out.add(case_id, lang, label, "checker_silent_probe", "or_split_probe", s["text"],
                    " ;; ".join(describe_entry(e) for e in items), overlapping[0]["source_text"],
                    f"{len(overlapping)} items quote parts of this statement and it contains 'or'",
                    f"item texts: {texts}; alternatives kept on {kept} of {len(items)}")
            counts["checker_silent_probe"] += 1
    return {"coverage": rep["coverage"], "warnings_total": len(rep["review_queue"]), "listed": counts,
            "partial_quote_mechanical_classes": mech_classes}


def number_rows(rows: list[dict], prefix: str) -> None:
    for i, r in enumerate(rows, 1):
        r["review_id"] = f"{prefix}{i:03d}"


def write_csv(path: pathlib.Path, rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in COLUMNS})
    data = buf.getvalue().encode("utf-8")
    path.write_bytes(data)
    return data


# ── sources ──────────────────────────────────────────────────────────────────────────────────────────────────────────────

def unseen_sources() -> list[dict]:
    rows = [json.loads(x) for x in (SRC / "benchmark_v2-2_run1_calls.jsonl").read_text(encoding="utf-8").splitlines()]
    out = []
    for f in sorted(BENCH.glob("*.json")):
        case = json.loads(f.read_text(encoding="utf-8"))
        raw = [r for r in rows if r["case"] == case["id"]]
        assert len(raw) == 1 and raw[0]["error"] is None, case["id"]
        out.append({"case_id": case["id"], "language": case["language"], "jd": case["jd"], "raw": raw[0]["raw"],
                    "label": f"v2-2 run1 ({raw[0]['returned_model']})", "source_file": f.name})
    return out


def dev_sources() -> list[dict]:
    out = []
    jd_tc20, _ = ev.load_inputs()
    routing = [json.loads(x) for x in ROUTING.read_text(encoding="utf-8").splitlines()]
    scope = [json.loads(x) for x in SCOPE.read_text(encoding="utf-8").splitlines()]
    out.append({"case_id": "TC20", "language": "en", "jd": jd_tc20, "raw": [r for r in routing if r["order"] == 1][0]["raw"],
                "label": "routing run, CONTROL call 1 (gpt-4.1)", "source_file": "routing run calls.jsonl"})
    out.append({"case_id": "TC20", "language": "en", "jd": jd_tc20, "raw": [r for r in scope if r["order"] == 4][0]["raw"],
                "label": "scope run, CLARIFIED call 2 (gpt-4.1)", "source_file": "scope run calls.jsonl"})
    evals = [json.loads(x) for x in (SRC / "eval_gpt-4.1_calls.jsonl").read_text(encoding="utf-8").splitlines()]
    for name, lang in (("eval_en_field_service_01", "en"), ("eval_ar_facilities_01", "ar")):
        jd = (EVAL / "jds" / f"{name}.txt").read_text(encoding="utf-8")
        for arm in ("v2-3", "v2-5"):
            r = [x for x in evals if x["jd"] == name and x["arm"] == arm and x["call"] == 1][0]
            out.append({"case_id": name, "language": lang, "jd": jd, "raw": r["raw"],
                        "label": f"{arm} call 1 (gpt-4.1)", "source_file": "eval_gpt-4.1_calls.jsonl"})
    return out


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
    for name, lang in (("eval_en_field_service_01", "en"), ("eval_ar_facilities_01", "ar")):
        jd, ref = _reference(name)
        heads = {h["text"]: h for h in P.segment(jd)[1]}

        def run(kind, mutate, expect):
            m = copy.deepcopy(ref)
            detail = mutate(m)
            rep = P.validate(jd, json.dumps(m, ensure_ascii=False))
            types = sorted({x["type"] for x in rep["review_queue"]})
            results.append({"seed_set": "reference (dev)", "language": lang, "defect": kind, "seed": detail,
                            "expected_flag": expect, "flags_observed": ";".join(types),
                            "detected": expect in types, "known_gap": "no"})
        run("wrong_list", lambda m: _move_location(m), "wrong_list")
        run("lost_or", lambda m: _null_alts(m, "education"), "possible_lost_or")
        if lang == "en":
            run("bundled_and", lambda m: _merge_soft(m), "possible_bundled_and")
        run("shorter_verbatim_quote", lambda m: _shorten(m, "experience"), "partial_quote")
        run("non_verbatim_quote", lambda m: _unverbatim(m), "source_not_verbatim")
        run("ambiguous_category_other", lambda m: _other_category(m), "unclassified_other")
        run("unknown_category", lambda m: _unknown_category(m), "unknown_category")
        sec = ("Requirements:", "Preferred:") if lang == "en" else ("المتطلبات:", "يفضل:")
        run("omitted_section", lambda m: _omit_section(m, jd, heads, *sec), "uncovered_statement")
    # a requirement written as a bare, unpunctuated line: the segmenter classes it as a heading (known gap when the answer omits it)
    jd_en, ref_en = _reference("eval_en_field_service_01")
    bullet = "- Communication and teamwork skills."
    assert jd_en.count(bullet) == 1
    jd_bare = jd_en.replace(bullet, "Communication and teamwork skills")
    for quoted in (True, False):
        m = copy.deepcopy(ref_en)
        if quoted:
            for it in m["categories"]["soft_skills"]:
                if "Communication and teamwork" in it["source_text"]:
                    it["source_text"] = "Communication and teamwork skills"
            detail = "bare line; the answer quotes it without its final period"
        else:
            m["categories"]["soft_skills"] = [it for it in m["categories"]["soft_skills"] if "Communication and teamwork" not in it["source_text"]]
            detail = "bare line; the answer omits it; only the heading-candidate row can surface it"
        rep_ = P.validate(jd_bare, json.dumps(m, ensure_ascii=False))
        flags = sorted({x["type"] for x in rep_["review_queue"]})
        expect = "source_is_heading" if quoted else "none"
        results.append({"seed_set": "reference (dev)", "language": "en", "defect": "unpunctuated_requirement_as_heading",
                        "seed": detail, "expected_flag": expect if quoted else "none (silent by construction)",
                        "flags_observed": ";".join(flags), "detected": (expect in flags) if quoted else False,
                        "known_gap": "no" if quoted else "yes"})
    # attached و: the checker is designed not to detect it; recorded as a known gap, not a detection
    jd, ref = _reference("eval_ar_facilities_01")
    m = copy.deepcopy(ref)
    soft = m["categories"]["soft_skills"]
    m["categories"]["soft_skills"] = [dict(soft[0], text="merged")]
    rep = P.validate(jd, json.dumps(m, ensure_ascii=False))
    results.append({"seed_set": "reference (dev)", "language": "ar", "defect": "bundled_and_attached_waw",
                    "seed": "two items for 'مهارات التواصل والعمل ضمن فريق' merged into one; the second item removed",
                    "expected_flag": "possible_bundled_and", "flags_observed": ";".join(sorted({x["type"] for x in rep["review_queue"]})),
                    "detected": "possible_bundled_and" in {x["type"] for x in rep["review_queue"]}, "known_gap": "yes"})
    return results


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


def _shorten(m, cat):
    it = m["categories"][cat][0]
    before = it["source_text"]
    it["source_text"] = before.split(" ")[0] + " " + before.split(" ")[1]
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
    return "all items whose source lies in the Requirements section removed"


# ── build ────────────────────────────────────────────────────────────────────────────────────────────────────────────────

INSTRUCTIONS = """# Human review of the staged-validation checker: reviewer instructions

Status: **offline, no judgments filled.** Every reviewer choice cell is blank. No precision, recall or agreement is reported here, because
none exists until reviewers have filled the sheets. Keep seeded results separate from real-answer results (see the sheets and
`seeded_defect_results.csv`).

## 1. What the checker does, and what this review asks

The checker (`scripts/requirements_v2_staged_check.py`) reads a job description (JD) and one stored answer in the existing output contract.
It quotes back each item and condition's `source_text` to a JD statement, lists statements no quotation covers, checks each condition's list
against the rule 9 table, and flags possible lost OR alternatives and possible bundled AND items. It does not decide any of these.

Each row asks one question: **does the answer have the defect the row names?** The row also shows the exact JD statement, the matching
extracted item (if any), the checker's warning and its rationale.

## 2. Split of the cases

- **unseen** (`review_sheet_unseen.csv`): the 12 benchmark cases B01 to B12. The checker's code was not changed in response to them, and their
  expected results were not used to tune it. **They are not unseen in the strict sense:** while designing this package the author ran the
  checker on them and saw the warning counts, and chose the sampling and the probes after doing so. No other held-out set exists. The answers are the stored v2-2 prompt run 1, produced by **gpt-4o-mini-2024-07-18**, not gpt-4.1. Run 2 of the
  same cases is not sampled.
- **dev** (`review_sheet_dev.csv`): TC20 and the English and Arabic eval job descriptions. The checker's tests and seeded mutations were built on
  these. Answers: two TC20 answers (routing run, CONTROL call 1; scope run, CLARIFIED call 2) and the v2-3 and v2-5 call 1 answers for each eval JD,
  all gpt-4.1. Treat dev results as development evidence only.
- **seeded** (`seeded_defect_results.csv`): mutations of the English and Arabic reference answers. Detection here is **by construction**. These
  rows are not reviewed and are not evidence about real answers. Two rows are documented gaps and are recorded as `known_gap = yes` with
  `detected = False`: an attached Arabic و (not detected), and a bare unpunctuated requirement line that the answer omits (silent by
  construction; only a heading-candidate row can show it). A bare line the answer does quote is detected as `source_is_heading`.

## 3. Sampling (what is and is not in the sheets)

- One answer per unseen case and six dev answers. The TC20 answer with all sixteen responsibility lines missing is **not** included; the checker's
  behaviour on it is pinned by its tests.
- Punctuation-only partial quotes: the sheets list **one** per answer. The other such warnings in the same answer are counted in
  `MANIFEST.json` (`punctuation_only_partial_not_listed`) and are not in the sheet. Every warning class is otherwise listed in full.
- Heading candidates and probes that repeat the same JD statement across answers of one case appear once; the answer labels are joined.

## 4. Row kinds

| row_kind | Meaning |
|---|---|
| checker_warning | A warning the checker actually raised (uncovered background or omitted text, partial quote, wrong list, unknown or other category, possible lost OR, possible bundled AND, ambiguous or non-verbatim quote, and so on). |
| heading_candidate | A line the checker classed as a heading: not a bullet, no terminal punctuation, at most eight words. It may be a title or section label, or a requirement the checker did not extract. |
| location_probe | A condition in the location category, with the list the answer placed it in. Rule 9 assigns location to non_scoreable_requirements. |
| checker_silent_probe | Something the checker does **not** warn about but that this package asks you to look at: an OR statement with no whole-statement quotation (the lost-alternatives test did not run), an OR statement quoted by two or more items
  (rule 4 keeps the options as one item), and an attached Arabic و. |

## 5. Columns

- `review_id`, `split`, `case_id`, `language`, `answer_label`, `row_kind`, `warning_type`: identify the row.
- `jd_statement_exact`: the JD statement, copied exactly. `(no span in the job description)` when the checker found no span.
- `matched_item`: `path | category or list | text` of the extracted item or condition that the checker linked to the statement, or `none`.
- `matched_source_text`: the item's quoted source, as written in the answer.
- `warning_text`: the checker's message. For probes, the reason the probe was added.
- `rationale`: what the warning type means (fixed text).
- `mechanical_note`: a **fact** about the row, not a judgment. Examples: "quote equals the statement without its terminal punctuation",
  "quote omits the label before the colon", "partly quoting item keeps no alternatives", "placed in non_scoreable_requirements".
- `reviewer_choice`: **fill with one of** `genuine defect`, `false alarm`, `ambiguous`. Leave blank if unsure, and use `ambiguous` then.
- `reviewer_note`: optional, one line.

## 6. How to choose

For **checker warnings**:
- *genuine defect*: the answer has the defect the warning names. The warning is correct about the answer.
- *false alarm*: the answer does not have that defect. The warning is wrong.
- *ambiguous*: the JD or the rule allows both readings, so the warning cannot be settled from the text.

For **probes** (location, silent OR, attached و, duplicate) the choice describes the **answer**:
- *genuine defect*: the answer has the defect the row names (for example, a location in the wrong list, an OR list that should have been kept,
  an AND pair that should have been split, a duplicate item).
- *false alarm*: the answer is fine on that point.
- *ambiguous*: both readings are defensible.

For **heading candidates**:
- *genuine defect*: the line is a requirement, duty or condition that the checker did not extract.
- *false alarm*: the line is a title or section label and is correctly a heading.
- *ambiguous*: it could be either.

## 7. What the checker cannot detect (not in any sheet)

- **Item category** (for example experience versus skills). The checker does not judge categories of items. Wrong item categories are pinned as a gap.
- **Importance** (Required or Preferred) beyond the cue check. Not checked.
- **Experience fields** (subject, minimum years) beyond the presence of the object. Not checked.
- **Alternatives content**. The checker checks only that an alternatives list has two or more entries when present, and that OR text has alternatives.
  It does not check that the alternatives are the right options.
- **OR statements split across items** are probed, not warned about. The checker does not count the items per statement for OR.
- **Whether a statement is a requirement, a duty, a condition or context.** Uncovered statements may be any of these. Only the reviewer decides.
- **Weights and warnings** in the answer. Not checked.
- **Attached Arabic و** is detected only as a standalone token. Attached forms are probed, not warned.

## 8. Checker limitations found while building this package (mechanical, from the data)

- Partial-quote warnings are dominated by mechanical differences. The counts for every warning in the 12 unseen answers are in `MANIFEST.json`
  under `partial_quote_mechanical_classes`. Most partial quotes lack only the statement's terminal punctuation, or omit a label before a colon.
- The lost-OR and bundled-AND tests run only on statements a quotation covers in full. A quotation that lacks the final punctuation therefore
  switches those tests off. The silent-OR probes show this case.
- An OR statement split across items, each keeping the alternatives, is not warned about. Attached Arabic conjunctions are not warned about.

These are facts about the checker's behaviour on these answers. They are not claims about how often real reviewers would agree.

## 9. Returning the sheets

Fill `reviewer_choice` and optionally `reviewer_note`. Do not change other columns. Keep the sheet's file name. Two reviewers should work
independently if possible; agreement will be computed only after both have filled the sheets, and not from this repository.
"""


def manifest_for(files: dict, summary: dict) -> dict:
    sources = {}
    for f in sorted(SRC.glob("*.jsonl")):
        sources[f"sources/{f.name}"] = sha_file(f)
    for f in sorted(BENCH.glob("*.json")):
        sources[f"benchmark cases/{f.name}"] = sha_file(f)
    for name in ("jds/eval_en_field_service_01.txt", "jds/eval_ar_facilities_01.txt", "expected/eval_en_field_service_01.json",
                 "expected/eval_ar_facilities_01.json"):
        sources[f"eval set/{name}"] = sha_file(EVAL / name)
    return {
        "package": "requirements_v2 staged-validation checker, human review (offline)",
        "builder": "scripts/requirements_v2_review_package.py",
        "builder_sha256": sha_file(pathlib.Path(__file__).resolve()),
        "checker_sha256": sha_file(BACKEND / "scripts" / "requirements_v2_staged_check.py"),
        "sources_sha256": sources,
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
            "unseen": "benchmark cases B01 to B12, answers from the v2-2 run 1 (gpt-4o-mini-2024-07-18); one answer per case",
            "dev": "TC20 (routing CONTROL call 1; scope CLARIFIED call 2) and the EN and AR eval JDs (v2-3 and v2-5 call 1), gpt-4.1",
            "not_included": "TC20 paired v2-3 call 1 (all sixteen responsibility statements uncovered; behaviour pinned by tests)",
        },
        "summary": summary,
        "human_judgments": "none filled; every reviewer choice cell is blank",
        "precision_or_recall": "not computed: no human judgments exist",
        "unseen_disclosure": ("the checker was run on the unseen cases while this package was designed, and the sampling and probes were chosen "
                              "after seeing the warning counts; the checker's code was not changed in response"),
    }


def build() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    summary = {}
    for split, sources, prefix in (("unseen", unseen_sources(), "U"), ("dev", dev_sources(), "D")):
        out = Rows(split)
        per = []
        for s in sources:
            info = answer_rows(out, s["case_id"], s["language"], s["label"], s["jd"], s["raw"])
            per.append({"case_id": s["case_id"], "answer": s["label"], "source_file": s["source_file"], **info})
        rows = out.merged()
        rows.sort(key=lambda r: (r["case_id"], r["answer_label"], r["row_kind"], r["warning_type"], r["jd_statement_exact"]))
        number_rows(rows, prefix)
        files[f"review_sheet_{split}.csv"] = write_csv(PKG / f"review_sheet_{split}.csv", rows)
        summary[split] = {"rows": len(rows), "answers": per}
    seeds = seeded_results()
    buf = io.StringIO()
    keys = ["seed_set", "language", "defect", "seed", "expected_flag", "flags_observed", "detected", "known_gap"]
    w = csv.DictWriter(buf, fieldnames=keys, lineterminator="\n")
    w.writeheader()
    for r in seeds:
        w.writerow({k: r[k] for k in keys})
    files["seeded_defect_results.csv"] = buf.getvalue().encode("utf-8")
    files["INSTRUCTIONS.md"] = INSTRUCTIONS.encode("utf-8")
    summary["seeded"] = {"rows": len(seeds), "detected": sum(1 for r in seeds if r["detected"] is True and r["known_gap"] == "no"),
                         "known_gap_rows": sum(1 for r in seeds if r["known_gap"] == "yes")}
    return files, summary


def main() -> int:
    files, summary = build()
    files["MANIFEST.json"] = (json.dumps(manifest_for(files, summary), ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")
    for name, data in files.items():
        (PKG / name).write_bytes(data)
    print(json.dumps({k: (v if not isinstance(v, dict) or "rows" not in v else {"rows": v["rows"]}) for k, v in summary.items()}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
