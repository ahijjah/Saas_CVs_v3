"""Offline tests for services.qualifying_context (phase P1: not wired; no API, no database, no model call)."""
import ast
import asyncio
import hashlib
import importlib.util
import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

from services import qualifying_context as qcs
from services.qualifying_context import runner, schema, validation

BACKEND = Path(__file__).resolve().parent.parent
PKG = BACKEND / "services" / "qualifying_context"
FROZEN_PROMPT = BACKEND / "scripts" / "qc_eval_fixtures" / "prompts" / "candidate_qc-1.txt"
FROZEN_SHA = "fc979dd4a48b2aa5922c87f62e613700f898da13599e43e62a4fbc1304c851df"

_spec = importlib.util.spec_from_file_location("qc_context_eval", BACKEND / "scripts" / "qc_context_eval.py")
qe = importlib.util.module_from_spec(_spec)          # the frozen evaluator, loaded for parity only
_spec.loader.exec_module(qe)
CASES = qe.load_cases(qe.MAIN_FIXTURE) + qe.load_cases(qe.HELDOUT_FIXTURE)
BY_ID = {c["id"]: c for c in CASES}


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def qc(state, *contexts, source="analysis"):
    return json.dumps({"state": state, "contexts": list(contexts), "source": source}, ensure_ascii=False)


# ── frozen prompt ───────────────────────────────────────────────────────────

class TestPromptPin:
    def test_production_copy_is_byte_identical_to_frozen_prompt(self):
        assert runner.PROMPT_PATH.read_bytes() == FROZEN_PROMPT.read_bytes()

    def test_sha256_is_the_frozen_value(self):
        assert runner.PROMPT_SHA256 == FROZEN_SHA == qcs.PROMPT_SHA256
        assert hashlib.sha256(runner.PROMPT_PATH.read_bytes()).hexdigest() == FROZEN_SHA
        assert hashlib.sha256(FROZEN_PROMPT.read_bytes()).hexdigest() == FROZEN_SHA
        assert runner.PROMPT_VERSION == "candidate_qc-1"

    def test_tampered_prompt_refuses_before_any_call(self, tmp_path, monkeypatch):
        bad = tmp_path / "qc-1.txt"
        bad.write_bytes(FROZEN_PROMPT.read_bytes() + b" ")
        monkeypatch.setattr(runner, "PROMPT_PATH", bad)
        client = FakeClient()
        with pytest.raises(runner.PromptIntegrityError):
            run(runner.run_qualifying_context("- 3 years as a Clerk.", client=client))
        assert client.calls == []


# ── pinned configuration and request ───────────────────────────────────────

