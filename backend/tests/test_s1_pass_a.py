"""
S1 two-pass Step 2 — Pass A (s1a-1.3, the experience TARGET pass; s1a-1.1 runnable for replay, s1a-1.0 / s1a-1.2 kept for
audit): prompt integrity and leakage, request
construction, cache / fingerprint / version identity, validator behaviour for the real wire, the one-repair
orchestration with F6, and the independence of Pass A from the qualifying-context analysis.
Offline only: a scripted fake client, no OpenAI, no network, no database. All JDs SYNTHETIC.
"""
import ast
import asyncio
import copy
import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.s1_requirements import classifier as v3clf
from services.s1_requirements.criteria import enumerate_experience_criteria
from services.s1_requirements.jd_text import JDText
from services.s1_two_pass import assemble as asm
from services.s1_two_pass import pass_a as pa
from services.s1_two_pass import pass_a_runner as pr
from services.s1_two_pass import prompt_a
from services.s1_two_pass import schema as sc

BACKEND = Path(__file__).resolve().parent.parent
PKG = BACKEND / "services" / "s1_two_pass"
PROMPT = prompt_a.load_pass_a_prompt()
PROMPT_11 = prompt_a.load_pass_a_prompt("s1a-1.1")
S1A_13 = "0cf68cadc53d05e8e26c75bb94d2ea279f91dcbb65d8663f17b9052f4c98656d"


class FakeClient:
    """Returns queued answers (str, (str, finish) or an Exception) and records every request."""

    def __init__(self, *items):
        self.items, self.requests = list(items), []

        async def create(**kw):
            self.requests.append(copy.deepcopy(kw))
            item = self.items.pop(0)
            if isinstance(item, Exception):
                raise item
            content, finish = item if isinstance(item, tuple) else (item, "stop")
            usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content),
                                                            finish_reason=finish)], usage=usage)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def job(hints, years, lines):
    a = {"experience": {"minimum_years": years, "relevant_roles": list(hints)}}
    return a, "\n".join(lines), enumerate_experience_criteria("J1", a)


def hinted(cid, spans, *, hints=1, types=None, basis="targets", duration="D1", ambiguity=(), **over):
    types = types or ["role"] * hints
    it = {"criterion_id": cid, "requirement_spans": [{"line": ln, "text": t} for ln, t in spans],
          "targets": [{"hint": f"T{i}", "type": types[i - 1], "match": "exact", "jd_span": None}
                      for i in range(1, hints + 1)],
          "target_basis": basis, "duration": duration, "ambiguity": list(ambiguity), "note": "n"}
    it.update(over)
    return it


def free(cid, spans, restrictions, *, basis, duration="D1", ambiguity=(), **over):
    it = {"criterion_id": cid, "requirement_spans": [{"line": ln, "text": t} for ln, t in spans],
          "restrictions": [{"line": ln, "text": t, "kind": k} for ln, t, k in restrictions],
          "target_basis": basis, "duration": duration, "ambiguity": list(ambiguity), "note": "n"}
    it.update(over)
    return it


def resp(*items):
    return json.dumps({"criteria": list(items)}, ensure_ascii=False)


def pass_a_job(a, jd, *answers, cache=None, **kw):
    client = FakeClient(*answers)
    return client, run(pr.run_pass_a_job("J1", jd, a, client=client, cache=cache, **kw))


AUD = "Internal Auditor"
L_ROLE = "Minimum 5 years as an Internal Auditor"
L_FUNC = "Minimum 4 years of payroll administration experience"
L_MIXED = "Minimum 4 years as a Laboratory Technician or in laboratory testing"
L_TOTAL = "Minimum 4 years of professional experience"
L_WHERE = "Minimum 4 years of experience gained with public hospitals"
L_VAGUE = "Minimum 4 years of relevant experience"
L_COMPOUND = "Minimum 5 years of experience as an Internal Auditor, including 2 years in a supervisory role"


# ── A. prompt integrity, version and fingerprint ─────────────────────────────

