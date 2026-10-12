#!/usr/bin/env python3
"""Offline validation prototype for a staged extraction approach (Stage 1 inventory and Stage 4 checks), reusing stored outputs.

It makes NO model call. It takes a job description and one stored answer in the existing output contract (the single-call
shape) and returns a report that traces every item and condition to a verbatim span of the job description, lists the source
statements the answer does not cover, checks the routing of conditions against the label-to-list mapping in rule 9 of the prompt,
and flags possible OR and AND grouping defects. It never edits, repairs or adds to the answer, and it never claims semantic
completeness: text coverage is a surfaced signal for recruiter review, not proof.

  python3 backend/scripts/requirements_v2_staged_check.py --jd JD.txt --answer ANSWER.json

Outside the frozen requirements_v2 package. Reads the prompt file only to parse the rule 9 mapping.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

BACKEND = pathlib.Path(__file__).resolve().parent.parent
RULE9_SOURCE = BACKEND / "prompt_candidates" / "criteria_extraction_v2-3" / "criteria_extraction_v2-3.txt"
CATEGORIES = ("skills", "experience", "education", "certifications", "soft_skills", "domain_knowledge", "other_requirements")
CONDITION_LISTS = ("non_scoreable_requirements", "post_hiring_conditions", "informational_items")
TERMINAL = ".!?؟؛"
BULLET = re.compile(r"^\s*(?:[-•*]|\d+[.)])\s+")
OR_EN = re.compile(r"\bor\b|\beither\b", re.I)
OR_AR = re.compile(r"(?<!\S)أو(?!\S)")
AND_EN = re.compile(r",\s*and\b|\band\b", re.I)
AND_AR = re.compile(r"(?<!\S)و(?!\S)")          # standalone و
# Attached و, conservative: only when the word after و starts with the definite article ال or with a Latin letter ("والعمل",
# "وPower"). A word that merely begins with و (وظيفة, وفق, وزارة) is NOT matched. Other attached conjunctions (وتحتاج, وإجازة) are not
# matched: that is a review limitation, kept explicit in the review package.
ATTACHED_CONJ = re.compile(r"(?<!\S)و(?:ال[\u0600-\u06FF]|[A-Za-z])")
LABEL_PREFIX = re.compile(r"^(\S+(?: \S+){0,2}):\s+(\S.*)$")   # "Conditions: <text>": a label of at most three words
HEADING_MAX_WORDS = 8
# a sentence ends at terminal punctuation followed by space and a capital, digit, quote, bracket or Arabic letter (no case); a
# period followed by a lower-case letter continues the sentence ("Coordinator. will ...")
SENTENCE_END = r"[.!?؟؛]+(?=\s+[A-Z0-9\"“(\u0600-\u06FF]|\s*$)"


def sha(s: str | bytes) -> str:
    return hashlib.sha256(s.encode("utf-8") if isinstance(s, str) else s).hexdigest()


# ── the label-to-list mapping, read from rule 9 of the prompt ────────────────────────────────────────────────────────────

def _top_level_split(body: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


def parse_rule9_mapping(prompt_text: str) -> dict[str, str]:
    """label -> list name, from the rule 9 table of the prompt. The table's 'other' label is not mapped (it is always surfaced)."""
    block = prompt_text[prompt_text.index("9. Not scoreable"):prompt_text.index("10. Do not output")]
    mapping: dict[str, str] = {}
    for line in block.splitlines():
        m = re.match(r"\s*(non_scoreable_requirements|post_hiring_conditions|informational_items):\s*(.*)$", line)
        if not m:
            continue
        body = m.group(2).rstrip().rstrip(".")
        for seg in _top_level_split(body):
            label = re.match(r"\s*([a-z_]+)", seg)
            if label:
                mapping[label.group(1)] = m.group(1)
    return mapping


def rule9_mapping() -> dict[str, str]:
    return parse_rule9_mapping(RULE9_SOURCE.read_text(encoding="utf-8"))


# ── Stage 1: source statements, with stable references and verbatim offsets ─────────────────────────────────────────────

def _core_end(text: str, end: int) -> int:
    """End of the statement without its trailing terminal punctuation and spaces (terminal punctuation is not substantive)."""
    return end - (len(text) - len(text.rstrip(TERMINAL + " \t")))


def _sub_start(text: str, start: int) -> int | None:
    """Where the substantive text starts after a leading label, or None. Only with at least three words after the label."""
    m = LABEL_PREFIX.match(text)
    if m and len(m.group(2).split()) >= 3:
        return start + m.start(2)
    return None


def covers_statement(loc: dict, s: dict) -> bool:
    """True when the quotation covers the statement's substantive text. A missing final punctuation mark and a label before a colon
    are not substantive, so a quotation that omits them still covers the statement. Offsets of the quotation are not changed."""
    if s["core_end"] <= s["start"] or loc["end"] < s["core_end"]:
        return False
    if loc["start"] <= s["start"]:
        return True
    return s["sub_start"] is not None and loc["start"] <= s["sub_start"]


