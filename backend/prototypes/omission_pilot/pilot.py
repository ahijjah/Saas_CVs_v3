"""Pre-registered omission pilot: CONTROL (pinned v2-3 request) against INVENTORY (the same request plus the marked inventory block).

This file builds, freezes and dry-runs the pilot. It has NO default client: run_pilot needs a client object passed in by the caller, and
an approval record whose manifest hash and cap match this run. The CLI only supports --dry-run. Nothing here calls a model or a network.

Pre-registered rules live in PROTOCOL.md and in the constants below; the test suite checks that the two agree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parent.parent
PROTOCOL = HERE / "PROTOCOL.md"
RESULTS = HERE / "results"
sys.path[:0] = [str(BACKEND / "prototypes" / "omission_inventory"), str(BACKEND)]
import coverage as C  # noqa: E402
import inventory as I  # noqa: E402
import preview as P  # noqa: E402
import replay as R  # noqa: E402
from scripts import requirements_v2_tc20_compare as cmp  # noqa: E402  (model and standard prices; no network at import)
from scripts import requirements_v2_tc20_eval as ev  # noqa: E402  (pinned arm, user message, frozen scorer)

# ── pre-registered constants (PROTOCOL.md section 6 states the same values) ─────────────────────────────────────────────────
PAIRS = 10                          # calls per arm: 10 CONTROL and 10 INVENTORY, alternating, CONTROL first
ARMS = ("CONTROL", "INVENTORY")
MODEL = cmp.MODEL                   # gpt-4.1-2025-04-14, the snapshot the TC20 repeat batch used
TEMPERATURE = ev.TEMPERATURE        # 0.1
MAX_TOKENS = ev.MAX_TOKENS          # 6000: the output ceiling, also used for the reservation
RESPONSE_FORMAT = {"type": "json_object"}
TIMEOUT_S = ev.TIMEOUT_S            # 90
RETRIES = 0
PRICE_IN, PRICE_OUT = cmp.PRICE_IN, cmp.PRICE_OUT
PRICING_STATUS = cmp.PRICING_STATUS
REQUIRED_SCHEMA_KEYS = ("scoreability", "categories", "category_weights", "non_scoreable_requirements", "post_hiring_conditions",
                        "informational_items", "warnings")
CATEGORY_KEYS = ("skills", "experience", "education", "certifications", "soft_skills", "domain_knowledge", "other_requirements")
GUARD_CHECKS = {"OR": ("C_EXP_OR", "C_FAM_OR"), "AND": ("C_AND",), "category": ("C_LOCAL",), "location": ("C_LOCATION",),
                "reporting": ("C_REPORTING",)}
GUARD_NAMES = ("OR", "AND", "category", "location", "reporting", "schema", "invented")

# Interpretation rules, fixed before any call (PROTOCOL.md section 6).
FISHER_ALPHA = 0.05                 # two-sided Fisher exact test; PRIMARY_SUPPORTED needs p < FISHER_ALPHA and INVENTORY above CONTROL
GUARD_REGRESSIONS_ALLOWED = 0       # any paired guard regression blocks the verdict (no offset by coverage or duty counts)

DUTY_COUNT = 16
UNITS_REVIEWED = 35


class PilotError(RuntimeError):
    pass


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha_file(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def settings() -> dict:
    return {"model": MODEL, "temperature": TEMPERATURE, "max_tokens": MAX_TOKENS, "response_format": RESPONSE_FORMAT,
            "timeout_s": TIMEOUT_S, "retries": RETRIES}


def build_requests() -> dict:
    """The two complete requests. CONTROL is the pinned v2-3 request; INVENTORY is identical except for the marked block appended to the user
    message. The system prompt, the job context and the JD are byte-identical in both."""
    jd, labels = R.load_jd()
    inv = I.build_inventory(jd, json.loads(R.ANNOTATION.read_text(encoding="utf-8")))
    control = ev.messages_for("v2-3", jd)
    if sha_text(control[1]["content"]) != P.USER_SHA256 or sha_text(control[0]["content"]) != P.SYSTEM_SHA256:
        raise PilotError("the CONTROL request does not match the pinned baseline hashes")
    addition = P.addition_block(inv)
    inventory = [dict(control[0]), {"role": "user", "content": control[1]["content"] + "\n\n" + addition}]
    return {"CONTROL": control, "INVENTORY": inventory, "_jd": jd, "_labels": labels, "_inv": inv, "_addition": addition}


def reservation(messages: list[dict]) -> float:
    """Worst case for one call, from the FULL request: input tokens estimated as ceil(characters / 2), the harness convention (conservative for
    the Latin and Arabic text here), plus the full output ceiling MAX_TOKENS at the output price. Not an average."""
    est_in = math.ceil(sum(len(m["content"]) for m in messages) / 2)
    return round(est_in * PRICE_IN + MAX_TOKENS * PRICE_OUT, 6)


def schedule() -> list[tuple[str, int]]:
    """Alternate the arms, CONTROL first: (arm, pair index) for 2 * PAIRS calls. No call is repeated."""
    order = []
    for k in range(1, PAIRS + 1):
        order += [("CONTROL", k), ("INVENTORY", k)]
    return order


def _binom_cdf(k: int, n: int, p: float) -> float:
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Exact two-sided 95% interval for a proportion k/n (bisection on the binomial tail)."""
    def bisect(f):
        lo, hi = 0.0, 1.0
        for _ in range(200):
            mid = (lo + hi) / 2
            if f(mid):
                hi = mid
            else:
                lo = mid
        return (lo + hi) / 2
    lower = 0.0 if k == 0 else bisect(lambda q: 1 - _binom_cdf(k - 1, n, q) >= alpha / 2)
    upper = 1.0 if k == n else bisect(lambda q: _binom_cdf(k, n, q) <= alpha / 2)
    return round(lower, 4), round(upper, 4)


