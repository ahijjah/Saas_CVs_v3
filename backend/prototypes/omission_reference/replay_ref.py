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

import csv
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parent.parent
sys.path[:0] = [str(HERE), str(HERE.parent / "omission_inventory"), str(BACKEND / "scripts"), str(BACKEND)]
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


CHILD_OBLIGATION = {"non-scoreable condition": "non_scoreable_requirements", "post-hiring condition": "post_hiring_conditions",
                    "informational benefit": "informational_items"}


def answer_quotes_by_list(raw: str) -> dict:
    """Quotes of each list in the answer. 'categories' holds the seven category arrays; the other three are the condition and info lists."""
    obj = json.loads(raw)
    cats = [it.get("source_text") for _, it in frozen._items(obj)]
    out = {"categories": cats}
    for key in ("non_scoreable_requirements", "post_hiring_conditions", "informational_items"):
        out[key] = [c.get("source_text") for c in (obj.get(key) or []) if isinstance(c, dict)]
    return out


def child_fraction(jd_text: str, quotes: list, a: int, b: int) -> float:
    """Share of the child's non-space characters [a, b) credited by the quotes (unique, verbatim, exact offsets)."""
    verdicts = C.judge_quotes(jd_text, quotes)
    credited = set()
    for v in verdicts:
        if v["status"] == "unique":
            s0 = v["occurrences"][0]
            credited.update(range(s0, s0 + len(v["quote"])))
    positions = [p for p in range(a, b) if not jd_text[p].isspace()]
    return sum(1 for p in positions if p in credited) / len(positions) if positions else 0.0


def child_obligations(jd_id: str, jd_text: str, raw: str) -> list[dict]:
    """Each DECIDED child with an extraction obligation is checked against its expected list. Children are never scored criteria."""
    import build_queue as Q  # noqa: PLC0415
    lists = answer_quotes_by_list(raw)
    out = []
    for c in Q.load_children():
        if c["jd_id"] != jd_id or not c["semantic_role_decision"]:
            continue
        expected = CHILD_OBLIGATION.get(c["semantic_role_decision"])
        if expected is None:
            continue
        a, b = int(c["content_start"]), int(c["content_end"])
        frac_expected = child_fraction(jd_text, lists[expected], a, b)
        everything = [q for key in lists for q in lists[key]]
        frac_any = child_fraction(jd_text, everything, a, b)
        if frac_expected >= C.COVER_THRESHOLD:
            status = "met"
        elif frac_any >= C.COVER_THRESHOLD:
            status = "misrouted"
        elif frac_expected > 0:
            status = "partial"
        else:
            status = "missing"
        out.append({"child_id": c["child_id"], "role": c["semantic_role_decision"], "expected_list": expected,
                    "status": status, "fraction_expected_list": round(frac_expected, 4), "fraction_any_list": round(frac_any, 4)})
    return out


IMPORTANCE_VALUES = {"required", "preferred"}   # the schema's importance values, as used by the benchmark scorer


def accepted_importance(rq_id: str) -> set | None:
    """Accepted importance values for a yield unit, from the amendment files (reviews/AMENDMENT_*.csv). None when no amendment sets one."""
    accepted = None
    for path in sorted((HERE / "reviews").glob("AMENDMENT_*.csv")):
        with path.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row["rq_id"] == rq_id and row["field"] == "importance_expectation":
                    accepted = {v for v in IMPORTANCE_VALUES if v in row["corrected_value"]}
    return accepted


