"""Offline replay of STORED answers against the reference package. Calls nothing: reads stored calls.jsonl files and the package.

For every answer, two separate blocks:
  1. Uncovered spans (quotation coverage, prototype rules): reported for requirement units and for uncertain units separately; headings and
     background are not counted. Each span has the class punctuation_only | intro_wording | paraphrase | genuine_omission.
  2. Defects from the EXISTING scorers, reported separately and never added to the spans:
     OR (alternatives), AND (merged independent items), category, routing (conditions), item-level omissions (missing items), other fields.
     B-cases: scripts/requirements_v2_extraction_eval.score_case (frozen benchmark labels; author-written, not reviewer-confirmed).
     AND for B-cases: label groups of expected items sharing one evidence span must be separate items in the answer.
     TC20: the frozen scorer score_answer (C_* checks).

Models, prompts and runs are kept in separate groups; nothing is pooled across them.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parent.parent
sys.path[:0] = [str(HERE.parent / "omission_inventory"), str(BACKEND / "scripts"), str(BACKEND)]
import coverage as C  # noqa: E402
import inventory as I  # noqa: E402
import replay as R  # noqa: E402  (answer_quotes, classify_span: TC20 prototype, reused)
import requirements_v2_extraction_eval as E  # noqa: E402  (benchmark scorer: score_case; reused read-only)
import requirements_v2_tc20_eval as frozen  # noqa: E402  (TC20 scorer: score_answer; reused read-only)

BENCH_RUNS = BACKEND / "benchmark_results" / "requirements_v2"
STORED = [
    {"group": "TC20 repeat", "path": BACKEND / "tests" / "fixtures" / "requirements_v2_tc20_repeat" / "run" / "calls.jsonl", "kind": "tc20"},
    {"group": "TC20 audit", "path": BACKEND / "tests" / "fixtures" / "requirements_v2_technical_coordinator_20" / "audit" / "run_tc20_v2-3_v2-4" / "calls.jsonl", "kind": "tc20"},
    {"group": "benchmark run dir v2-2_run1", "path": BENCH_RUNS / "v2-2_run1" / "calls.jsonl", "kind": "bench"},
    {"group": "benchmark run dir baseline_v2-1", "path": BENCH_RUNS / "baseline_v2-1" / "calls.jsonl", "kind": "bench"},
]
PACKAGE_JDS = ["TC20", "B02_en_data_analyst", "B03_en_warehouse_supervisor", "B07_ar_accountant", "B08_ar_hr_specialist", "B12_ar_injection"]
RESULTS = HERE / "results"


def load_package():
    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    jds, cand = {}, {}
    for jd_id in PACKAGE_JDS:
        text = (HERE / "jd" / f"{jd_id}.txt").read_bytes().decode("utf-8")
        meta = manifest["jds"][jd_id]
        import hashlib  # noqa: PLC0415
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != meta["jd_sha256"]:
            raise SystemExit(f"{jd_id}: stored JD text does not match its recorded sha256")
        jds[jd_id] = {"text": text, "sha": meta["jd_sha256"]}
        c = json.loads((HERE / "annotations" / f"{jd_id}.candidate.json").read_text(encoding="utf-8"))
        cand[jd_id] = c
    return jds, cand


# Package-only extensions, documented in README: Arabic structural labels that open a sentence ("الشروط:" = "the terms:"), and one- or two-word
# function fragments (conjunctions, prepositions) left behind when a sentence is partly quoted. Neither is a requirement by itself.
AR_LABELS = {"الشروط", "المهام", "المتطلبات", "يفضل", "ملاحظة", "الأعمال", "اليومية"}
FUNCTION_WORDS = {"و", "أو", "في", "من", "على", "عن", "مع", "إلى", "a", "an", "and", "or", "of", "to", "in", "for", "with", "on", "at", "by"}


def classify_ref(span: dict, unit: dict, not_verbatim: list[str], title: frozenset) -> str:
    text = span["text"]
    toks = re.findall(r"\w+", text.lower())
    if not any(ch.isalnum() for ch in text):
        return "punctuation_only"
    if toks and len(toks) <= 2 and all(t in FUNCTION_WORDS for t in toks):
        return "connector_fragment"
    if span["start"] == unit["first_offset"] and toks and len(toks) <= 12 and all(t in R.LEAD_IN_WORDS | AR_LABELS | title for t in toks):
        return "intro_wording"
    return R.classify_span(span, unit, not_verbatim, title)["class"]


def jd_title_words(jd_id: str) -> frozenset:
    if jd_id == "TC20":
        return R.TITLE_WORDS
    import requirements_v2_extraction_eval as E2  # noqa: PLC0415
    case = next(c for c in E2.load_cases() if c["id"] == jd_id)
    return frozenset(re.findall(r"\w+", (case.get("job_metadata", {}).get("title") or case["title"]).lower()))


def inventory_for(jd_id, jd, cand):
    lines = cand["lines"]
    headings = [l["text"].strip() for l in lines if l["proposed_type"] == "heading"]
    background = [l["text"].strip() for l in lines if l["proposed_type"] == "background"]
    return I.build_inventory(jd["text"], {"jd_sha256": jd["sha"], "headings": headings, "background": background})


def type_of_line(cand) -> dict:
    return {l["first_line"]: l["proposed_type"] for l in cand["lines"]}


def and_groups(case) -> list[list[dict]]:
    """Label groups of two or more expected items that share one evidence span: independent skills that the label wants as separate items."""
    by_ev: dict[str, list[dict]] = {}
    for it in case["expected"]["items"]:
        by_ev.setdefault(it["evidence"], []).append(it)
    return [g for g in by_ev.values() if len(g) > 1]


def defects_bench(case, raw, finish):
    s = E.score_case(case, raw, finish)
    if not s.get("ok"):
        return {"parsed": False, "errors": s.get("errors")}
    field = s.get("field_errors", [])
    actual = E._actual_items(E.parse_response(raw, case["jd"], finish))
    and_split_missing, and_defects = [], []
    for g in and_groups(case):
        hits = [a for a in actual if any(E._norm(it["evidence"]) in E._norm(a.get("source_text") or "") for it in g)]
        distinct = len({a["text"] for a in hits})
        if distinct < len(g):
            and_defects.append({"evidence": g[0]["evidence"], "expected_items": [it["text"] for it in g], "answer_items": [a["text"] for a in hits]})
            and_split_missing += [it["text"] for it in g]
    missing_outside_and = [m for m in s.get("missing", []) if m not in and_split_missing]
    return {"parsed": True,
            "OR": sum(": alternatives " in e for e in field),
            "AND": len(and_defects),
            "category": sum(": category " in e for e in field),
            "routing": s.get("conditions_expected", 0) - s.get("conditions_routed", 0),
            "item_omissions": len(missing_outside_and),
            "other_field_errors": sum(not any(f": {k} " in e for k in ("alternatives", "category")) for e in field),
            "detail": {"OR_errors": [e for e in field if ": alternatives " in e], "AND": and_defects,
                       "category_errors": [e for e in field if ": category " in e], "missing": missing_outside_and}}


def _tc20_missing_lines(raw: str, labels: dict) -> int:
    """TC20 item-level omissions as LINES: duty lines with no from_responsibilities item containing them, plus competency lines with no
    item containing them. Same unit as the benchmark's item omissions (a missing item), so the two columns can be compared."""
    obj = json.loads(raw)
    items = [it for _, it in frozen._items(obj)]
    resp = [str(it.get("source_text", "")) for it in items if it.get("origin") == "from_responsibilities"]
    ev = labels["expected_values"]
    missing_duties = sum(1 for d in ev["duty_lines"] if not any(d in t for t in resp))
    missing_comp = sum(1 for c in ev["competency_lines"] if not any(c in str(it.get("source_text", "")) for it in items))
    return missing_duties + missing_comp