class TestPromptIdentity:
    def test_version_file_and_pinned_sha(self):
        assert sc.S1A_PROMPT_VERSION == "s1a-1.3" and prompt_a.S1A_PROMPT_PATH.name == "s1a-1.3.txt"
        raw = prompt_a.S1A_PROMPT_PATH.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == prompt_a.S1A_PROMPT_SHA256 == S1A_13
        assert prompt_a.pass_a_prompt_fingerprint() == "0cf68cadc53d"
        assert PROMPT == raw.decode("utf-8")
        # s1a-1.1 stays pinned and loadable by explicit version (replay / comparison)
        assert prompt_a.pass_a_prompt_sha256("s1a-1.1") == (
            "952299303431f68d62b9544d6897baa488855c37c22d0fd2890789b15d463e11")
        assert prompt_a.pass_a_prompt_fingerprint("s1a-1.1") == "952299303431" and PROMPT_11 != PROMPT

    def test_s1a_1_0_kept_unchanged_for_audit(self):
        old = prompt_a.PROMPT_DIR / "s1a-1.0.txt"
        assert hashlib.sha256(old.read_bytes()).hexdigest() == prompt_a.PROMPT_SHA256["s1a-1.0"] == (
            "4caabb71429c0cc1ac997986c2a6c95775b06f4f7acebcb7025e27757f74bf36")
        assert prompt_a.load_pass_a_prompt("s1a-1.0") == old.read_text(encoding="utf-8") != PROMPT
        # s1a-1.2 (withdrawn Option D) stays pinned for audit only; every pinned file still matches its hash
        assert set(prompt_a.PROMPT_SHA256) == {"s1a-1.0", "s1a-1.1", "s1a-1.2", "s1a-1.3"}
        for v, sha in prompt_a.PROMPT_SHA256.items():
            assert hashlib.sha256((prompt_a.PROMPT_DIR / f"{v}.txt").read_bytes()).hexdigest() == sha, v

    def test_tampered_prompt_is_an_integrity_error(self, tmp_path, monkeypatch):
        bad = tmp_path / "s1a-1.1.txt"
        bad.write_text(PROMPT + " ", encoding="utf-8")
        monkeypatch.setattr(prompt_a, "S1A_PROMPT_PATH", bad)
        with pytest.raises(prompt_a.PromptIntegrityError):
            prompt_a.load_pass_a_prompt()

    def test_security_suffix_is_the_shared_one(self):
        from services.ai_service import _SECURITY_HARDENING_SUFFIX
        assert PROMPT.endswith(_SECURITY_HARDENING_SUFFIX)
        assert PROMPT.count("SECURITY RULES") == 1

    def test_model_settings(self):
        assert (sc.S1A_MODEL, sc.S1A_TEMPERATURE, sc.S1A_MAX_TOKENS) == ("gpt-4o-mini", 0.0, 4000)
        assert sc.S1A_PROMPT_CODE == "recruitment.experience_target_pass"
        assert sc.S1A_INPUT_VERSION == "s1a-in-1" and sc.S1V4_VERSION == "2.0.0"

    def test_v3_prompt_untouched(self):
        assert v3clf.S1_PROMPT_VERSION == "s1-6.1"
        assert PROMPT != v3clf.S1_SYSTEM_PROMPT and v3clf.prompt_fingerprint() != prompt_a.pass_a_prompt_fingerprint()


# ── B. prompt leakage: no qualifying-context interpretation ──────────────────

# s1a-1.3 names "sector" / "industry" ONLY to define a where limit (the evidence of setting_only); every other
# qualifying-context word stays banned, and s1a-1.1 still passes the full list
WHERE_WORDS = [r"\bsector", r"industr"]
BANNED = [r"\bsettings?\b", r"context", r"geograph", r"\bregion", r"countr", r"organi[sz]ation",
          r"environment", r"qualifying", r"\bscope", r"multinational", r"government", r"infrastructure",
          r"\bgcc\b", r"locat", r"project type", r"\bsetting\"", r"ambiguous_context_scope", r"compound_requirement"]


class TestPromptLeakage:
    @pytest.mark.parametrize("pattern", BANNED)
    def test_no_context_vocabulary(self, pattern):
        assert not re.search(pattern, PROMPT, flags=re.IGNORECASE), pattern

    @pytest.mark.parametrize("pattern", BANNED + WHERE_WORDS)
    def test_s1a_1_1_has_no_context_vocabulary(self, pattern):
        assert not re.search(pattern, PROMPT_11, flags=re.IGNORECASE), pattern

    def test_where_words_only_define_a_where_limit(self):
        body = PROMPT[:PROMPT.index("SECURITY RULES")]
        hits = [ln for ln in body.splitlines() if any(re.search(w, ln, flags=re.IGNORECASE) for w in WHERE_WORDS)]
        assert hits and all("where" in ln.lower() or '"setting_only"' in ln for ln in hits)

    def test_no_context_fields_or_kinds_in_the_wire(self):
        for frag in ('"setting"', '"settings"', '"contexts"', '"context_spans"', '"target_gap"', '"sector"',
                     '"context"', '"kind": "sector"', '"kind": "context"'):
            assert frag not in PROMPT, frag

    def test_where_is_only_the_evidence_of_setting_only(self):
        # s1a-1.3: a where limit is never a target / restriction / ambiguity; it is quoted only as the evidence of a
        # setting_only reading (s1a-1.1 forbade quoting it at all, leaving setting_only without evidence)
        body = PROMPT[:PROMPT.index("SECURITY RULES")]
        assert "Do not quote or describe that limit." not in body and "Do not quote or describe that limit." in PROMPT_11
        assert "is never a target, a restriction or an ambiguity" in body and "In every other case you ignore it." in body
        assert 'REQUIRED for "setting_only" and ONLY for it' in body
        assert "Decide the basis from the role and work words ONLY." not in body       # the s1a-1.1 contradiction

    def test_target_basis_contract(self):
        sec = PROMPT[PROMPT.index("5 TARGET_BASIS"):PROMPT.index("6 DURATION")]
        for b in sc.TARGET_BASES:
            assert f'"{b}"' in sec, b
        assert "never a default" in sec and "an empty restriction list does not mean \"total_experience\"" in sec
        assert '"kind": "role" | "function" | "vague"' in PROMPT
        assert "These three are the only kinds." in PROMPT
        out = PROMPT[PROMPT.index("OUTPUT:"):]
        assert out.count('"target_basis"') == 5 and '"target_basis": "total_experience"' not in out
        assert '"target_basis": "setting_only", "where_evidence": [{"line": 13' in out
        assert '"target_basis": "unspecified", "where_evidence": []' in out

    def test_sections_in_order(self):
        heads = ["1 REQUIREMENT_SPANS", "2 TARGETS", "3 MATCH, JD_SPAN and ALIGNMENT", "4 RESTRICTIONS",
                 "5 TARGET_BASIS", "6 DURATION", "7 AMBIGUITY", "8 NOTE", "OUTPUT:"]
        pos = [PROMPT.index(h) for h in heads]
        assert pos == sorted(pos)

    @pytest.mark.parametrize("sentence", [
        # s1-5.2 target / alignment / abbreviation / duration / compound rules, kept verbatim
        'Return exactly ONE target object for each supplied hint id',
        'Test: if "she is a <target>" makes sense, it is a role',
        'If the type cannot be decided, use "function" and report "ambiguous_relevance".',
        '"equivalent"  a verbatim phrase inside this criterion\'s requirement_spans names SUBSTANTIALLY THE SAME '
        'role or function as the hint',
        'abbreviation  an all-capitals acronym and its expansion (HR / Human Resources)',
        'Name a role ONCE: if the JD gives both the full form and its acronym',
        'An abbreviation is equivalent only when the requirement span itself makes its meaning unambiguous.',
        'A phrase may be the jd_span of at most one hint',
        'ANY material word means the JD phrase is NOT the same role/function: use "none" instead.',
        'If more than one candidate could be this criterion\'s minimum, return null and report "multiple_durations".',
        'keep the whole statement in requirement_spans; the code records it as a compound requirement.',
        'Alternatives are separate restrictions ("as a Laboratory Technician or in laboratory testing" -> one role '
        'and one function).',
        'Leaving out a restriction silently broadens the requirement.',
        '"conflicting_requirements"  the JD itself states this experience requirement in materially different ways',
    ])
    def test_s1_5_2_rules_preserved(self, sentence):
        assert sentence in PROMPT

    def test_ambiguity_vocabulary_is_s1_5_2(self):
        sec = PROMPT[PROMPT.index("7 AMBIGUITY"):PROMPT.index("8 NOTE")]
        assert re.findall(r'^  "([a-z_]+)"', sec, flags=re.M) == list(pa.PASS_A_AMBIGUITY_CODES)

    def test_examples_avoid_the_context_fixture_vocabulary(self):
        p = PROMPT.lower()
        for word in ("gcc", "islamic", "government entities", "infrastructure", "multinational", "big four",
                     "energy sector", "banks", "public hospitals"):
            assert word not in p, word