def fisher_two_sided(a: int, n1: int, b: int, n2: int) -> float:
    """Two-sided Fisher exact p-value for a successes out of n1 (group 1) against b out of n2 (group 2). Exact: integer hypergeometric
    weights, and a table counts when its probability is at most the observed table's probability."""
    total_draws, successes = n1 + n2, a + b
    denom = math.comb(total_draws, n1)
    weight = lambda x: math.comb(successes, x) * math.comb(total_draws - successes, n1 - x)  # noqa: E731
    observed = weight(a)
    p = sum(weight(x) for x in range(max(0, successes - (total_draws - n1)), min(successes, n1) + 1) if weight(x) <= observed)
    return min(1.0, p / denom)


def power_fisher(pc: float, pi: float, n: int = PAIRS) -> float:
    """Exact power of the verdict rule (INVENTORY higher, two-sided Fisher p < FISHER_ALPHA) when the true per-call retention rates are
    pc (CONTROL) and pi (INVENTORY). Computed by enumerating every pair of outcomes; no simulation."""
    total = 0.0
    for a in range(n + 1):
        for b in range(n + 1):
            if a > b and fisher_two_sided(a, n, b, n) < FISHER_ALPHA:
                total += math.comb(n, a) * pi ** a * (1 - pi) ** (n - a) * math.comb(n, b) * pc ** b * (1 - pc) ** (n - b)
    return round(total, 4)


def justification() -> dict:
    """Historical variability, the call count, and what the pre-registered rule can detect with PAIRS calls per arm.
    Historical evidence: TC20 repeat batch on this model (requirements_v2_tc20_repeat). v2-3 retained all 16 duty lines in 4 of 5 calls
    and lost them in one; v2-5 retained them in 5 of 5."""
    loss_k, loss_n = 1, 5
    examples = {"10/10 vs 9/10 (INVENTORY vs CONTROL)": {"fisher_two_sided_p": fisher_two_sided(10, 10, 9, 10), "verdict": "INCONCLUSIVE"},
                "10/10 vs 5/10 (INVENTORY vs CONTROL)": {"fisher_two_sided_p": round(fisher_two_sided(10, 10, 5, 10), 6), "verdict": "PRIMARY_SUPPORTED"}}
    smallest_supported_control = max(c for c in range(PAIRS + 1) if fisher_two_sided(PAIRS, PAIRS, c, PAIRS) < FISHER_ALPHA)
    return {"historical_control_losses": loss_k, "historical_calls": loss_n,
            "clopper_pearson_95_for_historical_loss_rate": clopper_pearson(loss_k, loss_n),
            "p_at_least_one_control_loss_if_loss_rate_0.2_by_pairs": {n: round(1 - 0.8 ** n, 3) for n in (5, 10, 15, 20)},
            "chosen_pairs": PAIRS, "fisher_examples": examples,
            "with_inventory_10_of_10_the_largest_control_count_that_is_supported": smallest_supported_control,
            "exact_power_of_the_rule": {f"CONTROL {pc} vs INVENTORY {pi}": power_fisher(pc, pi)
                                         for pc, pi in ((0.8, 1.0), (0.8, 0.9), (0.5, 1.0), (0.5, 0.9))},
            "reading": ("With 10 calls per arm the rule is exploratory. A 10/10 vs 9/10 result is never supported (p = 1.0). "
                        "Only a gap of about half the calls, or more, reaches p < 0.05. The power table gives the exact chance of a "
                        "supported verdict for some true rates; it is low for modest gaps. A supported verdict is a statistical "
                        "signal in this pilot, not an accuracy claim.")}


