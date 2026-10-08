"""
Offline validation of the Option D evaluation fixture (scripts/s1_eval_fixtures/s1a_target_basis_cases.json):
schema, gold consistency, balanced English/Arabic coverage of names_role_or_work true/false and of the
targets / total_experience / setting_only (/ unspecified) bases, difficult contrasts, deterministic loading and
fingerprint. The labelled Pass A oracle answer of every case is replayed through the Pass A validator and assembly
with a scripted client to prove gold and deterministic code agree. No model call, no network, no database; the
held-out fixtures are never read.
S1-A-1.3: s1a-basis-1 (this file, frozen byte for byte) holds the s1a-1.1 oracle answers and replays under the
s1a-1.1 contract; s1a-basis-2 (s1a_target_basis_cases_v2.json) is the same cases and gold with the s1a-1.3 output
contract (where_evidence on the 10 setting_only oracle answers) and replays under the CURRENT contract.
"""
import asyncio
import copy
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

from services.s1_requirements.criteria import enumerate_experience_criteria
from services.s1_requirements.jd_text import JDText
from services.s1_requirements.validator import implied_policy
from services.s1_two_pass import pass_a_runner as pr
from services.s1_two_pass import prompt_a
from services.s1_two_pass.schema import TARGET_BASES

BACKEND = Path(__file__).resolve().parent.parent
FIX = BACKEND / "scripts" / "s1_eval_fixtures"
PATH = FIX / "s1a_target_basis_cases.json"
FIXTURE_SHA256 = "02a452bba2de4374f394db5a9d4d11065ab80f70749f3337be5550007d304293"
FIXTURE_VERSION = "s1a-basis-1"
PATH_V2 = FIX / "s1a_target_basis_cases_v2.json"
FIXTURE_V2_SHA256 = "2daadcb18350dc908fb48ab6081c8ba2fda010f78c2fbb9c55233f780d2ea3e0"
# existing fixtures this one must leave untouched (held-out files are deliberately not listed or read)
UNCHANGED = {"s1_ctx_main_cases.json": "cf5844a22092a43d296e227de317ac75c6919f7d77e21da618c3945b4f0ca975",
             "s1_boundary_cases.json": "2f9dace9b884430772e8eb9c3cc108f16d2c815b459d62c11b2ec3621f47e6c0"}

GROUPS = {"total_professional", "vague", "setting_only", "x_experience", "experience_in_x", "ar_khibra_fi_work",
          "role"}
CASE_KEYS = {"id", "group", "lang", "contrast_with", "analysis", "jd_lines", "gold", "oracle"}
GOLD_KEYS = {"names_role_or_work", "target_basis", "targets", "policy", "rationale"}
ORACLE_KEYS = {"requirement_spans", "restrictions", "target_basis", "duration", "ambiguity", "note"}
NO_TARGET_POLICY = {"setting_only": "sector", "total_experience": "pure_duration", "unspecified": "pure_duration"}

FX = json.loads(PATH.read_text(encoding="utf-8"))
CASES = FX["cases"]
BY = {c["id"]: c for c in CASES}


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Client:
    def __init__(self, content):
        from types import SimpleNamespace
        self.requests = []

        async def create(**kw):
            self.requests.append(kw)
            usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content),
                                                            finish_reason="stop")], usage=usage)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def statement(c):
    return c["oracle"]["requirement_spans"][0]["text"]


# ── loading and fingerprint ─────────────────────────────────────────────────

class TestIdentity:
    def test_fingerprint_and_version(self):
        raw = PATH.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == FIXTURE_SHA256
        assert FX["fixture_version"] == FIXTURE_VERSION
        assert set(FX) == {"_comment", "fixture_version", "cases"}
        assert "SYNTHETIC" in FX["_comment"] and "not a held-out set" in FX["_comment"]

    def test_deterministic_loading(self):
        a = json.loads(PATH.read_text(encoding="utf-8"))
        b = json.loads(PATH.read_text(encoding="utf-8"))
        assert a == b == FX
        assert [c["id"] for c in CASES] == sorted([c["id"] for c in CASES], key=lambda i: (i[:2] == "BA", i))
        # canonical re-serialisation is stable (key order / unicode preserved)
        assert json.dumps(a, ensure_ascii=False, sort_keys=True) == json.dumps(FX, ensure_ascii=False, sort_keys=True)

    @pytest.mark.parametrize("name,sha", sorted(UNCHANGED.items()))
    def test_existing_fixtures_untouched(self, name, sha):
        assert hashlib.sha256((FIX / name).read_bytes()).hexdigest() == sha

    def test_new_file_only(self):
        assert PATH.name not in UNCHANGED and "heldout" not in PATH.name