# ── C. request construction ──────────────────────────────────────────────────

class TestRequest:
    def _req(self):
        a, jd, crits = job([AUD], 5, ["Requirements", "- " + L_ROLE + "."])
        return pa.build_pass_a_request(JDText(jd), crits)

    def test_messages(self):
        req = self._req()
        msgs = pr.build_pass_a_messages(req)
        assert [m["role"] for m in msgs] == ["system", "user"]
        assert msgs[0]["content"] == PROMPT and msgs[1]["content"] == req.user_message
        payload = json.loads(req.user_message[len("INPUT:\n"):])
        assert payload == req.payload
        assert set(payload) == {"s1a_input_version", "jd_lines", "duration_candidates", "criteria"}
        assert payload["s1a_input_version"] == "s1a-in-1"
        assert payload["criteria"][0]["target_hints"] == [{"id": "T1", "text": AUD}]
        assert payload["duration_candidates"][0]["id"] == "D1"

    def test_call_arguments(self):
        req = self._req()
        call = pr.build_pass_a_call(req)
        assert set(call) == {"model", "messages", "temperature", "max_tokens", "response_format"}
        assert (call["model"], call["temperature"], call["max_tokens"]) == ("gpt-4o-mini", 0.0, 4000)
        assert call["response_format"] == {"type": "json_object"}
        assert pr.build_pass_a_call(req, "other")["model"] == "other"

    def test_fake_client_receives_exactly_the_built_call(self):
        a, jd, crits = job([AUD], 5, ["Requirements", "- " + L_ROLE + "."])
        cid = crits[0].criterion_id
        client, res = pass_a_job(a, jd, resp(hinted(cid, [(2, L_ROLE)])))
        assert res.status == "ok"
        (sent,) = client.requests
        assert sent == pr.build_pass_a_call(pa.build_pass_a_request(JDText(jd), crits))

    def test_request_never_carries_context_or_analysis_extras(self):
        a, jd, crits = job([AUD], 5, ["Requirements", "- " + L_ROLE + "."])
        a["experience"]["qualifying_context"] = {"state": "identified", "contexts": ["Zeta"], "source": "analysis"}
        a["qualifying_context_audit"] = {"current": {"jd_sha256": "x"}}
        a["experience"]["preferred_sectors"] = ["Zeta sector"]
        um = pa.build_pass_a_request(JDText(jd), enumerate_experience_criteria("J1", a)).user_message
        for frag in ("qualifying", "Zeta", "sector", "context", "display_text", "min_years"):
            assert frag not in um, frag

    def test_request_token_bound_recorded(self):
        a, jd, crits = job([AUD], 5, ["Requirements", "- " + L_ROLE + "."])
        _, res = pass_a_job(a, jd, resp(hinted(crits[0].criterion_id, [(2, L_ROLE)])))
        m = res.outcome.meta
        assert m["request_token_upper_bound"] > len(PROMPT.encode("utf-8"))
        assert (m["prompt_version"], m["prompt_fingerprint"], m["prompt_sha256"]) == (
            "s1a-1.3", "0cf68cadc53d", prompt_a.S1A_PROMPT_SHA256)


