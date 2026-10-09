"""criteria_extraction_v2-3 (offline candidate): PROMPT-CONTRACT consistency checks for two topics only: (1) OR/AND item structure, (2) routing of conditions
and information.

WHAT THESE TESTS ARE: static checks of the candidate text (instructions, schema guidance, count lines, routing table, worked examples) against each other, against
the real offline parser, the split-OR guard and a routing checker, plus preservation checks of everything that must not change.
WHAT THEY ARE NOT: evidence that any model follows the prompt. Every "answer" below is written by this test file, not produced by a model; passing says nothing about
real-model compliance (recall, precision, alternatives, routing, injection resistance). That needs a new, separately approved benchmark run. No model call is made.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import re
import subprocess
import sys

import pytest

from parser_candidates.requirements_v2_split_or_guard_1 import inspect_result as inspect_split_or
from services.requirements_v2.extraction import parse_response
from services.requirements_v2.extraction.prompt import PROMPT_PATH, PROMPT_SHA256

BACKEND = pathlib.Path(__file__).resolve().parent.parent
D2, D3 = BACKEND / "prompt_candidates" / "criteria_extraction_v2-2", BACKEND / "prompt_candidates" / "criteria_extraction_v2-3"
T2 = (D2 / "criteria_extraction_v2-2.txt").read_text(encoding="utf-8")
T3 = (D3 / "criteria_extraction_v2-3.txt").read_text(encoding="utf-8")
M2, M3 = json.loads((D2 / "MANIFEST.json").read_text(encoding="utf-8")), json.loads((D3 / "MANIFEST.json").read_text(encoding="utf-8"))
V1 = PROMPT_PATH.read_text(encoding="utf-8")
CASES_DIR = BACKEND / "tests" / "fixtures" / "requirements_v2_benchmark" / "cases"
BENCH = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(CASES_DIR.glob("B*.json"))]
RESULTS = BACKEND / "benchmark_results" / "requirements_v2"
LISTS = ("non_scoreable_requirements", "post_hiring_conditions", "informational_items")
CATS = ("skills", "experience", "education", "certifications", "soft_skills", "domain_knowledge", "other_requirements")
V22_SHA = "40ea678b65a5782da3f74f1c0b52f4dbeb10cc369f78efd25f1e38827ff48dda"
V21_SHA = "f2017d2879554935baa5d0d4f51d53fa0600e18858cc875d4a791c5ac0678b04"

EX_RE = re.compile(r"Example (\d+) \((.*?)\)\n<<<JD\n(.*?)\nJD>>>\nAnswer:\n(\{[^\n]*\})\n", re.S)


def examples(text):
    return [{"n": int(n), "jd": jd, "answer": json.loads(a)} for n, t, jd, a in EX_RE.findall(text)]


def rule(text, n):
    m = re.search(rf"^{n}\. .*?(?=^\d+\. |\nEXAMPLES|\Z)", text, re.S | re.M)
    assert m, n
    return m.group(0).rstrip()


def block(text, start, end):
    return text[text.index(start):text.index(end)]


def _eval():
    spec = importlib.util.spec_from_file_location("req_v2_eval_v23", BACKEND / "scripts" / "requirements_v2_extraction_eval.py")
    ev = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ev)
    return ev


# ══ identity, preservation, non-registration ══════════════════════════════════════════════════════════════════════════════════════
def test_manifest_matches_the_file_and_states_scope_and_status():
    assert hashlib.sha256((D3 / "criteria_extraction_v2-3.txt").read_bytes()).hexdigest() == M3["sha256"]
    assert M3["version"] == "criteria_extraction_v2-3" and M3["prompt_code"] == "criteria_extraction_v2" and M3["base_sha256"] == V22_SHA
    assert M3["sha256"] not in (V21_SHA, V22_SHA) and M3["frozen_baseline_commit"] == "059c56b"
    assert "not registered" in M3["status"] and "not run against any model" in M3["status"] and "not wired into the executor" in M3["status"]
    assert "no evidence of real-model compliance" in M3["evidence_level"]
    assert "OR/AND" in M3["scope"] and "routing" in M3["scope"] and "omission" in M3["scope"]


def test_v2_1_and_v2_2_and_their_manifests_are_unchanged():
    assert hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest() == PROMPT_SHA256 == V21_SHA
    assert hashlib.sha256((D2 / "criteria_extraction_v2-2.txt").read_bytes()).hexdigest() == V22_SHA == M2["sha256"]
    assert M2["status"].startswith("offline candidate: not registered")


@pytest.mark.parametrize("run", ["baseline_v2-1", "v2-2_run1"])
def test_stored_results_are_unchanged(run):
    manifest = json.loads((RESULTS / run / "MANIFEST.json").read_text(encoding="utf-8"))
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((RESULTS / run / name).read_bytes()).hexdigest() == digest, (run, name)


def test_frozen_parser_scorer_labels_and_registries_are_byte_identical_to_059c56b():
    sys.path.insert(0, str(BACKEND / "scripts"))
    try:
        spec = importlib.util.spec_from_file_location("req_v2_run_v23", BACKEND / "scripts" / "requirements_v2_extraction_run.py")
        run = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(run)
    finally:
        sys.path.remove(str(BACKEND / "scripts"))
    try:
        subprocess.run(["git", "-C", str(run.REPO), "cat-file", "-e", run.FROZEN_COMMIT], check=True, capture_output=True)
    except Exception:
        pytest.skip("frozen commit not in this checkout")
    assert run.verify_frozen() == [] and run.verify_candidate() == []


def test_the_candidate_is_not_registered_activated_or_wired():
    for sub in ("routers", "workers", "services", "db"):
        for path in (BACKEND / sub).rglob("*"):
            if path.is_file() and path.suffix in {".py", ".sql", ".txt"} and "__pycache__" not in path.parts:
                assert "criteria_extraction_v2-3" not in path.read_text(encoding="utf-8", errors="ignore"), path
    assert "criteria_extraction_v2-3" not in (BACKEND / "main.py").read_text(encoding="utf-8")
    assert "criteria_extraction_v2-3" not in (BACKEND / "scripts" / "requirements_v2_extraction_run.py").read_text(encoding="utf-8")    # not selectable in the executor
    assert not list(D3.glob("*.py"))


# ══ scope: only the two topics changed ══════════════════════════════════════════════════════════════════════════════════════════
def test_only_rules_1_4_6_and_9_changed_and_the_rest_is_byte_identical_to_v2_2():
    assert [int(m) for m in re.findall(r"^(\d+)\. ", T3.split("EXAMPLES")[0], re.M)] == list(range(1, 14))
    for n in (2, 3, 5, 7, 8, 10, 11, 12, 13):                    # omission, importance, categories, duties, weights, scoreability, language: untouched
        assert rule(T3, n) == rule(T2, n), n
    for n in (1, 4, 6, 9):
        assert rule(T3, n) != rule(T2, n), n
    assert rule(T3, 1).replace(' (options joined by "or" are one requirement: rule 4)', "") == rule(T2, 1)
    assert rule(T3, 6).replace(" When the experience is accepted in any one of several fields or settings, it stays ONE item: subject names them as the job description does, and alternatives lists each option (rule 4).", "") == rule(T2, 6)
    assert T3.split("RULES")[0].split("TASK")[1].split("OUTPUT FORMAT")[0] == T2.split("RULES")[0].split("TASK")[1].split("OUTPUT FORMAT")[0]    # untrusted-text paragraph


def test_the_json_shapes_and_enums_are_identical_to_v2_2_and_only_two_guidance_lines_were_added():
    a2, a3 = block(T2, "OUTPUT FORMAT", "RULES\n"), block(T3, "OUTPUT FORMAT", "RULES\n")
    added = [ln for ln in a3.splitlines() if ln not in a2.splitlines()]
    assert len(added) == 2 and added[0].startswith("Item count: one item per independent requirement") and added[1].startswith("Each condition goes in exactly ONE of the three lists")
    assert "\n".join(ln for ln in a3.splitlines() if ln not in added) == "\n".join(a2.splitlines())
    assert block(T3, "ITEM\n{", "}\n") == block(T2, "ITEM\n{", "}\n")


def test_examples_1_3_4_are_unchanged_and_example_2_differs_only_in_its_company_sentence():
    e2, e3 = examples(T2), examples(T3)
    assert [e["n"] for e in e3] == [1, 2, 3, 4]
    for n in (1, 3, 4):
        assert e3[n - 1] == e2[n - 1]
    old, new = "The library opens six days a week", "The library belongs to the municipal network of public libraries"
    assert json.dumps(e2[1], ensure_ascii=False).replace(old, new) == json.dumps(e3[1], ensure_ascii=False)
    assert T2.split("ADDITIONAL EXAMPLES")[1].replace(old, new) == T3.split("ADDITIONAL EXAMPLES")[1]            # the whole additional-examples section: only that sentence differs
    assert T3.split("ADDITIONAL EXAMPLES")[0].split("EXAMPLES (illustrative only")[1] == T2.split("ADDITIONAL EXAMPLES")[0].split("EXAMPLES (illustrative only")[1]


# ══ what v2-2 left contradictory or unstated (documented defects, closed in v2-3) ═══════════════════════════════════════════════
def test_v2_2_defects_are_documented_by_the_texts_and_closed_in_v2_3():
    # 1. v2-2 said nothing about a "و" attached to the next word although its own Arabic example uses one; the item-count rule was split over rule 4, the schema and rule 6
    assert "وPowerPoint" in T2 and "attached" not in rule(T2, 4) and "attached" in rule(T3, 4)
    # 2. v2-2's schema never said how many entries alternatives needs or when it is null; its experience rule never mentioned OR
    assert "two or more entries" not in block(T2, "ITEM\n{", "RULES") and "two or more entries" in block(T3, "ITEM\n{", "RULES")
    assert "alternatives" not in rule(T2, 6) and "alternatives" in rule(T3, 6)
    # 3. v2-2 showed ONE `category` enum for three lists without saying the label is not the list; models wrote list names as categories
    assert "never the name of a list" not in T2 and "never the name of a list" in T3 and "never a list name" in rule(T3, 9)
    # 4. v2-2 example 2 filed "the library opens six days a week" as company information, while rule 9 sends "work schedule" to non_scoreable_requirements
    assert "opens six days a week" in T2 and "opens six days a week" not in T3
    assert "employer's own opening hours" in rule(T3, 9)


# ══ item count: one rule, stated in instructions, schema guidance and examples ═══════════════════════════════════════════════════
def test_the_item_count_rule_is_stated_consistently_in_every_place():
    r4 = rule(T3, 4)
    for frag in ('one item per independent requirement', 'options joined by "or" are ONE requirement', '"or" = "أو", "and" = "و"', 'attached to the next word',
                 '"وظيفة"', "exactly ONE item", "EVERY option (two or more entries)", "never an alternatives list with a single entry", "never alternatives for \"and\"",
                 "Experience follows the same rule", "Item-count check"):
        assert frag in r4, frag
    schema = block(T3, "ITEM\n{", "RULES")
    assert "ONE requirement: one item whose alternatives lists every option (two or more entries)" in schema and "alternatives is null (rule 4)" in schema
    assert "ONE item" in rule(T3, 6) and "alternatives lists each option (rule 4)" in rule(T3, 6) and "rule 4" in rule(T3, 1)
    # no leftover contradictory phrasing
    assert 'Alternatives are ONE item' not in T3 and "Independent requirements are separate items:" not in T3
    assert T3.count("never alternatives") == 1


COUNT_LINE = re.compile(r'"([^"]+)" = (\d) (?:experience )?items?')


def count_checks():
    section = rule(T3, 4).split("Item-count check")[1]
    return [(m.group(1), int(m.group(2))) for ln in section.splitlines() for m in COUNT_LINE.finditer(ln)]


def options_of(phrase: str) -> list[str]:
    """The options / parts of a coordination in a count line or a test phrase."""
    body = re.split(r"\s+(?:in|في)\s+", phrase)[-1] if re.search(r"\s(?:in|في)\s", phrase) else phrase
    return [re.sub(r"^(?:an?|the)\s+", "", p.strip()) for p in re.split(r"\s+(?:or|and|أو)\s+|,\s*|\s+و(?=\S)", body) if p.strip()]


def kind_of(phrase: str, expected: int) -> str:
    return "experience" if re.search(r"experience|خبرة", phrase) else ("or" if expected == 1 else "and")


def build(phrase: str, expected: int):
    """(job description, answer dict) that a conforming model would give for one requirement sentence, written by THIS TEST from the count line's own numbers."""
    parts = options_of(phrase)
    kind = kind_of(phrase, expected)
    item = lambda text, **kw: {"text": text, "importance": "required", "importance_cue": None, "source_text": phrase, "origin": "stated", "alternatives": None, "experience": None, **kw}
    cats = {c: [] for c in CATS}
    if kind == "experience":
        years = 2 if "two" in phrase else None
        cats["experience"] = [item(phrase, alternatives=parts, experience={"subject": " or ".join(parts) if "or" in phrase else " أو ".join(parts), "min_years": years})]
    elif expected == 1:
        cats["skills"] = [item(" or ".join(parts) if re.search(r"[a-z]", phrase) else " أو ".join(parts), alternatives=parts)]
    else:
        cats["skills"] = [item(p) for p in parts]
    weights = {c: 0 for c in CATS}
    weights["experience" if kind == "experience" else "skills"] = 100
    answer = {"scoreability": {"status": "scoreable", "reason": ""}, "categories": cats, "category_weights": weights, "non_scoreable_requirements": [],
              "post_hiring_conditions": [], "informational_items": [], "warnings": []}
    return f"Requirements:\n- {phrase}\n", answer


