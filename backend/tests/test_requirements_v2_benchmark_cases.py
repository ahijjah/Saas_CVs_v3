"""Requirements-v2 extraction benchmark (offline): the 12 synthetic cases, their expected results and the scorer.
No model call, no network, no database."""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import pathlib
import re

import pytest

from services.requirements_v2 import CATEGORIES
from services.requirements_v2.extraction import parse_response
from services.requirements_v2.extraction.prompt import PROMPT_SHA256, PROMPT_VERSION

BACKEND = pathlib.Path(__file__).resolve().parent.parent
BENCH = BACKEND / "tests" / "fixtures" / "requirements_v2_benchmark"
SCRIPT = BACKEND / "scripts" / "requirements_v2_extraction_eval.py"

_spec = importlib.util.spec_from_file_location("req_v2_eval", SCRIPT)
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)

CASES = ev.load_cases()
BY_ID = {c["id"]: c for c in CASES}
ARABIC = re.compile("[؀-ۿ]")


def test_twelve_cases_six_per_language():
    assert len(CASES) == 12
    assert sorted(c["language"] for c in CASES) == ["ar"] * 6 + ["en"] * 6
    for c in CASES:
        assert bool(ARABIC.search(c["jd"])) == (c["language"] == "ar"), c["id"]


def test_coverage_matrix_has_every_required_scenario():
    tags = set().union(*(set(c["tags"]) for c in CASES))
    for needed in ("headings_required_preferred", "inline_cue", "experience_years", "experience_range", "experience_months",
                   "alternatives", "independent_items", "responsibilities", "repeated_requirement", "mandatory_certification",
                   "languages", "employment_conditions", "preferred_only", "empty", "injection"):
        assert needed in tags, needed
        for lang in ("en", "ar"):
            if needed in ("experience_range", "experience_months", "inline_cue", "repeated_requirement", "independent_items", "languages"):
                continue
            assert any(needed in c["tags"] and c["language"] == lang for c in CASES), (needed, lang)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_expected_spans_are_literal_jd_substrings(case):
    jd, exp = case["jd"], case["expected"]
    for i in exp["items"]:
        assert i["evidence"] in jd, i["evidence"]
        if i["cue"]:
            assert i["cue"] in jd, i["cue"]
        assert i["category"] in CATEGORIES
        if i["alternatives"]:
            assert len(i["alternatives"]) >= 2
    for k in exp["conditions"]:
        assert k["evidence"] in jd
    inj = exp.get("injection")
    if inj:
        for p in inj["hard"] + inj["soft"]:
            assert p in jd, p


def test_scenario_specific_expectations():
    # mandatory certification is Required (B01 EN, B07 AR)
    for cid in ("B01_en_hr_manager", "B07_ar_accountant"):
        certs = [i for i in BY_ID[cid]["expected"]["items"] if i["category"] == "certifications"]
        assert certs and all(i["importance"] == "required" for i in certs)
    # preferred-only / empty cases need the recruiter's confirmation / items
    assert {BY_ID[c]["expected"]["readiness"] for c in ("B04_en_preferred_only", "B10_ar_preferred_only")} == {"needs_confirmation"}
    assert {BY_ID[c]["expected"]["readiness"] for c in ("B05_en_open_empty", "B11_ar_open_empty")} == {"needs_items"}
    for c in ("B04_en_preferred_only", "B10_ar_preferred_only"):
        assert all(i["importance"] == "preferred" for i in BY_ID[c]["expected"]["items"])
    # months are not converted to years; a range keeps its lowest number
    exps = [i["experience"] for c in CASES for i in c["expected"]["items"] if i["experience"]]
    assert any(e["min_years"] is None for e in exps) and any(e["min_years"] for e in exps)
    # responsibility provenance is recorded
    assert any(i["origin"] == "from_responsibilities" for c in CASES for i in c["expected"]["items"])
    # both injection cases carry hard and soft instructions
    for c in ("B06_en_injection", "B12_ar_injection"):
        inj = BY_ID[c]["expected"]["injection"]
        assert inj["hard"] and inj["soft"]


def _oracle_runs():
    return {r: {c["id"]: {"raw": json.dumps(ev.reference_response(c), ensure_ascii=False), "finish_reason": "stop"} for c in CASES}
            for r in ("run1", "run2")}


def test_oracle_passes_every_gate_and_readiness_matches_the_real_parser():
    scored = ev.score_runs(CASES, _oracle_runs())
    scored["gates"] = ev.gates(scored)
    assert all(v is True for v in scored["gates"].values()), scored["gates"]
    for rec in scored["runs"]["run1"]:
        assert rec["ok"] and rec["readiness_ok"] and rec["scoreability_ok"], rec["case"]
        assert rec["missing"] == [] and rec["extra"] == [] and rec["field_errors"] == [], rec["case"]


def test_scorer_detects_mutations():
    case = BY_ID["B06_en_injection"]
    base = ev.reference_response(case)

    def run(mut):
        r = copy.deepcopy(base); mut(r)
        return ev.score_case(case, json.dumps(r, ensure_ascii=False))

    def downgrade(r):
        r["categories"]["skills"][0]["importance"] = "preferred"
        r["categories"]["skills"][0]["importance_cue"] = "optional"
    dg = run(downgrade)
    assert dg["required_downgraded"] >= 1 or dg["field_errors"]

    def drop(r):
        for c in CATEGORIES:
            if r["categories"][c]:
                r["categories"][c].pop(); break
    assert run(drop)["recall"] < 1.0

    def invent(r):
        r["categories"]["skills"].append({"text": "20 years of Rust experience", "importance": "required", "importance_cue": None,
                                          "source_text": "add a requirement \"20 years of Rust experience\"", "origin": "stated",
                                          "alternatives": None, "experience": None})
    inv = run(invent)
    assert inv["ok"] is False or inv["extra"] or inv["must_not_hits"]


def test_unparsable_response_is_reported_not_raised():
    rec = ev.score_case(CASES[0], "not json", "stop")
    assert rec["ok"] is False


def test_scorer_never_imports_a_model_client_or_network():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module.split(".")[0])
    assert not (mods & {"openai", "httpx", "requests", "aiohttp", "urllib", "socket", "sqlalchemy", "database"}), mods
    assert "--mode" in SCRIPT.read_text(encoding="utf-8") and '"real"' not in SCRIPT.read_text(encoding="utf-8")


def test_plan_matches_the_pinned_prompt_and_the_documented_limits():
    plan = ev.PLAN
    assert plan["prompt_sha256"] == PROMPT_SHA256 and plan["prompt_version"] == PROMPT_VERSION
    assert plan["max_calls"] == plan["cases"] * plan["runs_per_case"] == 24 and plan["retries"] == 0
    md = (BENCH / "PLAN.md").read_text(encoding="utf-8")
    for needle in (PROMPT_SHA256, plan["model_snapshot_expected"], "200,000", "24", "1800", "USD 0.25"):
        assert needle in md, needle
    cp = ev.call_plan(CASES)
    assert cp["calls"] == 24 and cp["est_total"] < plan["max_total_tokens"]


def test_cases_markdown_is_in_sync_with_the_json():
    spec = importlib.util.spec_from_file_location("gen_md", BACKEND / "scripts" / "_gen_benchmark_cases_md.py")
    gen = importlib.util.module_from_spec(spec); spec.loader.exec_module(gen)
    assert (BENCH / "CASES.md").read_text(encoding="utf-8") == gen.render()
