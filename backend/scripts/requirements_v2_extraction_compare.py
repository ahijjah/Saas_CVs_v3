"""
requirements-v2 extraction: baseline-versus-candidate COMPARISON REPORT (offline; reads recorded runs only, never calls a model).

  python scripts/requirements_v2_extraction_compare.py --candidate DIR [--baseline DIR] [--out DIR]

A run directory holds calls.jsonl (and optionally meta.json / results.json) as written by requirements_v2_extraction_run.py.
Both runs are re-scored here with the FROZEN scorer (scripts/requirements_v2_extraction_eval.py: same parser, labels, matching and
gates). The saved results.json, when present, is cross-checked against the re-score.

DIAGNOSTICS are reporting only. They split the unmatched items into three separate causes and never feed back into a score:
  truly omitted               no item with that wording was returned
  present, invalid evidence   the item is there, but the parser could not accept its source_text (not found in the job description,
                              or a span that joins extra text such as a heading to the entry)
  valid, rejected by matching the item is there with evidence that IS in the job description (a sub-span, a list-marker prefix), but the
                              benchmark's strict evidence-equality matching does not pair it with the expected item
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import requirements_v2_extraction_eval as ev  # noqa: E402  (the frozen scorer)
from services.requirements_v2.extraction import parse_response  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent
BASELINE_DIR = BACKEND / "benchmark_results" / "requirements_v2" / "baseline_v2-1"
_MARKER = re.compile(r"^\s*(?:[-*•·]|\d+[.)])\s*")
RUNS = ("run1", "run2")


# ── loading + re-scoring ───────────────────────────────────────────────────────────────────────────────────────────
def load_records(run_dir: Path) -> list[dict]:
    return [json.loads(x) for x in (run_dir / "calls.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]


def answers_of(records: list[dict]) -> dict:
    runs: dict[str, dict] = {r: {} for r in RUNS}
    for r in records:
        if r.get("raw") and not r.get("quarantined"):
            runs[r["run"]][r["case"]] = {"raw": r["raw"], "finish_reason": r["finish_reason"]}
    return runs


def rescore(cases: list[dict], runs: dict) -> dict:
    scored = ev.score_runs(cases, runs)
    scored["gates"] = ev.gates(scored)
    by_lang = {}
    for lang in ("en", "ar"):
        sub = [c for c in cases if c["language"] == lang]
        ids = {c["id"] for c in sub}
        sr = ev.score_runs(sub, {r: {k: v for k, v in a.items() if k in ids} for r, a in runs.items()})
        by_lang[lang] = {"summary": sr["summary"], "consistency": sr["consistency"]}
    scored["by_language"] = by_lang
    return scored


def run_metadata(run_dir: Path, records: list[dict]) -> dict:
    meta_f, man_f = run_dir / "meta.json", run_dir / "MANIFEST.json"
    if meta_f.exists():
        m = json.loads(meta_f.read_text(encoding="utf-8"))
        src = "meta.json"
    elif man_f.exists():
        m = json.loads(man_f.read_text(encoding="utf-8"))
        src = "MANIFEST.json (the baseline's per-call records predate the metadata fields)"
    else:
        m, src = {}, "none"
    versions = {(r.get("prompt_version"), r.get("prompt_sha256")) for r in records if r.get("prompt_version")}
    return {"source": src, "prompt_version": m.get("prompt_version"), "prompt_sha256": m.get("prompt_sha256"), "model": m.get("model"),
            "settings": m.get("settings"), "per_call_prompt_records": sorted(map(list, versions))}


def usage_of(records: list[dict]) -> dict:
    ok = [r for r in records if r.get("usage")]
    tin, tout = sum(r["usage"]["prompt_tokens"] for r in ok), sum(r["usage"]["completion_tokens"] for r in ok)
    lat = [r["latency_s"] for r in records if r.get("latency_s") is not None]
    return {"calls": len(records), "calls_with_usage": len(ok), "errors": sum(1 for r in records if r.get("error")), "input_tokens": tin,
            "output_tokens": tout, "total_tokens": tin + tout, "cost_usd": round(tin * 0.15e-6 + tout * 0.60e-6, 5),
            "latency_total_s": round(sum(lat), 1), "latency_max_s": max(lat) if lat else None,
            "returned_models": sorted({r.get("returned_model") for r in ok}), "finish_reasons": sorted({r.get("finish_reason") for r in ok})}


# ── diagnostics (reporting only) ─────────────────────────────────────────────────────────────────────────────────────
def _strip_marker(s: str) -> str:
    return ev._norm(_MARKER.sub("", s or ""))


def diagnose_items(case: dict, res) -> dict:
    """Split every expected item that the scorer did not match, and every unmatched returned item, into separate causes."""
    exp, act = case["expected"]["items"], ev._actual_items(res)
    pairs = ev._match(exp, act)
    used = set(pairs.values())
    codes: dict[str, set] = {}
    for i in res.review:
        if i.item_id:
            codes.setdefault(i.item_id, set()).add(i.code)
    cands = sorted(((-ev._jacc(e["text"], a["text"]), n, j) for n, e in enumerate(exp) if n not in pairs
                    for j, a in enumerate(act) if j not in used and ev._jacc(e["text"], a["text"]) >= 0.5))
    present: dict[int, int] = {}
    taken: set[int] = set()
    for _, n, j in cands:
        if n not in present and j not in taken:
            present[n] = j
            taken.add(j)
    out: dict = {"matched": len(pairs), "truly_omitted": [], "present_invalid_evidence": [], "valid_rejected_by_matching": [],
                 "extra_split_alternative_half": [], "extra_other": []}
    for n, e in enumerate(exp):
        if n in pairs:
            continue
        if n not in present:
            out["truly_omitted"].append({"expected": e["text"], "importance": e["importance"], "origin": e["origin"]})
            continue
        a = act[present[n]]
        evs = [ev._norm(e["evidence"])] + [ev._norm(x) for x in e.get("alt_evidence", [])]
        row = {"expected": e["text"], "returned": a["text"], "returned_source_text": a.get("source_text"), "codes": sorted(codes.get(a["id"], []))}
        src = ev._norm(a["source_text"]) if a.get("source_text") else None
        if src is None:
            out["present_invalid_evidence"].append({**row, "subtype": "source_text_not_found_in_job_description"})
        elif any(_strip_marker(a["source_text"]) == x for x in evs):
            out["valid_rejected_by_matching"].append({**row, "subtype": "list_marker_prefix"})
        elif any(src in x for x in evs):
            out["valid_rejected_by_matching"].append({**row, "subtype": "sub_span_of_expected_evidence"})
        elif any(x in src and len(src) > len(x) for x in evs):
            out["present_invalid_evidence"].append({**row, "subtype": "over_wide_span_joins_other_text"})
        else:
            out["valid_rejected_by_matching"].append({**row, "subtype": "different_valid_span"})
    for j, a in enumerate(act):
        if j in used or j in taken:
            continue
        src = ev._norm(a["source_text"]) if a.get("source_text") else None
        half = any(src and ev._norm(act[j2]["source_text"] or "") == src and exp[n].get("alternatives")
                   and ev._toks(a["text"]) <= ev._toks(exp[n]["text"]) for n, j2 in pairs.items())
        (out["extra_split_alternative_half"] if half else out["extra_other"]).append({"returned": a["text"], "category": a["category"]})
    out["expected_total"] = len(exp)
    assert out["expected_total"] == out["matched"] + len(out["truly_omitted"]) + len(out["present_invalid_evidence"]) + len(out["valid_rejected_by_matching"])
    return out


def diagnose_conditions(case: dict, res) -> dict:
    lists = res.conditions or {}
    act_items = ev._actual_items(res)
    out = {"expected_total": len(case["expected"]["conditions"]), "routed": 0, "wrong_list": [], "truly_omitted": [], "present_invalid_evidence": [], "as_scored_item": []}
    for k in case["expected"]["conditions"]:
        ev_n = ev._norm(k["evidence"])

        def hit(lst):
            return any(x.get("source_text") and (ev_n in ev._norm(x["source_text"]) or ev._norm(x["source_text"]) in ev_n) for x in lists.get(lst, []))
        if hit(k["list"]):
            out["routed"] += 1
        elif any(hit(l) for l in lists):
            out["wrong_list"].append({"expected": k["text"], "expected_list": k["list"], "got": [l for l in lists if hit(l)]})
        else:
            inv = [(l, x) for l, v in lists.items() for x in v if not x.get("source_text") and ev._jacc(k["text"], x["text"]) >= 0.5]
            scored = [a["text"] for a in act_items if a.get("source_text") and ev_n in ev._norm(a["source_text"])]
            if scored:
                out["as_scored_item"].append({"expected": k["text"], "expected_list": k["list"], "returned_as_item": scored})
            elif inv:
                out["present_invalid_evidence"].append({"expected": k["text"], "list": inv[0][0], "right_list": inv[0][0] == k["list"]})
            else:
                out["truly_omitted"].append({"expected": k["text"], "expected_list": k["list"]})
    return out


def diagnose_runs(cases: list[dict], runs: dict) -> dict:
    """Per run and case diagnostics. Read-only: it re-parses the recorded raw text and touches no score."""
    result: dict = {}
    for run, answers in runs.items():
        per_case, tot = {}, {"expected_items": 0, "matched": 0, "truly_omitted": 0, "present_invalid_evidence": 0, "valid_rejected_by_matching": 0,
                             "extra_split_alternative_half": 0, "extra_other": 0, "conditions_expected": 0, "conditions_routed": 0,
                             "conditions_wrong_list": 0, "conditions_omitted": 0, "conditions_invalid_evidence": 0, "conditions_as_scored_item": 0}
        for c in cases:
            a = answers.get(c["id"])
            if not a:
                per_case[c["id"]] = {"not_parsed": True}
                continue
            res = parse_response(a["raw"], c["jd"], a.get("finish_reason", "stop"))
            if not res.ok:
                per_case[c["id"]] = {"not_parsed": True}
                continue
            it, cd = diagnose_items(c, res), diagnose_conditions(c, res)
            per_case[c["id"]] = {"items": it, "conditions": cd}
            tot["expected_items"] += it["expected_total"]
            for k in ("matched",):
                tot[k] += it[k]
            for k in ("truly_omitted", "present_invalid_evidence", "valid_rejected_by_matching", "extra_split_alternative_half", "extra_other"):
                tot[k] += len(it[k])
            tot["conditions_expected"] += cd["expected_total"]
            tot["conditions_routed"] += cd["routed"]
            tot["conditions_wrong_list"] += len(cd["wrong_list"])
            tot["conditions_omitted"] += len(cd["truly_omitted"])
            tot["conditions_invalid_evidence"] += len(cd["present_invalid_evidence"])
            tot["conditions_as_scored_item"] += len(cd["as_scored_item"])
        result[run] = {"totals": tot, "per_case": per_case}
    return result


# ── the comparison ───────────────────────────────────────────────────────────────────────────────────────────────────
METRICS = (("item recall", lambda s: s["recall"]), ("item precision", lambda s: s["precision"]),
           ("importance accuracy", lambda s: _r(s["field_checks"]["importance"])), ("min_years exact", lambda s: _r(s["field_checks"]["min_years"])),
           ("alternatives exact", lambda s: _r(s["field_checks"]["alternatives"])), ("readiness ok (non-conflict, of 10)", lambda s: s["readiness_ok_nonconflict"]),
           ("conditions routed", lambda s: _r(s["conditions"])), ("conflict items surfaced", lambda s: f'{s["conflict"]["surfaced"]}/{s["conflict"]["items"]}'),
           ("hard injection compliance (cases)", lambda s: s["injection_hard"]), ("required downgraded", lambda s: s["required_downgraded"]),
           ("calls parsed (of 12)", lambda s: s["parsed"]))


def _r(pair):
    return round(pair[0] / pair[1], 3) if pair[1] else None


def compare(cases: list[dict], baseline_dir: Path, candidate_dir: Path) -> dict:
    side = {}
    for name, d in (("baseline", baseline_dir), ("candidate", candidate_dir)):
        recs = load_records(d)
        runs = answers_of(recs)
        scored = rescore(cases, runs)
        gates_before = copy.deepcopy(scored["gates"])
        diag = diagnose_runs(cases, runs)
        assert ev.gates(scored) == gates_before == scored["gates"], "diagnostics must not change any gate"
        saved = None
        if (d / "results.json").exists():
            sv = json.loads((d / "results.json").read_text(encoding="utf-8"))
            saved = {"gates_match": sv.get("gates") == scored["gates"], "summary_match": sv.get("summary") == json.loads(json.dumps(scored["summary"])),
                     "consistency_match": sv.get("consistency") == json.loads(json.dumps(scored["consistency"]))}
        side[name] = {"dir": str(d), "meta": run_metadata(d, recs), "usage": usage_of(recs), "scored": scored, "diagnostics": diag, "saved_results_check": saved}
    b, c = side["baseline"]["scored"], side["candidate"]["scored"]
    gate_rows = [{"gate": g, "baseline": b["gates"][g], "candidate": c["gates"].get(g),
                  "change": ("regressed" if b["gates"][g] and not c["gates"].get(g) else "improved" if not b["gates"][g] and c["gates"].get(g) else "same")}
                 for g in b["gates"]]
    per_case = []
    for cs in cases:
        row = {"case": cs["id"], "language": cs["language"]}
        for name in ("baseline", "candidate"):
            for run in RUNS:
                rec = next((r for r in side[name]["scored"]["runs"][run] if r["case"] == cs["id"]), {})
                row[f"{name}_{run}"] = {"recall": rec.get("recall"), "precision": rec.get("precision"), "readiness": rec.get("readiness"), "readiness_ok": rec.get("readiness_ok")}
        per_case.append(row)
    # items matched in the baseline that the candidate no longer matches (by expected wording), per run
    lost = {}
    for run in RUNS:
        for cs in cases:
            bm = _matched_texts(cs, side["baseline"]["scored"], run)
            cm = _matched_texts(cs, side["candidate"]["scored"], run)
            for t in sorted(bm - cm):
                lost.setdefault(run, []).append(f'{cs["id"]}: {t}')
    return {"report_version": "req-v2-compare-1", "scorer_version": ev.EVAL_VERSION, "sides": side, "gates": gate_rows, "per_case": per_case,
            "regressions": {"gates": [r["gate"] for r in gate_rows if r["change"] == "regressed"], "items_matched_in_baseline_not_in_candidate": lost},
            "note": "Diagnostics are reporting only: they never change a gate or a score. Gates are the frozen G1-G12."}


def _matched_texts(case: dict, scored: dict, run: str) -> set:
    rec = next((r for r in scored["runs"][run] if r["case"] == case["id"]), {})
    if not rec.get("ok"):
        return set()
    missing = set(rec.get("missing", []))
    return {i["text"] for i in case["expected"]["items"]} - missing


# ── markdown ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def _fmt(v):
    return f"{v:.3f}" if isinstance(v, float) else str(v)


def _cell(v):
    return "PASS" if v is True else "FAIL" if v is False else "n/a" if v is None else str(v)


def render_markdown(rep: dict) -> str:
    S = rep["sides"]
    L = ["# requirements-v2 extraction: baseline vs candidate", "",
         "Gates are the frozen G1-G12 (same parser, labels, matching, thresholds). Diagnostics below are reporting only.", "",
         "## Runs", "", "| | baseline | candidate |", "|---|---|---|"]
    for k in ("prompt_version", "prompt_sha256", "model"):
        L.append(f"| {k} | {S['baseline']['meta'][k]} | {S['candidate']['meta'][k]} |")
    for k in ("calls", "errors", "input_tokens", "output_tokens", "total_tokens", "cost_usd", "latency_total_s", "latency_max_s"):
        L.append(f"| {k} | {S['baseline']['usage'][k]} | {S['candidate']['usage'][k]} |")
    L += ["", "## Gates", "", "| gate | baseline | candidate | change |", "|---|---|---|---|"]
    L += [f"| {g['gate']} | {_cell(g['baseline'])} | {_cell(g['candidate'])} | {g['change']} |" for g in rep["gates"]]
    L += ["", "## Metrics per run", "", "| metric | base run1 | base run2 | cand run1 | cand run2 |", "|---|---|---|---|---|"]
    for name, fn in METRICS:
        L.append(f"| {name} | " + " | ".join(_fmt(fn(S[s]["scored"]["summary"][r])) for s in ("baseline", "candidate") for r in RUNS) + " |")
    for s in ("baseline", "candidate"):
        c = S[s]["scored"]["consistency"]
        L.append(f"| consistency ({s}) | mean Jaccard {c['mean_jaccard']:.3f}, classification agreement {c['classification_agreement']}, readiness agreement {c['readiness_agreement']} | | | |")
    L += ["", "## English / Arabic", "", "| | metric | base run1 | base run2 | cand run1 | cand run2 |", "|---|---|---|---|---|---|"]
    for lang in ("en", "ar"):
        for name, fn in METRICS[:5]:
            vals = [_fmt(fn(S[s]["scored"]["by_language"][lang]["summary"][r])) for s in ("baseline", "candidate") for r in RUNS]
            L.append(f"| {lang} | {name} | " + " | ".join(vals) + " |")
    L += ["", "## Unmatched items by cause (diagnostics; gates unaffected)", "",
          "| cause | base run1 | base run2 | cand run1 | cand run2 |", "|---|---|---|---|---|"]
    for key, label in (("expected_items", "expected items"), ("matched", "matched by the scorer"), ("truly_omitted", "truly omitted"),
                       ("present_invalid_evidence", "present, invalid evidence"), ("valid_rejected_by_matching", "valid, rejected by benchmark matching"),
                       ("extra_split_alternative_half", "extra: split OR half"), ("extra_other", "extra: other"),
                       ("conditions_expected", "conditions expected"), ("conditions_routed", "conditions routed"), ("conditions_wrong_list", "conditions: wrong list"),
                       ("conditions_omitted", "conditions: truly omitted"), ("conditions_invalid_evidence", "conditions: present, invalid evidence"),
                       ("conditions_as_scored_item", "conditions returned as scored items")):
        L.append(f"| {label} | " + " | ".join(str(S[s]["diagnostics"][r]["totals"][key]) for s in ("baseline", "candidate") for r in RUNS) + " |")
    L += ["", "## Per case (recall / precision)", "", "| case | base run1 | base run2 | cand run1 | cand run2 |", "|---|---|---|---|---|"]
    for row in rep["per_case"]:
        cells = []
        for s in ("baseline", "candidate"):
            for r in RUNS:
                d = row[f"{s}_{r}"]
                cells.append("-" if d["recall"] is None else f'{d["recall"]:.2f} / {d["precision"]:.2f}')
        L.append(f'| {row["case"]} | ' + " | ".join(cells) + " |")
    L += ["", "## Regressions", "", f"Gates that passed in the baseline and fail in the candidate: {rep['regressions']['gates'] or 'none'}"]
    for run, items in rep["regressions"]["items_matched_in_baseline_not_in_candidate"].items():
        L += ["", f"Items matched in the baseline but not in the candidate ({run}):"] + [f"- {x}" for x in items]
    for s in ("baseline", "candidate"):
        chk = S[s]["saved_results_check"]
        L += ["", f"Saved results.json vs re-score ({s}): {chk if chk else 'no results.json'}"]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidate", required=True); ap.add_argument("--baseline", default=str(BASELINE_DIR)); ap.add_argument("--out")
    a = ap.parse_args(argv)
    rep = compare(ev.load_cases(), Path(a.baseline), Path(a.candidate))
    md = render_markdown(rep)
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "comparison.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        (out / "comparison.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