def test_the_count_lines_cover_or_and_and_experience_in_english_and_arabic():
    checks = count_checks()
    assert [c for _, c in checks] == [1, 2, 1, 3, 1, 1, 2, 1]
    assert any(re.search("[؀-ۿ]", p) for p, _ in checks) and any("experience" in p for p, _ in checks) and any("خبرة" in p for p, _ in checks)


@pytest.mark.parametrize("phrase,expected", count_checks(), ids=lambda v: str(v)[:30])
def test_each_count_line_agrees_with_the_real_parser_and_the_split_or_guard(phrase, expected):
    jd, answer = build(phrase, expected)
    res = parse_response(json.dumps(answer, ensure_ascii=False), jd, "stop")
    assert res.ok and [i.code for i in res.review] == [], [(i.code, i.message) for i in res.review]
    items = [i for c in res.requirements["categories"].values() for i in c["items"]]
    assert len(items) == expected
    if kind_of(phrase, expected) in ("or", "experience"):
        assert items[0]["alternatives"] and len(items[0]["alternatives"]) == len(options_of(phrase)) >= 2
    else:
        assert all(i["alternatives"] is None for i in items)
    assert inspect_split_or(res)["issues"] == []                                    # the conforming shape is not a split OR


@pytest.mark.parametrize("phrase,expected", [c for c in count_checks() if c[1] == 1], ids=lambda v: str(v)[:30])
def test_the_forbidden_split_shapes_are_exactly_what_the_guard_and_parser_reject(phrase, expected):
    jd, good = build(phrase, expected)
    cat = "experience" if kind_of(phrase, expected) == "experience" else "skills"
    parts = options_of(phrase)
    only = good["categories"][cat][0]
    # (a) one item per option, each pointing at the other(s): the shape rule 4 forbids; the guard flags it
    bad = json.loads(json.dumps(good))
    bad["categories"][cat] = [{**only, "text": p, "alternatives": [q for q in parts if q != p] if len(parts) == 2 else parts, "experience": only["experience"]} for p in parts]
    res = parse_response(json.dumps(bad, ensure_ascii=False), jd, "stop")
    assert res.ok and len([i for c in res.requirements["categories"].values() for i in c["items"]]) == len(parts)
    assert inspect_split_or(res)["issues"], phrase
    # (b) a single-entry alternatives list: the parser drops it (rule 4: never a single entry)
    single = json.loads(json.dumps(good))
    single["categories"][cat][0]["alternatives"] = [parts[0]]
    res = parse_response(json.dumps(single, ensure_ascii=False), jd, "stop")
    assert "alternatives_invalid_dropped" in [i.code for i in res.review]