# ── schema ──────────────────────────────────────────────────────────────────

class TestSchema:
    def test_ids(self):
        ids = [c["id"] for c in CASES]
        assert len(ids) == len(set(ids)) == 44
        assert all(re.fullmatch(r"B[EA]\d\d", i) for i in ids)
        assert all((c["id"][1] == "E") == (c["lang"] == "en") for c in CASES)

    @pytest.mark.parametrize("cid", sorted(BY))
    def test_case_shape(self, cid):
        c = BY[cid]
        assert set(c) == CASE_KEYS and c["group"] in GROUPS and c["lang"] in ("en", "ar")
        assert c["analysis"]["relevant_roles"] == [] and isinstance(c["analysis"]["minimum_years"], int)
        assert c["jd_lines"][0] in ("Requirements", "المتطلبات") and len(c["jd_lines"]) == 2
        g = c["gold"]
        assert set(g) == GOLD_KEYS and isinstance(g["names_role_or_work"], bool)
        assert g["target_basis"] in TARGET_BASES and isinstance(g["rationale"], str) and len(g["rationale"]) > 20
        assert all(set(t) == {"text", "kind"} and t["kind"] in ("role", "function") for t in g["targets"])
        assert set(c["oracle"]) == ORACLE_KEYS

    @pytest.mark.parametrize("cid", sorted(BY))
    def test_gold_is_internally_consistent(self, cid):
        g = BY[cid]["gold"]
        assert g["names_role_or_work"] == (g["target_basis"] == "targets") == bool(g["targets"])
        if g["targets"]:
            assert g["policy"] == implied_policy([t["kind"] for t in g["targets"]])
        else:
            assert g["policy"] == NO_TARGET_POLICY[g["target_basis"]]

    @pytest.mark.parametrize("cid", sorted(BY))
    def test_oracle_matches_gold_and_is_verbatim(self, cid):
        c = BY[cid]
        o, g = c["oracle"], c["gold"]
        stmt = statement(c)
        assert c["jd_lines"][1] == "- " + stmt + "."
        assert o["target_basis"] == g["target_basis"]
        rf = [(r["text"], r["kind"]) for r in o["restrictions"] if r["kind"] != "vague"]
        assert rf == [(t["text"], t["kind"]) for t in g["targets"]]
        assert all(r["text"] in stmt and r["line"] == 2 for r in o["restrictions"])
        vague = [r for r in o["restrictions"] if r["kind"] == "vague"]
        assert bool(vague) == (g["target_basis"] == "unspecified")
        assert o["ambiguity"] == (["ambiguous_relevance"] if vague else [])
        durs = JDText("\n".join(c["jd_lines"])).durations()
        assert [m.text for _, _, m in durs] == [o["duration"]]

    def test_contrasts_reference_cases_with_a_different_answer(self):
        for c in CASES:
            for other in c["contrast_with"]:
                assert other in BY and other != c["id"], (c["id"], other)
                a, b = c["gold"], BY[other]["gold"]
                assert (a["target_basis"], a["targets"]) != (b["target_basis"], b["targets"]), (c["id"], other)


# ── coverage and balance ────────────────────────────────────────────────────