def yield_obligations(jd_id: str, jd_text: str, raw: str, cov_units: list) -> list[dict]:
    """One source-coverage obligation per source unit that yields independent items (batch seven). The unit counts once in coverage. Its
    items are checked for STRUCTURE only: missing, correctly split (independent items), or incorrectly merged. Importance is reported apart
    and is checked against the accepted values from the amendment (required or preferred for RQ-032). Not scored."""
    import build_queue as Q  # noqa: PLC0415
    obj = json.loads(raw)
    items = [it for _, it in frozen._items(obj)]
    cov_by_line = {u["first_line"]: u["status"] for u in cov_units}
    out = []
    for sub in Q.load_submissions().values():
        if sub.get("jd_id") != jd_id or not sub.get("yields_independent_items"):
            continue
        unit_text = sub["source_text"].lstrip("- ").strip()
        relevant = [it for it in items if str(it.get("source_text", "")).strip()
                    and (str(it.get("source_text", "")).strip() in unit_text or unit_text in str(it.get("source_text", "")).strip())]
        tokens = [t.strip() for t in sub["yields_independent_items"].split(",")]
        found = {}
        for t in tokens:
            # An item is identified by its own text; the quotation is used only when the item has no text of its own.
            hits = [i for i, it in enumerate(relevant) if t.lower() in str(it.get("text") or it.get("source_text", "")).lower()]
            found[t] = hits[0] if hits else None
        missing = [t for t, v in found.items() if v is None]
        if missing:
            structure = "missing: " + ", ".join(missing)
        elif len(set(found.values())) < len(tokens):
            structure = "incorrectly merged"
        else:
            structure = "correctly split"
        importance = {t: (relevant[found[t]].get("importance") if found[t] is not None else None) for t in tokens}
        accepted = accepted_importance(sub["rq_id"])
        within = None if (missing or accepted is None) else all(v in accepted for v in importance.values())
        out.append({"rq_id": sub["rq_id"], "unit_line": int(sub["first_line"]), "yields": tokens,
                    "source_coverage": cov_by_line.get(int(sub["first_line"]), "absent"), "source_coverage_obligations": 1,
                    "structure": structure, "importance": importance,
                    "importance_accepted": sorted(accepted) if accepted is not None else None,
                    "importance_within_accepted": within,
                    "conflict_evidence": sub.get("conflict_evidence", "")})
    return out


def effective_types(jd_id: str, cand) -> dict:
    """The type used for replay, by first line. The candidate type is the default. A CONFIRMED decision (reviews/CONFIRMED_*.csv) overrides
    it when the decision is a resolved type. An 'uncertain' decision is UNRESOLVED: it is neither a requirement nor an exclusion. The
    candidate annotation files themselves are never changed."""
    import build_queue as Q  # noqa: PLC0415
    subs = Q.load_submissions()
    out = {}
    for l in cand["lines"]:
        kind = l["proposed_type"]
        prefix = f"{cand['jd_sha256'][:12]}:L{l['first_line']}:"
        sub = next((v for uid, v in subs.items() if uid.startswith(prefix)), None)
        kids = [c for c in Q.load_children() if c["parent_unit_id"].startswith(prefix)]
        if kids and all(c["semantic_role_decision"] for c in kids):
            out[l["first_line"]] = "by_children"    # resolved by its child units: never a span block and never a separate requirement
            continue
        if sub is not None:
            kind = "unresolved" if sub["confirmed_decision"] == "uncertain" else sub["confirmed_decision"]
            if sub.get("classification_tag") == "ai_directed_injection":
                kind = "excluded_injection"     # AI-directed text: not a requirement, excluded, kept under the injection guard
            elif sub.get("relation", "").startswith("repeats_content_of"):
                kind = "repeated"               # repeated requirement content: counted only through its related unit
            elif sub.get("relation", "").startswith("modifies_importance_of"):
                kind = "conflict_modifier"      # conflict and importance evidence: never a second scored criterion
        out[l["first_line"]] = kind
    return out


def inventory_for(jd_id, jd, cand, eff=None):
    eff = eff if eff is not None else effective_types(jd_id, cand)
    lines = cand["lines"]
    headings = [l["text"].strip() for l in lines if eff[l["first_line"]] == "heading"]
    background = [l["text"].strip() for l in lines if eff[l["first_line"]] == "background"]
    return I.build_inventory(jd["text"], {"jd_sha256": jd["sha"], "headings": headings, "background": background})