# Text allowed BETWEEN two quotations that jointly cover a statement: separators, whitespace and the conjunction و or "and" only.
# Any word in a gap means a substantive part is unquoted, so the statement is not jointly covered.
GAP_OK = re.compile(r"^[\s،,;؛.:\-–—]*(?:(?:و|and)[\s،,;؛.:]*)?$", re.I)


def jointly_covers(jd: str, s: dict, spans: list[tuple[int, int]]) -> tuple[bool, str]:
    """True when the quotations' spans, together, cover the statement's substantive text with no substantive gap.
    Returns (ok, the first unquoted substantive text, or '')."""
    a = s["sub_start"] if s["sub_start"] is not None else s["start"]
    b = s["core_end"]
    clipped = sorted((max(x, a), min(y, b)) for x, y in spans if min(y, b) > max(x, a))
    cur = a
    for x, y in clipped:
        if x > cur:
            gap = jd[cur:x]
            if not GAP_OK.match(gap):
                return False, gap.strip()
        cur = max(cur, y)
    if cur < b:
        tail = jd[cur:b]
        if not GAP_OK.match(tail):
            return False, tail.strip()
    return True, ""


def coverage_notes(loc: dict, s: dict) -> list[str]:
    """Mechanical facts about how a covering quotation differs from the statement (shown in the trace, never a warning)."""
    notes = []
    if loc["end"] < s["end"]:
        notes.append("terminal punctuation omitted")
    if loc["start"] > s["start"]:
        notes.append("label before the colon omitted")
    return notes


def segment(jd: str) -> tuple[list[dict], list[dict]]:
    """Statements (classified later) and headings (never classified, always surfaced). Each text is the exact slice jd[start:end]."""
    statements, headings = [], []
    idx = 0
    for line in jd.split("\n"):
        line_start = idx
        idx += len(line) + 1
        content = line.rstrip()
        if not content.strip():
            continue
        bullet = BULLET.match(content)
        body_start = line_start + (bullet.end() if bullet else len(content) - len(content.lstrip()))
        body = jd[body_start:line_start + len(content)]
        is_heading = (not bullet) and (body.endswith(":") or (body[-1] not in TERMINAL and len(body.split()) <= HEADING_MAX_WORDS))
        if is_heading:
            headings.append({"id": f"H{len(headings) + 1:03d}", "text": body, "start": body_start, "end": body_start + len(body)})
            continue
        pieces, cur = [], 0
        for m in re.finditer(SENTENCE_END, body):
            pieces.append((cur, m.end()))
            cur = m.end()
        if cur < len(body):
            pieces.append((cur, len(body)))
        for s, e in pieces:
            seg_text = body[s:e]
            lead = len(seg_text) - len(seg_text.lstrip())
            text = seg_text.strip()
            if not text:
                continue
            start = body_start + s + lead
            end = start + len(text)
            assert jd[start:end] == text
            statements.append({"id": f"S{len(statements) + 1:03d}-{sha(text)[:8]}", "text": text, "start": start, "end": end,
                               "core_end": _core_end(text, end), "sub_start": _sub_start(text, start)})
    return statements, headings


# ── verbatim location of a quotation ────────────────────────────────────────────────────────────────────────────────────

def locate(jd: str, quote: str) -> dict | None:
    """Exact occurrence first (character for character); a whitespace-only difference is reported separately. Never fuzzy."""
    if not quote or not quote.strip():
        return None
    exact = [m.start() for m in re.finditer(re.escape(quote), jd)]
    if exact:
        return {"start": exact[0], "end": exact[0] + len(quote), "occurrences": len(exact), "kind": "exact"}
    tokens = quote.split()
    ws = [m for m in re.finditer(r"\s+".join(map(re.escape, tokens)), jd)]
    if ws:
        return {"start": ws[0].start(), "end": ws[0].end(), "occurrences": len(ws), "kind": "whitespace_only"}
    return None


# ── the answer: every item and condition, traced ─────────────────────────────────────────────────────────────────────

def entries_of(answer: dict) -> list[dict]:
    out = []
    cats = answer.get("categories") if isinstance(answer.get("categories"), dict) else {}
    for cat, lst in cats.items():
        for i, it in enumerate(lst if isinstance(lst, list) else []):
            if isinstance(it, dict):
                out.append({"path": f"categories.{cat}[{i}]", "kind": "item", "category": cat, "entry": it})
    for name in CONDITION_LISTS:
        for i, c in enumerate(answer.get(name) if isinstance(answer.get(name), list) else []):
            if isinstance(c, dict):
                out.append({"path": f"{name}[{i}]", "kind": "condition", "list": name, "category": c.get("category"), "entry": c})
    return out