def defects_tc20(raw, jd, labels):
    out = frozen.score_answer(raw, jd, labels)
    ch = out["checks"]
    return {"parsed": out["valid_json"], "OR": int(not (ch["C_EXP_OR"] and ch["C_FAM_OR"])), "AND": int(not ch["C_AND"]),
            "category": int(not ch["C_LOCAL"]), "routing": int(not ch["C_LOCATION"]) + int(not ch["C_REPORTING"]),
            "item_omissions": _tc20_missing_lines(raw, labels), "other_field_errors": 0,
            "detail": {"failed_checks": [k for k, v in ch.items() if not v]}}


def replay_answer(rec, jd_id, jd, cand, labels, case, types):
    quotes = R.answer_quotes(rec["raw"])
    inv = inventory_for(jd_id, jd, cand)
    cov = C.cover(inv, jd["text"], quotes)
    not_verbatim = [v["quote"] for v in cov["quotes"] if v["status"] == "not_verbatim"]
    title = jd_title_words(jd_id)
    spans = {"requirement": [], "uncertain": []}
    for u in cov["units"]:
        kind = types.get(u["first_line"], "requirement")
        if kind not in spans:
            kind = "requirement"            # a heading or background unit is never a requirement denominator
        for sp in u["uncovered_spans"]:
            cls = classify_ref(sp, u, not_verbatim, title)
            spans[kind].append({"first_line": u["first_line"], "text": sp["text"], "class": cls, "unit_status": u["status"]})
    if jd_id == "TC20":
        defects = defects_tc20(rec["raw"], jd["text"], labels)
    else:
        defects = defects_bench(case, rec["raw"], rec.get("finish_reason"))
    return {"coverage": {k: cov["summary"][k] for k in ("covered", "partial", "omitted", "requirement_units")},
            "uncovered": spans, "defects": defects}


