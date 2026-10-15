"""EXPERIMENTAL scorer v1 for the omission reference set. Offline, read-only, and NOT part of production scoring or candidate evaluation.

It replays stored answers and reports, separately:
  1. source-unit omissions: reviewed requirement units with no credited quotation (and word-bearing uncovered text);
  2. alternative structure: the Python-or-R unit (RQ-027) should be ONE item carrying both alternatives ("together");
  3. independent-item structure: the CSS/HTML unit (RQ-032) should yield two items (correctly split), not one (incorrectly merged) or none;
  4. child routing obligations: each decided child (RQ-029, RQ-030) is expected in its list: met / partial / misrouted / missing;
  5. importance: the Arabic cue "اختيارية" maps to preferred; required or preferred are accepted for the conflicting CSS/HTML items;
     an importance value outside the schema ("optional") is flagged as a schema finding;
  6. conflict evidence: reported as review evidence, with its exact text, and never used in the importance result.

The frozen benchmark scorer (scripts/requirements_v2_extraction_eval.py) is only imported and is not changed. Outputs go to results/ only.
"""
from __future__ import annotations

import csv
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parent.parent
sys.path[:0] = [str(HERE), str(HERE.parent / "omission_inventory"), str(BACKEND / "scripts"), str(BACKEND)]
import build_queue as Q  # noqa: E402
import coverage as C  # noqa: E402
import replay_ref as RR  # noqa: E402

VERSION = "omission-reference-experimental-scorer-v1"
CUE_MAP = {"اختيارية": "preferred"}                 # Arabic cue for optional, mapped to the schema's preferred
SCHEMA_IMPORTANCE = {"required", "preferred"}       # the schema has no optional value
ALTERNATIVE_UNITS = {("B02_en_data_analyst", 6): {"rq": "RQ-027", "alternatives": ["Python", "R"]}}   # S002: kept together


def _has(text: str, token: str) -> bool:
    """Whole-token match (case-insensitive): 'R' must not match the r inside 'Proficiency'."""
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", text, re.IGNORECASE) is not None


def _items(raw: str) -> list[dict]:
    obj = json.loads(raw)
    return [it for _, it in RR.frozen._items(obj)]


def alternative_structure(jd_id: str, unit_text: str, first_line: int, raw: str) -> dict | None:
    """Structure of the alternatives unit. 'together': one item holds both alternatives. 'split': each alternative is its own item.
    'incomplete': one item holds only one alternative. 'missing': neither. The alternatives each item records are reported as well,
    because a split item can still record an incomplete list."""
    spec = ALTERNATIVE_UNITS.get((jd_id, first_line))
    if spec is None:
        return None
    toks = spec["alternatives"]
    items = [it for it in _items(raw) if str(it.get("source_text", "")).strip()
             and (str(it["source_text"]).strip() in unit_text or unit_text in str(it["source_text"]).strip())]
    recorded = [{"text": str(it.get("text", "")), "alternatives": [str(a) for a in (it.get("alternatives") or [])]} for it in items]
    holding = [it for it in items if all(_has(str(it.get("text", "")), t) for t in toks)]
    found = [it for it in items if any(_has(str(it.get("text", "")), t) for t in toks)]
    each = [t for t in toks if any(_has(str(it.get("text", "")), t) for it in items)]
    if holding:
        status = "together" if sorted(str(a) for a in (holding[0].get("alternatives") or [])) == sorted(toks) else "together_alternatives_incomplete"
    elif len(found) >= 2 and each == toks:
        status = "split"
    elif len(found) == 1 and len(each) == 1:
        status = "incomplete"
    else:
        status = "missing"
    return {"rq_id": spec["rq"], "expected": "together", "structure": status, "alternatives_recorded": recorded}


def importance_checks(raw: str, cue_units: list[dict]) -> dict:
    """Importance of the conflicting items (CSS, HTML), found by the token inside each item's text, so a merged item is still seen. A value
    outside the schema ('optional') is a schema finding, not an importance result. The cue mapping is reported for review."""
    items = _items(raw)
    out = {"schema_findings": [], "cue_mapping": {c["cue"]: CUE_MAP[c["cue"]] for c in cue_units}}
    per_token = {}
    for tok in ("CSS", "HTML"):
        hits = [it for it in items if _has(str(it.get("text", "")), tok)]
        per_token[tok] = [it.get("importance") for it in hits]
        for it in hits:
            if it.get("importance") not in SCHEMA_IMPORTANCE:
                out["schema_findings"].append({"token": tok, "importance": it.get("importance")})
    out["items"] = per_token
    values = [v for vs in per_token.values() for v in vs]
    out["within_accepted"] = (all(v in SCHEMA_IMPORTANCE for v in values) if values else None)
    return out