# the phrases the task names (they are NOT in the prompt text; they are benchmark-adjacent wording, used here as test input only)
NAMED = [("Python or Java", 1), ("CSS and HTML", 2), ("experience in a clinic or hotel", 1), ("بايثون أو جافا", 1), ("CSS وHTML", 2), ("خبرة في عيادة أو فندق", 1)]


@pytest.mark.parametrize("phrase,expected", NAMED, ids=lambda v: str(v)[:30])
def test_the_named_cases_have_the_stated_item_count_and_alternatives(phrase, expected):
    jd, answer = build(phrase, expected)
    res = parse_response(json.dumps(answer, ensure_ascii=False), jd, "stop")
    items = [i for c in res.requirements["categories"].values() for i in c["items"]]
    assert res.ok and len(items) == expected and inspect_split_or(res)["issues"] == []
    assert (items[0]["alternatives"] is not None) == (expected == 1)
    if "clinic" in phrase or "عيادة" in phrase:
        assert items[0]["experience"] is not None and set(items[0]["alternatives"]) == (set(["clinic", "hotel"]) if "clinic" in phrase else {"عيادة", "فندق"})
    for t in ("Python", "Java", "CSS", "HTML", "clinic", "hotel", "عيادة", "فندق", "بايثون", "جافا"):
        assert t not in T3, t                                                      # kept out of the prompt: benchmark wording


