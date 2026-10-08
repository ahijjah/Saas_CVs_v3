"""
S1-A-1.3 Stage D preparation: the target-basis HELD-OUT fixture (scripts/s1_eval_fixtures/
s1a_target_basis_heldout_cases.json, s1a-basis-heldout-1). Offline structural checks only: identity and SHA pin,
schema, coverage (English and Arabic; functional, setting-only, combined function + setting, unrestricted, vague,
ambiguous wording, adjective forms), internal gold consistency, independence from the development fixtures and the
prompts, and that its labelled oracle answers agree with the deterministic Pass A code (gold representable; NO
prompt and NO model involved). The harness refuses a real run without --allow-heldout.
The v3 held-out fixtures are never read here.
"""
import asyncio
import copy
import hashlib
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.s1_requirements.criteria import enumerate_experience_criteria
from services.s1_requirements.jd_text import JDText
from services.s1_requirements.validator import implied_policy
from services.s1_two_pass import pass_a_runner as pr
from services.s1_two_pass import prompt_a
from services.s1_two_pass.schema import TARGET_BASES

BACKEND = Path(__file__).resolve().parent.parent
FIX = BACKEND / "scripts" / "s1_eval_fixtures"
PATH = FIX / "s1a_target_basis_heldout_cases.json"
SHA = "81e607d183a186bf1d153a534bdd92898a7cb247f5ad3378cf4b109fc95f7018"
DEV = ("s1_ctx_main_cases.json", "s1_boundary_cases.json", "s1a_target_basis_cases_v2.json")
GROUPS = {"functional", "role", "setting_only", "combined", "total", "vague", "ambiguous", "adjective"}
NO_TARGET_POLICY = {"setting_only": "sector", "total_experience": "pure_duration", "unspecified": "pure_duration"}