# ── D. cache / fingerprint / version identity ───────────────────────────────

class TestCacheIdentity:
    def _setup(self):
        a, jd, crits = job([AUD], 5, ["Requirements", "- " + L_ROLE + "."])
        return a, jd, crits, pa.build_pass_a_request(JDText(jd), crits)

    def test_key_components(self, monkeypatch):
        a, jd, crits, req = self._setup()
        k = pa.pass_a_cache_key(req)
        assert k == pa.pass_a_cache_key(req, prompt_fingerprint="0cf68cadc53d", model="gpt-4o-mini")
        assert k == pa.pass_a_cache_key(req, prompt_version="s1a-1.3")
        assert k != pa.pass_a_cache_key(req, prompt_version="s1a-1.1")          # another prompt, another key
        assert k != pa.pass_a_cache_key(req, prompt_fingerprint="000000000000")
        assert k != pa.pass_a_cache_key(req, model="other")
        monkeypatch.setattr(pa, "S1A_PROMPT_VERSION", "s1a-9.9")
        assert pa.pass_a_cache_key(req) != k
        monkeypatch.undo()
        monkeypatch.setattr(pa, "S1V4_VERSION", "9.9.9")
        assert pa.pass_a_cache_key(req) != k

    def test_key_differs_from_v3_and_pass_b(self):
        a, jd, crits, req = self._setup()
        k = pa.pass_a_cache_key(req)
        assert k != v3clf.s1_cache_key(v3clf.build_request(JDText(jd), crits))
        from services.s1_two_pass import pass_b as pb
        assert k != pb.pass_b_cache_key(pb.build_pass_b_request(JDText(jd), []))

    def test_cache_hit_makes_no_call_and_gives_the_same_result(self):
        a, jd, crits, req = self._setup()
        cache = pr.InMemoryPassACache()
        cid = crits[0].criterion_id
        c1, r1 = pass_a_job(a, jd, resp(hinted(cid, [(2, L_ROLE)])), cache=cache)
        c2, r2 = pass_a_job(a, jd, cache=cache)                              # no answer queued: must not call
        assert c2.requests == [] and r2.outcome.meta["cache_hit"] is True
        assert r2.outcome.cache_key == r1.outcome.cache_key == pa.pass_a_cache_key(req)
        assert [f.artefact.to_dict() for f in r2.frozen] == [f.artefact.to_dict() for f in r1.frozen]

    def test_technical_failures_are_not_cached_validation_failures_are(self):
        a, jd, crits, req = self._setup()
        cache = pr.InMemoryPassACache()
        _, r = pass_a_job(a, jd, RuntimeError("down"), cache=cache)
        assert r.outcome.reason == "ai_unavailable" and cache.store == {}
        _, r = pass_a_job(a, jd, "not json", "still not json", cache=cache)
        assert r.outcome.reason == "validation_failed" and len(cache.store) == 1


# ── E. outcomes for the target families (scripted answers through the real runner) ────

def _only(res):
    assert res.status == "ok", (res.outcome.reason, res.outcome.errors)
    (f,) = res.frozen
    return f


