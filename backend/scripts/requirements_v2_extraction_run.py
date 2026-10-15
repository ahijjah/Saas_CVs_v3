"""
requirements-v2 extraction baseline benchmark: EXECUTOR (plan version 2, frozen at commit 059c56b).

Runs the 12 synthetic cases x 2 runs against gpt-4o-mini-2024-07-18 with hard limits, then scores the raw answers with the
frozen offline scorer. It never touches the database, production settings or any repository file other than reading frozen
inputs. The only network host it contacts is api.openai.com, and only after every offline preflight check has passed.

  python scripts/requirements_v2_extraction_run.py --preflight                    # baseline prompt v2-1: offline checks, no key, no network
  python scripts/requirements_v2_extraction_run.py --preflight --prompt v2-2      # candidate comparison run: offline checks, token reservations
  python scripts/requirements_v2_extraction_run.py --preflight --prompt v2-3      # candidate v2-3: offline checks, token reservations
  python scripts/requirements_v2_extraction_run.py --run --out DIR [--prompt v2-2|v2-3]   # needs OPENAI_API_KEY in the environment

--prompt selects the system prompt: "baseline" (the default, criteria_extraction_v2-1, unchanged behavior), "v2-2" (an offline
candidate, verified byte-for-byte against commit 916d1058 and its manifest hash) or "v2-3" (an offline candidate, verified against
commit 7d5c671, its manifest and SHA-256 21a2f942...; a v2-3 preflight also re-verifies v2-2). Everything else (model, settings,
limits, stop conditions, scorer) is identical. Every call record and the run metadata carry the selected prompt version and hash.

Secrets: the key is read from the environment only. It is never printed, logged or written; API error messages (which can echo
a key fragment) are never stored, only the error kind and HTTP status.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import requirements_v2_extraction_eval as ev  # noqa: E402  (the frozen scorer)
from services.requirements_v2.extraction.prompt import (  # noqa: E402
    EXTRACTION_CONFIG, PROMPT_SHA256, PROMPT_VERSION, build_request, build_user_message, load_prompt,
)

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
FROZEN_COMMIT = "059c56b"
FROZEN_PATHS = ("backend/services/requirements_v2", "backend/tests/fixtures/requirements_v2_benchmark",
                "backend/scripts/requirements_v2_extraction_eval.py")
RUN_MODEL = "gpt-4o-mini-2024-07-18"                 # requested explicitly (the approved configuration)
PRICE_IN, PRICE_OUT = 0.15e-6, 0.60e-6               # USD/token, gpt-4o-mini list price (verified 2026-10-09 on OpenAI's model page; cached input 0.075/M is ignored = upper bound)
INPUT_MARGIN_EXACT = 64                              # tokens added to an exact tiktoken count (chat framing differences)
KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_\-]{16,}")

CANDIDATE_COMMIT = "916d1058"
CANDIDATE_DIR_REL = "backend/prompt_candidates/criteria_extraction_v2-2"
CANDIDATE_DIR = BACKEND / "prompt_candidates" / "criteria_extraction_v2-2"
CANDIDATE_VERSION = "criteria_extraction_v2-2"
CANDIDATE_V22_SHA256 = "40ea678b65a5782da3f74f1c0b52f4dbeb10cc369f78efd25f1e38827ff48dda"   # pinned here AND in the manifest
BASELINE_V21_SHA256 = "f2017d2879554935baa5d0d4f51d53fa0600e18858cc875d4a791c5ac0678b04"

CANDIDATE_V23_COMMIT = "7d5c671"
CANDIDATE_V23_DIR_REL = "backend/prompt_candidates/criteria_extraction_v2-3"
CANDIDATE_V23_DIR = BACKEND / "prompt_candidates" / "criteria_extraction_v2-3"
CANDIDATE_V23_VERSION = "criteria_extraction_v2-3"
CANDIDATE_V23_SHA256 = "21a2f9420c12e1a43579c6817b6a8601547d92b653cd432a693ef590b784b43b"   # pinned here AND in the manifest; base = v2-2
BASELINE_DATA = BACKEND / "benchmark_results" / "requirements_v2" / "baseline_v2-1"


class CandidateIntegrityError(RuntimeError):
    """The candidate prompt is not the one approved at CANDIDATE_COMMIT."""


class PromptSpec:
    """The system prompt a run uses. `build` produces the full chat-completion arguments (model, settings and user message are
    identical for every spec); `text` returns the verified system prompt."""
    __slots__ = ("key", "version", "sha256", "source", "text", "commit")

    def __init__(self, key: str, version: str, sha256: str, source: str, text, commit: str | None = None):
        self.key, self.version, self.sha256, self.source, self.text = key, version, sha256, source, text   # text: () -> str, verifies the hash
        self.commit = commit                                                                              # the commit the candidate is verified against (None: baseline)

    def build(self, jd_text: str, job_metadata) -> dict:
        if self.key == "baseline":                 # the unchanged baseline path
            return build_request(jd_text, job_metadata, model=RUN_MODEL)
        return {"model": RUN_MODEL,
                "messages": [{"role": "system", "content": self.text()}, {"role": "user", "content": build_user_message(jd_text, job_metadata)}],
                "temperature": EXTRACTION_CONFIG["temperature"], "max_tokens": EXTRACTION_CONFIG["max_tokens"],
                "response_format": dict(EXTRACTION_CONFIG["response_format"])}

    def leak_lines(self) -> list[str]:
        body = self.text().split("ADDITIONAL EXAMPLES")[0]
        return [ln.strip() for ln in body.splitlines() if len(ln.strip()) >= 60][:40]

    def meta(self) -> dict:
        return {"prompt_key": self.key, "prompt_version": self.version, "prompt_sha256": self.sha256, "prompt_source": self.source}


def load_candidate_v22() -> str:
    raw = (CANDIDATE_DIR / "criteria_extraction_v2-2.txt").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != CANDIDATE_V22_SHA256:
        raise CandidateIntegrityError(f"candidate sha256 {digest} != pinned {CANDIDATE_V22_SHA256}")
    manifest = json.loads((CANDIDATE_DIR / "MANIFEST.json").read_text(encoding="utf-8"))
    if (manifest.get("sha256"), manifest.get("version"), manifest.get("base_sha256")) != (digest, CANDIDATE_VERSION, BASELINE_V21_SHA256):
        raise CandidateIntegrityError("candidate manifest does not match the file, the version or the v2-1 base hash")
    return raw.decode("utf-8")


def load_candidate_v23() -> str:
    raw = (CANDIDATE_V23_DIR / "criteria_extraction_v2-3.txt").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != CANDIDATE_V23_SHA256:
        raise CandidateIntegrityError(f"candidate sha256 {digest} != pinned {CANDIDATE_V23_SHA256}")
    manifest = json.loads((CANDIDATE_V23_DIR / "MANIFEST.json").read_text(encoding="utf-8"))
    if (manifest.get("sha256"), manifest.get("version"), manifest.get("base_sha256"), manifest.get("frozen_baseline_commit")) != (
            digest, CANDIDATE_V23_VERSION, CANDIDATE_V22_SHA256, FROZEN_COMMIT):
        raise CandidateIntegrityError("candidate manifest does not match the file, the version, the v2-2 base hash or the frozen commit")
    return raw.decode("utf-8")


BASELINE_SPEC = PromptSpec("baseline", PROMPT_VERSION, PROMPT_SHA256, "services/requirements_v2/extraction/prompts (pinned, frozen at 059c56b)", load_prompt)
CANDIDATE_SPEC = PromptSpec("v2-2", CANDIDATE_VERSION, CANDIDATE_V22_SHA256, f"{CANDIDATE_DIR_REL} @ {CANDIDATE_COMMIT}", load_candidate_v22, CANDIDATE_COMMIT)
CANDIDATE_V23_SPEC = PromptSpec("v2-3", CANDIDATE_V23_VERSION, CANDIDATE_V23_SHA256, f"{CANDIDATE_V23_DIR_REL} @ {CANDIDATE_V23_COMMIT}", load_candidate_v23, CANDIDATE_V23_COMMIT)
SPECS = {"baseline": BASELINE_SPEC, "v2-2": CANDIDATE_SPEC, "v2-3": CANDIDATE_V23_SPEC}


class ApiError(Exception):
    """Carries only a kind and an HTTP status, never the provider's message."""
    def __init__(self, kind: str, status: int | None = None):
        super().__init__(kind)
        self.kind, self.status = kind, status