class TestPinnedConfig:
    def test_exact_validated_configuration(self):
        assert dict(runner.QC_CONFIG) == {"model": "gpt-4o-mini", "temperature": 0.2, "max_tokens": 200,
                                          "response_format": runner.QC_CONFIG["response_format"]}
        assert dict(runner.QC_CONFIG["response_format"]) == {"type": "json_object"}
        assert runner.CLIENT_MAX_RETRIES == 0

    def test_configuration_is_immutable(self):
        with pytest.raises(TypeError):
            runner.QC_CONFIG["model"] = "gpt-4o"
        with pytest.raises(TypeError):
            runner.QC_CONFIG["response_format"]["type"] = "text"

    def test_runner_accepts_no_model_or_prompt_overrides(self):
        params = set(inspect.signature(runner.run_qualifying_context).parameters)
        assert params == {"jd_text", "client"}
        assert set(inspect.signature(runner.build_request).parameters) == {"jd_text"}

    def test_request_is_prompt_plus_jd_only(self):
        jd = "Requirements\n- 4 years as a Credit Analyst {jd} in the shipping sector."
        req = runner.build_request(jd)
        assert req == {"model": "gpt-4o-mini", "temperature": 0.2, "max_tokens": 200,
                       "response_format": {"type": "json_object"},
                       "messages": [{"role": "system", "content": FROZEN_PROMPT.read_text(encoding="utf-8")},
                                    {"role": "user", "content": "Job description (verbatim, between the markers):"
                                                                "\n<<<JD\n" + jd + "\nJD>>>"}]}

    @pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
    def test_request_identical_to_validated_harness_request(self, case):
        harness = qe.request_payload(case, qe.load_prompt())
        assert runner.build_request(qe.case_jd(case)) == harness
        assert runner.USER_TEMPLATE == qe.USER_TEMPLATE

    def test_db_prompt_and_model_registry_cannot_affect_it(self, monkeypatch):
        import services.ai_service as ai
        before = runner.build_request("- 2 years as a Clerk.")

        def boom(*a, **k):
            raise AssertionError("must not be used")
        monkeypatch.setattr(ai, "load_active_prompt", boom)
        reg = pytest.importorskip("services.ai_model_registry_service")
        monkeypatch.setattr(reg, "resolve_stage_client", boom)
        assert runner.build_request("- 2 years as a Clerk.") == before
        client = FakeClient(qc("none"))
        assert run(runner.run_qualifying_context("- 2 years as a Clerk.", client=client)).ok

    def test_static_imports_are_isolated(self):
        banned = ("ai_service", "ai_model_registry", "prompt_config", "database", "routers", "workers",
                  "sqlalchemy", "llm_call", "s1_requirements.classifier", "s2_experience", "scripts",
                  "runtime_config", "criteria_matcher", "deterministic_scoring")
        top, lazy = set(), {}
        for path in PKG.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.ImportFrom):
                    top.add(node.module)
                elif isinstance(node, ast.Import):
                    top |= {a.name for a in node.names}
            for fn in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
                for n in ast.walk(fn):
                    if isinstance(n, ast.ImportFrom):
                        lazy.setdefault(fn.name, set()).add(n.module)
                    elif isinstance(n, ast.Import):
                        lazy.setdefault(fn.name, set()).update(a.name for a in n.names)
        assert not {m for m in top if any(b in m for b in banned)}
        assert "openai" not in top and "config" not in top
        assert lazy == {"make_client": {"openai", "config"}}

    def test_import_and_offline_validation_never_load_openai(self):
        code = ("import sys, json\n"
                "import services.qualifying_context as q\n"
                "q.build_request('- 3 years as a Clerk.')\n"
                "q.validate_response(json.dumps({'state':'none','contexts':[],'source':'analysis'}), 'x')\n"
                "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('openai','sqlalchemy','httpx') "
                "or m in ('config','services.ai_service','services.s0_experience.llm_call'))\n"
                "print('BAD', bad)")
        out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True,
                             env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(BACKEND)}, timeout=60)
        assert out.stdout.strip().splitlines()[-1] == "BAD []", out.stdout + out.stderr

    def test_client_has_no_sdk_retries(self, monkeypatch):
        created = {}

        class FakeAsyncOpenAI:
            def __init__(self, **kw):
                created.update(kw)
        fake_openai = type(sys)("openai")
        fake_openai.AsyncOpenAI = FakeAsyncOpenAI
        fake_cfg = type(sys)("config")
        fake_cfg.get_settings = lambda: type("S", (), {"openai_api_key": "sk-test"})()
        monkeypatch.setitem(sys.modules, "openai", fake_openai)
        monkeypatch.setitem(sys.modules, "config", fake_cfg)
        runner.make_client()
        assert created == {"api_key": "sk-test", "max_retries": 0, "timeout": runner.CLIENT_TIMEOUT_S}


# ── runner (fake async client only) ─────────────────────────────────────────

class _Resp:
    def __init__(self, content, finish="stop", model="gpt-4o-mini-2024-07-18"):
        msg = type("M", (), {"content": content})()
        self.choices = [type("C", (), {"message": msg, "finish_reason": finish})()]
        self.model = model
        self.usage = type("U", (), {"prompt_tokens": 2100, "completion_tokens": 20, "total_tokens": 2120})()