def validate(jd: str, raw: str) -> dict:
    """The report for one stored answer. `raw` is the model's response text and is never modified."""
    answer = json.loads(raw)
    mapping = rule9_mapping()
    statements, headings = segment(jd)
    report = {"jd_sha256": sha(jd), "raw_response_sha256": sha(raw), "model_calls": 0,
              "claims": {"text_matching_only": True, "semantic_completeness": "not established by this prototype"},
              "statements": [{"id": s["id"], "text": s["text"], "start": s["start"], "end": s["end"]} for s in statements],
              "headings": headings, "trace": [], "review_queue": []}
    review = report["review_queue"]
    covering: dict[str, list[str]] = {s["id"]: [] for s in statements}
    partial: dict[str, list[str]] = {s["id"]: [] for s in statements}
    partial_spans: dict[str, list[tuple[int, int]]] = {s["id"]: [] for s in statements}
    heading_spans = {(h["start"], h["end"]) for h in headings}
    quoted_spans: list[tuple[int, int]] = []
    for ent in entries_of(answer):
        src = ent["entry"].get("source_text")
        loc = locate(jd, src) if isinstance(src, str) else None
        row = {"path": ent["path"], "kind": ent["kind"], "source_text": src, "span": None, "match": None, "statements": []}
        report["trace"].append(row)
        if loc is None:
            review.append({"type": "source_not_verbatim", "path": ent["path"], "detail": "source_text does not occur in the job description"})
            continue
        row["span"] = [loc["start"], loc["end"]]
        row["origin"] = ent["entry"].get("origin") if ent["kind"] == "item" else None
        row["match"] = loc["kind"]
        if loc["kind"] == "whitespace_only":
            review.append({"type": "whitespace_differs", "path": ent["path"], "detail": "source_text matches only after whitespace changes"})
        if loc["occurrences"] > 1:
            review.append({"type": "ambiguous_quote", "path": ent["path"], "detail": f"source_text occurs {loc['occurrences']} times"})
        quoted_spans.append((loc["start"], loc["end"]))
        if (loc["start"], loc["end"]) in heading_spans:
            review.append({"type": "source_is_heading", "path": ent["path"],
                           "detail": "the quotation matches a heading-shaped line; review whether it is a requirement or a label"})
        inside = [s for s in statements if covers_statement(loc, s)]
        overlap = [s for s in statements if s["start"] < loc["end"] and loc["start"] < s["end"]]
        row["statements"] = [s["id"] for s in overlap]
        row["covers"] = [s["id"] for s in inside]
        row["notes"] = [f"{s['id']}: {n}" for s in inside for n in coverage_notes(loc, s)]
        if len(inside) >= 2:
            review.append({"type": "multi_statement_span", "path": ent["path"], "detail": f"source spans {len(inside)} statements"})
        for s in inside:
            covering[s["id"]].append(ent["path"])
        for s in overlap:
            if s not in inside:
                partial[s["id"]].append(ent["path"])
                partial_spans[s["id"]].append((loc["start"], loc["end"]))
        if ent["kind"] == "condition":
            cat, lst = ent["category"], ent["list"]
            if cat == "other":
                review.append({"type": "unclassified_other", "path": ent["path"], "detail": "category 'other' is only for statements that fit no label"})
            elif cat not in mapping:
                review.append({"type": "unknown_category", "path": ent["path"], "detail": f"category {cat!r} is not a rule 9 label"})
            elif mapping[cat] != lst:
                review.append({"type": "wrong_list", "path": ent["path"],
                               "detail": f"category {cat!r} belongs in {mapping[cat]}, found in {lst}"})
        else:
            if ent["category"] not in CATEGORIES:
                review.append({"type": "unknown_category", "path": ent["path"], "detail": f"item category {ent['category']!r} is not one of the seven"})
            alt = ent["entry"].get("alternatives")
            if alt is not None and (not isinstance(alt, list) or len(alt) < 2):
                review.append({"type": "alternatives_too_short", "path": ent["path"], "detail": "alternatives must hold two or more options"})
    coverage = {"statements": len(statements), "covered": 0, "short_quote": 0, "uncovered": 0}
    report["joint_split_statements"] = []
    for s in statements:
        if covering[s["id"]]:
            coverage["covered"] += 1
        elif partial[s["id"]] and jointly_covers(jd, s, partial_spans[s["id"]])[0]:
            # two or more quotations, with no substantive gap between them, cover the statement: a split, not a shortened quotation
            coverage["covered"] += 1
            report["joint_split_statements"].append({"statement": s["id"], "paths": partial[s["id"]]})
            for row in report["trace"]:
                if row["path"] in partial[s["id"]]:
                    row.setdefault("notes", []).append(f"{s['id']}: covered jointly with other quotations (split across items)")
        elif partial[s["id"]]:
            coverage["short_quote"] += 1
            _, gap = jointly_covers(jd, s, partial_spans[s["id"]])
            review.append({"type": "partial_quote", "statement": s["id"],
                           "detail": f"only part of the statement is quoted: {s['text'][:80]}"
                                     + (f"; unquoted substantive text: '{gap[:60]}'" if gap else ""),
                           "paths": partial[s["id"]]})
        else:
            coverage["uncovered"] += 1
            review.append({"type": "uncovered_statement", "statement": s["id"],
                           "detail": f"no item or condition quotes it (it may be context, a duty not extracted, or an omission): {s['text'][:80]}"})
    report["coverage"] = coverage
    for h in headings:
        if h["text"].endswith(":"):
            continue
        if any(a < h["end"] and h["start"] < b for a, b in quoted_spans):
            continue
        position = "first line of the job description" if h["start"] == 0 or h["start"] == min(x["start"] for x in headings) else "not the first line"
        review.append({"type": "ambiguous_heading_line", "heading": h["id"],
                       "detail": f"no quotation covers this line, which has no terminal punctuation and resembles a heading ({position}); "
                                 f"it may be a title or section label, or a requirement the answer did not extract: {h['text'][:80]}"})
    _or_and(report, statements, answer, mapping, review)
    items = [it for lst in (answer.get("categories") or {}).values() if isinstance(lst, list) for it in lst if isinstance(it, dict)]
    report["supplementary"] = {"items_per_category": {c: len(v) for c, v in (answer.get("categories") or {}).items() if isinstance(v, list)},
                               "conditions_per_list": {n: len(answer.get(n) or []) for n in CONDITION_LISTS},
                               "items_from_responsibilities": sum(1 for it in items if it.get("origin") == "from_responsibilities")}
    report["review_count"] = len(review)
    return report