class TestCoverage:
    def test_language_balance(self):
        assert Counter(c["lang"] for c in CASES) == {"en": 22, "ar": 22}

    @pytest.mark.parametrize("lang", ["en", "ar"])
    def test_every_basis_and_both_classes_per_language(self, lang):
        cs = [c for c in CASES if c["lang"] == lang]
        bases = Counter(c["gold"]["target_basis"] for c in cs)
        assert set(bases) == {"targets", "total_experience", "setting_only", "unspecified"}
        assert bases["total_experience"] >= 4 and bases["setting_only"] >= 4 and bases["unspecified"] >= 2
        named = Counter(c["gold"]["names_role_or_work"] for c in cs)
        assert named[True] >= 10 and named[False] >= 10

    def test_overall_class_balance(self):
        named = Counter(c["gold"]["names_role_or_work"] for c in CASES)
        assert named == {True: 22, False: 22}
        assert Counter(c["gold"]["target_basis"] for c in CASES) == {
            "targets": 22, "setting_only": 10, "total_experience": 8, "unspecified": 4}

    @pytest.mark.parametrize("lang,groups", [
        ("en", {"total_professional", "vague", "setting_only", "x_experience", "experience_in_x", "role"}),
        ("ar", {"total_professional", "vague", "setting_only", "ar_khibra_fi_work", "x_experience", "role"}),
    ])
    def test_required_families(self, lang, groups):
        assert groups <= {c["group"] for c in CASES if c["lang"] == lang}

    def test_required_wording_shapes(self):
        en = [statement(c) for c in CASES if c["lang"] == "en"]
        ar = [c for c in CASES if c["lang"] == "ar"]
        # "<X> experience" and "experience in <X>" with a work target
        assert any(re.search(r"\b(?!professional|overall|relevant|related|prior)\w[\w-]* experience\b", s)
                   for s in en if BY[[c["id"] for c in CASES if statement(c) == s][0]]["gold"]["names_role_or_work"])
        assert sum(1 for c in CASES if c["group"] == "experience_in_x") >= 3
        # Arabic "خبرة ... في <X>" both as work and as setting only
        fi = [c for c in ar if re.search(r"خبرة .* في ", statement(c))]
        assert {c["gold"]["target_basis"] for c in fi} >= {"targets", "setting_only", "total_experience"}
        # roles in both languages, including the Arabic attached "ك" ("as a")
        assert any(statement(c).split()[-1].startswith("ك") or " ك" in statement(c)
                   for c in ar if c["group"] == "role")
        # professional / overall / prior / relevant / related wording without a target
        lows = " ".join(statement(c).lower() for c in CASES if not c["gold"]["names_role_or_work"])
        for w in ("professional", "overall", "prior", "relevant", "related", "مهنية", "إجمالية", "ذات صلة"):
            assert w in lows, w

    def test_difficult_contrasts(self):
        pairs = {tuple(sorted((c["id"], o))) for c in CASES for o in c["contrast_with"]}
        assert len(pairs) >= 18
        for lang in ("en", "ar"):
            cross = [p for p in pairs if BY[p[0]]["lang"] == lang
                     and BY[p[0]]["gold"]["names_role_or_work"] != BY[p[1]]["gold"]["names_role_or_work"]]
            assert len(cross) >= 7, lang
        # the named near-identical wordings with different answers
        for a, b in (("BE15", "BE16"), ("BE17", "BE18"), ("BE19", "BE09"), ("BE12", "BE06"), ("BE14", "BE05"),
                     ("BA07", "BA12"), ("BA08", "BA13"), ("BA18", "BA09"), ("BA04", "BA10"), ("BA15", "BA16"),
                     ("BA21", "BA01"), ("BA14", "BA05")):
            assert tuple(sorted((a, b))) in pairs, (a, b)


# ── gold agrees with the deterministic Pass A code (scripted, offline) ─────

def _replay(c, prompt_version=None):
    jd = "\n".join(c["jd_lines"])
    analysis = {"experience": copy.deepcopy(c["analysis"])}
    (crit,) = enumerate_experience_criteria("BASIS", analysis)
    durs = {m.text: did for did, _, m in JDText(jd).durations()}
    o = {**copy.deepcopy(c["oracle"]), "criterion_id": crit.criterion_id}
    o["duration"] = durs[o["duration"]]
    client = _Client(json.dumps({"criteria": [o]}, ensure_ascii=False))
    return run(pr.run_pass_a_job("BASIS", jd, analysis, client=client, prompt_version=prompt_version))


class TestOracleReplay:
    @pytest.mark.parametrize("cid", sorted(BY))
    def test_s1a_1_1_reproduces_gold(self, cid):
        # s1a-basis-1 holds the s1a-1.1 oracle answers: they replay under the s1a-1.1 contract unchanged
        c = BY[cid]
        res = _replay(c, "s1a-1.1")
        assert res.status == "ok" and res.outcome.meta["calls"] == 1 and not res.outcome.meta["repair_used"]
        (f,) = res.frozen
        g = c["gold"]
        assert f.frame.target_basis == g["target_basis"]
        assert f.artefact.policy == g["policy"]
        assert [(t.text, t.type) for t in f.frame.targets] == [(t["text"], t["kind"]) for t in g["targets"]]
        assert f.frame.where_evidence == ()

    @pytest.mark.parametrize("cid", sorted(c["id"] for c in CASES if c["gold"]["target_basis"] == "setting_only"))
    def test_current_contract_rejects_the_unanchored_s1a_1_1_setting_only(self, cid):
        # the s1a-1.3 contract: a setting_only answer without where_evidence is never accepted as it stands
        res = _replay(BY[cid])
        assert res.status == "failed" and res.outcome.meta["repair_used"]
        assert any("where_evidence" in e for e in res.outcome.errors["errors"])


