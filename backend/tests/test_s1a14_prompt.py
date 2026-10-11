"""
S1-A-1.4 — prompt-only correction after the s1a-1.3 target-basis Stage B (every wrong answer was a first answer
"restrictions: [] + total_experience", 30 of them on statements naming work). Offline checks only:
  1. identity: s1a-1.4 is the pinned current prompt; s1a-1.3 / s1a-1.1 stay pinned and runnable for replay;
  2. scope: only sections 4 and 5 changed; the wire (OUTPUT example, keys, kinds) is byte-identical to s1a-1.3;
  3. every required correction is present, the "how long" descriptor is gone, the setting-only rules are kept;
  4. no new example reproduces evaluation, MAIN, boundary or held-out fixture wording;
  5. schema, validator, repair and harness gates unchanged: the s1a-1.4 contract has the s1a-1.3 rules and
     validates every representative answer exactly as s1a-1.3 does (English and Arabic; function, role, setting,
     vague, total, combined).
Model behaviour is NOT tested here (no model call); that is Stage B. No network, no database, no held-out run.
"""
import json
import re
from pathlib import Path

import pytest

from services.s1_requirements.criteria import enumerate_experience_criteria
from services.s1_requirements.jd_text import JDText
from services.s1_two_pass import pass_a as pa
from services.s1_two_pass import pass_a_runner as pr
from services.s1_two_pass import prompt_a
from services.s1_two_pass import schema as sc

BACKEND = Path(__file__).resolve().parent.parent
FIX = BACKEND / "scripts" / "s1_eval_fixtures"
S1A_14 = "1cc53afc9e79e2137ed85c5569f9a07a348350367153ed0396190dbe58613de3"
P14 = prompt_a.load_pass_a_prompt()
P13 = prompt_a.load_pass_a_prompt("s1a-1.3")
FIXTURES = ("s1_ctx_main_cases.json", "s1_boundary_cases.json", "s1a_target_basis_cases.json",
            "s1a_target_basis_cases_v2.json", "s1a_target_basis_heldout_cases.json")


def sec(p, a, b):
    return p[p.index(a):p.index(b)]


def new_lines():
    old = set(P13.splitlines())
    return [ln for ln in P14.splitlines() if ln not in old]


# ── 1. identity ─────────────────────────────────────────────────────────────

class TestIdentity:
    def test_current_prompt_pinned(self):
        assert sc.S1A_PROMPT_VERSION == "s1a-1.4" and prompt_a.S1A_PROMPT_PATH.name == "s1a-1.4.txt"
        assert prompt_a.S1A_PROMPT_SHA256 == prompt_a.PROMPT_SHA256["s1a-1.4"] == S1A_14
        assert prompt_a.pass_a_prompt_fingerprint() == S1A_14[:12]
        assert prompt_a.PROMPT_SHA256["s1a-1.3"] == "0cf68cadc53d05e8e26c75bb94d2ea279f91dcbb65d8663f17b9052f4c98656d"
        assert pa.pass_a_contract("s1a-1.3").prompt_version == "s1a-1.3"
        assert pa.pass_a_contract("s1a-1.1").prompt_version == "s1a-1.1"

    def test_security_suffix_shared(self):
        from services.ai_service import _SECURITY_HARDENING_SUFFIX
        assert P14.endswith(_SECURITY_HARDENING_SUFFIX) and P14.count("SECURITY RULES") == 1


# ── 2. scope: prompt-only, the wire unchanged ───────────────────────────────

class TestScope:
    def test_only_sections_4_and_5_changed(self):
        head = ("You read the EXPERIENCE", "4 RESTRICTIONS")
        assert sec(P14, *head) == sec(P13, *head)
        assert P14[P14.index("6 DURATION"):] == P13[P13.index("6 DURATION"):]       # 6-8, OUTPUT, SECURITY

    def test_wire_unchanged(self):
        out14, out13 = sec(P14, "OUTPUT:", "SECURITY RULES"), sec(P13, "OUTPUT:", "SECURITY RULES")
        assert out14 == out13
        assert '"kind": "role" | "function" | "vague"' in P14 and "These three are the only kinds." in P14
        for frag in ('"setting"', '"settings"', '"contexts"', '"context_spans"', '"target_gap"', '"sector"',
                     '"context"', '"kind": "sector"', '"kind": "context"'):
            assert frag not in P14, frag

    def test_no_stock_phrases_that_broaden_vague(self):
        assert "or the like" not in P14            # s1a-1.1 narrowed vague; s1a-1.4 keeps it narrow


# ── 3. the corrections ──────────────────────────────────────────────────────