def type_of_line(cand) -> dict:
    return effective_types("", cand)


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
            "injection_guard": s.get("injection_hard"),
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
    """types: effective type by first line (see effective_types). Coverage counts use EFFECTIVE REQUIREMENT units only. Uncertain and
    unresolved units have their own span blocks; headings and background are excluded from every denominator."""
    quotes = R.answer_quotes(rec["raw"])
    inv = inventory_for(jd_id, jd, cand, types)
    cov = C.cover(inv, jd["text"], quotes)
    not_verbatim = [v["quote"] for v in cov["quotes"] if v["status"] == "not_verbatim"]
    title = jd_title_words(jd_id)
    spans = {"requirement": [], "uncertain": [], "unresolved": [], "repeated": [], "conflict_modifier": [], "excluded_injection": []}
    for u in cov["units"]:
        kind = types.get(u["first_line"], "requirement")
        if kind not in spans:
            continue                        # heading or background: never a denominator, never an omission
        for sp in u["uncovered_spans"]:
            cls = classify_ref(sp, u, not_verbatim, title)
            spans[kind].append({"first_line": u["first_line"], "text": sp["text"], "class": cls, "unit_status": u["status"]})
    req = [u for u in cov["units"] if types.get(u["first_line"], "requirement") == "requirement"]     # repeated units are not a denominator
    coverage = {"requirement_units": len(req), **{k: sum(1 for u in req if u["status"] == k) for k in ("covered", "partial", "omitted")}}
    if jd_id == "TC20":
        defects = defects_tc20(rec["raw"], jd["text"], labels)
    else:
        defects = defects_bench(case, rec["raw"], rec.get("finish_reason"))
    return {"coverage": coverage, "uncovered": spans, "defects": defects,
            "child_obligations": child_obligations(jd_id, jd["text"], rec["raw"]),
            "yield_obligations": yield_obligations(jd_id, jd["text"], rec["raw"], cov["units"])}


def prompt_of(src_path: pathlib.Path) -> str:
    """The prompt version named by the run's MANIFEST.json (benchmark runs); TC20 groups use the arm name."""
    manifest = src_path.parent / "MANIFEST.json"
    return json.loads(manifest.read_text(encoding="utf-8"))["prompt_version"] if manifest.exists() else "?"


def guard_cell(answers) -> str:
    """Injection guard hard failures for answers whose JD carries an injection block; n/a otherwise."""
    flags = [a["defects"].get("injection_guard") for a in answers]
    if all(f is None for f in flags):
        return "n/a"
    return f"{sum(1 for f in flags if f)} of {len(flags)}"


def _rng(values):
    values = sorted(values)
    return str(values[0]) if values[0] == values[-1] else f"{values[0]}-{values[-1]}"