sys.path.insert(0, str(BACKEND / "scripts"))
_spec = importlib.util.spec_from_file_location("s1_pass_a_eval", BACKEND / "scripts" / "s1_pass_a_eval.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)

FX = json.loads(PATH.read_text(encoding="utf-8"))
CASES = FX["cases"]
BY = {c["id"]: c for c in CASES}


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def statement(c):
    return c["oracle"]["requirement_spans"][0]["text"]


class TestIdentity:
    def test_pinned(self):
        assert hashlib.sha256(PATH.read_bytes()).hexdigest() == SHA == ev.FIXTURE_SHA256["target_basis_heldout"]
        assert ev.FIXTURES["target_basis_heldout"] == PATH and "target_basis_heldout" in ev.HELDOUT_FIXTURES
        assert FX["fixture_version"] == "s1a-basis-heldout-1" and set(FX) == {"_comment", "fixture_version", "cases"}
        for frag in ("HELD-OUT", "FROZEN", "never used to tune", "--allow-heldout", "8ef8e4c"):
            assert frag in FX["_comment"], frag

    def test_real_run_refused_without_allow_heldout(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(ev.ctx, "make_real_client", lambda: (_ for _ in ()).throw(AssertionError("client")))
        assert ev.main(["--out", str(tmp_path), "--fixture", "target_basis_heldout", "--mode", "real",
                        "--confirm-real"]) == 2
        assert "--allow-heldout" in capsys.readouterr().out

    def test_no_service_code_reads_it(self):
        for p in (BACKEND / "services").rglob("*.py"):
            assert "s1a_target_basis_heldout" not in p.read_text(encoding="utf-8"), p


class TestSchemaAndGold:
    @pytest.mark.parametrize("cid", sorted(BY))
    def test_shape_and_consistency(self, cid):
        c = BY[cid]
        assert set(c) == {"id", "group", "lang", "ambiguous", "analysis", "jd_lines", "gold", "oracle"}
        assert c["group"] in GROUPS and c["lang"] in ("en", "ar") and isinstance(c["ambiguous"], bool)
        assert c["jd_lines"] == ["Requirements" if c["lang"] == "en" else "المتطلبات", "- " + statement(c) + "."]
        g, o = c["gold"], c["oracle"]
        assert g["target_basis"] in TARGET_BASES and len(g["rationale"]) > 20
        assert g["names_role_or_work"] == (g["target_basis"] == "targets") == bool(g["targets"])
        assert g["policy"] == (implied_policy([t["kind"] for t in g["targets"]]) if g["targets"]
                               else NO_TARGET_POLICY[g["target_basis"]])
        assert o["target_basis"] == g["target_basis"]
        assert [(r["text"], r["kind"]) for r in o["restrictions"] if r["kind"] != "vague"] == [
            (t["text"], t["kind"]) for t in g["targets"]]
        assert bool(o.get("where_evidence")) == (g["target_basis"] == "setting_only")
        vague = [r for r in o["restrictions"] if r["kind"] == "vague"]
        assert bool(vague) == (g["target_basis"] == "unspecified")
        jd = JDText("\n".join(c["jd_lines"]))
        assert [m.text for _, _, m in jd.durations()] == [o["duration"]]
        for x in o["restrictions"] + o.get("where_evidence", []):
            assert x["line"] == 2 and jd.span_on_line(2, x["text"], boundaries=True) is not None

    def test_ids_and_ordering(self):
        ids = [c["id"] for c in CASES]
        assert len(ids) == len(set(ids)) == 52
        assert all(i[:2] in ("HE", "HA") and (i[:2] == "HE") == (BY[i]["lang"] == "en") for i in ids)


class TestCoverage:
    def test_language_balance(self):
        assert Counter(c["lang"] for c in CASES) == {"en": 26, "ar": 26}

    @pytest.mark.parametrize("lang", ["en", "ar"])
    @pytest.mark.parametrize("group", sorted(GROUPS))
    def test_every_group_in_both_languages(self, lang, group):
        assert any(c["lang"] == lang and c["group"] == group for c in CASES)

    @pytest.mark.parametrize("lang", ["en", "ar"])
    def test_every_basis_in_both_languages(self, lang):
        assert {c["gold"]["target_basis"] for c in CASES if c["lang"] == lang} == set(TARGET_BASES)

    def test_class_balance_and_required_shapes(self):
        assert Counter(c["gold"]["names_role_or_work"] for c in CASES) == {True: 26, False: 26}
        combined = [c for c in CASES if c["group"] == "combined"]
        assert combined and all(c["gold"]["target_basis"] == "targets" for c in combined)
        adj_ar = [c for c in CASES if c["group"] == "adjective" and c["lang"] == "ar"]
        assert {c["gold"]["target_basis"] for c in adj_ar} == {"targets", "total_experience"}
        assert all(statement(c).startswith("خبرة ") and len(statement(c).split()[1]) > 3 for c in adj_ar)
        amb = [c for c in CASES if c["group"] == "ambiguous"]
        assert all(c["ambiguous"] for c in amb) and not any(c["ambiguous"] for c in CASES if c not in amb)


class TestIndependence:
    def test_statements_and_phrases_not_in_dev_fixtures_or_prompts(self):
        dev = " ".join(json.dumps(json.loads((FIX / n).read_text(encoding="utf-8")), ensure_ascii=False)
                       for n in DEV).lower()
        prompts = " ".join(prompt_a.load_pass_a_prompt(v) for v in ("s1a-1.1", "s1a-1.3")).lower()
        for c in CASES:
            phrases = [statement(c)] + [t["text"] for t in c["gold"]["targets"]] + [
                w["text"] for w in c["oracle"].get("where_evidence", [])]
            for ph in phrases:
                if len(ph) > 4:
                    assert ph.lower() not in dev, (c["id"], ph)
                    assert ph.lower() not in prompts, (c["id"], ph)


class _Client:
    def __init__(self, content):
        async def create(**kw):
            usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content),
                                                            finish_reason="stop")], usage=usage)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


class TestGoldRepresentable:
    @pytest.mark.parametrize("cid", sorted(BY))
    def test_labelled_answer_reproduces_gold(self, cid):
        # deterministic code only: the labelled answer (not a model output) validates and yields the gold
        c = BY[cid]
        jd = "\n".join(c["jd_lines"])
        analysis = {"experience": copy.deepcopy(c["analysis"])}
        (crit,) = enumerate_experience_criteria("HELD", analysis)
        durs = {m.text: did for did, _, m in JDText(jd).durations()}
        o = {**copy.deepcopy(c["oracle"]), "criterion_id": crit.criterion_id}
        o["duration"] = durs[o["duration"]]
        res = run(pr.run_pass_a_job("HELD", jd, analysis, client=_Client(json.dumps({"criteria": [o]},
                                                                                     ensure_ascii=False))))
        assert res.status == "ok" and res.outcome.meta["calls"] == 1
        (f,) = res.frozen
        g = c["gold"]
        assert (f.frame.target_basis, f.artefact.policy) == (g["target_basis"], g["policy"])
        assert [(t.text, t.type) for t in f.frame.targets] == [(t["text"], t["kind"]) for t in g["targets"]]
        assert [w.text for w in f.frame.where_evidence] == [w["text"] for w in c["oracle"].get("where_evidence", [])]