class TestFamilies:
    def test_role(self):
        a, jd, crits = job([AUD], 5, ["Requirements", "- " + L_ROLE + "."])
        f = _only(pass_a_job(a, jd, resp(hinted(crits[0].criterion_id, [(2, L_ROLE)])))[1])
        assert f.frame.target_basis == "targets" and f.artefact.policy == "explicit_role"
        assert [(t.target_id, t.text, t.type) for t in f.frame.targets] == [("T1", AUD, "role")]
        assert f.frame.duration_span.text == "5 years"

    def test_function_without_hints(self):
        a, jd, crits = job([], 4, ["Requirements", "- " + L_FUNC + "."])
        f = _only(pass_a_job(a, jd, resp(free(crits[0].criterion_id, [(2, L_FUNC)],
                                               [(2, "payroll administration", "function")], basis="targets")))[1])
        assert f.artefact.policy == "functional" and [t.text for t in f.frame.targets] == ["payroll administration"]

    def test_mixed_role_function_alternatives(self):
        a, jd, crits = job([], 4, ["Requirements", "- " + L_MIXED + "."])
        f = _only(pass_a_job(a, jd, resp(free(crits[0].criterion_id, [(2, L_MIXED)], [
            (2, "Laboratory Technician", "role"), (2, "laboratory testing", "function")], basis="targets")))[1])
        assert f.artefact.policy == "mixed"
        assert [(t.text, t.type) for t in f.frame.targets] == [("Laboratory Technician", "role"),
                                                               ("laboratory testing", "function")]

    def test_mixed_hinted(self):
        a, jd, crits = job(["Laboratory Technician", "laboratory testing"], 4, ["Requirements", "- " + L_MIXED + "."])
        f = _only(pass_a_job(a, jd, resp(hinted(crits[0].criterion_id, [(2, L_MIXED)], hints=2,
                                                types=["role", "function"])))[1])
        assert f.artefact.policy == "mixed" and len(f.frame.targets) == 2

    def test_total_experience(self):
        a, jd, crits = job([], 4, ["Requirements", "- " + L_TOTAL + "."])
        f = _only(pass_a_job(a, jd, resp(free(crits[0].criterion_id, [(2, L_TOTAL)], [],
                                               basis="total_experience")))[1])
        assert f.frame.target_basis == "total_experience" and f.artefact.policy == "pure_duration"
        assert f.frame.targets == ()

    def test_setting_only(self):
        a, jd, crits = job([], 4, ["Requirements", "- " + L_WHERE + "."])
        f = _only(pass_a_job(a, jd, resp(free(crits[0].criterion_id, [(2, L_WHERE)], [], basis="setting_only",
                                               where_evidence=[{"line": 2, "text": "public hospitals"}])))[1])
        assert f.frame.target_basis == "setting_only" and f.artefact.policy == "sector"
        assert f.artefact.settings == () and f.frame.targets == ()      # Pass A never extracts a setting
        assert [w.text for w in f.frame.where_evidence] == ["public hospitals"]   # audit evidence only

    def test_unspecified(self):
        a, jd, crits = job([], 4, ["Requirements", "- " + L_VAGUE + "."])
        f = _only(pass_a_job(a, jd, resp(free(crits[0].criterion_id, [(2, L_VAGUE)], [(2, "relevant", "vague")],
                                               basis="unspecified")))[1])
        assert f.frame.target_basis == "unspecified"
        assert "ambiguous_relevance" in {r.code for r in f.artefact.reasons}

    def test_compound_detection_preserved(self):
        a, jd, crits = job([AUD], 5, ["Requirements", "- " + L_COMPOUND + "."])
        f = _only(pass_a_job(a, jd, resp(hinted(crits[0].criterion_id, [(2, L_COMPOUND)], duration=None,
                                                ambiguity=["multiple_durations"])))[1])
        assert "compound_requirement" in {r.code for r in f.artefact.reasons}
        assert f.artefact.required_years is None

    def test_abbreviation_equivalence_preserved(self):
        line = "Minimum 3 years as a Human Resources Manager"
        a, jd, crits = job(["HR Manager"], 3, ["Requirements", "- " + line + "."])
        t = {"hint": "T1", "type": "role", "match": "equivalent",
             "jd_span": {"line": 2, "text": "Human Resources Manager"},
             "alignment": [{"hint": "HR", "jd": "Human Resources", "relation": "abbreviation"},
                           {"hint": "Manager", "jd": "Manager", "relation": "same"}], "jd_extra": []}
        f = _only(pass_a_job(a, jd, resp(hinted(crits[0].criterion_id, [(2, line)], targets=[t])))[1])
        (tg,) = f.frame.targets
        # s1-5.2.2 preserved: an abbreviation the JD does not define is kept as a candidate, never trusted
        assert tg.text == "HR Manager" and tg.provenance == "original_ai" and tg.jd_span is None
        assert "equivalence_unverified" in {r.code for r in f.artefact.reasons}

    def test_material_qualifier_keeps_none(self):
        line = "Minimum 3 years as a Legal Translator"
        a, jd, crits = job(["Translator"], 3, ["Requirements", "- " + line + "."])
        t = {"hint": "T1", "type": "role", "match": "equivalent", "jd_span": {"line": 2, "text": "Legal Translator"},
             "alignment": [{"hint": "Translator", "jd": "Translator", "relation": "same"}],
             "jd_extra": [{"text": "Legal", "kind": "material"}]}
        cid = crits[0].criterion_id
        _, res = pass_a_job(a, jd, resp(hinted(cid, [(2, line)], targets=[t])),
                            resp(hinted(cid, [(2, line)], targets=[t])))
        assert res.status == "failed" and res.outcome.reason == "validation_failed"      # a claim kept is no target
        none = {"hint": "T1", "type": "role", "match": "none", "jd_span": None}
        _, res = pass_a_job(a, jd, resp(hinted(cid, [(2, line)], targets=[t])),
                            resp(hinted(cid, [(2, line)], targets=[none], ambiguity=["ambiguous_relevance"])))
        f = _only(res)
        # unchanged v3 assembly: the qualified phrase keeps the criterion in review (needs_confirmation)
        assert "ambiguous_relevance" in {r.code for r in f.artefact.reasons}
        assert f.artefact.spec_status == "needs_confirmation"


# ── F. validator + repair: contexts can never leak into Pass A ───────────────

def _note(client):
    (req,) = [r for r in client.requests[1:]]
    return req["messages"][-1]["content"]