def freeze(requests: dict) -> dict:
    """Every input the pilot depends on, hashed. An approval must name the manifest hash; any change to any of these changes it."""
    parts = {
        "control_request_sha256": sha_text(json.dumps({"settings": settings(), "messages": requests["CONTROL"]}, sort_keys=True, ensure_ascii=False)),
        "inventory_request_sha256": sha_text(json.dumps({"settings": settings(), "messages": requests["INVENTORY"]}, sort_keys=True, ensure_ascii=False)),
        "system_prompt_sha256": P.SYSTEM_SHA256,
        "control_user_sha256": sha_text(requests["CONTROL"][1]["content"]),
        "inventory_user_sha256": sha_text(requests["INVENTORY"][1]["content"]),
        "inventory_addition_sha256": sha_text(requests["_addition"]),
        "jd_sha256": requests["_inv"].jd_sha256,
        "annotation_sha256": sha_file(R.ANNOTATION),
        "labels_sha256": sha_file(R.FIX / "expected_labels.json"),
        "evidence_sha256": sha_file(R.FIX / "evidence.json"),
        "settings_sha256": sha_text(json.dumps(settings(), sort_keys=True)),
        "protocol_sha256": sha_file(PROTOCOL),
        "pilot_script_sha256": sha_file(pathlib.Path(__file__)),
        "pilot_tests_sha256": sha_file(HERE / "tests" / "test_pilot.py"),
        "scorer_sha256": sha_file(BACKEND / "scripts" / "requirements_v2_tc20_eval.py"),
        "pricing_source_sha256": sha_file(BACKEND / "scripts" / "requirements_v2_tc20_compare.py"),
        "replay_logic_sha256": {name: sha_file(HERE.parent / "omission_inventory" / name)
                                for name in ("replay.py", "coverage.py", "inventory.py", "preview.py")},
    }
    parts["manifest_sha256"] = sha_text(json.dumps(parts, sort_keys=True))
    return parts


def dry_run() -> dict:
    requests = build_requests()
    msgs = {arm: requests[arm] for arm in ARMS}
    per_call = {arm: reservation(msgs[arm]) for arm in ARMS}
    order = schedule()
    calls = [{"call": n + 1, "arm": arm, "pair": k, "worst_case_usd": per_call[arm],
              "input_characters": sum(len(m["content"]) for m in msgs[arm])} for n, (arm, k) in enumerate(order)]
    total = round(sum(c["worst_case_usd"] for c in calls), 6)
    frozen = freeze(requests)
    return {"mode": "dry-run", "model": MODEL, "settings": settings(), "pricing_status": PRICING_STATUS,
            "calls_per_arm": PAIRS, "total_calls": len(calls), "order": calls,
            "worst_case_per_call_usd": per_call, "reservation_total_usd": total,
            "cap": {"required_minimum_usd": total, "approved": False,
                    "note": "the cap is exactly the reservation; no average and no margin. Not approved: no paid call may start"},
            "justification": justification(), "thresholds": thresholds(), "frozen": frozen,
            "control_request_chars": sum(len(m["content"]) for m in requests["CONTROL"]),
            "inventory_request_chars": sum(len(m["content"]) for m in requests["INVENTORY"]),
            "inventory_addition_chars": len(requests["_addition"])}


def thresholds() -> dict:
    return {"fisher_alpha": FISHER_ALPHA, "fisher_test": "two-sided, exact", "guard_regressions_allowed": GUARD_REGRESSIONS_ALLOWED,
            "verdict_order": ["BLOCKED_GUARD_REGRESSION", "PRIMARY_REGRESSION", "PRIMARY_SUPPORTED", "INCONCLUSIVE", "INCOMPLETE (run stopped)"]}