def prompt_of(src_path: pathlib.Path) -> str:
    """The prompt version named by the run's MANIFEST.json (benchmark runs); TC20 groups use the arm name."""
    manifest = src_path.parent / "MANIFEST.json"
    return json.loads(manifest.read_text(encoding="utf-8"))["prompt_version"] if manifest.exists() else "?"


def _rng(values):
    values = sorted(values)
    return str(values[0]) if values[0] == values[-1] else f"{values[0]}-{values[-1]}"


def write_report(data: dict) -> str:
    lines = ["# Multilingual omission-reference replay (stored answers only)", "",
             "Generated by `replay_ref.py` from `results/replay_ref.json`. No model was called. Each group is one model, one prompt and one run,",
             "and groups are never pooled. Uncovered spans and defects are separate blocks in every table.", "",
             "Coverage denominators: requirement units (candidate type requirement) and uncertain units, reported separately. Headings and",
             "background are not counted. Defects use the existing scorers and frozen benchmark labels (author-written, not reviewer-confirmed).", ""]
    for group, per_jd in data["groups"].items():
        lines += [f"## {group}", "", "| JD | answers | requirement units | genuine spans | paraphrase | intro wording | fragments | punctuation | uncertain-unit spans (genuine) | OR | AND | category | routing | item omissions |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        recurring = []
        for jd_id, answers in per_jd.items():
            ok = [a for a in answers if not a.get("invalid")]
            if not ok:
                lines.append(f"| {jd_id} | {len(answers)} invalid | | | | | | | | | | | | |")
                continue
            cnt = lambda kind, cls=None: [sum(1 for sp in a["uncovered"][kind] if cls is None or sp["class"] == cls) for a in ok]  # noqa: E731
            d = lambda k: [a["defects"].get(k, 0) for a in ok]  # noqa: E731
            lines.append(f"| {jd_id} | {len(ok)} | {ok[0]['coverage']['requirement_units']} | {_rng(cnt('requirement', 'genuine_omission'))} | "
                         f"{_rng(cnt('requirement', 'paraphrase'))} | {_rng(cnt('requirement', 'intro_wording'))} | "
                         f"{_rng(cnt('requirement', 'connector_fragment'))} | "
                         f"{_rng(cnt('requirement', 'punctuation_only'))} | {_rng(cnt('uncertain', 'genuine_omission'))} | "
                         f"{_rng(d('OR'))} | {_rng(d('AND'))} | {_rng(d('category'))} | {_rng(d('routing'))} | {_rng(d('item_omissions'))} |")
            genuine_sets = [{(sp["first_line"], sp["text"]) for sp in a["uncovered"]["requirement"] if sp["class"] == "genuine_omission"} for a in ok]
            common = set.intersection(*genuine_sets) if genuine_sets else set()
            recurring += [(jd_id, fl, t) for fl, t in sorted(common)]
        lines.append("")
        if recurring:
            lines += ["Genuine spans uncovered in every answer of this group (recurring):", ""]
            lines += [f"- {jd_id} L{fl}: {t[:110]}" for jd_id, fl, t in recurring]
            lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    jds, cands = load_package()
    labels = json.loads((BACKEND / "tests" / "fixtures" / "requirements_v2_technical_coordinator_20" / "expected_labels.json").read_text(encoding="utf-8"))
    cases = {c["id"]: c for c in E.load_cases()}
    groups: dict[str, dict[str, list]] = {}
    invalid = 0
    for src in STORED:
        for line in src["path"].read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if src["kind"] == "tc20":
                jd_id, prompt, run_label = "TC20", rec["arm"], f"arm {rec['arm']}"
                model = rec.get("model")
            else:
                jd_id = rec["case"]
                if jd_id not in PACKAGE_JDS:
                    continue
                prompt, run_label, model = prompt_of(src["path"]), f"run {rec['run']}", rec.get("returned_model")
            key = f"{src['group']} | model {model} | prompt {prompt} | {run_label}"
            if rec.get("error") or not rec.get("raw"):
                invalid += 1
                groups.setdefault(key, {}).setdefault(jd_id, []).append({"invalid": True, "error": rec.get("error")})
                continue
            res = replay_answer(rec, jd_id, jds[jd_id], cands[jd_id], labels, cases.get(jd_id), type_of_line(cands[jd_id]))
            groups.setdefault(key, {}).setdefault(jd_id, []).append(res)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "replay_ref.json").write_text(json.dumps({"groups": groups, "invalid_answers": invalid}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (RESULTS / "replay_report.md").write_text(write_report({"groups": groups}), encoding="utf-8")
    print(f"{len(groups)} groups, invalid answers: {invalid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
