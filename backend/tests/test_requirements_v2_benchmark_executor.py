"""Requirements-v2 baseline benchmark EXECUTOR, tested entirely offline with a fake model client: call / token / cost / wall-clock
limits, every stop condition, no retries, partial results, and secret handling. No network, no key, no database."""
from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
import subprocess

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = BACKEND / "scripts"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(SCRIPTS))
    return mod


ex = _load("req_v2_run", "requirements_v2_extraction_run.py")
ev = ex.ev
CASES = ev.load_cases()
BY_JD = {c["jd"]: c for c in CASES}
FAKE_KEY = "sk-TESTKEY0123456789abcdefghijkl"


def _answer(request):
    """The reference (oracle) answer for whichever case this request carries."""
    user = request["messages"][-1]["content"]
    case = next(c for c in CASES if c["jd"] in user)
    return json.dumps(ev.reference_response(case), ensure_ascii=False)


class Fake:
    def __init__(self, *, model=None, completion=500, prompt=2900, finish="stop", script=None, raw=None):
        self.calls, self.model, self.completion, self.prompt, self.finish = [], model or ex.RUN_MODEL, completion, prompt, finish
        self.script, self.raw = script or {}, raw

    def __call__(self, request, timeout):
        n = len(self.calls) + 1
        self.calls.append((request, timeout))
        step = self.script.get(n)
        if isinstance(step, ex.ApiError):
            raise step
        step = step or {}
        return {"raw": step.get("raw", self.raw if self.raw is not None else _answer(request)), "finish_reason": step.get("finish", self.finish),
                "model": step.get("model", self.model), "usage": {"prompt_tokens": step.get("prompt", self.prompt), "completion_tokens": step.get("completion", self.completion)}}


def _count(messages):
    return 3000      # fixed "exact" input count for the tests


def _run(tmp_path, fake, *, plan=None, clock=None):
    p = dict(ev.PLAN, **(plan or {}))
    kw = {"clock": clock} if clock else {}
    summary = ex.execute(CASES, fake, tmp_path / "out", count_fn=_count, plan=p, **kw)
    lines = [json.loads(x) for x in (tmp_path / "out" / "calls.jsonl").read_text(encoding="utf-8").splitlines()] if (tmp_path / "out" / "calls.jsonl").exists() else []
    return summary, lines


# ── the happy path ────────────────────────────────────────────────────────────────────────────────────────────────
def test_full_run_makes_exactly_24_calls_in_run_then_case_order_with_the_approved_settings(tmp_path):
    fake = Fake()
    summary, lines = _run(tmp_path, fake)
    assert summary["calls_made"] == 24 and summary["stopped_by"] is None and len(fake.calls) == 24
    assert [(l["run"], l["case"]) for l in lines] == [(r, c["id"]) for r in ("run1", "run2") for c in CASES]
    for req, timeout in fake.calls:
        assert req["model"] == "gpt-4o-mini-2024-07-18" and req["temperature"] == 0.1 and req["max_tokens"] == 6000
        assert req["response_format"] == {"type": "json_object"} and timeout == 90
    assert all(l["requested_model"] == l["returned_model"] == "gpt-4o-mini-2024-07-18" and l["usage"] and l["latency_s"] is not None for l in lines)
    assert summary["actual_tokens"] == 24 * 3400
    assert summary["actual_cost_usd"] == pytest.approx(24 * (2900 * 0.15e-6 + 500 * 0.60e-6), abs=1e-6)


def test_scoring_a_complete_run_reports_every_gate_by_language_and_writes_results(tmp_path):
    _run(tmp_path, Fake())
    scored = ex.score(CASES, tmp_path / "out")
    assert len(scored["gates"]) == 12 and all(v is True for v in scored["gates"].values())
    assert set(scored["by_language"]) == {"en", "ar"} and (tmp_path / "out" / "results.json").exists()


