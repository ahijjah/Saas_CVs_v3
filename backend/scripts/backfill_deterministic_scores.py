#!/usr/bin/env python3
"""
F-01.2 — Backfill deterministic scores for historical D-01 rows.

Reads application_scores rows where llm_match_results_json IS NOT NULL
and det_final_score IS NULL, runs DeterministicScoringEngine on the
existing D-01 output, and writes det_final_score and det_score_json.

Does NOT:
  - Call OpenAI or any external API
  - Re-run D-01 mapper
  - Change final_score, decision, or any recruiter-facing field

Requirements:
  - DATABASE_URL env var (or DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD)
  - psycopg2 installed  (pip install psycopg2-binary)
  - Run from backend/ directory so that 'services' imports resolve

Usage:
  # Dry-run (no writes, shows what would be updated)
  python scripts/backfill_deterministic_scores.py --dry-run

  # Process first 10 rows for smoke test
  python scripts/backfill_deterministic_scores.py --limit 10

  # Full backfill (production)
  python scripts/backfill_deterministic_scores.py

  # Custom batch size
  python scripts/backfill_deterministic_scores.py --batch-size 50
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

# ── Path setup — allow running from project root or backend/ ─────────────────
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from services.deterministic_scoring import (
    DeterministicScoringConfig,
    DeterministicScoringEngine,
    deterministic_score_to_dict,
)
from services.evidence_serialiser import llm_matchresult_from_dict

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Database connection ───────────────────────────────────────────────────────

def _get_connection():
    """
    Return a psycopg2 connection using DATABASE_URL or individual env vars.
    Strips async driver specifiers (+asyncpg, +psycopg) from the URL so the
    same DATABASE_URL used by the FastAPI app works here without modification.
    """
    try:
        import psycopg2
        import psycopg2.extras  # for DictCursor
    except ImportError:
        logger.error("psycopg2 not installed. Run: pip install psycopg2-binary")
        sys.exit(1)

    url = os.environ.get("DATABASE_URL", "")
    if url:
        # Strip async driver: postgresql+asyncpg:// → postgresql://
        url = re.sub(r"\+\w+", "", url, count=1)
        return psycopg2.connect(url, options="-c search_path=cv_analyzer",
                                cursor_factory=psycopg2.extras.RealDictCursor)

    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", "5432")),
        dbname=os.environ.get("DB_NAME", "cv_analyzer_prod"),
        user=os.environ.get("DB_USER", "cv_app"),
        password=os.environ.get("DB_PASSWORD", ""),
        options="-c search_path=cv_analyzer",
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


# ── Config loading ────────────────────────────────────────────────────────────

def _load_scoring_config(conn) -> DeterministicScoringConfig:
    """
    Load F-01 deterministic scoring parameters from system_config.
    Falls back to DeterministicScoringConfig defaults if a key is absent.
    """
    keys = [
        "scoring_v2.det_partial_credit",
        "scoring_v2.det_required_weight",
        "scoring_v2.det_preferred_weight",
        "scoring_v2.det_enable_required_absent_floor",
        "scoring_v2.det_required_absent_floor_threshold",
        "scoring_v2.det_required_absent_floor_cap",
    ]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT key, value FROM system_config WHERE key = ANY(%s)",
            (keys,),
        )
        sys_map: dict[str, str] = {row["key"]: row["value"] for row in cur.fetchall()}

    defaults = DeterministicScoringConfig()
    return DeterministicScoringConfig(
        partial_credit=float(
            sys_map.get("scoring_v2.det_partial_credit", defaults.partial_credit)
        ),
        required_weight=float(
            sys_map.get("scoring_v2.det_required_weight", defaults.required_weight)
        ),
        preferred_weight=float(
            sys_map.get("scoring_v2.det_preferred_weight", defaults.preferred_weight)
        ),
        enable_required_absent_floor=(
            sys_map.get(
                "scoring_v2.det_enable_required_absent_floor",
                str(defaults.enable_required_absent_floor),
            ).lower() == "true"
        ),
        required_absent_floor_threshold=float(
            sys_map.get(
                "scoring_v2.det_required_absent_floor_threshold",
                defaults.required_absent_floor_threshold,
            )
        ),
        required_absent_floor_cap=float(
            sys_map.get(
                "scoring_v2.det_required_absent_floor_cap",
                defaults.required_absent_floor_cap,
            )
        ),
    )


# ── Core processing ───────────────────────────────────────────────────────────

def _parse_weights(raw: Any) -> dict[str, int]:
    """
    Parse weights_snapshot from the DB.

    weights_snapshot is JSONB NOT NULL — psycopg2 returns it as a dict.
    Accepts a string fallback for robustness.
    """
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise ValueError(f"weights_snapshot is not a dict: {type(raw)!r}")
    return {k: int(v) for k, v in raw.items()}


def _apply_soft_skill_routing(llm_result) -> int:
    """
    Apply F-01.4 soft-skill dimension routing correction in-memory.

    For each assessment where criterion_class == "soft_skill" and dimension
    is not "soft_skills" or "other", overrides dimension to "soft_skills".
    Mirrors _parse_one_assessment() in llm_criteria_mapper.py.

    Mutates llm_result.assessments in place.  Does NOT touch the stored
    llm_match_results_json — only the transient object used for det scoring.

    Returns the number of assessments corrected.
    """
    corrected = 0
    for a in llm_result.assessments:
        if a.criterion_class == "soft_skill" and a.dimension not in ("soft_skills", "other"):
            a.dimension = "soft_skills"
            corrected += 1
    return corrected


def _process_row(
    llm_match_json: dict[str, Any],
    weights_raw: Any,
    cfg: DeterministicScoringConfig,
) -> tuple[int, str, int]:
    """
    Deserialise D-01 output, apply routing correction, run the deterministic
    engine, return results.

    Pure function — no database access.  Extracted for testability.

    Returns
    -------
    (det_final_score, det_score_json_str, rerouted_count)
    """
    llm_result = llm_matchresult_from_dict(llm_match_json)
    rerouted = _apply_soft_skill_routing(llm_result)
    weights = _parse_weights(weights_raw)
    det_result = DeterministicScoringEngine(cfg).score(llm_result, weights)
    det_score_json = json.dumps(
        deterministic_score_to_dict(det_result), ensure_ascii=False
    )
    return det_result.final_score, det_score_json, rerouted


# ── Pending row query helpers ─────────────────────────────────────────────────

def _count_pending(conn, *, recalculate: bool = False) -> int:
    extra = "" if recalculate else "\n              AND det_final_score IS NULL"
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT COUNT(*) AS n FROM application_scores "
            f"WHERE llm_match_results_json IS NOT NULL{extra}"
        )
        return cur.fetchone()["n"]


def _fetch_pending_ids(conn, limit: int | None, *, recalculate: bool = False) -> list[str]:
    """
    Fetch all pending application_ids in created_at order.

    Fetching IDs upfront (rather than using OFFSET) gives stable, resumable
    iteration: rows processed between fetches drop out of the WHERE clause so
    are never double-counted, and dry-run mode doesn't cause an infinite loop.

    In recalculate mode ALL rows with llm_match_results_json are returned so
    that already-backfilled rows can be re-scored with F-01.4 routing applied.
    """
    det_guard = "" if recalculate else "\n              AND det_final_score IS NULL"
    with conn.cursor() as cur:
        sql = (
            f"SELECT application_id FROM application_scores "
            f"WHERE llm_match_results_json IS NOT NULL{det_guard} "
            f"ORDER BY created_at ASC"
        )
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        cur.execute(sql)
        return [row["application_id"] for row in cur.fetchall()]


def _fetch_row(conn, application_id: str, *, recalculate: bool = False) -> dict[str, Any] | None:
    """
    Fetch a single row's D-01 data.

    Normal mode: WHERE det_final_score IS NULL guard makes the fetch
    idempotent — another process already backfilling this row returns None.

    Recalculate mode: guard is dropped so already-scored rows are re-fetched
    for F-01.4 routing recalculation.
    """
    if recalculate:
        where_extra = "AND llm_match_results_json IS NOT NULL"
    else:
        where_extra = "AND det_final_score IS NULL"
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT llm_match_results_json, weights_snapshot
            FROM application_scores
            WHERE application_id = %s
              {where_extra}
            """,
            (application_id,),
        )
        return cur.fetchone()