def cue_units() -> list[dict]:
    """Reference-set units whose text carries an Arabic cue from CUE_MAP (the RQ-021 note)."""
    hits = []
    for jd_id in RR.PACKAGE_JDS:
        cand = json.loads((HERE / "annotations" / f"{jd_id}.candidate.json").read_text(encoding="utf-8"))
        for ln in cand["lines"]:
            for cue in CUE_MAP:
                if cue in ln["text"]:
                    hits.append({"jd_id": jd_id, "first_line": ln["first_line"], "cue": cue})
    return hits


def conflict_evidence() -> list[dict]:
    """The confirmed RQ-021 conflict, kept as evidence with its exact text. It is never used to compute importance."""
    out = []
    for path in sorted((HERE / "reviews").glob("CONFIRMED_*.csv"), key=lambda p: Q.batch_number(p.name)):
        import csv  # noqa: PLC0415
        for r in csv.DictReader(path.open(encoding="utf-8")):
            if r.get("relation", "").startswith("modifies_importance_of"):
                out.append({"rq_id": r["rq_id"], "conflict_unit": r["unit_id"], "statement": r["source_text"],
                            "against_unit": r["relation"].split(" ")[1], "role": "review evidence, not a finding"})
    return out


def item_sources(raw: str, tokens: list[str], required_text: str, conflict_text: str) -> dict:
    """For each token, where the item that names it was quoted from: the required unit, the conflict note, or elsewhere. An item quoted
    from the conflict note is evidence of the conflict, not a credited quotation of the required line."""
    out = {}
    for tok in tokens:
        srcs = []
        for it in _items(raw):
            if not _has(str(it.get("text", "")), tok):
                continue
            q = str(it.get("source_text", "")).strip()
            if q and q in required_text:
                srcs.append("required_unit")
            elif q and q in conflict_text:
                srcs.append("conflict_note")
            else:
                srcs.append("other")
        out[tok] = srcs
    return out


def score_answer(jd_id: str, raw: str, finish: str | None) -> dict:
    """Every experimental check for one stored answer. Coverage is computed per unit; the other checks use reference-set labels."""
    jds, cands = RR.load_package()
    jd, cand = jds[jd_id], cands[jd_id]
    types = RR.effective_types(jd_id, cand)
    inv = RR.inventory_for(jd_id, jd, cand, types)
    cov = C.cover(inv, jd["text"], RR.R.answer_quotes(raw))
    omissions = []
    for u in cov["units"]:
        if types.get(u["first_line"], "requirement") != "requirement":
            continue                       # only reviewed requirement units count as source units
        if u["status"] == "omitted" and any(ch.isalnum() for ch in u["text"]):
            omissions.append({"first_line": u["first_line"], "text": u["text"].strip()})
    alt_units = [u for u in cov["units"] if (jd_id, u["first_line"]) in ALTERNATIVE_UNITS]
    alternatives = [alternative_structure(jd_id, u["text"].strip().lstrip("- ").strip(), u["first_line"], raw) for u in alt_units]
    yields = RR.yield_obligations(jd_id, jd["text"], raw, cov["units"])
    children = RR.child_obligations(jd_id, jd["text"], raw)
    imp = importance_checks(raw, [c for c in cue_units() if c["jd_id"] == jd_id]) if jd_id == "B12_ar_injection" else None
    if jd_id == "B12_ar_injection":
        conflict_texts = [c["statement"] for c in conflict_evidence()]
        sheet = {r["unit_id"]: r["source_text"] for r in csv.DictReader((HERE / "reviewer_sheet.csv").open(encoding="utf-8"))}
        req_text = sheet["945b2154c3f2:L6:101-122"].lstrip("- ").strip()
        conf_text = sheet["945b2154c3f2:L12:182-245"]
        sources = item_sources(raw, ["CSS", "HTML"], req_text, conf_text)
        for y in yields:
            if y["rq_id"] == "RQ-032":
                y["item_sources"] = sources
    return {"version": VERSION, "jd_id": jd_id, "finish": finish,
            "source_unit_omissions": omissions,
            "alternative_structure": [a for a in alternatives if a],
            "independent_item_structure": [{"rq_id": y["rq_id"], "structure": y["structure"], "source_coverage": y["source_coverage"],
                                            "item_sources": y.get("item_sources", {})} for y in yields],
            "child_routing": [{"child_id": c["child_id"], "expected_list": c["expected_list"], "status": c["status"]} for c in children],
            "importance": imp,
            "conflict_evidence": conflict_evidence() if jd_id == "B12_ar_injection" else []}


def frozen_view(case: dict, raw: str, finish) -> dict:
    """The frozen benchmark scorer's output on one answer (read-only use of score_case)."""
    s = RR.E.score_case(case, raw, finish)
    return {"missing": s.get("missing", []), "merged": s.get("merged", []), "conditions": (s.get("conditions_routed"), s.get("conditions_expected")),
            "alt_errors": [e for e in s.get("field_errors", []) if ": alternatives " in e],
            "conflict": s.get("conflict", {}).get("surfaced") if s.get("conflict") else None}