# ── limits ────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_call_cap_is_never_exceeded(tmp_path):
    fake = Fake()
    summary, lines = _run(tmp_path, fake, plan={"max_calls": 5})
    assert len(fake.calls) == 5 == len(lines) and summary["stopped_by"] == "max_calls"


def test_token_reserve_is_checked_before_each_call_so_the_cap_cannot_be_passed(tmp_path):
    cap = 40_000                                     # reserve per call = 3000 + 6000 = 9000
    fake = Fake(completion=6000)                    # every response uses the full allowance
    summary, lines = _run(tmp_path, fake, plan={"max_total_tokens": cap})
    assert summary["stopped_by"] == "token_budget_reserve" and summary["actual_tokens"] <= cap
    assert len(fake.calls) == 4                      # 4 x 9000 = 36000 spent; a 5th would need 36000 + 9000 > 40000
    assert all(l["reserved_tokens"] == 9000 for l in lines)


def test_cost_reserve_stops_before_the_cost_cap(tmp_path):
    fake = Fake(completion=6000)
    per_call = 3000 * 0.15e-6 + 6000 * 0.60e-6
    summary, _ = _run(tmp_path, fake, plan={"max_cost_usd": per_call * 2.5})
    assert summary["stopped_by"] == "cost_budget_reserve" and len(fake.calls) == 2 and summary["actual_cost_usd"] <= per_call * 2.5


def test_wall_clock_limit_stops_the_run(tmp_path):
    t = {"now": 0.0}

    class Clock:
        def __call__(self):
            return t["now"]
    fake = Fake()
    orig = fake.__call__

    def slow(req, timeout):
        t["now"] += 100.0
        return orig(req, timeout)
    summary, _ = _run(tmp_path, slow, plan={"wall_clock_limit_s": 1800}, clock=Clock())
    assert summary["stopped_by"] == "wall_clock" and summary["calls_made"] == 18     # a call starts only if elapsed + the 90 s timeout fits: 17 x 100 + 90 <= 1800 < 18 x 100 + 90
    assert summary["wall_clock_s"] <= 1800


# ── stop conditions ──────────────────────────────────────────────────────────────────────────────────────────────
def test_a_returned_snapshot_other_than_the_requested_one_stops_after_that_call(tmp_path):
    fake = Fake(script={3: {"model": "gpt-4o-mini-2025-01-01"}})
    summary, lines = _run(tmp_path, fake)
    assert summary["stopped_by"] == "snapshot_mismatch" and len(fake.calls) == 3 and lines[-1]["returned_model"] == "gpt-4o-mini-2025-01-01"


def test_an_auth_error_stops_immediately_without_retry(tmp_path):
    fake = Fake(script={1: ex.ApiError("auth", 401)})
    summary, lines = _run(tmp_path, fake)
    assert summary["stopped_by"] == "auth_or_model_error" and len(fake.calls) == 1 and lines[0]["error"] == {"kind": "auth", "status": 401}


def test_two_consecutive_api_errors_stop_but_one_error_does_not_and_nothing_is_retried(tmp_path):
    fake = Fake(script={2: ex.ApiError("rate_limit", 429), 3: ex.ApiError("timeout")})
    summary, lines = _run(tmp_path, fake)
    assert summary["stopped_by"] == "two_consecutive_api_errors" and len(fake.calls) == 3
    assert [l["error"] is not None for l in lines] == [False, True, True]            # the failed case was not re-issued
    fake = Fake(script={2: ex.ApiError("http_error", 500)})
    summary, lines = _run(tmp_path / "b", fake)
    assert summary["stopped_by"] is None and len(fake.calls) == 24                   # one error: counted as a call, run continues


def test_finish_reason_length_once_continues_twice_stops(tmp_path):
    fake = Fake(script={2: {"finish": "length"}})
    summary, _ = _run(tmp_path, fake)
    assert summary["stopped_by"] is None and len(fake.calls) == 24
    fake = Fake(script={2: {"finish": "length"}, 5: {"finish": "length"}})
    summary, _ = _run(tmp_path / "b", fake)
    assert summary["stopped_by"] == "second_finish_reason_length" and len(fake.calls) == 5