class TestNoContextLeak:
    def _role(self):
        a, jd, crits = job([AUD], 5, ["Requirements", "- " + L_ROLE + " in public hospitals."])
        return a, jd, crits[0].criterion_id, L_ROLE + " in public hospitals."

    @pytest.mark.parametrize("key,value", [("settings", [{"line": 2, "text": "public hospitals"}]),
                                           ("setting", {"line": 2, "text": "public hospitals"}),
                                           ("contexts", [{"line": 2, "text": "public hospitals"}]),
                                           ("context_spans", [{"line": 2, "text": "public hospitals"}]),
                                           ("target_gap", [{"line": 2, "text": "public hospitals"}])])
    def test_context_field_is_repaired_away_without_context_words(self, key, value):
        a, jd, cid, line = self._role()
        client, res = pass_a_job(a, jd, resp(hinted(cid, [(2, line)], **{key: value})),
                                 resp(hinted(cid, [(2, line)])))
        assert res.status == "ok"
        assert res.outcome.meta["repair_used"] is True and res.outcome.meta["outcome"] == "repaired"
        note = _note(client)
        assert f"remove the key {key!r}" in note
        rest = note.replace(f"'{key}'", "")
        for w in ("context", "sector", "setting", "public hospitals"):
            assert w not in rest.lower(), w
        f = res.frozen[0]
        assert f.artefact.settings == () and [t.text for t in f.frame.targets] == [AUD]

    def test_context_field_kept_by_the_repair_fails_closed(self):
        a, jd, cid, line = self._role()
        bad = resp(hinted(cid, [(2, line)], settings=[{"line": 2, "text": "public hospitals"}]))
        _, res = pass_a_job(a, jd, bad, bad)
        assert res.status == "failed" and res.outcome.reason == "validation_failed"
        assert all(x.target_state == "target_failed" and x.policy is None for x in res.failed)

    @pytest.mark.parametrize("kind", ["context", "sector", "setting", "where"])
    def test_context_kind_is_retyped_or_fails(self, kind):
        a, jd, crits = job([], 4, ["Requirements", "- " + L_FUNC + "."])
        cid = crits[0].criterion_id
        main = resp(free(cid, [(2, L_FUNC)], [(2, "payroll administration", kind)], basis="setting_only"))
        client, res = pass_a_job(a, jd, main, resp(free(cid, [(2, L_FUNC)],
                                                        [(2, "payroll administration", "function")],
                                                        basis="targets")))
        assert res.status == "ok" and res.frozen[0].frame.target_basis == "targets"
        note = _note(client)
        assert f"kind {kind!r} is not allowed" in note
        assert "context" not in note.replace("'context'", "").lower()

    def test_context_kind_cannot_be_repaired_into_a_no_target_reading(self):
        # the s1-6.1 failure shape: a function returned as a context; a repair that drops it is F6-rejected
        a, jd, crits = job([], 4, ["Requirements", "- " + L_FUNC + "."])
        cid = crits[0].criterion_id
        main = resp(free(cid, [(2, L_FUNC)], [(2, "payroll administration", "context")], basis="setting_only"))
        _, res = pass_a_job(a, jd, main, resp(free(cid, [(2, L_FUNC)], [], basis="total_experience")))
        assert res.status == "failed" and res.outcome.reason == "validation_failed"
        assert any(pr.F6_ERROR in e for e in res.outcome.errors["repair_errors"])

    def test_context_scope_ambiguity_is_rejected(self):
        a, jd, cid, line = self._role()
        bad = resp(hinted(cid, [(2, line)], ambiguity=["ambiguous_context_scope"]))
        v = pa.validate_pass_a(bad, JDText(jd), enumerate_experience_criteria("J1", a),
                               pa.build_pass_a_request(JDText(jd), enumerate_experience_criteria("J1", a)).durations)
        assert not v.ok and any("ambiguity codes are only" in e for e in v.errors)

    def test_v3_context_messages_are_never_shown(self):
        # the v3 validator talks about contexts for hint-less settings; the repair-facing errors never do
        a, jd, crits = job([], 4, ["Requirements", "- " + L_WHERE + "."])
        cid = crits[0].criterion_id
        raw = resp(free(cid, [(2, L_WHERE)], [], basis="setting_only",
                        settings=[{"line": 2, "text": "public hospitals"}]))
        req = pa.build_pass_a_request(JDText(jd), crits)
        v = pa.validate_pass_a(raw, JDText(jd), crits, req.durations)
        assert not v.ok
        assert any("setting" in e.lower() or "context" in e.lower() for e in v.errors)      # audit keeps all
        shown = " ".join(e.message for e in v.scoped).replace("'settings'", "")
        assert "context" not in shown.lower() and "setting" not in shown.lower().replace("setting_only", "")

    def test_neutral_replacement_for_unflagged_context_messages(self):
        from services.s1_requirements.validator import ScopedError
        out = pa._repair_safe([ScopedError("c1", ("settings",), "c1: a context message"),
                               ScopedError("c2", ("duration",), "c2: duration message"),
                               ScopedError("c3", ("duration",), "c3: dropped")], {"c3"})
        assert [(e.criterion_id, e.scopes) for e in out] == [("c1", ("criterion",)), ("c2", ("duration",))]
        assert "context" not in out[0].message


