"""Offline replay of STORED TC20 answers through the coverage prototype. No model is called; stored files are read, never changed.

For every answer this reports, separately:
  * quotation coverage: which requirement units the credited quotes account for (coverage.py);
  * each uncovered span, classified as exactly one of:
      - intro_wording:     the span is the unit's opening lead-in (for example "It is expected that") and only lead-in words;
      - paraphrase:        an answer quote that is NOT verbatim shares most of the span's content words (the answer restated the text);
      - genuine_omission:  neither of the above: no verbatim quote and no paraphrase accounts for this text. It is a candidate for
                           review, not a proven semantic omission;
  * OR/AND and routing correctness: the frozen checks in scripts/requirements_v2_tc20_eval.py, reused read-only.
Coverage is not extraction accuracy, and it is not semantic completeness.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parent.parent
sys.path[:0] = [str(HERE), str(BACKEND / "scripts")]
import coverage as C  # noqa: E402
import inventory as I  # noqa: E402
import requirements_v2_tc20_eval as frozen  # noqa: E402  (read-only reuse: score_answer and _items)
import requirements_v2_tc20_eval as ev  # noqa: E402  (the pinned job context, JOB_METADATA)

FIX = BACKEND / "tests" / "fixtures" / "requirements_v2_technical_coordinator_20"
SOURCES = {
    "repeat_gpt-4.1": BACKEND / "tests" / "fixtures" / "requirements_v2_tc20_repeat" / "run" / "calls.jsonl",
    "audit_gpt-4o-mini": FIX / "audit" / "run_tc20_v2-3_v2-4" / "calls.jsonl",
}
ANNOTATION = HERE / "annotations" / "tc20.json"
RESULTS = HERE / "results"
# Every list in the answer that can carry a source_text: the seven category arrays (via frozen._items) and these three.
LIST_KEYS = ("non_scoreable_requirements", "post_hiring_conditions", "informational_items")
LEAD_IN_WORDS = {"it", "is", "expected", "that", "also", "as", "well", "in", "addition", "the", "following", "through", "will", "be", "are"}
STOP = {"the", "a", "an", "of", "and", "to", "in", "for", "with", "on", "at", "by", "is", "be", "will", "that", "it", "as", "or", "from"}
PARAPHRASE_SHARE = 0.6


def load_jd():
    jd = json.loads((FIX / "evidence.json").read_text(encoding="utf-8"))["description"]
    labels = json.loads((FIX / "expected_labels.json").read_text(encoding="utf-8"))
    if I.sha256_text(jd) != labels["jd_sha256"]:
        raise SystemExit("TC20 JD does not match the frozen label hash")
    return jd, labels


def answer_quotes(raw: str) -> list:
    """Every quote the answer makes, in answer order: the category arrays and the three extra lists."""
    obj = json.loads(raw)
    quotes = [it.get("source_text") for _, it in frozen._items(obj)]
    for key in LIST_KEYS:
        quotes += [c.get("source_text") for c in (obj.get(key) or []) if isinstance(c, dict)]
    return quotes


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", text.lower()) if w not in STOP}


def classify_span(span: dict, unit: dict, not_verbatim: list[str], title_words: frozenset = frozenset()) -> dict:
    """Exactly one class per uncovered span, tested in this order:
      punctuation_only  no letters or digits (for example the final full stop): not a clause;
      intro_wording     at the unit's opening, at most 12 words, every word a lead-in word or a word of the job title (the title is
                        part of the pinned job context), and the rest of the unit is quoted: the answer dropped an opening phrase;
      paraphrase        a non-verbatim answer quote shares at least PARAPHRASE_SHARE of the span's content words;
      genuine_omission  none of the above: no verbatim quote and no paraphrase accounts for this text (a review candidate)."""
    if not any(ch.isalnum() for ch in span["text"]):
        return {"class": "punctuation_only", "by": None}
    words = re.findall(r"\w+", span["text"].lower())
    if (span["start"] == unit["first_offset"] and words and len(words) <= 12
            and all(w in LEAD_IN_WORDS or w in title_words for w in words)):
        return {"class": "intro_wording", "by": None}
    span_words = _words(span["text"])
    if span_words:
        for q in not_verbatim:
            share = len(span_words & _words(q)) / len(span_words)
            if share >= PARAPHRASE_SHARE:
                return {"class": "paraphrase", "by": q}
    return {"class": "genuine_omission", "by": None}


def job_title_words() -> frozenset:
    """Words of the job title in the pinned job context (the same context the baseline request uses)."""
    return frozenset(re.findall(r"\w+", ev.JOB_METADATA["title"].lower()))


TITLE_WORDS = job_title_words()


def replay(jd, labels, inv, path):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        row = {"arm": rec["arm"], "call": rec["call"]}
        try:
            quotes = answer_quotes(rec["raw"])
        except (TypeError, ValueError, AttributeError):
            row["invalid_json"] = True
            rows.append(row)
            continue
        row["invalid_json"] = False
        cov = C.cover(inv, jd, quotes)
        not_verbatim = [v["quote"] for v in cov["quotes"] if v["status"] == "not_verbatim"]
        for u in cov["units"]:
            for sp in u["uncovered_spans"]:
                sp.update(classify_span(sp, u, not_verbatim, TITLE_WORDS))
        row["coverage"] = cov
        row["frozen_checks"] = frozen.score_answer(rec["raw"], jd, labels)["checks"]
        rows.append(row)
    return rows


def summarise(row, duty_lines):
    cov = row["coverage"]
    spans = [(u, sp) for u in cov["units"] for sp in u["uncovered_spans"]]
    classes = {k: sum(1 for _, sp in spans if sp["class"] == k) for k in ("genuine_omission", "paraphrase", "intro_wording", "punctuation_only")}
    return {"arm": row["arm"], "call": row["call"], "invalid_json": False,
            "coverage": cov["summary"], "quote_counts": cov["quote_counts"], "repeated_quotes_in_answer": cov["repeated_quotes_in_answer"],
            "span_classes": classes,
            "uncovered": [{"first_line": u["first_line"], "status": u["status"], "text": sp["text"], "start": sp["start"],
                           "end": sp["end"], "class": sp["class"]} for u, sp in spans],
            "duty_omissions": [u["text"].strip() for u in cov["units"] if u["status"] == "omitted" and u["text"].strip() in duty_lines],
            "frozen_checks": row["frozen_checks"]}


def main() -> int:
    jd, labels = load_jd()
    inv = I.build_inventory(jd, json.loads(ANNOTATION.read_text(encoding="utf-8")))
    duties = set(labels["expected_values"]["duty_lines"])
    report = {"jd_sha256": inv.jd_sha256, "inventory": inv.diagnostics, "cover_threshold": C.COVER_THRESHOLD, "title_words": sorted(TITLE_WORDS),
              "paraphrase_share": PARAPHRASE_SHARE, "list_keys": list(LIST_KEYS), "sources": {}}
    for name, path in SOURCES.items():
        report["sources"][name] = [
            {"arm": r["arm"], "call": r["call"], "invalid_json": True} if r["invalid_json"] else summarise(r, duties)
            for r in replay(jd, labels, inv, path)]
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "replay.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print_summary(report)
    return 0


def print_summary(report):
    for name, per in report["sources"].items():
        print(f"== {name}")
        for p in per:
            if p["invalid_json"]:
                print(f"  {p['arm']} call {p['call']}: invalid JSON")
                continue
            s, c = p["coverage"], p["span_classes"]
            print(f"  {p['arm']} call {p['call']}: quoted {s['covered']}/{s['requirement_units']} (all text {s['all_text_quoted']}), "
                  f"omitted {s['omitted']}, partial {s['partial']} | spans: genuine {c['genuine_omission']}, paraphrase {c['paraphrase']}, "
                  f"intro {c['intro_wording']}, punct {c['punctuation_only']} | duty omissions {len(p['duty_omissions'])}")


if __name__ == "__main__":
    raise SystemExit(main())