class TestCorrections:
    @pytest.mark.parametrize("rule", [
        # 1. named work / role is always extracted
        'A statement that names work or a role is never restrictions [] and never "total_experience".',
        # 2. total_experience is genuinely unrestricted; the duration never decides the basis
        '"total_experience"  no target_hints, restrictions [] and no where limit: ONLY genuinely unrestricted '
        'experience.',
        'THE DURATION NEVER DECIDES THE BASIS: every requirement states how many years; "total_experience" does not '
        'mean "the total number of years".',
        'Before answering "total_experience", read the statement again without its duration',
        'a working atmosphere does not count',
        # 3. relevance words are vague, never total, never remove work
        'is always returned as a vague restriction; such a statement is never "total_experience".',
        'A relevance word attached to named work (e.g. "<work>-related experience") never removes that work',
        # 4. Arabic adjectives
        'When the adjective names a field of work or a kind of work or responsibility, it is a function',
        'When it asks for relevance without saying to what (e.g. "ذات علاقة"), it is vague.',
        'If you cannot tell whether the adjective names work or only describes the experience, return it as a '
        'function and report "ambiguous_relevance".',
        # 5. workplaces / employer types
        'or for which kind of employer, workplace or client the work was done',
        'A kind of workplace or employer is a where limit even without the words sector or industry',
        # 6. descriptive wording never overrides named work
        'DESCRIBING WORDS NEVER REMOVE WORK: words that only describe the experience (how recent, how general, how '
        'good) never remove a role or work named in the same statement',
    ])
    def test_rule_present(self, rule):
        assert rule in P14 and rule not in P13

    @pytest.mark.parametrize("gone", ["how long", "خبرة طويلة"])
    def test_duration_descriptor_removed(self, gone):
        assert gone in P13 and gone not in P14

    @pytest.mark.parametrize("kept", [
        'REQUIRED for "setting_only" and ONLY for it',
        'A where or for whom limit never changes this',
        'If you cannot tell whether X names work or only where, return X as a function and report '
        '"ambiguous_relevance"; never drop it.',
        'The same subject can be either: "experience in freight forwarding" names work; "experience with shipping '
        'companies" names only where.',
        'Decide WHAT first; look at WHERE only when the statement names no role and no work.',
        'Return [] ONLY when the requirement statement names no role and no work and has no vague word.',
    ])
    def test_setting_and_safety_rules_kept(self, kept):
        assert kept in P13 and kept in P14

    def test_the_s1a_1_1_contradictions_stay_removed(self):
        for gone in ("Decide the basis from the role and work words ONLY.", "Do not quote or describe that limit.",
                     "Such a statement is never restrictions []."):
            assert gone not in P14


# ── 4. examples outside every fixture ───────────────────────────────────────

class TestNoFixtureWording:
    @pytest.mark.parametrize("name", FIXTURES)
    def test_new_text_quotes_no_fixture_statement_or_phrase(self, name):
        data = json.loads((FIX / name).read_text(encoding="utf-8"))
        text = "\n".join(new_lines()).lower()
        for case in data["cases"]:
            for line in case["jd_lines"]:
                t = line.strip("- .").lower()
                assert len(t) < 15 or t not in text, (name, case["id"], line)
            phrases = []
            for spec in case.get("criteria") or [case]:
                g = spec.get("gold") or {}
                phrases += [t["text"] for t in g.get("targets", []) if isinstance(t, dict)]
                phrases += [w["text"] for w in (spec.get("oracle") or {}).get("where_evidence", [])]
            for ph in phrases:
                assert len(ph) < 5 or ph.lower() not in text, (name, case["id"], ph)

    @pytest.mark.parametrize("example", ["call centres", "دور الحضانة", "خبرة تدريبية",
                                         "previous experience in freight forwarding", "<work>-related experience"])
    def test_new_examples_are_new(self, example):
        assert example in P14
        for name in FIXTURES:
            assert example.lower() not in (FIX / name).read_text(encoding="utf-8").lower(), (name, example)


# ── 5. contract, schema and validator unchanged ─────────────────────────────

def job(line, years=4, ar=False):
    a = {"experience": {"minimum_years": years, "relevant_roles": []}}
    jd = "\n".join(["المتطلبات" if ar else "Requirements", "- " + line + "."])
    return a, jd, enumerate_experience_criteria("J1", a)


def answer(cid, line, restrictions=(), basis="targets", where=()):
    return json.dumps({"criteria": [{
        "criterion_id": cid, "requirement_spans": [{"line": 2, "text": line}],
        "restrictions": [{"line": 2, "text": t, "kind": k} for t, k in restrictions], "target_basis": basis,
        "where_evidence": [{"line": 2, "text": w} for w in where], "duration": "D1", "ambiguity": [], "note": "n"}]},
        ensure_ascii=False)