# ── s1a-basis-2: the same cases and gold, the s1a-1.3 output contract ───────

FX2 = json.loads(PATH_V2.read_text(encoding="utf-8"))
BY2 = {c["id"]: c for c in FX2["cases"]}


class TestFixtureV2:
    def test_fingerprint_and_version(self):
        assert hashlib.sha256(PATH_V2.read_bytes()).hexdigest() == FIXTURE_V2_SHA256
        assert FX2["fixture_version"] == "s1a-basis-2" and set(FX2) == {"_comment", "fixture_version", "cases"}
        assert "s1a-basis-1" in FX2["_comment"] and "where_evidence" in FX2["_comment"]

    def test_only_the_output_contract_changed(self):
        assert [c["id"] for c in FX2["cases"]] == [c["id"] for c in CASES]
        for c1 in CASES:
            stmt = statement(c1)
            c2 = copy.deepcopy(BY2[c1["id"]])
            o2 = c2.pop("oracle")
            c1 = copy.deepcopy(c1)
            o1 = c1.pop("oracle")
            assert c1 == c2                                         # gold, jd lines, analysis, groups unchanged
            w = o2.pop("where_evidence", None)
            assert o1 == o2
            if c1["gold"]["target_basis"] == "setting_only":
                assert w and len(w) == 1 and w[0]["line"] == 2 and w[0]["text"] in stmt
                assert list(BY2[c1["id"]]["oracle"]).index("where_evidence") == list(o1).index("target_basis") + 1
            else:
                assert w is None

    @pytest.mark.parametrize("cid", sorted(BY2))
    def test_current_pass_a_reproduces_gold(self, cid):
        c = BY2[cid]
        res = _replay(c)
        assert res.status == "ok" and res.outcome.meta["calls"] == 1 and not res.outcome.meta["repair_used"]
        (f,) = res.frozen
        g = c["gold"]
        assert f.frame.target_basis == g["target_basis"]
        assert f.artefact.policy == g["policy"]
        assert [(t.text, t.type) for t in f.frame.targets] == [(t["text"], t["kind"]) for t in g["targets"]]
        assert [w.text for w in f.frame.where_evidence] == [w["text"] for w in c["oracle"].get("where_evidence", [])]
        assert f.artefact.settings == ()                    # evidence only: never a setting of the artefact


# ── no leakage into the prompt or from existing fixtures ────────────────────

class TestNoLeakage:
    @pytest.mark.parametrize("version", [None, "s1a-1.1"])
    def test_statements_not_in_the_prompt(self, version):
        p = prompt_a.load_pass_a_prompt(version).lower()
        for c in CASES:
            assert statement(c).lower() not in p, c["id"]
            for t in c["gold"]["targets"]:
                assert t["text"].lower() not in p or len(t["text"]) < 6, (c["id"], t["text"])

    def test_statements_not_copied_from_main_or_boundary(self):
        texts = set()
        for name in UNCHANGED:
            data = json.loads((FIX / name).read_text(encoding="utf-8"))
            for case in data["cases"]:
                texts |= {ln.strip("- .").lower() for ln in case["jd_lines"]}
        assert not {statement(c).lower() for c in CASES} & texts

    def test_only_the_evaluation_harness_references_it(self):
        # the Pass A harness evaluates it (--fixture target_basis); no service / production code reads it
        for p in (BACKEND / "services").rglob("*.py"):
            assert "s1a_target_basis_cases" not in p.read_text(encoding="utf-8"), p
        assert "s1a_target_basis_cases" in (BACKEND / "scripts" / "s1_pass_a_eval.py").read_text(encoding="utf-8")
        # s1a-1.3 where_evidence examples are not fixture where phrases
        p13 = prompt_a.load_pass_a_prompt().lower()
        for c in FX2["cases"]:
            for w in c["oracle"].get("where_evidence", []):
                assert w["text"].lower() not in p13, (c["id"], w["text"])
        # the fixture's names_role_or_work gold is a fixture field only: the active prompt never asks for it
        assert "names_role_or_work" not in prompt_a.load_pass_a_prompt()