class TestRepairAndF6:
    def test_missing_basis_is_taken_from_the_repair_only(self):
        a, jd, crits = job([], 4, ["Requirements", "- " + L_FUNC + "."])
        cid = crits[0].criterion_id
        main = free(cid, [(2, L_FUNC)], [(2, "payroll administration", "function")], basis="targets")
        del main["target_basis"]
        rep = free(cid, [(2, L_FUNC)], [(2, "payroll administration", "role")], basis="targets", duration=None)
        client, res = pass_a_job(a, jd, resp(main), resp(rep))
        f = _only(res)
        # only target_basis was in scope: the main restrictions and duration are kept
        assert [t.type for t in f.frame.targets] == ["function"] and f.frame.duration_span is not None
        assert {"criterion_id": cid, "field": "target_basis"} in res.outcome.meta["repair_merge"]["taken"]
        assert "target_basis is required" in _note(client)

    def test_repair_alone_never_creates_total_experience(self):
        a, jd, crits = job([], 4, ["Requirements", "- " + L_FUNC + "."])
        cid = crits[0].criterion_id
        main = free(cid, [(2, L_FUNC)], [], basis="targets")                    # inconsistent: F2
        _, res = pass_a_job(a, jd, resp(main), resp(free(cid, [(2, L_FUNC)], [], basis="total_experience")))
        assert res.status == "failed" and res.outcome.meta["outcome"] == "f6_rejected"
        assert all(x.target_state == "target_failed" for x in res.failed)

    def test_repair_alone_never_creates_setting_only(self):
        a, jd, crits = job([], 4, ["Requirements", "- " + L_WHERE + "."])
        cid = crits[0].criterion_id
        main = free(cid, [(2, L_WHERE)], [], basis="unspecified")
        _, res = pass_a_job(a, jd, resp(main), resp(free(cid, [(2, L_WHERE)], [], basis="setting_only")))
        assert res.status == "failed" and res.outcome.meta["outcome"] == "f6_rejected"

    def test_repair_never_empties_a_restriction_list(self):
        a, jd, crits = job([], 4, ["Requirements", "- " + L_FUNC + "."])
        cid = crits[0].criterion_id
        main = free(cid, [(2, L_FUNC)], [(2, "payroll admin", "function")], basis="targets")   # not verbatim
        _, res = pass_a_job(a, jd, resp(main), resp(free(cid, [(2, L_FUNC)], [], basis="total_experience")))
        assert res.status == "failed"

    def test_same_declared_absent_basis_survives_a_repair_of_another_field(self):
        a, jd, crits = job([], 4, ["Requirements", "- " + L_TOTAL + "."])
        cid = crits[0].criterion_id
        main = free(cid, [(2, L_TOTAL)], [], basis="total_experience", duration="D9")         # unknown duration
        f = _only(pass_a_job(a, jd, resp(main), resp(free(cid, [(2, L_TOTAL)], [], basis="total_experience")))[1])
        assert f.frame.target_basis == "total_experience" and f.frame.duration_span.text == "4 years"

    def test_restrictions_taken_from_the_repair_carry_its_basis(self):
        a, jd, crits = job([], 4, ["Requirements", "- " + L_MIXED + "."])
        cid = crits[0].criterion_id
        main = free(cid, [(2, L_MIXED)], [(2, "Laboratory Technician", "role"), (2, "lab testing", "function")],
                    basis="targets")
        rep = free(cid, [(2, L_MIXED)], [(2, "Laboratory Technician", "role"),
                                         (2, "laboratory testing", "function")], basis="targets")
        f = _only(pass_a_job(a, jd, resp(main), resp(rep))[1])
        assert [t.text for t in f.frame.targets] == ["Laboratory Technician", "laboratory testing"]

    def test_failed_repair_is_validation_failed_never_no_target(self):
        a, jd, crits = job([AUD], 5, ["Requirements", "- " + L_ROLE + "."])
        _, res = pass_a_job(a, jd, "{}", "{}")
        assert res.outcome.reason == "validation_failed" and res.outcome.meta["calls"] == 2
        assert all(x.spec_status == "failed_validation" and x.context_pass == "skipped" for x in res.failed)

    @pytest.mark.parametrize("answers,reason", [
        ((RuntimeError("down"),), "ai_unavailable"),
        ((("{}", "length"),), "output_truncated"),
        (("{}", RuntimeError("down")), "ai_unavailable"),
        (("{}", ("{}", "length")), "output_truncated"),
    ])
    def test_technical_failures(self, answers, reason):
        a, jd, crits = job([AUD], 5, ["Requirements", "- " + L_ROLE + "."])
        _, res = pass_a_job(a, jd, *answers)
        assert res.outcome.reason == reason
        assert all(x.spec_status == "failed_technical" and x.target_state == "target_failed" for x in res.failed)

    def test_exceeds_model_context(self, monkeypatch):
        monkeypatch.setattr(pr, "S1A_MAX_INPUT_TOKENS", 10)
        a, jd, crits = job([AUD], 5, ["Requirements", "- " + L_ROLE + "."])
        client, res = pass_a_job(a, jd)
        assert res.outcome.reason == "exceeds_model_context" and client.requests == []

    def test_at_most_two_calls(self):
        a, jd, crits = job([AUD], 5, ["Requirements", "- " + L_ROLE + "."])
        client, res = pass_a_job(a, jd, "{}", "{}", "{}")
        assert len(client.requests) == 2 and len(client.items) == 1

    def test_empty_job(self):
        _, res = pass_a_job({"experience": {}}, "x")
        assert res.status == "empty" and res.frozen == [] and res.failed == []


# ── G. integration with the committed foundation ─────────────────────────────

