"""Renders tests/fixtures/requirements_v2_benchmark/CASES.md from the case JSON files (keeps the review document in sync)."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from requirements_v2_extraction_eval import load_cases, CASES_DIR

def render() -> str:
    cases = load_cases()
    out = ["# requirements-v2 extraction benchmark: the 12 cases (for human review)", "",
           "Generated from `cases/*.json` by `scripts/_gen_benchmark_cases_md.py`; edit the JSON, not this file.",
           "All job descriptions are synthetic. Every evidence span, cue and injection phrase below is a literal substring of its JD.", "",
           "## Coverage matrix", "", "| Case | Lang | Tags | Expected readiness | Items |", "|---|---|---|---|---|"]
    for c in cases:
        e = c["expected"]
        out.append(f"| {c['id']} | {c['language']} | {', '.join(c['tags'])} | `{e['readiness']}` | {len(e['items'])} |")
    for c in cases:
        e = c["expected"]
        out += ["", f"## {c['id']} — {c['title']} ({c['language']})", "", f"Tags: {', '.join(c['tags'])}", "",
                "### Job description", "", "```text", c["jd"], "```", "",
                f"### Expected result — scoreability `{e['scoreability']}`, readiness `{e['readiness']}`, review codes {e['review_codes'] or 'none'}, "
                f"model warnings expected: {e['model_warnings_expected']}", ""]
        if e["items"]:
            out += ["| # | Category | Item | Class | Cue | Origin | Experience | Alternatives | Original evidence |", "|---|---|---|---|---|---|---|---|---|"]
            for n, i in enumerate(e["items"], 1):
                ex = i["experience"]
                exs = f"{ex['subject']} / {ex['min_years']}y" if ex and ex["min_years"] is not None else (f"{ex['subject']} / years null" if ex else "")
                alt = " ∣ ".join(i["alternatives"]) if i["alternatives"] else ""
                note = f" _({i['note']})_" if i.get("note") else ""
                out.append(f"| {n} | {i['category']} | {i['text']}{note} | **{i['importance']}** | {i['cue'] or ''} | {i['origin']} | {exs} | {alt} | {i['evidence']} |")
        else:
            out.append("_No scoreable items expected._")
        if e["conditions"]:
            out += ["", "Conditions (must NOT become scored items):", ""]
            out += [f"- `{k['list']}` ({k['category']}): {k['text']} — evidence: “{k['evidence']}”" for k in e["conditions"]]
        if e["must_not_extract"]:
            out += ["", "Must not appear in any extracted item: " + "; ".join(f"“{p}”" for p in e["must_not_extract"])]
        if e.get("injection"):
            inj = e["injection"]
            out += ["", "Embedded instructions (the JD is untrusted data):", ""]
            out += [f"- hard (any compliance fails gate G1): “{p}”" for p in inj["hard"]]
            out += [f"- soft (reported, not gated): “{p}”" for p in inj["soft"]]
        out += ["", f"Category weight hint (informational): {e['category_weights_hint']}"]
    return "\n".join(out) + "\n"

if __name__ == "__main__":
    p = CASES_DIR.parent / "CASES.md"
    p.write_text(render(), encoding="utf-8"); print("wrote", p)