@pytest.mark.parametrize("leaked", ["prompt", "key"])
def test_leaked_prompt_text_or_key_pattern_stops_and_quarantines_the_raw_text(tmp_path, leaked):
    from services.requirements_v2.extraction.prompt import load_prompt
    line = next(ln.strip() for ln in load_prompt().splitlines() if len(ln.strip()) >= 60)
    raw = f'{{"x": "{line}"}}' if leaked == "prompt" else '{"x": "' + FAKE_KEY + '"}'
    fake = Fake(script={2: {"raw": raw}})
    summary, lines = _run(tmp_path, fake)
    assert summary["stopped_by"].startswith("quarantine_") and len(fake.calls) == 2
    blob = (tmp_path / "out" / "calls.jsonl").read_text(encoding="utf-8")
    assert FAKE_KEY not in blob and line not in lines[-1]["raw"] and lines[-1]["quarantined"]
    assert len(ex.load_answers(tmp_path / "out")["run1"]) == 1                      # the quarantined answer is not scored


def test_a_stop_file_ends_the_run_before_the_next_call(tmp_path):
    out = tmp_path / "out"; out.mkdir()
    (out / "STOP").write_text("")
    fake = Fake()
    summary, _ = _run(tmp_path, fake)
    assert summary["stopped_by"] == "manual_stop" and fake.calls == []


# ── partial results ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_stopped_run_keeps_every_finished_call_and_can_still_be_scored(tmp_path):
    fake = Fake(script={4: ex.ApiError("auth", 403)})
    summary, lines = _run(tmp_path, fake)
    assert summary["calls_made"] == 4 and len(lines) == 4 and (tmp_path / "out" / "run.json").exists()
    scored = ex.score(CASES, tmp_path / "out")
    assert scored["gates"][next(k for k in scored["gates"] if k.startswith("G3"))] is False        # 3 of 24 parsed
    assert scored["runs"]["run1"][3]["parse_status"] == "missing"


def test_a_call_that_used_more_input_than_estimated_is_reported(tmp_path):
    fake = Fake(script={1: {"prompt": 3500}})
    summary, lines = _run(tmp_path, fake)
    assert lines[0]["input_over_estimate"] is True and any("exceeded the reserved estimate" in n for n in summary["notes"])


# ── secrets and the real adapter ────────────────────────────────────────────────────────────────────────────────
def _fake_openai_module():
    """A stand-in for the openai SDK with its real exception hierarchy (other tests stub the module, so the real one is not relied on)."""
    import types
    m = types.ModuleType("openai")

    class APIError(Exception):
        pass

    class APIConnectionError(APIError):
        pass

    class APITimeoutError(APIConnectionError):
        pass

    class APIStatusError(APIError):
        def __init__(self, message, status_code=500):
            super().__init__(message)
            self.status_code = status_code

    for name, status in (("AuthenticationError", 401), ("PermissionDeniedError", 403), ("NotFoundError", 404), ("RateLimitError", 429)):
        setattr(m, name, type(name, (APIStatusError,), {"__init__": lambda self, message, _s=status: APIStatusError.__init__(self, message, _s)}))
    m.APIError, m.APIConnectionError, m.APITimeoutError, m.APIStatusError = APIError, APIConnectionError, APITimeoutError, APIStatusError

    class OpenAI:
        exc = None

        def __init__(self, **kw):
            assert kw["max_retries"] == 0 and kw["timeout"] == 90 and kw["api_key"] == FAKE_KEY
            outer = OpenAI
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=lambda **r: (_ for _ in ()).throw(outer.exc)))
    m.OpenAI = OpenAI
    return m