def comparison_rows() -> list[str]:
    cases = {c["id"]: c for c in RR.E.load_cases()}
    rows = []
    for src in RR.STORED:
        if src["kind"] != "bench":
            continue
        for line in src["path"].read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec["case"] not in ("B02_en_data_analyst", "B03_en_warehouse_supervisor", "B12_ar_injection") or not rec.get("raw"):
                continue
            label = f"{src['group']} run {rec['run']} {rec['case'][:3]}"
            f = frozen_view(cases[rec["case"]], rec["raw"], rec.get("finish_reason"))
            e = score_answer(rec["case"], rec["raw"], rec.get("finish_reason"))
            if rec["case"] == "B02_en_data_analyst":
                a0 = e["alternative_structure"][0] if e["alternative_structure"] else None
                alt = f"{a0['structure']}; recorded {[(x['text'], x['alternatives']) for x in a0['alternatives_recorded']]}" if a0 else "n/a"
                rows.append(f"| S002 Python or R, one item with alternatives | {label}: frozen alternatives errors {len(f['alt_errors'])} | {label}: {alt} |")
            if rec["case"] == "B12_ar_injection":
                ind = e["independent_item_structure"][0]
                attribution = ", ".join(f"{t}←{'/'.join(v) or 'none'}" for t, v in ind["item_sources"].items())
                rows.append(f"| S008 CSS and HTML, two items | {label}: merged {len(f['merged'])}, missing {f['missing']} | {label}: {ind['structure']}; item sources {attribution} |")
                rows.append(f"| RQ-021 conflict and importance | {label}: conflict surfaced {f['conflict']} (in the label) | {label}: importance {e['importance']['items'] if e['importance'] else 'n/a'}; evidence kept separately |")
            if rec["case"] == "B03_en_warehouse_supervisor":
                routed = ", ".join(f"{c['child_id'].split('/')[1]}={c['status']}" for c in e["child_routing"])
                rows.append(f"| S010 / RQ-029 child routing | {label}: conditions routed {f['conditions'][0]} of {f['conditions'][1]} | {label}: {routed} |")
    return rows


def run() -> dict:
    """Replays every stored answer for the package JDs (no model call) and writes results/experimental_scorer_v1.json and .md."""
    out = {"version": VERSION, "frozen_scorer_unchanged": True, "answers": []}
    for src in RR.STORED:
        for line in src["path"].read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if src["kind"] == "tc20":
                jd_id, group = "TC20", f"{src['group']} | arm {rec['arm']} | call {rec['call']}"
            else:
                if rec["case"] not in RR.PACKAGE_JDS:
                    continue
                jd_id, group = rec["case"], f"{src['group']} | run {rec['run']}"
            if rec.get("error") or not rec.get("raw"):
                continue
            res = score_answer(jd_id, rec["raw"], rec.get("finish_reason"))
            out["answers"].append({"group": group, **res})
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "experimental_scorer_v1.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    lines = [f"# Experimental scorer {VERSION}", "",
             "Replay of stored answers only. Experimental: not production scoring, not candidate evaluation, and not a change to the frozen scorer.", "",
             "| group | JD | source-unit omissions | alternatives (RQ-027) | independent items (RQ-032) | child routing | importance (CSS/HTML) |",
             "|---|---|---|---|---|---|---|"]
    for a in out["answers"]:
        alt = ", ".join(x["structure"] for x in a["alternative_structure"]) or "n/a"
        ind = ", ".join(f"{x['rq_id']}={x['structure']}" for x in a["independent_item_structure"]) or "n/a"
        routing = ", ".join(f"{x['child_id'].split('/')[1]}={x['status']}" for x in a["child_routing"]) or "n/a"
        imp = ("n/a" if a["importance"] is None else
               f"{a['importance']['items']} within accepted: {a['importance']['within_accepted']}; schema findings: {len(a['importance']['schema_findings'])}")
        lines.append(f"| {a['group'].replace('|', '·')} | {a['jd_id']} | {len(a['source_unit_omissions'])} | {alt} | {ind} | {routing} | {imp} |")
    lines += ["", "Conflict evidence (review evidence, not used in importance):"]
    lines += [f"- {c['rq_id']}: {c['statement']} (against unit line {c['against_unit']})" for c in conflict_evidence()]
    lines += ["", "## Comparison with the frozen benchmark scorer (current behaviour on the same stored answers)", "",
              "| defect | frozen scorer output | experimental output |", "|---|---|---|"]
    lines += comparison_rows()
    (HERE / "results" / "experimental_scorer_v1.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    res = run()
    print(f"{VERSION}: {len(res['answers'])} stored answers replayed (no model call)")