# ── analysis ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def schema_ok(obj) -> bool:
    if not isinstance(obj, dict) or any(k not in obj for k in REQUIRED_SCHEMA_KEYS):
        return False
    cats = obj.get("categories")
    if not isinstance(cats, dict) or set(cats) != set(CATEGORY_KEYS) or not all(isinstance(cats[k], list) for k in CATEGORY_KEYS):
        return False
    return all(isinstance(obj[k], list) for k in ("non_scoreable_requirements", "post_hiring_conditions", "informational_items", "warnings"))


def analyse_answer(raw: str, requests: dict) -> dict:
    """Primary, secondary and guard readings for one returned answer. A non-JSON answer fails every guard and retains no duty line."""
    jd, labels, inv = requests["_jd"], requests["_labels"], requests["_inv"]
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        obj = None
    if not isinstance(obj, dict):
        return {"valid_json": False, "duty_retained": 0, "guards": {g: False for g in GUARD_NAMES}, "spans": {}}
    checks = ev_checks(raw, jd, labels)
    duties = labels["expected_values"]["duty_lines"]
    resp = [it for _, it in R.frozen._items(obj) if it.get("origin") == "from_responsibilities"]
    retained = sum(1 for d in duties if any(d in str(it.get("source_text", "")) for it in resp))
    cov = C.cover(inv, jd, R.answer_quotes(raw))
    classes = {"genuine_omission": 0, "paraphrase": 0, "intro_wording": 0, "punctuation_only": 0}
    not_verbatim = [v["quote"] for v in cov["quotes"] if v["status"] == "not_verbatim"]
    for u in cov["units"]:
        for sp in u["uncovered_spans"]:
            classes[R.classify_span(sp, u, not_verbatim, R.TITLE_WORDS)["class"]] += 1
    word_bearing = classes["genuine_omission"] + classes["paraphrase"] + classes["intro_wording"]
    guards = {}
    for name, keys in GUARD_CHECKS.items():
        guards[name] = all(checks[k] for k in keys)
    guards["schema"] = schema_ok(obj)
    guards["invented"] = checks["invented"]["source_not_verbatim"] == 0 and checks["invented"]["duty_not_a_duty_line"] == 0
    return {"valid_json": True, "duty_retained": retained, "primary_retained_all": retained == DUTY_COUNT, "guards": guards,
            "frozen_checks": checks, "coverage": cov["summary"], "spans": classes, "word_bearing_spans": word_bearing,
            "review_candidates": classes["genuine_omission"], "not_verbatim_quotes": len(not_verbatim)}


def ev_checks(raw, jd, labels) -> dict:
    out = ev.score_answer(raw, jd, labels)
    return {**out["checks"], "invented": out["invented"]}