class FakeClient:
    def __init__(self, *items):
        self.items, self.calls = list(items), []
        self.chat = type("Chat", (), {"completions": self})()

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.items[len(self.calls) - 1] if len(self.calls) <= len(self.items) else self.items[-1]
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, _Resp) else _Resp(item)


JD = "Requirements\n- Minimum 4 years of experience as a Maintenance Planner in the cement industry."


class TestRunner:
    def test_success(self):
        client = FakeClient(qc("identified", "cement industry"))
        r = run(runner.run_qualifying_context(JD, client=client))
        assert r.ok and r.error is None and r.status == "ok"
        assert r.qualifying_context.to_dict() == {"state": "identified", "contexts": ["cement industry"],
                                                  "source": "analysis"}
        assert (r.prompt_version, r.prompt_sha256, r.model, r.temperature, r.max_tokens) == (
            "candidate_qc-1", FROZEN_SHA, "gpt-4o-mini", 0.2, 200)
        assert r.jd_sha256 == hashlib.sha256(JD.encode()).hexdigest()
        assert r.usage == {"prompt_tokens": 2100, "completion_tokens": 20, "total_tokens": 2120}
        assert len(client.calls) == 1 and client.calls[0] == runner.build_request(JD)

    @pytest.mark.parametrize("content,err", [
        (None, "no_response"), ("", "invalid_json"), ("{oops", "invalid_json"),
        ('```json\n{"state": "none", "contexts": [], "source": "analysis"}\n```', "invalid_json"),
        ("[]", "not_an_object"),
        (json.dumps({"state": "none", "contexts": []}), "bad_keys"),
        (json.dumps({"state": "none", "contexts": [], "source": "analysis", "note": "x"}), "bad_keys"),
        (json.dumps({"experience": {"qualifying_context": {"state": "none", "contexts": [],
                                                           "source": "analysis"}}}), "bad_keys"),
        (qc("partial"), "bad_state"), (qc("identified", "cement industry", source="recruiter"), "bad_source"),
        (json.dumps({"state": "identified", "contexts": "cement industry", "source": "analysis"}), "bad_contexts"),
        (json.dumps({"state": "identified", "contexts": [7], "source": "analysis"}), "bad_contexts"),
        (qc("identified", "  "), "bad_contexts"),
        (qc("identified", "cement industry", "Cement  Industry"), "duplicate_contexts"),
        (qc("identified"), "inconsistent_state"), (qc("none", "cement industry"), "inconsistent_state"),
        (qc("identified", "cement sector"), "ungrounded_context"),
        (qc("uncertain", "concrete plants"), "ungrounded_context"),
    ])
    def test_every_failure_returns_no_object(self, content, err):
        client = FakeClient(content)
        r = run(runner.run_qualifying_context(JD, client=client))
        assert r.status == "failed_validation" and r.error == err and r.qualifying_context is None
        assert r.raw == content and len(client.calls) == 1        # no repair call

    def test_technical_failure_is_explicit_with_no_retry_or_fallback(self):
        client = FakeClient(RuntimeError("upstream 503"), qc("none"))
        r = run(runner.run_qualifying_context(JD, client=client))
        assert r.status == "failed_technical" and r.qualifying_context is None
        assert r.error == "RuntimeError: upstream 503" and r.raw is None
        assert len(client.calls) == 1 and client.calls[0]["model"] == "gpt-4o-mini"

    def test_client_construction_failure_is_technical(self, monkeypatch):
        monkeypatch.setattr(runner, "make_client", lambda: (_ for _ in ()).throw(RuntimeError("no key")))
        r = run(runner.run_qualifying_context(JD))
        assert r.status == "failed_technical" and r.qualifying_context is None and "no key" in r.error

    def test_truncated_output_is_never_trusted(self):
        r = run(runner.run_qualifying_context(JD, client=FakeClient(_Resp(qc("none"), finish="length"))))
        assert r.status == "failed_validation" and r.error == "output_truncated" and r.qualifying_context is None

    def test_ungrounded_contexts_are_reported(self):
        r = run(runner.run_qualifying_context(JD, client=FakeClient(qc("identified", "cement sector"))))
        assert r.ungrounded == ("cement sector",)

    def test_result_invariants(self):
        good = schema.QualifyingContext("none", (), "analysis")
        base = dict(prompt_version="v", prompt_sha256="s", model="m", temperature=0.2, max_tokens=200,
                    jd_sha256="j")
        with pytest.raises(ValueError):
            schema.QCRunResult(status="failed_validation", qualifying_context=good, error="x", **base)
        with pytest.raises(ValueError):
            schema.QCRunResult(status="ok", qualifying_context=None, error=None, **base)
        with pytest.raises(ValueError):
            schema.QCRunResult(status="failed_technical", qualifying_context=None, error=None, **base)
        for bad in (("identified", ()), ("none", ("x",)), ("maybe", ())):
            with pytest.raises(ValueError):
                schema.QualifyingContext(bad[0], bad[1], "analysis")

    def test_audit_record(self):
        r = run(runner.run_qualifying_context(JD, client=FakeClient(qc("none"))))
        a = r.to_audit()
        assert a["status"] == "ok" and a["prompt_sha256"] == FROZEN_SHA and a["model"] == "gpt-4o-mini"