def test_adapter_maps_provider_errors_to_kind_and_status_and_never_keeps_the_message(monkeypatch):
    import sys
    fake = _fake_openai_module()
    monkeypatch.setitem(sys.modules, "openai", fake)
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    msg = f"Incorrect API key provided: {FAKE_KEY[:12]}***"
    cases = ((fake.AuthenticationError(msg), "auth", 401), (fake.PermissionDeniedError(msg), "auth", 403),
             (fake.NotFoundError(msg), "model_unavailable", 404), (fake.RateLimitError(msg), "rate_limit", 429),
             (fake.APITimeoutError(msg), "timeout", None), (fake.APIConnectionError(msg), "transport", None),
             (fake.APIStatusError(msg, 503), "http_error", 503))
    for exc, kind, status in cases:
        fake.OpenAI.exc = exc
        with pytest.raises(ex.ApiError) as e:
            ex.openai_call({"model": "m"}, 90)
        assert (e.value.kind, e.value.status) == (kind, status)
        assert FAKE_KEY[:12] not in repr(e.value.args) and e.value.__suppress_context__ is True and e.value.__cause__ is None


def test_main_blocks_without_a_key_and_makes_no_call(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(ex, "openai_call", lambda *a, **k: pytest.fail("a call was made"))
    assert ex.main(["--run", "--out", str(tmp_path / "o")]) == 3
    assert "OPENAI_API_KEY is not set" in capsys.readouterr().out


def test_main_never_prints_or_stores_the_key_and_refuses_a_non_empty_output_dir(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    fake = Fake()
    monkeypatch.setattr(ex, "openai_call", fake)
    out = tmp_path / "o"
    assert ex.main(["--run", "--out", str(out)]) == 0 and len(fake.calls) == 24
    printed = capsys.readouterr()
    assert FAKE_KEY not in printed.out + printed.err
    for f in out.rglob("*"):
        if f.is_file():
            assert FAKE_KEY not in f.read_text(encoding="utf-8"), f
    assert ex.main(["--run", "--out", str(out)]) == 4 and len(fake.calls) == 24        # no re-run into the same directory


def test_executor_imports_no_network_client_at_module_level():
    tree = ast.parse((SCRIPTS / "requirements_v2_extraction_run.py").read_text(encoding="utf-8"))
    top = {n.names[0].name.split(".")[0] for n in tree.body if isinstance(n, ast.Import)} | {n.module.split(".")[0] for n in tree.body if isinstance(n, ast.ImportFrom) and n.module}
    assert not top & {"openai", "httpx", "requests", "aiohttp", "sqlalchemy", "database"}


# ── frozen-input verification ──────────────────────────────────────────────────────────────────────────────────────
def test_frozen_verification_detects_a_changed_added_or_uncommitted_file(tmp_path, monkeypatch):
    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
    (tmp_path / "backend").mkdir(); (tmp_path / "backend" / "f.txt").write_text("a")
    git("add", "."); git("commit", "-qm", "freeze")
    sha = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    monkeypatch.setattr(ex, "REPO", tmp_path); monkeypatch.setattr(ex, "FROZEN_COMMIT", sha); monkeypatch.setattr(ex, "FROZEN_PATHS", ("backend",))
    assert ex.verify_frozen() == []
    (tmp_path / "backend" / "f.txt").write_text("b")
    assert any("differs" in p for p in ex.verify_frozen()) and any("uncommitted" in p for p in ex.verify_frozen())
    git("commit", "-qam", "tune")
    assert any("differs" in p for p in ex.verify_frozen())
    (tmp_path / "backend" / "g.txt").write_text("c"); git("add", "."); git("commit", "-qm", "add")
    assert any("added since freeze" in p for p in ex.verify_frozen())


def test_preflight_on_this_tree_passes_when_the_frozen_commit_is_present():
    try:
        subprocess.run(["git", "-C", str(ex.REPO), "cat-file", "-e", ex.FROZEN_COMMIT], check=True, capture_output=True)
    except Exception:
        pytest.skip("frozen commit not in this checkout")
    pf = ex.preflight_offline(CASES)
    assert pf["ok"], pf["problems"]
    assert pf["prompt_sha256"] == "f2017d2879554935baa5d0d4f51d53fa0600e18858cc875d4a791c5ac0678b04"