def decide(records: list[dict]) -> dict:
    """Verdict from paired records. records: [{'arm', 'pair', 'analysis'}]. Order: BLOCKED_GUARD_REGRESSION, PRIMARY_REGRESSION,
    PRIMARY_SUPPORTED, INCONCLUSIVE; INCOMPLETE when the run stopped before every call returned. Review-candidate counts are descriptive
    only and never enter the verdict."""
    by = {(r["arm"], r["pair"]): r["analysis"] for r in records}
    pairs = sorted({r["pair"] for r in records})
    complete = len(records) == 2 * PAIRS and all(by.get((a, k)) for a in ARMS for k in range(1, PAIRS + 1))
    if not complete:
        return {"verdict": "INCOMPLETE", "reason": "the run stopped before every call returned an answer; no verdict is drawn"}
    k = {arm: sum(by[(arm, j)]["primary_retained_all"] for j in pairs) for arm in ARMS}
    counts = {arm: {"retained_all_duties": k[arm], "calls": PAIRS, "proportion": round(k[arm] / PAIRS, 4),
                    "ci95_clopper_pearson": list(clopper_pearson(k[arm], PAIRS))} for arm in ARMS}
    p = fisher_two_sided(k["INVENTORY"], PAIRS, k["CONTROL"], PAIRS)
    regressions = {g: sum(1 for j in pairs if by[("CONTROL", j)]["guards"][g] and not by[("INVENTORY", j)]["guards"][g]) for g in GUARD_NAMES}
    guard_passes = {arm: {g: sum(by[(arm, j)]["guards"][g] for j in pairs) for g in GUARD_NAMES} for arm in ARMS}
    descriptive = {arm: {"median": sorted(by[(arm, j)]["review_candidates"] for j in pairs)[len(pairs) // 2],
                         "min": min(by[(arm, j)]["review_candidates"] for j in pairs),
                         "max": max(by[(arm, j)]["review_candidates"] for j in pairs)} for arm in ARMS}
    if sum(regressions.values()) > GUARD_REGRESSIONS_ALLOWED:
        verdict = "BLOCKED_GUARD_REGRESSION"
    elif k["INVENTORY"] < k["CONTROL"]:
        verdict = "PRIMARY_REGRESSION"
    elif k["INVENTORY"] > k["CONTROL"] and p < FISHER_ALPHA:
        verdict = "PRIMARY_SUPPORTED"
    else:
        verdict = "INCONCLUSIVE"
    return {"verdict": verdict, "counts": counts, "fisher_two_sided_p": round(p, 6), "guard_regressions_paired": regressions,
            "guard_passes": guard_passes, "review_candidates_descriptive_only": descriptive}


# ── execution (library only; no CLI path, no default client) ─────────────────────────────────────────────────────────────
def check_approval(approval: dict | None, frozen: dict, total: float) -> None:
    if not approval:
        raise PilotError("no approval record: a paid run needs an owner approval naming the manifest hash and the cap")
    if approval.get("manifest_sha256") != frozen["manifest_sha256"]:
        raise PilotError("the approval names a different manifest: something frozen has changed")
    if not isinstance(approval.get("approved_cap_usd"), (int, float)) or approval["approved_cap_usd"] < total:
        raise PilotError(f"the approved cap is below the reservation of {total}")


def run_pilot(client, approval: dict | None) -> dict:
    """Runs the schedule through `client.complete(messages, settings) -> {raw, finish_reason, model, usage}`. Stops at the first failed call
    (exception, empty answer or finish_reason other than 'stop') or unexpected returned model. There are no retries. Stops if the running
    worst-case spend would exceed the approved cap. Results are returned, not written here."""
    requests = build_requests()
    frozen = freeze(requests)
    total = round(sum(reservation(requests[arm]) for arm, _ in schedule()), 6)
    check_approval(approval, frozen, total)
    records, spent = [], 0.0
    for arm, k in schedule():
        spent_worst = round(spent + reservation(requests[arm]), 6)
        if spent_worst > approval["approved_cap_usd"]:
            return {"status": "stopped", "reason": "cap would be exceeded", "records": records, "spent_usd": spent}
        try:
            resp = client.complete(requests[arm], settings())
        except Exception as exc:  # noqa: BLE001 - a failed call stops the run; it is recorded, never retried
            records.append({"arm": arm, "pair": k, "failed": True, "error": repr(exc)})
            return {"status": "stopped", "reason": "failed call", "records": records, "spent_usd": spent}
        usage = resp.get("usage") or {}
        spent += usage.get("prompt_tokens", 0) * PRICE_IN + usage.get("completion_tokens", 0) * PRICE_OUT
        if resp.get("finish_reason") != "stop" or not resp.get("raw"):
            records.append({"arm": arm, "pair": k, "failed": True, "finish_reason": resp.get("finish_reason")})
            return {"status": "stopped", "reason": "failed call", "records": records, "spent_usd": spent}
        if resp.get("model") != MODEL:
            records.append({"arm": arm, "pair": k, "failed": True, "unexpected_model": resp.get("model")})
            return {"status": "stopped", "reason": "unexpected returned model", "records": records, "spent_usd": spent}
        records.append({"arm": arm, "pair": k, "failed": False, "raw": resp["raw"], "analysis": analyse_answer(resp["raw"], requests)})
    return {"status": "complete", "records": records, "spent_usd": spent, "decision": decide(records), "manifest_sha256": frozen["manifest_sha256"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="build and freeze everything, print the order, hashes and reservation; no call")
    args = ap.parse_args(argv)
    if not args.dry_run:
        ap.error("only --dry-run exists in this change; a paid run needs an approval and is not wired into the CLI")
    out = dry_run()
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "dryrun.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    lines = [f"model {out['model']}, {out['total_calls']} calls ({PAIRS} per arm), reservation {out['reservation_total_usd']} USD, cap NOT APPROVED",
             f"per call worst case: CONTROL {out['worst_case_per_call_usd']['CONTROL']}, INVENTORY {out['worst_case_per_call_usd']['INVENTORY']}",
             "order: " + " ".join(f"{c['call']}:{c['arm'][0]}{c['pair']}" for c in out["order"]),
             f"manifest {out['frozen']['manifest_sha256']}"]
    lines += [f"  {k}: {v}" for k, v in out["frozen"].items() if k != "manifest_sha256" and not isinstance(v, dict)]
    (RESULTS / "dryrun.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