def test_the_prompt_never_uses_benchmark_wording():
    def lines(t):
        return {ln.strip().lstrip("-• ").rstrip(".") for ln in t.splitlines() if len(ln.strip()) >= 12 and not ln.strip().endswith(":")}
    bench_lines = set().union(*(lines(c["jd"]) for c in BENCH))
    assert not lines(T3.split("ADDITIONAL EXAMPLES")[0].split("RULES")[1]) & bench_lines
    for e in examples(T3):
        assert not lines(e["jd"]) & bench_lines
    bench_items = {i["text"].lower() for c in BENCH for i in c["expected"]["items"]}
    for e in examples(T3):
        assert not {i["text"].lower() for items in e["answer"]["categories"].values() for i in items} & bench_items
    for c in BENCH:
        assert c["id"] not in T3 and c["title"] not in T3
    for word in ("PostgreSQL", "Power BI", "Tableau", "Oracle HCM", "Front Desk", "SuccessFactors", "Docker", "Rust", "Python", "Java", "SQL", "CSS", "HTML"):
        assert T3.count(word) <= T2.count(word), word                          # nothing benchmark-like was added
    assert "Power BI" in T2 and "Power BI" not in T3                           # v2-2's rule-4 example carried benchmark wording; v2-3 no longer does
    for word in ("Python", "Java", "SQL", "CSS", "HTML", "clinic", "hotel", "عيادة", "فندق"):
        assert word not in T3, word


