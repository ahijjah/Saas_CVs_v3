"""
requirements-v2 extraction baseline benchmark: EXECUTOR (plan version 2, frozen at commit 059c56b).

Runs the 12 synthetic cases x 2 runs against gpt-4o-mini-2024-07-18 with hard limits, then scores the raw answers with the
frozen offline scorer. It never touches the database, production settings or any repository file other than reading frozen
inputs. The only network host it contacts is api.openai.com, and only after every offline preflight check has passed.

  python scripts/requirements_v2_extraction_run.py --preflight            # offline checks only, no key needed, no network
  python scripts/requirements_v2_extraction_run.py --run --out DIR        # needs OPENAI_API_KEY in the environment

Secrets: the key is read from the environment only. It is never printed, logged or written; API error messages (which can echo
a key fragment) are never stored, only the error kind and HTTP status.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import requirements_v2_extraction_eval as ev  # noqa: E402  (the frozen scorer)
from services.requirements_v2.extraction.prompt import PROMPT_SHA256, build_request, load_prompt  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
FROZEN_COMMIT = "059c56b"
FROZEN_PATHS = ("backend/services/requirements_v2", "backend/tests/fixtures/requirements_v2_benchmark",
                "backend/scripts/requirements_v2_extraction_eval.py")
RUN_MODEL = "gpt-4o-mini-2024-07-18"                 # requested explicitly (the approved configuration)
PRICE_IN, PRICE_OUT = 0.15e-6, 0.60e-6               # USD/token, gpt-4o-mini list price (verified 2026-10-09 on OpenAI's model page; cached input 0.075/M is ignored = upper bound)
INPUT_MARGIN_EXACT = 64                              # tokens added to an exact tiktoken count (chat framing differences)
KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_\-]{16,}")


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


def preflight_offline(cases: list[dict]) -> dict:
    count, method = make_counter()
    problems = verify_frozen()
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
        req = build_request(c["jd"], c["job_metadata"], model=RUN_MODEL)
        if (req["model"], req["temperature"], req["max_tokens"], req["response_format"]) != (RUN_MODEL, 0.1, 6000, {"type": "json_object"}):
            problems.append(f"request settings differ for {c['id']}")
        reserve[c["id"]] = count(req["messages"]) + ev.PLAN["max_completion_tokens_per_call"]
    return {"ok": not problems, "problems": problems, "token_counting": method, "model": RUN_MODEL, "prompt_sha256": PROMPT_SHA256,
            "frozen_commit": FROZEN_COMMIT, "reserve_per_call_tokens": reserve,
            "worst_case_reserved_total": sum(reserve.values()) * ev.PLAN["runs_per_case"]}


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
def _leak(raw: str, prompt_lines: list[str]) -> str | None:
    if KEY_PATTERN.search(raw):
        return "api_key_pattern"
    return "system_prompt_text" if any(line in raw for line in prompt_lines) else None


def execute(cases: list[dict], call_fn, out_dir: Path, *, count_fn, clock=time.monotonic, plan: dict = ev.PLAN) -> dict:
    """Issue the calls under every stop condition of the plan. Each call is appended to calls.jsonl the moment it finishes,
    so a stopped run keeps everything it did. Returns the run summary (also written to run.json)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / "calls.jsonl"
    prompt_lines = [ln.strip() for ln in load_prompt().splitlines() if len(ln.strip()) >= 60][:40]
    t0 = clock()
    made = tokens = errors_in_row = length_hits = 0
    cost, stop, notes = 0.0, None, []
    order = [(f"run{r}", c) for r in range(1, plan["runs_per_case"] + 1) for c in cases]
    for run, case in order:
        req = build_request(case["jd"], case["job_metadata"], model=RUN_MODEL)
        est_in = count_fn(req["messages"])
        reserve_t = est_in + plan["max_completion_tokens_per_call"]
        reserve_c = est_in * PRICE_IN + plan["max_completion_tokens_per_call"] * PRICE_OUT
        if (out_dir / "STOP").exists():
            stop = "manual_stop"
        elif made >= plan["max_calls"]:
            stop = "max_calls"
        elif tokens + reserve_t > plan["max_total_tokens"]:
            stop = "token_budget_reserve"
        elif cost + reserve_c > plan["max_cost_usd"]:
            stop = "cost_budget_reserve"
        elif clock() - t0 + plan["per_call_timeout_s"] > plan["wall_clock_limit_s"]:
            stop = "wall_clock"
        if stop:
            break
        rec = {"case": case["id"], "run": run, "language": case["language"], "requested_model": req["model"], "settings": {
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
    summary = {"calls_made": made, "planned_calls": len(order), "stopped_by": stop, "actual_tokens": tokens,
               "actual_cost_usd": round(cost, 6), "wall_clock_s": round(clock() - t0, 2), "notes": notes}
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
    (out_dir / "results.json").write_text(json.dumps(scored, ensure_ascii=False, indent=1), encoding="utf-8")
    return scored


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preflight", action="store_true"); ap.add_argument("--run", action="store_true"); ap.add_argument("--out")
    a = ap.parse_args(argv)
    cases = ev.load_cases()
    pf = preflight_offline(cases)
    print(json.dumps({k: pf[k] for k in ("ok", "problems", "token_counting", "model", "prompt_sha256", "frozen_commit", "worst_case_reserved_total")}, indent=1))
    if not pf["ok"]:
        return 2
    if not a.run:
        return 0
    if not os.environ.get("OPENAI_API_KEY"):
        print("BLOCKED: OPENAI_API_KEY is not set in this environment. No call was made."); return 3
    if not a.out:
        ap.error("--out DIR is required with --run")
    out = Path(a.out)
    if out.exists() and any(out.iterdir()):
        print("BLOCKED: the output directory is not empty (no overwriting, no re-runs)."); return 4
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    count_fn, _ = make_counter()
    summary = execute(cases, openai_call, out, count_fn=count_fn)
    print(json.dumps(summary, indent=1))
    scored = score(cases, out)
    for k, v in scored["gates"].items():
        print(("PASS " if v else "FAIL " if v is False else "n/a  ") + k)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