# ── parity with the frozen evaluator ────────────────────────────────────────

def harness_verdict(case, raw):
    """(accepted, parse_error, consistency_ok, grounding_ok, ungrounded, parsed) per the frozen evaluator."""
    parsed, err = qe.parse_qc(raw)
    chk = qe.check(case, parsed)
    return {"accepted": err is None and chk["consistency_ok"] and chk["grounding_ok"], "parse_error": err,
            "consistency_ok": chk["consistency_ok"], "grounding_ok": chk["grounding_ok"],
            "ungrounded": tuple(chk.get("ungrounded", ())), "parsed": parsed}


def mutations(case):
    """Per-case answers covering every state, grounding failure and malformed structure."""
    o = case["oracle"]
    ctx = list(o["contexts"])
    first = ctx[0] if ctx else None
    out = {"oracle": json.dumps(o, ensure_ascii=False),
           "identified_empty": qc("identified"), "none_empty": qc("none"), "uncertain_empty": qc("uncertain"),
           "bad_state": qc("partial"), "bad_source": json.dumps({**o, "source": "recruiter"}, ensure_ascii=False),
           "extra_key": json.dumps({**o, "why": "x"}, ensure_ascii=False),
           "missing_key": json.dumps({"state": o["state"], "contexts": ctx}, ensure_ascii=False),
           "contexts_not_list": json.dumps({**o, "contexts": "x"}, ensure_ascii=False),
           "non_string_context": json.dumps({**o, "contexts": [1]}, ensure_ascii=False),
           "empty_context": qc(o["state"], ""), "invalid_json": "{nope", "not_object": "[1, 2]",
           "fenced": "```json\n" + json.dumps(o, ensure_ascii=False) + "\n```", "none_response": None,
           "ungrounded": qc("identified", "zzqx unseen phrase"),
           "uncertain_ungrounded": qc("uncertain", "zzqx unseen phrase"),
           "wrapper": json.dumps({"experience": {"qualifying_context": o}}, ensure_ascii=False)}
    if first:
        out.update({"none_with_context": qc("none", first), "identified_first": qc("identified", first),
                    "uncertain_first": qc("uncertain", first),
                    "duplicate": qc("identified", first, first.upper() + " "),
                    "paraphrase_suffix": qc("identified", first + "s"),
                    "partial_word": qc("identified", first.split()[-1][:-1] or "q")})
    return out


PARITY = [(c, name, raw) for c in CASES for name, raw in mutations(c).items()]