def write_report(data: dict) -> str:
    import build_queue as Q  # noqa: PLC0415
    subs = Q.load_submissions().values()
    repeated = [v for v in subs if v.get("relation", "").startswith("repeats_content_of")]
    modifiers = [v for v in subs if v.get("relation", "").startswith("modifies_importance_of")]
    injections = [v for v in subs if v.get("classification_tag") == "ai_directed_injection"]
    lines = ["# Multilingual omission-reference replay (stored answers only)", "",
             "Generated by `replay_ref.py` from `results/replay_ref.json`. No model was called. Each group is one model, one prompt and one run,",
             "and groups are never pooled. Uncovered spans and defects are separate blocks in every table.", "",
             "Coverage denominators: EFFECTIVE requirement units (candidate type, or the confirmed decision where one exists). Uncertain units and",
             "unresolved units (a confirmed 'uncertain' decision) are reported in their own blocks: never as a requirement, never as an exclusion.",
             "Headings and background are not counted. Defects use the existing scorers and frozen benchmark labels (author-written, not reviewer-confirmed).", ""]
    if repeated:
        lines += ["**Repeated requirement content (not a second scored criterion):**", ""]
        lines += [f"- {v['rq_id']} ({v['jd_id']}, unit `{v['unit_id']}`): {v['relation']}. Reviewer reason: {v['reason']}" for v in repeated]
        lines += ["", "Such units appear only in the repeated-requirement column. Their related unit is the one counted.", ""]
    if modifiers:
        lines += ["**Conflict and importance modifiers (conflict evidence, not a second scored criterion):**", ""]
        lines += [f"- {v['rq_id']} ({v['jd_id']}, unit `{v['unit_id']}`): {v['relation']}. Reviewer reason: {v['reason']}" for v in modifiers]
        lines += ["", "Their spans appear only in the conflict-modifier column. The related unit keeps its own requirement status.", ""]
    if injections:
        lines += ["**Excluded AI-directed injection text (kept under the injection guard, never a requirement):**", ""]
        lines += [f"- {v['rq_id']} ({v['jd_id']}, unit `{v['unit_id']}`): tag {v['classification_tag']}. {v['reason']}" for v in injections]
        lines += ["", "Their spans appear only in the excluded-injection column. The injection guard is reported per JD in the table column (hard failures of answers).", ""]
    children = {}
    for group, per_jd in data["groups"].items():
        for jd_id, answers in per_jd.items():
            for a in answers:
                for ob in a.get("child_obligations", []):
                    children.setdefault((group, jd_id, ob["child_id"]), []).append(ob)
    if children:
        lines += ["**Child-unit extraction obligations (decided children; never a scored criterion):**", "",
                  "| group | child | role | expected list | met | partial | misrouted | missing |", "|---|---|---|---|---|---|---|---|"]
        for (group, jd_id, cid), obs in sorted(children.items()):
            counts = {k: sum(1 for o in obs if o["status"] == k) for k in ("met", "partial", "misrouted", "missing")}
            lines.append(f"| {group.replace('|', '·')} | {cid} | {obs[0]['role']} | {obs[0]['expected_list']} | {counts['met']} | {counts['partial']} | "
                         f"{counts['misrouted']} | {counts['missing']} |")
        lines += ["", "Each child is an extraction-coverage obligation. None is a scored criterion: a child contributes no requirement denominator, "
                  "and its obligation status is reported only in this table.", ""]
    yields = {}
    for group, per_jd in data["groups"].items():
        for jd_id, answers in per_jd.items():
            for a in answers:
                for ob in a.get("yield_obligations", []):
                    yields.setdefault((group, ob["rq_id"]), []).append(ob)
    if yields:
        lines += ["**Source units that yield independent items (one source-coverage obligation each; never a second scored criterion):**", "",
                  "| group | RQ | yields | source coverage | structure | importance per item | within accepted (required or preferred) |",
                  "|---|---|---|---|---|---|---|"]
        for (group, rq), obs in sorted(yields.items()):
            o = obs[0]
            imp = ", ".join(f"{k}={v}" for k, v in o["importance"].items())
            within = o["importance_within_accepted"]
            lines.append(f"| {group.replace('|', '·')} | {rq} | {', '.join(o['yields'])} | {o['source_coverage']} | {o['structure']} | "
                         f"{imp} | {'yes' if within else ('no' if within is False else 'n/a')} |")
        lines += ["", "Structure (missing, correctly split, incorrectly merged) and importance are reported separately. Importance is "
                  "checked only against the accepted values (required or preferred), so a preferred item is not a finding.",
                  "", "Conflict evidence kept for these units (review evidence, not a finding): " + " ".join(sorted({o["conflict_evidence"] for obs in yields.values() for o in obs if o["conflict_evidence"]})), ""]
    for group, per_jd in data["groups"].items():
        lines += [f"## {group}", "", "| JD | answers | requirement units | genuine spans | paraphrase | intro wording | fragments | punctuation | uncertain-unit spans (genuine) | unresolved-unit spans (genuine) | OR | AND | category | routing | item omissions |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        recurring = []
        for jd_id, answers in per_jd.items():
            ok = [a for a in answers if not a.get("invalid")]
            if not ok:
                lines.append(f"| {jd_id} | {len(answers)} invalid | | | | | | | | | | | | | | | | | |")
                continue
            cnt = lambda kind, cls=None: [sum(1 for sp in a["uncovered"].get(kind, []) if cls is None or sp["class"] == cls) for a in ok]  # noqa: E731
            d = lambda k: [a["defects"].get(k, 0) for a in ok]  # noqa: E731
            lines.append(f"| {jd_id} | {len(ok)} | {ok[0]['coverage']['requirement_units']} | {_rng(cnt('requirement', 'genuine_omission'))} | "
                         f"{_rng(cnt('requirement', 'paraphrase'))} | {_rng(cnt('requirement', 'intro_wording'))} | "
                         f"{_rng(cnt('requirement', 'connector_fragment'))} | "
                         f"{_rng(cnt('requirement', 'punctuation_only'))} | {_rng(cnt('uncertain', 'genuine_omission'))} | "
                         f"{_rng(cnt('unresolved', 'genuine_omission'))} | {_rng(cnt('repeated', 'genuine_omission'))} | "
                         f"{_rng(cnt('conflict_modifier', 'genuine_omission'))} | {_rng(cnt('excluded_injection', 'genuine_omission'))} | "
                         f"{_rng(d('OR'))} | {_rng(d('AND'))} | {_rng(d('category'))} | {_rng(d('routing'))} | {_rng(d('item_omissions'))} | "
                         f"{guard_cell(ok)} |")
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
