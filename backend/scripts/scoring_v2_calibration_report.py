#!/usr/bin/env python3
"""
scoring_v2_calibration_report.py
─────────────────────────────────────────────────────────────────────────────
Scoring V2 Phase 2B-1 — Calibration Assessment

Reads application_scores rows with match_results_json populated, compares
LLM dimension scores against rule-based algorithmic_scores, and prints a
formatted calibration report with Phase 3 readiness recommendations.

Usage:
    python scoring_v2_calibration_report.py [--limit N] [--json-out FILE]

    DATABASE_URL env var must point at the cv_analyzer PostgreSQL database.
    Alternatively, set DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD.

    --limit N       Analyse only the N most recent V2 rows (default: all)
    --json-out FILE Write machine-readable results to FILE (optional)
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from typing import Any

# ── Optional numpy / scipy for Pearson r; fall back to manual calc ────────────
try:
    from scipy.stats import pearsonr as _pearsonr  # type: ignore[import]
    def _corr(xs: list[float], ys: list[float]) -> float:
        if len(xs) < 2:
            return float("nan")
        r, _ = _pearsonr(xs, ys)
        return float(r)
except ImportError:
    def _corr(xs: list[float], ys: list[float]) -> float:  # type: ignore[misc]
        n = len(xs)
        if n < 2:
            return float("nan")
        mx, my = sum(xs) / n, sum(ys) / n
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        denom = (
            sum((x - mx) ** 2 for x in xs) ** 0.5
            * sum((y - my) ** 2 for y in ys) ** 0.5
        )
        return num / denom if denom else float("nan")


DIMENSIONS = [
    ("skills",          "score_skills"),
    ("experience",      "score_experience"),
    ("education",       "score_education"),
    ("certifications",  "score_certifications"),
    ("soft_skills",     "score_soft_skills"),
    ("domain_knowledge","score_domain_knowledge"),
    ("other",           "score_other"),
]


# ── Database connection ───────────────────────────────────────────────────────

def _get_connection():
    """Return a psycopg2 connection using DATABASE_URL or individual env vars."""
    try:
        import psycopg2  # type: ignore[import]
    except ImportError:
        print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary", file=sys.stderr)
        sys.exit(1)

    url = os.environ.get("DATABASE_URL")
    if url:
        return psycopg2.connect(url, options="-c search_path=cv_analyzer")

    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", "5432")),
        dbname=os.environ.get("DB_NAME", "cv_analyzer_prod"),
        user=os.environ.get("DB_USER", "cv_app"),
        password=os.environ.get("DB_PASSWORD", ""),
        options="-c search_path=cv_analyzer",
    )


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_rows(limit: int | None) -> list[dict[str, Any]]:
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            sql = """
                SELECT
                    application_id,
                    score_skills, score_experience, score_education,
                    score_certifications, score_soft_skills,
                    score_domain_knowledge, score_other,
                    final_score,
                    weights_snapshot,
                    match_results_json,
                    created_at
                FROM application_scores
                WHERE match_results_json IS NOT NULL
                ORDER BY created_at DESC
            """
            if limit:
                sql += f" LIMIT {int(limit)}"
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = []
            for row in cur.fetchall():
                r: dict[str, Any] = dict(zip(cols, row))
                # psycopg2 may already parse JSONB as dict; handle both
                for key in ("weights_snapshot", "match_results_json"):
                    if isinstance(r.get(key), str):
                        try:
                            r[key] = json.loads(r[key])
                        except (json.JSONDecodeError, TypeError):
                            r[key] = {}
                rows.append(r)
            return rows
    finally:
        conn.close()


# ── Analysis ──────────────────────────────────────────────────────────────────

def _algo_score(row: dict, dim: str) -> float | None:
    mr = row.get("match_results_json") or {}
    algo = mr.get("algorithmic_scores") or {}
    v = algo.get(dim)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _weighted_algo(row: dict) -> float | None:
    ws = row.get("weights_snapshot") or {}
    default_weights = {
        "skills": 0.30, "experience": 0.25, "education": 0.15,
        "certifications": 0.10, "soft_skills": 0.10, "domain_knowledge": 0.10,
    }
    total = 0.0
    total_weight = 0.0
    for dim, _ in DIMENSIONS[:-1]:  # exclude "other" from weighted sum
        a = _algo_score(row, dim)
        if a is None:
            continue
        w = float(ws.get(dim, default_weights.get(dim, 0.0)))
        total += w * a
        total_weight += w
    if total_weight == 0:
        return None
    # Normalise in case weights don't sum to 1.0
    return total / total_weight * sum(default_weights.values())


def _analyse(rows: list[dict]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "n_v2_rows": len(rows),
        "dimensions": {},
        "weighted_final": {},
        "blocking_gap_buckets": {},
        "match_method_totals": {},
        "outliers": {},
    }

    # Per-dimension deltas
    for dim, llm_col in DIMENSIONS:
        deltas: list[float] = []
        llm_high_algo_low = 0
        llm_low_algo_high = 0
        both_high = 0
        both_low = 0
        for row in rows:
            a = _algo_score(row, dim)
            l = row.get(llm_col)
            if a is None or l is None:
                continue
            l = float(l)
            deltas.append(l - a)
            if l >= 60 and a >= 60:
                both_high += 1
            elif l < 60 and a < 60:
                both_low += 1
            elif l >= 60 and a < 60:
                llm_high_algo_low += 1
            else:
                llm_low_algo_high += 1

        if not deltas:
            result["dimensions"][dim] = {"n": 0, "signal": "no data"}
            continue

        avg_d = statistics.mean(deltas)
        result["dimensions"][dim] = {
            "n": len(deltas),
            "avg_delta": round(avg_d, 1),
            "median_delta": round(statistics.median(deltas), 1),
            "stddev": round(statistics.stdev(deltas) if len(deltas) > 1 else 0.0, 1),
            "min_delta": round(min(deltas), 1),
            "max_delta": round(max(deltas), 1),
            "both_high": both_high,
            "both_low": both_low,
            "llm_high_algo_low": llm_high_algo_low,
            "llm_low_algo_high": llm_low_algo_high,
            "agreement_pct": round(
                (both_high + both_low) / len(deltas) * 100, 1
            ) if deltas else 0,
            "signal": _classify_delta(avg_d),
        }

    # Weighted algo vs final_score
    llm_finals: list[float] = []
    algo_finals: list[float] = []
    final_deltas: list[float] = []
    for row in rows:
        aw = _weighted_algo(row)
        lf = row.get("final_score")
        if aw is not None and lf is not None:
            llm_finals.append(float(lf))
            algo_finals.append(aw)
            final_deltas.append(float(lf) - aw)

    if final_deltas:
        result["weighted_final"] = {
            "n": len(final_deltas),
            "avg_llm_final": round(statistics.mean(llm_finals), 1),
            "avg_algo_weighted": round(statistics.mean(algo_finals), 1),
            "avg_delta": round(statistics.mean(final_deltas), 1),
            "stddev_delta": round(statistics.stdev(final_deltas) if len(final_deltas) > 1 else 0.0, 1),
            "pearson_r": round(_corr(llm_finals, algo_finals), 3),
            "min_delta": round(min(final_deltas), 1),
            "max_delta": round(max(final_deltas), 1),
        }

    # blocking_gap_count buckets
    gap_buckets: dict[int, list[float]] = {}
    for row in rows:
        mr = row.get("match_results_json") or {}
        bgc = int(mr.get("blocking_gap_count") or 0)
        fs = row.get("final_score")
        if fs is not None:
            gap_buckets.setdefault(bgc, []).append(float(fs))
    for bgc, scores in sorted(gap_buckets.items()):
        result["blocking_gap_buckets"][bgc] = {
            "n": len(scores),
            "avg_final": round(statistics.mean(scores), 1),
            "min_final": round(min(scores), 1),
            "max_final": round(max(scores), 1),
        }

    # Match method distribution
    method_totals: dict[str, int] = {}
    for row in rows:
        mr = row.get("match_results_json") or {}
        for method, count in (mr.get("matching_method_summary") or {}).items():
            method_totals[method] = method_totals.get(method, 0) + int(count)
    result["match_method_totals"] = method_totals

    # Outlier rows (skills)
    skill_outliers = sorted(
        [
            {
                "application_id": str(row["application_id"]),
                "llm_skills": row.get("score_skills"),
                "algo_skills": _algo_score(row, "skills"),
                "delta": round(float(row.get("score_skills") or 0) - (_algo_score(row, "skills") or 0), 1),
                "final_score": row.get("final_score"),
                "blocking_gaps": int((row.get("match_results_json") or {}).get("blocking_gap_count") or 0),
            }
            for row in rows
            if _algo_score(row, "skills") is not None
        ],
        key=lambda x: abs(x["delta"]),
        reverse=True,
    )[:10]
    result["outliers"]["skills_top10"] = skill_outliers

    return result


def _classify_delta(avg_delta: float) -> str:
    if avg_delta > 15:
        return "LLM MUCH MORE GENEROUS — algo too strict"
    if avg_delta > 5:
        return "LLM slightly more generous — algo may under-detect"
    if avg_delta < -15:
        return "LLM MUCH STRICTER — algo too loose (fuzzy threshold?)"
    if avg_delta < -5:
        return "LLM slightly stricter — LLM sees implicit evidence"
    return "broadly aligned"


# ── Phase 3 readiness recommendation ─────────────────────────────────────────

def _phase3_recommendation(analysis: dict) -> list[str]:
    lines: list[str] = []
    wf = analysis.get("weighted_final", {})
    r = wf.get("pearson_r", float("nan"))

    lines.append("─── Phase 3 readiness ─────────────────────────────────────────────────")

    if analysis["n_v2_rows"] < 20:
        lines.append("⚠  INSUFFICIENT DATA: fewer than 20 V2 rows. Run Batch 2A-6 in")
        lines.append("   production for at least one week before re-running this report.")
        lines.append("   Phase 3 bounding SHOULD NOT be activated yet.")
        return lines

    if r != r:  # NaN
        lines.append("⚠  Pearson r could not be computed (insufficient paired data).")
    elif r > 0.70:
        lines.append(f"✓  Pearson r = {r:.3f} — strong LLM/algo alignment.")
        lines.append("   Phase 3 bounding is SAFE to activate, subject to dimension review below.")
    elif r > 0.40:
        lines.append(f"~  Pearson r = {r:.3f} — moderate alignment.")
        lines.append("   Phase 3 bounding is PREMATURE. Fix high-delta dimensions first.")
    else:
        lines.append(f"✗  Pearson r = {r:.3f} — weak alignment.")
        lines.append("   Phase 3 bounding MUST NOT be activated. Significant calibration needed.")

    lines.append("")
    lines.append("─── Per-dimension action items ────────────────────────────────────────")
    for dim, info in sorted(analysis.get("dimensions", {}).items(),
                             key=lambda x: abs(x[1].get("avg_delta", 0)), reverse=True):
        avg_d = info.get("avg_delta")
        n = info.get("n", 0)
        if n == 0 or avg_d is None:
            continue
        sig = info["signal"]
        lines.append(f"  {dim:20s}  avg_delta={avg_d:+.1f}  → {sig}")
        if avg_d > 15:
            lines.append(f"    → Expand synonym list for '{dim}'; consider a +15 score floor in Phase 3")
        elif avg_d > 5:
            lines.append(f"    → Review synonym coverage for '{dim}'; LLM may be rewarding implicit evidence")
        elif avg_d < -15:
            lines.append(f"    → Tighten fuzzy threshold or reduce scope of '{dim}' pattern registry")
        elif avg_d < -5:
            lines.append(f"    → LLM penalises '{dim}' criteria not visible in rule-based signals")

    lines.append("")
    lines.append("─── Structural checks ─────────────────────────────────────────────────")
    gb = analysis.get("blocking_gap_buckets", {})
    no_gap_avg = (gb.get(0) or {}).get("avg_final")
    one_gap_avg = (gb.get(1) or {}).get("avg_final")
    if no_gap_avg is not None and one_gap_avg is not None:
        if no_gap_avg < one_gap_avg:
            lines.append("⚠  Unexpected: blocking_gaps=1 has higher avg final_score than blocking_gaps=0.")
            lines.append("   Check gap severity classification in CriteriaMatchEngine.")
        else:
            lines.append(f"✓  blocking_gaps=0 avg={no_gap_avg:.1f}, blocking_gaps=1 avg={one_gap_avg:.1f} — expected ordering.")

    methods = analysis.get("match_method_totals", {})
    total_matches = sum(methods.values())
    fuzzy_count = methods.get("fuzzy", 0) + methods.get("fuzzy_translated", 0)
    if total_matches > 0:
        fuzzy_pct = fuzzy_count / total_matches * 100
        if fuzzy_pct > 50:
            lines.append(f"~  {fuzzy_pct:.0f}% of matches via fuzzy — consider expanding exact synonym list")
        elif fuzzy_pct < 10:
            lines.append(f"✓  {fuzzy_pct:.0f}% fuzzy matches — synonym coverage is good")

    return lines


# ── Report printer ────────────────────────────────────────────────────────────

def _print_report(analysis: dict) -> None:
    print()
    print("=" * 70)
    print(" Scoring V2 Phase 2B-1 — Calibration Assessment Report")
    print("=" * 70)
    print(f"  V2 rows analysed: {analysis['n_v2_rows']}")
    wf = analysis.get("weighted_final", {})
    if wf:
        print(f"  Avg LLM final:    {wf.get('avg_llm_final', 'n/a')}")
        print(f"  Avg algo weighted:{wf.get('avg_algo_weighted', 'n/a')}")
        print(f"  Avg delta (final):{wf.get('avg_delta', 'n/a'):+.1f}")
        print(f"  Pearson r:        {wf.get('pearson_r', 'n/a')}")
    print()

    print("─── Per-dimension delta (LLM − algorithmic) ───────────────────────────")
    print(f"  {'Dimension':20s} {'n':>5} {'avg_Δ':>7} {'median':>7} {'std':>6} {'min':>7} {'max':>7}  {'agree%':>7}  Signal")
    print("  " + "─" * 90)
    for dim, info in sorted(analysis.get("dimensions", {}).items(),
                             key=lambda x: abs(x[1].get("avg_delta", 0)), reverse=True):
        n = info.get("n", 0)
        if n == 0:
            print(f"  {dim:20s} {'—':>5}")
            continue
        print(
            f"  {dim:20s} {n:>5} "
            f"{info['avg_delta']:>+7.1f} {info['median_delta']:>+7.1f} "
            f"{info['stddev']:>6.1f} {info['min_delta']:>+7.1f} {info['max_delta']:>+7.1f}  "
            f"{info['agreement_pct']:>6.1f}%  {info['signal']}"
        )
    print()

    print("─── Top skills outliers ───────────────────────────────────────────────")
    print(f"  {'app_id':>36}  {'LLM_sk':>7} {'algo_sk':>8} {'delta':>6} {'final':>6} {'gaps':>5}")
    print("  " + "─" * 75)
    for o in analysis.get("outliers", {}).get("skills_top10", []):
        print(
            f"  {o['application_id']:>36}  "
            f"{o['llm_skills']:>7}  {o['algo_skills']:>7.1f}  {o['delta']:>+6.1f}  "
            f"{o['final_score']:>6}  {o['blocking_gaps']:>5}"
        )
    print()

    print("─── blocking_gap_count vs avg final_score ─────────────────────────────")
    print(f"  {'gaps':>5} {'n':>5} {'avg_final':>10} {'min':>6} {'max':>6}")
    print("  " + "─" * 40)
    for bgc, info in sorted(analysis.get("blocking_gap_buckets", {}).items()):
        print(f"  {bgc:>5} {info['n']:>5} {info['avg_final']:>10.1f} {info['min_final']:>6.1f} {info['max_final']:>6.1f}")
    print()

    print("─── Match method distribution ─────────────────────────────────────────")
    methods = analysis.get("match_method_totals", {})
    total = sum(methods.values())
    for method, count in sorted(methods.items(), key=lambda x: -x[1]):
        pct = count / total * 100 if total else 0
        print(f"  {method:25s} {count:>7}  ({pct:.1f}%)")
    print()

    for line in _phase3_recommendation(analysis):
        print(line)
    print()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None,
                        help="Analyse only the N most recent V2 rows")
    parser.add_argument("--json-out", metavar="FILE", default=None,
                        help="Write machine-readable JSON results to FILE")
    args = parser.parse_args()

    try:
        rows = _load_rows(args.limit)
    except Exception as exc:
        print(f"ERROR loading rows: {exc}", file=sys.stderr)
        sys.exit(1)

    if not rows:
        print("No rows with match_results_json found. Deploy Batch 2A-6 first.")
        sys.exit(0)

    analysis = _analyse(rows)
    _print_report(analysis)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(analysis, f, indent=2, default=str)
        print(f"Machine-readable results written to: {args.json_out}")


if __name__ == "__main__":
    main()