class TestEvaluatorParity:
    @pytest.mark.parametrize("case,name,raw", PARITY, ids=[f"{c['id']}-{n}" for c, n, _ in PARITY])
    def test_same_verdict_as_frozen_evaluator(self, case, name, raw):
        h = harness_verdict(case, raw)
        qc_obj, err, ungrounded = validation.validate_response(raw, qe.case_jd(case))
        if name == "wrapper":
            # the single intentional difference: production accepts only the bare three-key object
            assert qc_obj is None and err == "bad_keys"
            return
        assert (qc_obj is not None) == h["accepted"], (name, err, h)
        if h["parse_error"] is not None:
            assert err == h["parse_error"]
        elif not h["consistency_ok"]:
            assert err == "inconsistent_state"
        elif not h["grounding_ok"]:
            assert err == "ungrounded_context" and ungrounded == h["ungrounded"]
        else:
            assert err is None and qc_obj.to_dict() == h["parsed"]

    def test_every_oracle_answer_is_accepted_identically(self):
        for c in CASES:
            raw = json.dumps(c["oracle"], ensure_ascii=False)
            qc_obj, err, _ = validation.validate_response(raw, qe.case_jd(c))
            assert err is None and qc_obj.to_dict() == qe.parse_qc(raw)[0] == c["oracle"], c["id"]

    def test_parity_covers_every_state_and_outcome(self):
        states, errors = set(), set()
        for c, name, raw in PARITY:
            q, e, _ = validation.validate_response(raw, qe.case_jd(c))
            states.add(q.state if q else None)
            errors.add(e)
        assert {"identified", "none", "uncertain"} <= states
        assert {"no_response", "invalid_json", "not_an_object", "bad_keys", "bad_state", "bad_source",
                "bad_contexts", "duplicate_contexts", "inconsistent_state", "ungrounded_context"} <= errors

    def test_divergence_is_only_the_wrapper_and_only_stricter(self):
        diverged = []
        for c, name, raw in PARITY:
            h = harness_verdict(c, raw)
            q, _, _ = validation.validate_response(raw, qe.case_jd(c))
            if (q is not None) != h["accepted"]:
                diverged.append((name, q is not None, h["accepted"]))
        assert diverged and {d[0] for d in diverged} == {"wrapper"}
        assert all(prod is False and harness is True for _, prod, harness in diverged)

    @pytest.mark.parametrize("actual,ok", [("بالقطاع الحكومي", True), ("القطاع الحكومي", True),
                                           ("قطاع حكومي", False), ("القطاع الحكومية", False)])
    def test_arabic_proclitic_grounding_parity(self, actual, ok):
        case = BY_ID["QC-F3"]
        raw = qc("identified", actual)
        q, err, _ = validation.validate_response(raw, qe.case_jd(case))
        assert (q is not None) is ok is harness_verdict(case, raw)["accepted"]

    def test_negative_answers_from_the_evaluator_tests(self):
        # the frozen evaluator's parse rejection table (test_qc_context_eval.TestParse), bare-object subset
        table = [(None, "no_response"), ("not json", "invalid_json"), ("[]", "not_an_object"),
                 (json.dumps({"state": "none", "contexts": []}), "bad_keys"),
                 (json.dumps({"state": "none", "contexts": [], "source": "analysis", "extra": 1}), "bad_keys"),
                 (json.dumps({"state": "partial", "contexts": [], "source": "analysis"}), "bad_state"),
                 (json.dumps({"state": None, "contexts": [], "source": "analysis"}), "bad_state"),
                 (json.dumps({"state": "identified", "contexts": ["x"], "source": "recruiter"}), "bad_source"),
                 (json.dumps({"state": "identified", "contexts": "banking sector", "source": "analysis"}),
                  "bad_contexts"),
                 (json.dumps({"state": "identified", "contexts": [""], "source": "analysis"}), "bad_contexts"),
                 (json.dumps({"state": "identified", "contexts": ["banking sector", "Banking  Sector"],
                              "source": "analysis"}), "duplicate_contexts")]
        for raw, err in table:
            assert validation.parse_response(raw) == (None, err) == qe.parse_qc(raw)

    def test_non_string_input_is_rejected(self):
        assert validation.parse_response({"state": "none", "contexts": [], "source": "analysis"}) == (
            None, "invalid_json")