def _or_and(report: dict, statements: list[dict], answer: dict, mapping: dict, review: list) -> None:
    """Possible OR and AND grouping defects, flagged for review and never repaired. Heuristic: text markers only."""
    by_id = {s["id"]: s for s in statements}
    for st in statements:
        if not (OR_EN.search(st["text"]) or OR_AR.search(st["text"])):
            continue
        quoting = [r for r in report["trace"] if r["kind"] == "item" and st["id"] in r.get("covers", [])]
        if len(quoting) >= 2:
            kept = sum(1 for r in quoting if _alternatives(answer, r["path"]))
            review.append({"type": "possible_or_split", "path": quoting[0]["path"], "paths": [r["path"] for r in quoting],
                           "statement": st["id"],
                           "detail": f"one statement with 'or' is quoted by {len(quoting)} items (alternatives kept on {kept}); "
                                     f"rule 4 keeps the options as one item. Not merged: a recruiter decides."})
    for row in report["trace"]:
        if row["kind"] != "item" or row["span"] is None:
            continue
        stmts = [by_id[i] for i in row["covers"] if i in by_id]
        if not stmts:
            continue
        text = " ".join(s["text"] for s in stmts)
        if (OR_EN.search(text) or OR_AR.search(text)) and not _alternatives(answer, row["path"]):
            review.append({"type": "possible_lost_or", "path": row["path"],
                           "detail": "the statement joins options with 'or' but the item keeps no alternatives"})
            continue
        if OR_EN.search(text) or OR_AR.search(text):
            continue
        has_and = bool(AND_EN.search(text) or AND_AR.search(text))
        attached = bool(ATTACHED_CONJ.search(text))
        if (has_and or attached) and row.get("origin") == "stated":
            n_items = sum(1 for r in report["trace"] if r["kind"] == "item" and r["span"] is not None
                          and set(r["covers"]) & {s["id"] for s in stmts})
            if n_items == 1 and has_and:
                review.append({"type": "possible_bundled_and", "path": row["path"],
                               "detail": "the statement joins independent requirements with 'and' or a comma but one item carries them all"})
            elif n_items == 1:
                review.append({"type": "possible_attached_and", "path": row["path"],
                               "detail": "a word begins with the conjunction و attached to the definite article or a Latin word; "
                                         "a candidate only (a word that merely begins with و is not matched). Other attached forms are not checked"})


def _alternatives(answer: dict, path: str):
    m = re.match(r"categories\.([a-z_]+)\[(\d+)\]$", path)
    if not m:
        return None
    return answer["categories"][m.group(1)][int(m.group(2))].get("alternatives")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Offline validation of a stored answer against its job description (no model call).")
    ap.add_argument("--jd", type=pathlib.Path, required=True)
    ap.add_argument("--answer", type=pathlib.Path, required=True)
    args = ap.parse_args(argv)
    jd = args.jd.read_text(encoding="utf-8")
    raw = args.answer.read_text(encoding="utf-8")
    print(json.dumps(validate(jd, raw), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