# (line, arabic, restrictions, basis, where, valid) — SYNTHETIC statements, not fixture wording
CASES = [
    ("Minimum 4 years of site inspection experience", False, [("site inspection", "function")], "targets", (), True),
    ("Minimum 4 years of experience as a Fleet Planner", False, [("Fleet Planner", "role")], "targets", (), True),
    ("Minimum 4 years of experience in call centres", False, [], "setting_only", ("call centres",), True),
    ("Minimum 4 years of comparable relevant experience", False, [("relevant", "vague")], "unspecified", (), True),
    ("Minimum 4 years of overall experience", False, [], "total_experience", (), True),
    ("Minimum 4 years of site inspection experience in call centres", False, [("site inspection", "function")],
     "targets", (), True),
    ("Minimum 4 years of site inspection experience", False, [], "total_experience", (), True),   # residual
    ("Minimum 4 years of experience in call centres", False, [], "total_experience", ("call centres",), False),
    ("Minimum 4 years of comparable relevant experience", False, [("relevant", "vague")], "total_experience", (),
     False),
    ("خبرة تدريبية لا تقل عن 4 سنوات", True, [("تدريبية", "function")], "targets", (), True),
    ("خبرة 4 سنوات في دور الحضانة", True, [], "setting_only", ("دور الحضانة",), True),
    ("خبرة ذات علاقة لا تقل عن 4 سنوات", True, [("ذات علاقة", "vague")], "unspecified", (), True),
    ("خبرة 4 سنوات في دور الحضانة", True, [], "setting_only", (), False),
]


class TestContractUnchanged:
    def test_contract_and_schema(self):
        c13, c14 = pa.CONTRACTS["s1a-1.3"], pa.CONTRACTS["s1a-1.4"]
        assert (c14.where_evidence, c14.basis_message) == (c13.where_evidence, c13.basis_message)
        assert pa.pass_a_contract() is c14
        assert sc.TARGET_BASES == ("targets", "total_experience", "setting_only", "unspecified")
        assert sc.PASS_A_RESTRICTION_KINDS == ("role", "function", "vague")
        assert pa.PASS_A_FORBIDDEN_KEYS == ("settings", "setting", "contexts", "context_spans", "target_gap")
        assert pr.NO_TARGET_BASES == ("total_experience", "setting_only", "unspecified")

    @pytest.mark.parametrize("line,ar,restr,basis,where,valid", CASES)
    def test_validation_identical_to_s1a_1_3(self, line, ar, restr, basis, where, valid):
        a, jd, crits = job(line, ar=ar)
        raw = answer(crits[0].criterion_id, line, restr, basis, where)
        durs = pa.build_pass_a_request(JDText(jd), crits).durations
        v13 = pa.validate_pass_a(raw, JDText(jd), crits, durs, contract=pa.CONTRACTS["s1a-1.3"])
        v14 = pa.validate_pass_a(raw, JDText(jd), crits, durs)
        assert v14.ok is v13.ok is valid, v14.errors
        assert v14.errors == v13.errors
        if valid:
            r13, r14 = v13.results[crits[0].criterion_id], v14.results[crits[0].criterion_id]
            assert (r14.target_basis, r14.parsed.policy, r14.where_evidence) == (
                r13.target_basis, r13.parsed.policy, r13.where_evidence)

    def test_request_payload_unchanged_only_the_prompt_differs(self):
        a, jd, crits = job("Minimum 4 years of site inspection experience")
        req = pa.build_pass_a_request(JDText(jd), crits)
        c13, c14 = pr.build_pass_a_call(req, prompt_version="s1a-1.3"), pr.build_pass_a_call(req)
        assert c13["messages"][1] == c14["messages"][1]
        assert {k: v for k, v in c13.items() if k != "messages"} == {k: v for k, v in c14.items() if k != "messages"}
        assert c13["messages"][0]["content"] == P13 and c14["messages"][0]["content"] == P14
        assert pa.pass_a_cache_key(req) != pa.pass_a_cache_key(req, prompt_version="s1a-1.3")

    def test_runtime_code_references_s1a_1_4_only_as_version_pins(self):
        # prompt-only: outside docstrings, "s1a-1.4" appears only as the current version, its SHA pin and the
        # contract entry (which carries the s1a-1.3 rules)
        import ast
        found = {}
        for path in sorted((BACKEND / "services" / "s1_two_pass").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docs = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body \
                        and isinstance(node.body[0], ast.Expr):
                    docs.add(id(node.body[0].value))
            hits = [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and "s1a-1.4" in n.value and id(n) not in docs]
            if hits:
                found[path.name] = len(hits)
        assert found == {"pass_a.py": 2, "prompt_a.py": 1, "schema.py": 1}, found