# ══ routing ═════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
def condition_enum(text):
    m = re.search(r'"category": "([^"]+)", "reason"', text)
    return m.group(1).split("|")


def routing_table(text):
    """{label: list} read from rule 9's table rows."""
    out = {}
    for ln in rule(text, 9).splitlines():
        m = re.match(r"\s+(non_scoreable_requirements|post_hiring_conditions|informational_items): (.*)", ln)
        if m:
            for label in re.findall(r"(?:^|, )([a-z_]+)(?: \([^)]*\))?(?=,|$|\.)", m.group(2).rstrip(".")):
                assert label not in out, label
                out[label] = m.group(1)
    return out


def routing_problems(answer: dict, table: dict, enum: list) -> list[str]:
    """What rule 9 forbids, as a checker: a list name written as a label, a label in the wrong list, an unknown label."""
    problems = []
    for lst in LISTS:
        for c in answer.get(lst) or []:
            label = c.get("category")
            if label in LISTS:
                problems.append(f"{lst}: label is a list name ({label})")
            elif label == "other":
                continue
            elif label not in enum:
                problems.append(f"{lst}: unknown label {label!r}")
            elif table[label] != lst:
                problems.append(f"{lst}: {label} belongs in {table[label]}")
    return problems


def test_the_routing_table_covers_every_label_of_the_condition_schema_exactly_once():
    enum, table = condition_enum(T3), routing_table(T3)
    assert enum[-1] == "other" and set(table) == set(enum) - {"other"} and len(table) == len(enum) - 1
    assert set(table.values()) == set(LISTS)
    assert condition_enum(T2) == enum                                     # the schema enum itself did not change
    assert table["background_check"] == table["reference_check"] == "post_hiring_conditions"
    assert table["benefits"] == table["company_description"] == "informational_items"
    assert {table[k] for k in ("salary", "location", "schedule", "travel", "work_authorization", "availability")} == {"non_scoreable_requirements"}
    assert '"other": only when no label fits' in rule(T3, 9)


def test_the_table_matches_the_agreed_routing_used_by_the_benchmark_labels():
    table = routing_table(T3)
    seen = 0
    for c in BENCH:
        for cond in c["expected"]["conditions"]:
            assert table[cond["category"]] == cond["list"], (c["id"], cond)
            seen += 1
    assert seen > 10


def test_the_schema_guidance_and_rule_9_agree_that_the_label_is_not_the_list():
    assert "never the name of a list" in block(T3, "CONDITION\n", "RULES") and "does not choose the list" in block(T3, "CONDITION\n", "RULES")
    assert "never a list name" in rule(T3, 9) and "exactly ONE of three lists" in rule(T3, 9)
    assert "These are never items in categories" not in T3 and "never items in categories" in rule(T3, 9)
    assert "a benefit that mentions medical or health cover is still a benefit" in rule(T3, 9)


@pytest.mark.parametrize("version,text", [("v2-1 examples", V1), ("v2-3 examples", T3)])
def test_every_worked_example_routes_by_the_table(version, text):
    table, enum = routing_table(T3), condition_enum(T3)
    for e in (examples(text) if version != "v2-1 examples" else []):
        assert routing_problems(e["answer"], table, enum) == [], (version, e["n"])