def _update_row(
    conn,
    application_id: str,
    det_final_score: int,
    det_score_json: str,
    *,
    recalculate: bool = False,
) -> bool:
    """
    Write det_final_score and det_score_json for one row.

    Normal mode: WHERE det_final_score IS NULL guard prevents overwriting a
    row already processed by a concurrent run.

    Recalculate mode: guard is dropped so all mapped rows can be overwritten
    with the F-01.4-corrected score.  Returns True if exactly one row updated.
    """
    if recalculate:
        where_extra = "AND llm_match_results_json IS NOT NULL"
    else:
        where_extra = "AND det_final_score IS NULL"
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE application_scores
            SET det_final_score = %s,
                det_score_json  = %s
            WHERE application_id = %s
              {where_extra}
            """,
            (det_final_score, det_score_json, application_id),
        )
        return cur.rowcount == 1


# ── Main backfill loop ────────────────────────────────────────────────────────

def run_backfill(
    *,
    dry_run: bool,
    limit: int | None,
    batch_size: int,
    verbose: bool,
    recalculate: bool = False,
) -> None:
    if dry_run:
        logger.info("=== DRY-RUN MODE — no writes will be made ===")
    if recalculate:
        logger.info(
            "=== RECALCULATE MODE — all mapped rows will be rescored "
            "with F-01.4 soft-skill routing applied ==="
        )

    conn = _get_connection()
    try:
        # Load scoring config from system_config
        cfg = _load_scoring_config(conn)
        logger.info(
            "Scoring config loaded: partial_credit=%.2f required_weight=%.2f "
            "preferred_weight=%.2f absent_floor=%s",
            cfg.partial_credit,
            cfg.required_weight,
            cfg.preferred_weight,
            cfg.enable_required_absent_floor,
        )

        # Count and announce
        total_pending = _count_pending(conn, recalculate=recalculate)
        effective_limit = limit if limit is not None else total_pending
        logger.info(
            "Pending rows: %d  |  Effective limit: %d  |  Batch size: %d",
            total_pending, effective_limit, batch_size,
        )

        if total_pending == 0:
            logger.info("No pending rows — backfill already complete.")
            return

        # Fetch all pending IDs upfront
        pending_ids = _fetch_pending_ids(conn, limit, recalculate=recalculate)
        logger.info("Fetched %d application IDs to process.", len(pending_ids))

        rows_scanned = 0
        rows_updated = 0
        rows_skipped = 0
        rows_failed = 0
        rows_rerouted = 0
        start_time = time.time()

        # Process in batches
        for batch_start in range(0, len(pending_ids), batch_size):
            batch = pending_ids[batch_start : batch_start + batch_size]
            batch_num = batch_start // batch_size + 1
            logger.info(
                "Processing batch %d (%d–%d of %d)…",
                batch_num,
                batch_start + 1,
                min(batch_start + batch_size, len(pending_ids)),
                len(pending_ids),
            )

            for app_id in batch:
                rows_scanned += 1

                # Fetch row data (idempotency guard active in normal mode)
                row = _fetch_row(conn, app_id, recalculate=recalculate)
                if row is None:
                    rows_skipped += 1
                    if verbose:
                        logger.debug("[%s] Skipped — already processed.", app_id)
                    continue

                try:
                    det_final_score, det_score_json, rerouted = _process_row(
                        row["llm_match_results_json"],
                        row["weights_snapshot"],
                        cfg,
                    )
                    rows_rerouted += rerouted

                    if dry_run:
                        rows_updated += 1
                        if verbose:
                            logger.info(
                                "[DRY-RUN] [%s] det_final_score=%d rerouted=%d",
                                app_id, det_final_score, rerouted,
                            )
                    else:
                        updated = _update_row(
                            conn, app_id, det_final_score, det_score_json,
                            recalculate=recalculate,
                        )
                        conn.commit()
                        if updated:
                            rows_updated += 1
                            if verbose:
                                logger.info(
                                    "[%s] Updated  det_final_score=%d rerouted=%d",
                                    app_id, det_final_score, rerouted,
                                )
                        else:
                            rows_skipped += 1
                            if verbose:
                                logger.debug(
                                    "[%s] Skipped (race condition — already updated).", app_id
                                )

                except Exception as exc:
                    rows_failed += 1
                    logger.error("[%s] FAILED: %s", app_id, exc, exc_info=verbose)
                    try:
                        conn.rollback()
                    except Exception:
                        pass

        elapsed = time.time() - start_time

        # ── Summary ───────────────────────────────────────────────────────────
        mode_label = "(DRY-RUN) " if dry_run else ""
        print()
        print(f"Backfill {mode_label}complete")
        print(f"  processed  = {rows_scanned}")
        print(f"  updated    = {rows_updated}")
        print(f"  skipped    = {rows_skipped}")
        print(f"  failed     = {rows_failed}")
        print(f"  rerouted   = {rows_rerouted}  (soft_skill criteria moved to soft_skills dimension)")
        print(f"  elapsed    = {elapsed:.1f}s")

        if rows_failed > 0:
            logger.warning(
                "%d row(s) failed. Check logs above for application_id and exception details.",
                rows_failed,
            )

    finally:
        conn.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "F-01.2 — Backfill det_final_score and det_score_json for historical "
            "rows that already contain D-01 mapping results (llm_match_results_json)."
        )
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Read and process rows but do not write to the database.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Process at most N rows (useful for smoke testing).",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=100,
        metavar="N",
        help="Number of rows to process per batch (default: 100).",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Log per-row details (det_final_score per application_id).",
    )
    p.add_argument(
        "--recalculate",
        action="store_true",
        default=False,
        help=(
            "F-01.6: recalculate ALL rows with llm_match_results_json, "
            "including rows that already have det_final_score. "
            "Applies the F-01.4 soft-skill dimension routing correction "
            "in-memory without modifying llm_match_results_json. "
            "Use this to fix historical rows scored before F-01.4 was deployed."
        ),
    )
    return p


if __name__ == "__main__":
    args = _build_parser().parse_args()
    run_backfill(
        dry_run=args.dry_run,
        limit=args.limit,
        batch_size=args.batch_size,
        verbose=args.verbose,
        recalculate=args.recalculate,
    )