class TestIntegration:
    def test_pass_a_output_feeds_the_scripted_two_pass_assembly(self):
        line = "Minimum 5 years as an Internal Auditor in public hospitals"
        a, jd, crits = job([AUD], 5, ["Requirements", "- " + line + "."])
        cid = crits[0].criterion_id
        _, res = pass_a_job(a, jd, resp(hinted(cid, [(2, line)])))
        pb_raw = json.dumps({"criteria": [{"criterion_id": cid, "contexts": [
            {"line": 2, "text": "public hospitals", "scope": "all", "applies_to": ["T1"]}],
            "context_spans": [], "target_gap": []}]})
        out = asm.run_scripted_job("J1", jd, a, res.outcome.raw, pb_raw)
        (art,) = out.artifacts
        assert art.spec_status == "resolved" and [s.text for s in art.settings] == ["public hospitals"]
        assert [t.to_dict() for t in art.targets] == [t.to_dict() for t in res.frozen[0].frame.targets]

    def test_versions_record_the_pass_a_run(self):
        a, jd, crits = job([AUD], 5, ["Requirements", "- " + L_ROLE + "."])
        _, res = pass_a_job(a, jd, resp(hinted(crits[0].criterion_id, [(2, L_ROLE)])))
        assert res.frozen[0].artefact.prompt_version == ""        # the frozen v3 artefact is not the run record
        failed = pass_a_job(a, jd, RuntimeError("x"))[1].failed[0]
        assert failed.versions["pass_a"]["prompt_version"] == "s1a-1.3"
        assert failed.versions["pass_a"]["reason"] == "ai_unavailable"


# ── H. independence from the qualifying-context analysis ─────────────────────

def _variants(a):
    out = {"absent": copy.deepcopy(a)}
    for name, qc in {"identified": {"state": "identified", "contexts": ["public hospitals"], "source": "analysis"},
                     "none": {"state": "none", "contexts": [], "source": "analysis"},
                     "uncertain": {"state": "uncertain", "contexts": [], "source": "analysis"},
                     "recruiter": {"state": "identified", "contexts": ["Zeta"], "source": "recruiter"},
                     "garbage": "garbage"}.items():
        v = copy.deepcopy(a)
        v["experience"]["qualifying_context"] = qc
        v["qualifying_context_audit"] = {"schema": "qc_audit_v1", "current": {"jd_sha256": "0" * 64},
                                         "latest_run": {"status": "failed_technical"}}
        out[name] = v
    return out


class TestIndependence:
    def test_requests_identical_across_qc_variants_main_and_repair(self):
        a, jd, crits = job([AUD], 5, ["Requirements", "- " + L_ROLE + "."])
        cid = crits[0].criterion_id
        answers = (resp(hinted(cid, [(2, L_ROLE)], settings=[{"line": 2, "text": "Internal"}])),
                   resp(hinted(cid, [(2, L_ROLE)])))
        base_client, base = pass_a_job(a, jd, *answers)
        assert len(base_client.requests) == 2
        for name, v in _variants(a).items():
            client, res = pass_a_job(v, jd, *answers)
            assert client.requests == base_client.requests, name
            assert res.outcome.cache_key == base.outcome.cache_key, name
            assert [f.artefact.to_dict() for f in res.frozen] == [f.artefact.to_dict() for f in base.frozen], name

    def test_cache_shared_across_qc_variants(self):
        a, jd, crits = job([AUD], 5, ["Requirements", "- " + L_ROLE + "."])
        cache = pr.InMemoryPassACache()
        pass_a_job(a, jd, resp(hinted(crits[0].criterion_id, [(2, L_ROLE)])), cache=cache)
        for name, v in _variants(a).items():
            client, res = pass_a_job(v, jd, cache=cache)
            assert client.requests == [] and res.outcome.meta["cache_hit"], name

    def test_entry_points_take_no_qc_parameter(self):
        import inspect
        # prompt_version selects a pinned prompt and its contract (replay); it is never a qualifying context
        assert list(inspect.signature(pr.run_pass_a).parameters) == ["jd", "criteria", "client", "model", "cache",
                                                                     "prompt_version"]
        assert list(inspect.signature(pr.run_pass_a_job).parameters) == [
            "job_id", "jd_text", "analysis_json", "client", "model", "cache", "recruiter_fields", "prompt_version"]
        assert list(inspect.signature(pr.build_pass_a_messages).parameters) == ["req", "prompt_version"]

    @pytest.mark.parametrize("fname,allowed_extra", [
        ("prompt_a.py", set()),
        ("pass_a_runner.py", {"services.s0_experience.llm_call", "services.s1_requirements.repair"}),
    ])
    def test_static_imports(self, fname, allowed_extra):
        text = (PKG / fname).read_text(encoding="utf-8")
        assert "qualifying_context" not in text and "candidate_qc" not in text
        mods = set()
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Import):
                mods |= {x.name for x in node.names}
            elif isinstance(node, ast.ImportFrom):
                mods.add(node.module or "")
        for m in mods - allowed_extra:
            for banned in ("qualifying_context", "openai", "ai_service", "llm_", "classifier", "repair",
                           "s2_experience", "routers", "workers", "database", "sqlalchemy", "criteria_matcher",
                           "deterministic_scoring", "config", "pass_b"):
                assert banned not in m, (fname, m)

    def test_no_openai_import_at_module_load(self):
        src = (BACKEND / "services" / "s0_experience" / "llm_call.py").read_text(encoding="utf-8")
        top = [n for n in ast.parse(src).body if isinstance(n, (ast.Import, ast.ImportFrom))]
        assert all("openai" not in (getattr(n, "module", None) or "") and
                   all("openai" not in a.name for a in n.names) for n in top)