def test_every_example_still_parses_cleanly_with_the_same_readiness_as_v2_2():
    expected = {1: "ready", 2: "needs_confirmation", 3: "ready", 4: "needs_items"}
    for e in examples(T3):
        res = parse_response(json.dumps(e["answer"], ensure_ascii=False), e["jd"], "stop")
        assert res.ok and [i.code for i in res.review] == (["no_items"] if e["n"] == 4 else []) and res.readiness.state == expected[e["n"]]
        for lst in LISTS:
            assert len(res.conditions[lst]) == len(e["answer"][lst])


# one conforming condition per label, each in the list the table names: the parser keeps every list as written
def test_a_conforming_answer_with_every_label_in_its_list_parses_and_passes_the_checker():
    table, enum = routing_table(T3), condition_enum(T3)
    answer = {"scoreability": {"status": "insufficient", "reason": "x"}, "categories": {c: [] for c in CATS}, "category_weights": {c: 0 for c in CATS},
              "non_scoreable_requirements": [], "post_hiring_conditions": [], "informational_items": [], "warnings": []}
    jd = "\n".join(f"Statement about {label}." for label in table)
    for label, lst in table.items():
        answer[lst].append({"text": f"about {label}", "category": label, "reason": "r", "source_text": f"Statement about {label}."})
    assert routing_problems(answer, table, enum) == []
    res = parse_response(json.dumps(answer), jd, "stop")
    assert res.ok
    for lst in LISTS:
        assert {c["category"] for c in res.conditions[lst]} == {l for l, v in table.items() if v == lst}


def test_the_checker_flags_the_misroutings_seen_in_the_stored_results_and_counts_them():
    """EVIDENCE ABOUT THE OLD OUTPUTS ONLY (what rule 9 and the schema note target); it says nothing about how a model will answer v2-3."""
    table, enum = routing_table(T3), condition_enum(T3)
    counts = {}
    for run in ("baseline_v2-1", "v2-2_run1"):
        n = {"list_name_as_label": 0, "wrong_list": 0, "unknown": 0}
        for line in (RESULTS / run / "calls.jsonl").read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            try:
                out = json.loads(rec["raw"])
            except Exception:
                continue
            for p in routing_problems(out, table, enum):
                n["list_name_as_label" if "list name" in p else "wrong_list" if "belongs in" in p else "unknown"] += 1
        counts[run] = n
    assert counts["baseline_v2-1"] == {"list_name_as_label": 0, "wrong_list": 8, "unknown": 6}
    assert counts["v2-2_run1"] == {"list_name_as_label": 5, "wrong_list": 2, "unknown": 0}
    # synthetic cases of the same kinds are flagged
    base = {"non_scoreable_requirements": [{"text": "x", "category": "post_hiring_conditions"}], "post_hiring_conditions": [{"text": "y", "category": "benefits"}],
            "informational_items": [{"text": "z", "category": "salary"}]}
    assert len(routing_problems(base, table, enum)) == 3


# ══ no tuning of omission or category assignment ═══════════════════════════════════════════════════════════════════════════════════
def test_importance_category_omission_and_weight_rules_are_untouched():
    for n in (2, 3, 5, 7, 10, 11, 12):
        assert rule(T3, n) == rule(T2, n)
    assert "Put a requirement in exactly one category." in rule(T3, 5) and "Preferred items are requirements" in rule(T3, 1)


# ══ size (informational) ═══════════════════════════════════════════════════════════════════════════════════════════════════════
def test_size_comparison_is_recorded_and_bounded():
    ev = _eval()
    t1, t2, t3 = (ev.estimate_tokens(x) for x in (V1, T2, T3))
    print(f"\nprompt size: v2-1 {len(V1.encode())} B ~{t1} tok; v2-2 {len(T2.encode())} B ~{t2} tok; v2-3 {len(T3.encode())} B ~{t3} tok (heuristic estimate; exact tiktoken unavailable offline)")
    assert t3 > t2 and t3 - t2 < 800 and len(T3.encode()) - len(T2.encode()) < 3000


def test_the_documents_state_that_offline_tests_do_not_prove_model_compliance():
    changes = (D3 / "CHANGES.md").read_text(encoding="utf-8")
    assert "not prove model compliance" in changes.replace("**", "")
    assert "not registered" in changes.lower() and "criteria_extraction_v2-3" in changes
    assert M3["sha256"][:12] in changes