# ── input token counting ────────────────────────────────────────────────────────────────────────────────────────
def make_counter():
    """(count_fn, method). Exact tiktoken o200k_base count when the package and its encoding are available, else the frozen
    heuristic x the plan's 1.3 safety factor (so a missing tokenizer only makes the reserve more conservative)."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("o200k_base")
        def exact(messages):
            return sum(3 + len(enc.encode(m["content"])) + len(enc.encode(m["role"])) for m in messages) + 3 + INPUT_MARGIN_EXACT
        exact([{"role": "user", "content": "x"}])
        return exact, "tiktoken_o200k_base"
    except Exception:
        def heuristic(messages):
            return math.ceil((sum(ev.estimate_tokens(m["content"]) for m in messages) + 12) * ev.PLAN["input_safety_factor"])
        return heuristic, "heuristic_x1.3"


# ── frozen-input verification (offline) ─────────────────────────────────────────────────────────────────────────
def _git(*args: str) -> bytes:
    return subprocess.run(["git", "-C", str(REPO), *args], check=True, capture_output=True).stdout


def verify_frozen() -> list[str]:
    """Problems found (empty = the working tree's frozen files are byte-identical to FROZEN_COMMIT)."""
    problems = []
    try:
        _git("merge-base", "--is-ancestor", FROZEN_COMMIT, "HEAD")
    except Exception:
        return [f"{FROZEN_COMMIT} is not an ancestor of HEAD"]
    tracked = _git("ls-tree", "-r", "--name-only", FROZEN_COMMIT, "--", *FROZEN_PATHS).decode().split()
    now = {p for p in _git("ls-files", "--", *FROZEN_PATHS).decode().split()}
    problems += [f"added since freeze: {p}" for p in sorted(now - set(tracked)) if not p.endswith(".pyc")]
    for p in tracked:
        f = REPO / p
        if not f.exists():
            problems.append(f"missing: {p}")
        elif f.read_bytes() != _git("show", f"{FROZEN_COMMIT}:{p}"):
            problems.append(f"differs from {FROZEN_COMMIT}: {p}")
    if _git("status", "--porcelain", "--", *FROZEN_PATHS).strip():
        problems.append("uncommitted changes in frozen paths")
    return problems


def _verify_candidate_dir(commit: str, dir_rel: str, loader) -> list[str]:
    """Problems found (empty = the candidate directory is byte-identical to `commit`, hashes and manifest verified by `loader`)."""
    problems = []
    try:
        _git("cat-file", "-e", commit)
        _git("merge-base", "--is-ancestor", commit, "HEAD")
    except Exception:
        return [f"{commit} is missing or not an ancestor of HEAD"]
    tracked = _git("ls-tree", "-r", "--name-only", commit, "--", dir_rel).decode().split()
    now = set(_git("ls-files", "--", dir_rel).decode().split())
    problems += [f"added to the candidate since {commit}: {p}" for p in sorted(now - set(tracked))]
    if not tracked:
        problems.append("the candidate directory does not exist at the candidate commit")
    for p in tracked:
        f = REPO / p
        if not f.exists():
            problems.append(f"missing: {p}")
        elif f.read_bytes() != _git("show", f"{commit}:{p}"):
            problems.append(f"differs from {commit}: {p}")
    if _git("status", "--porcelain", "--", dir_rel).strip():
        problems.append("uncommitted changes in the candidate directory")
    try:
        loader()
    except Exception as exc:
        problems.append(f"candidate integrity: {type(exc).__name__}: {exc}")
    return problems


def verify_candidate() -> list[str]:
    """The v2-2 candidate versus CANDIDATE_COMMIT (module globals are read at call time)."""
    return _verify_candidate_dir(CANDIDATE_COMMIT, CANDIDATE_DIR_REL, load_candidate_v22)


def verify_candidate_v23() -> list[str]:
    """The v2-3 candidate versus CANDIDATE_V23_COMMIT, its manifest and SHA-256."""
    return _verify_candidate_dir(CANDIDATE_V23_COMMIT, CANDIDATE_V23_DIR_REL, load_candidate_v23)


def preflight_offline(cases: list[dict], spec: PromptSpec = BASELINE_SPEC) -> dict:
    count, method = make_counter()
    problems = verify_frozen()
    if spec.key != "baseline":
        problems += verify_candidate()                 # v2-2 stays verified for every candidate run (it is the comparison base)
    if spec.key == "v2-3":
        problems += verify_candidate_v23()
    if PROMPT_SHA256 != ev.PLAN["prompt_sha256"] or ev.PLAN["prompt_sha256"] != "f2017d2879554935baa5d0d4f51d53fa0600e18858cc875d4a791c5ac0678b04":
        problems.append("prompt hash differs from the plan")
    try:
        load_prompt()                                  # raises if the file does not hash to the pin
    except Exception as exc:
        problems.append(f"prompt integrity: {type(exc).__name__}")
    if len(cases) != 12 or sorted(c["language"] for c in cases) != ["ar"] * 6 + ["en"] * 6:
        problems.append("cases are not 6 EN + 6 AR")
    if ev.PLAN["max_calls"] != 24 or ev.PLAN["max_total_tokens"] != 200_000 or ev.PLAN["max_cost_usd"] != 0.25 or ev.PLAN["retries"] != 0:
        problems.append("plan limits differ from the approved configuration")
    reserve = {}
    for c in cases:
        req = spec.build(c["jd"], c["job_metadata"])
        if (req["model"], req["temperature"], req["max_tokens"], req["response_format"]) != (RUN_MODEL, 0.1, 6000, {"type": "json_object"}):
            problems.append(f"request settings differ for {c['id']}")
        reserve[c["id"]] = count(req["messages"]) + ev.PLAN["max_completion_tokens_per_call"]
    return {"ok": not problems, "problems": problems, "token_counting": method, "model": RUN_MODEL, "prompt_sha256": spec.sha256,
            **spec.meta(), "frozen_commit": FROZEN_COMMIT, "candidate_commit": spec.commit,
            "reserve_per_call_tokens": reserve, "worst_case_reserved_total": sum(reserve.values()) * ev.PLAN["runs_per_case"]}


# ── the real API adapter (the only place that touches the network) ─────────────────────────────────────────────────
def openai_call(request: dict, timeout: float) -> dict:
    import openai
    key = os.environ.get("OPENAI_API_KEY")
    client = openai.OpenAI(api_key=key, max_retries=0, timeout=timeout)
    try:
        r = client.chat.completions.create(**request)
    except openai.AuthenticationError:
        raise ApiError("auth", 401) from None
    except openai.PermissionDeniedError:
        raise ApiError("auth", 403) from None
    except openai.NotFoundError:
        raise ApiError("model_unavailable", 404) from None
    except openai.RateLimitError:
        raise ApiError("rate_limit", 429) from None
    except openai.APITimeoutError:
        raise ApiError("timeout") from None
    except openai.APIConnectionError:
        raise ApiError("transport") from None
    except openai.APIStatusError as exc:
        raise ApiError("http_error", exc.status_code) from None
    ch = r.choices[0]
    return {"raw": ch.message.content or "", "finish_reason": ch.finish_reason, "model": r.model,
            "usage": {"prompt_tokens": r.usage.prompt_tokens, "completion_tokens": r.usage.completion_tokens}}


# ── the run ────────────────────────────────────────────────────────────────────────────────────────────────────
def gate(made: int, tokens: int, cost: float, elapsed: float, est_in: int, plan: dict, *, stop_file: bool = False) -> str | None:
    """May the next call be issued? The one place the pre-call stop conditions live (used by the run and by the simulation):
    manual stop, call cap, then input + the maximum output reserved against the token and cost caps, then the wall clock."""
    reserve_t = est_in + plan["max_completion_tokens_per_call"]
    reserve_c = est_in * PRICE_IN + plan["max_completion_tokens_per_call"] * PRICE_OUT
    if stop_file:
        return "manual_stop"
    if made >= plan["max_calls"]:
        return "max_calls"
    if tokens + reserve_t > plan["max_total_tokens"]:
        return "token_budget_reserve"
    if cost + reserve_c > plan["max_cost_usd"]:
        return "cost_budget_reserve"
    if elapsed + plan["per_call_timeout_s"] > plan["wall_clock_limit_s"]:
        return "wall_clock"
    return None


def _strings(node) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [t for k, v in node.items() for t in _strings(k) + _strings(v)]
    if isinstance(node, list):
        return [t for v in node for t in _strings(v)]
    return []


def _leak(raw: str, prompt_lines: list[str]) -> str | None:
    """A key pattern, or a line of the system prompt, in the raw text or in any decoded JSON string (JSON escapes quotes, so a
    quoted prompt line would not match the raw text)."""
    if KEY_PATTERN.search(raw):
        return "api_key_pattern"
    texts = [raw]
    try:
        texts.append("\n".join(_strings(json.loads(raw))))
    except ValueError:
        pass
    return "system_prompt_text" if any(line in t for line in prompt_lines for t in texts) else None


def execute(cases: list[dict], call_fn, out_dir: Path, *, count_fn, clock=time.monotonic, plan: dict = ev.PLAN,
            spec: PromptSpec = BASELINE_SPEC) -> dict:
    """Issue the calls under every stop condition of the plan. Each call is appended to calls.jsonl the moment it finishes,
    so a stopped run keeps everything it did. Returns the run summary (also written to run.json)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / "calls.jsonl"
    prompt_lines = spec.leak_lines()
    run_meta = {**spec.meta(), "model": RUN_MODEL, "settings": {"temperature": EXTRACTION_CONFIG["temperature"], "max_tokens": EXTRACTION_CONFIG["max_tokens"],
                "response_format": dict(EXTRACTION_CONFIG["response_format"])}, "limits": {k: plan[k] for k in (
                    "max_calls", "max_total_tokens", "max_completion_tokens_per_call", "max_cost_usd", "wall_clock_limit_s", "per_call_timeout_s", "retries")},
                "plan_version": plan.get("plan_version"), "frozen_benchmark_commit": FROZEN_COMMIT,
                "candidate_commit": spec.commit, "eval_version": ev.EVAL_VERSION,
                "started_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    (out_dir / "meta.json").write_text(json.dumps(run_meta, indent=1), encoding="utf-8")     # written before the first call: a partial run keeps it
    t0 = clock()
    made = tokens = errors_in_row = length_hits = 0
    cost, stop, notes = 0.0, None, []
    order = [(f"run{r}", c) for r in range(1, plan["runs_per_case"] + 1) for c in cases]
    for run, case in order:
        req = spec.build(case["jd"], case["job_metadata"])
        est_in = count_fn(req["messages"])
        reserve_t = est_in + plan["max_completion_tokens_per_call"]
        stop = gate(made, tokens, cost, clock() - t0, est_in, plan, stop_file=(out_dir / "STOP").exists())
        if stop:
            break
        rec = {"case": case["id"], "run": run, "language": case["language"], **spec.meta(), "requested_model": req["model"], "settings": {
            "temperature": req["temperature"], "max_tokens": req["max_tokens"], "response_format": req["response_format"]},
            "est_input_tokens": est_in, "reserved_tokens": reserve_t, "started_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
        s = clock()
        try:
            res = call_fn(req, plan["per_call_timeout_s"])
            rec["latency_s"] = round(clock() - s, 3)
            made += 1
            errors_in_row = 0
            u = res["usage"]
            tokens += u["prompt_tokens"] + u["completion_tokens"]
            cost += u["prompt_tokens"] * PRICE_IN + u["completion_tokens"] * PRICE_OUT
            rec.update(returned_model=res["model"], finish_reason=res["finish_reason"], usage=u, raw=res["raw"], error=None,
                       input_over_estimate=u["prompt_tokens"] > est_in)
            if u["prompt_tokens"] > est_in:
                notes.append(f"{case['id']} {run}: actual input {u['prompt_tokens']} exceeded the reserved estimate {est_in}")
            leak = _leak(res["raw"], prompt_lines)
            if leak:
                rec["quarantined"] = leak
                rec["raw"] = "[quarantined: " + leak + "]"
                stop = "quarantine_" + leak
            elif res["model"] != RUN_MODEL:
                stop = "snapshot_mismatch"
            if res["finish_reason"] == "length":
                length_hits += 1
                if length_hits >= 2 and not stop:
                    stop = "second_finish_reason_length"
        except ApiError as exc:
            rec.update(latency_s=round(clock() - s, 3), error={"kind": exc.kind, "status": exc.status}, raw=None)
            made += 1                                   # the attempt counts against the call cap (no retries)
            errors_in_row += 1
            if exc.kind in ("auth", "model_unavailable"):
                stop = "auth_or_model_error"
            elif errors_in_row >= 2:
                stop = "two_consecutive_api_errors"
        with jsonl.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if stop:
            break
    summary = {**spec.meta(), "calls_made": made, "planned_calls": len(order), "stopped_by": stop, "actual_tokens": tokens,
               "actual_cost_usd": round(cost, 6), "wall_clock_s": round(clock() - t0, 2), "notes": notes,
               "finished_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    (out_dir / "run.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary


# ── scoring + report ───────────────────────────────────────────────────────────────────────────────────────────────
def load_answers(out_dir: Path) -> dict:
    runs: dict[str, dict] = {"run1": {}, "run2": {}}
    for line in (out_dir / "calls.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r.get("raw") and not r.get("quarantined"):
            runs[r["run"]][r["case"]] = {"raw": r["raw"], "finish_reason": r["finish_reason"]}
    return runs


def score(cases: list[dict], out_dir: Path) -> dict:
    runs = load_answers(out_dir)
    scored = ev.score_runs(cases, runs)
    scored["gates"] = ev.gates(scored)
    by_lang = {}
    for lang in ("en", "ar"):
        sub = [c for c in cases if c["language"] == lang]
        sr = ev.score_runs(sub, {r: {k: v for k, v in a.items() if k in {c["id"] for c in sub}} for r, a in runs.items()})
        by_lang[lang] = {"summary": sr["summary"], "consistency": sr["consistency"]}
    scored["by_language"] = by_lang
    meta = out_dir / "meta.json"
    scored["run_meta"] = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else None
    (out_dir / "results.json").write_text(json.dumps(scored, ensure_ascii=False, indent=1), encoding="utf-8")
    return scored


# ── token / cost / wall-clock assessment (offline; no model call) ───────────────────────────────────────────────────────
def _heuristic_raw(messages) -> int:
    return sum(ev.estimate_tokens(m["content"]) for m in messages) + 12


def load_baseline_calls(path: Path | None = None) -> list[dict]:
    f = path or (BASELINE_DATA / "calls.jsonl")
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


def calibration(cases: list[dict], baseline: list[dict]) -> dict | None:
    """Heuristic-to-actual input ratio measured on the REAL baseline calls (the v2-1 requests; actual = API usage.prompt_tokens)."""
    by_id = {c["id"]: c for c in cases}
    ratios = []
    for r in baseline:
        if r.get("usage") and r["case"] in by_id and r["run"] == "run1":
            msgs = BASELINE_SPEC.build(by_id[r["case"]]["jd"], by_id[r["case"]]["job_metadata"])["messages"]
            ratios.append(r["usage"]["prompt_tokens"] / _heuristic_raw(msgs))
    return {"n": len(ratios), "mean": sum(ratios) / len(ratios), "min": min(ratios), "max": max(ratios)} if ratios else None


def simulate_calls(cases: list[dict], spec: PromptSpec, count_fn, usage_fn, latency_fn, plan: dict = ev.PLAN) -> dict:
    """Walk the planned order through the SAME pre-call gate as execute(). usage_fn(case, run, est_in) -> (input, completion) tokens
    the call is assumed to use; latency_fn(case, run) -> seconds. No model is called."""
    made = tokens = 0
    cost, elapsed, stop = 0.0, 0.0, None
    for run in (f"run{r}" for r in range(1, plan["runs_per_case"] + 1)):
        for case in cases:
            est_in = count_fn(spec.build(case["jd"], case["job_metadata"])["messages"])
            stop = gate(made, tokens, cost, elapsed, est_in, plan)
            if stop:
                break
            u_in, u_out = usage_fn(case, run, est_in)
            assert u_out <= plan["max_completion_tokens_per_call"]
            made += 1
            tokens += u_in + u_out
            cost += u_in * PRICE_IN + u_out * PRICE_OUT
            elapsed += latency_fn(case, run)
        if stop:
            break
    return {"calls_made": made, "tokens": tokens, "cost_usd": round(cost, 5), "elapsed_s": round(elapsed, 1), "stopped_by": stop}


def budget_assessment(cases: list[dict], spec: PromptSpec, count_fn, method: str, plan: dict = ev.PLAN,
                      baseline: list[dict] | None = None) -> dict:
    """Are the approved limits practical for `spec`? Uses the real baseline calls (actual input tokens, completion tokens, latency)
    as the yardstick. With an exact tokenizer (method tiktoken) the reservation is exact; without one (the sandbox) the executor's
    reservation is the conservative heuristic x1.3 and a calibrated projection (heuristic x the baseline's measured actual/heuristic
    ratio) shows what an exact count should give. No model is called."""
    baseline = load_baseline_calls() if baseline is None else baseline
    exact = method.startswith("tiktoken")
    cal = None if exact else calibration(cases, baseline)
    comp = {(r["case"], r["run"]): r["usage"]["completion_tokens"] for r in baseline if r.get("usage")}
    lat = {(r["case"], r["run"]): r["latency_s"] for r in baseline if r.get("latency_s") is not None}
    proj, rows = {}, []
    for c in cases:
        msgs = spec.build(c["jd"], c["job_metadata"])["messages"]
        est, raw = count_fn(msgs), _heuristic_raw(msgs)
        if exact:
            proj[c["id"]] = {"mean": est - INPUT_MARGIN_EXACT, "high": est - INPUT_MARGIN_EXACT}
        elif cal:
            proj[c["id"]] = {"mean": round(raw * cal["mean"]), "low": round(raw * cal["min"]), "high": round(raw * cal["max"])}
        else:
            proj[c["id"]] = {"mean": round(raw * 0.88), "high": round(raw * 0.9)}
        rows.append({"case": c["id"], "language": c["language"], "executor_reserved_input_tokens": est,
                     "executor_reserve_tokens_per_call": est + plan["max_completion_tokens_per_call"],
                     "executor_reserve_cost_usd": round(est * PRICE_IN + plan["max_completion_tokens_per_call"] * PRICE_OUT, 5),
                     "projected_actual_input": proj[c["id"]],
                     "reserve_tokens_per_call_with_exact_count": proj[c["id"]]["mean"] + INPUT_MARGIN_EXACT + plan["max_completion_tokens_per_call"]})

    def exact_like(msgs):                                          # what the reservation will be once an exact tokenizer is used
        by = next(c for c in cases if spec.build(c["jd"], c["job_metadata"])["messages"] == msgs)
        return proj[by["id"]]["mean"] + INPUT_MARGIN_EXACT
    def base_out(case, run): return comp.get((case["id"], run), 700)
    def base_lat(case, run): return lat.get((case["id"], run), 8.0)
    cap = plan["max_completion_tokens_per_call"]
    use_exact = count_fn if exact else exact_like
    scen = {
        "A expected: exact-count reserve, v2-1 completions, projected input": simulate_calls(
            cases, spec, use_exact, lambda c, r, e: (proj[c["id"]]["mean"], base_out(c, r)), base_lat, plan),
        "B growth: exact-count reserve, completions x1.5, latency x1.5, input at the projected high": simulate_calls(
            cases, spec, use_exact, lambda c, r, e: (proj[c["id"]]["high"], min(int(base_out(c, r) * 1.5), cap)), lambda c, r: base_lat(c, r) * 1.5, plan),
        "C stress: exact-count reserve, every completion at the 6000 cap, 60 s per call": simulate_calls(
            cases, spec, use_exact, lambda c, r, e: (proj[c["id"]]["high"], cap), lambda c, r: 60.0, plan),
    }
    if not exact:
        scen["D sandbox heuristic reserve (x1.3), expected usage"] = simulate_calls(
            cases, spec, count_fn, lambda c, r, e: (proj[c["id"]]["mean"], base_out(c, r)), base_lat, plan)
        scen["E sandbox heuristic reserve (x1.3), growth"] = simulate_calls(
            cases, spec, count_fn, lambda c, r, e: (proj[c["id"]]["high"], min(int(base_out(c, r) * 1.5), cap)), lambda c, r: base_lat(c, r) * 1.5, plan)
    keys = list(scen)
    A, B, C = scen[keys[0]], scen[keys[1]], scen[keys[2]]
    pct = lambda used, limit: round(100 * (1 - used / limit), 1)
    return {"token_counting": method, "calibration_from_baseline": cal, "per_case": rows,
            "executor_reserve_range_tokens": [min(r["executor_reserve_tokens_per_call"] for r in rows), max(r["executor_reserve_tokens_per_call"] for r in rows)],
            "scenarios": scen,
            "limits_practical": {
                "all_24_calls_A_expected": A["calls_made"] == plan["max_calls"], "all_24_calls_B_growth": B["calls_made"] == plan["max_calls"],
                "token_headroom_pct_A": pct(A["tokens"], plan["max_total_tokens"]), "token_headroom_pct_B": pct(B["tokens"], plan["max_total_tokens"]),
                "cost_usd_A": A["cost_usd"], "cost_usd_B": B["cost_usd"], "cost_cap_usd": plan["max_cost_usd"],
                "wall_clock_s_A": A["elapsed_s"], "wall_clock_s_B": B["elapsed_s"], "wall_clock_limit_s": plan["wall_clock_limit_s"],
                "stress_C_calls_before_reserve_stop": C["calls_made"], "stress_C_stopped_by": C["stopped_by"]}}


def reservation_comparison(cases: list[dict], count_fn, other: PromptSpec, base: PromptSpec, plan: dict = ev.PLAN) -> dict:
    """Per-call reservations (input estimate + the 6000-token output reserve) of two prompts for the same 12 cases, and the worst-case total over the
    24 planned calls. Offline; nothing is called."""
    rows = {}
    for c in cases:
        a = count_fn(base.build(c["jd"], c["job_metadata"])["messages"])
        b = count_fn(other.build(c["jd"], c["job_metadata"])["messages"])
        rows[c["id"]] = {f"{base.key}_input": a, f"{other.key}_input": b, "added_input_tokens": b - a}
    cap = plan["max_completion_tokens_per_call"]
    tot = lambda k: sum(r[f"{k}_input"] + cap for r in rows.values()) * plan["runs_per_case"]          # noqa: E731
    return {"per_case": rows, f"{base.key}_worst_case_reserved_total": tot(base.key), f"{other.key}_worst_case_reserved_total": tot(other.key),
            "added_input_tokens_per_call_min_max": [min(r["added_input_tokens"] for r in rows.values()), max(r["added_input_tokens"] for r in rows.values())],
            "note": "Worst-case totals reserve the output cap on every call, so they exceed the 200,000-token limit by design; the run gate reserves input + 6000 only for the NEXT call "
                    "against tokens already used (see assessment.limits_practical for the projected headroom)."}


def check_network(host: str = "api.openai.com", port: int = 443, timeout: float = 5.0) -> dict:
    """TCP reachability only: no request, no credentials, no data sent."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"host": host, "reachable": True}
    except OSError as exc:
        return {"host": host, "reachable": False, "error": type(exc).__name__}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preflight", action="store_true"); ap.add_argument("--run", action="store_true"); ap.add_argument("--out")
    ap.add_argument("--prompt", choices=sorted(SPECS), default="baseline", help="system prompt: baseline (v2-1, default), v2-2 or v2-3 (offline candidates)")
    ap.add_argument("--check-network", action="store_true", help="with --preflight: TCP-connect to api.openai.com:443 (no request, no key used)")
    a = ap.parse_args(argv)
    spec = SPECS[a.prompt]
    cases = ev.load_cases()
    pf = preflight_offline(cases, spec)
    count_fn, method = make_counter()
    out = {k: pf[k] for k in ("ok", "problems", "token_counting", "model", "prompt_key", "prompt_version", "prompt_sha256", "prompt_source",
                              "frozen_commit", "candidate_commit", "worst_case_reserved_total")}
    if pf["ok"] or a.preflight:
        out["assessment"] = budget_assessment(cases, spec, count_fn, method)
        if spec.key == "v2-3":
            out["reservations_vs_v2_2"] = reservation_comparison(cases, count_fn, spec, CANDIDATE_SPEC)
    if a.preflight:
        out["openai_key_present"] = bool(os.environ.get("OPENAI_API_KEY"))        # a boolean only; the value is never read into the output
        if a.check_network:
            out["network"] = check_network()
    print(json.dumps(out, indent=1))
    if not pf["ok"]:
        return 2
    if not a.run:
        return 0
    if not os.environ.get("OPENAI_API_KEY"):
        print("BLOCKED: OPENAI_API_KEY is not set in this environment. No call was made."); return 3
    if not a.out:
        ap.error("--out DIR is required with --run")
    out_dir = Path(a.out)
    if out_dir.exists() and any(out_dir.iterdir()):
        print("BLOCKED: the output directory is not empty (no overwriting, no re-runs)."); return 4
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    summary = execute(cases, openai_call, out_dir, count_fn=count_fn, spec=spec)
    print(json.dumps(summary, indent=1))
    scored = score(cases, out_dir)
    for k, v in scored["gates"].items():
        print(("PASS " if v else "FAIL " if v is False else "n/a  ") + k)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
