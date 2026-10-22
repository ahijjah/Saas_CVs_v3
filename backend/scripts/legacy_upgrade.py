"""Convert the legacy production database (source commit 63f3bd7, no migration ledger) for the current release. See db/legacy/upgrade.py
and docs/LEGACY_PRODUCTION_UPGRADE.md.

    LEGACY_UPGRADE_DATABASE_URL=postgresql://<bootstrap superuser>@host/db  python -m scripts.legacy_upgrade check
    LEGACY_UPGRADE_DATABASE_URL=...  python -m scripts.legacy_upgrade prepare --dry-run
    LEGACY_UPGRADE_DATABASE_URL=...  python -m scripts.legacy_upgrade prepare

  check     read-only report: PASS / REPAIR / ATTEST / ACK / INFO / WARN / FAIL. Exit 0 when the database is the exact legacy lineage,
            1 when anything is FAIL.
  prepare   the conversion, in one transaction (rolled back on any failure). --dry-run runs everything and rolls back at the end.

The login must be a superuser that is not one of the runtime roles. The URL comes from the environment and is never printed; a password
in it, or in a driver message, is replaced by ***. --acknowledge-data FILE accepts the rows a named data check still finds (repeatable);
the acknowledgement is recorded in the evidence table."""
from __future__ import annotations

import argparse
import os
import pathlib
import sys

import psycopg2

BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from db.legacy import lineage as L  # noqa: E402
from db.legacy import upgrade as U  # noqa: E402
from scripts.db_preflight import redact  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert the legacy production database for the current release.")
    parser.add_argument("command", choices=("check", "prepare"))
    parser.add_argument("--dry-run", action="store_true", help="prepare: run the conversion and roll it back")
    parser.add_argument("--json", action="store_true", help="check: print the report as JSON")
    parser.add_argument("--acknowledge-data", action="append", default=[], metavar="FILE",
                        help="accept the rows a data check still finds for this migration file (repeatable)")
    parser.add_argument("--acknowledge-missing-seed", action="append", default=[], metavar="KEY",
                        help="accept that a seeded configuration row (identity as printed, 'seed|table|key') was deleted by an operator")
    args = parser.parse_args(argv)
    url = os.environ.get("LEGACY_UPGRADE_DATABASE_URL", "")
    if not url:
        print("LEGACY_UPGRADE_DATABASE_URL is not set", file=sys.stderr)
        return 2
    unknown = [f for f in args.acknowledge_data if f not in L.DATA_CHECK_FILES]
    if unknown:
        print(f"STOPPED: no data check exists for {unknown}", file=sys.stderr)
        return 2
    bad_seed = [k for k in args.acknowledge_missing_seed if not k.startswith("seed|")]
    if bad_seed:
        print(f"STOPPED: only seeded rows can be accepted as absent, not {bad_seed}", file=sys.stderr)
        return 2
    acknowledged = frozenset(args.acknowledge_data)
    accepted_seeds = frozenset(args.acknowledge_missing_seed)
    try:
        conn = psycopg2.connect(url.replace("postgresql+asyncpg://", "postgresql://"))
    except psycopg2.Error as exc:
        print(f"UNKNOWN: cannot connect: {redact(exc, url)}", file=sys.stderr)
        return 2
    try:
        if args.command == "check":
            assessment = U.assess(conn, acknowledged=acknowledged, accepted_seeds=accepted_seeds, for_prepare=False)
            print(U.to_json(assessment) if args.json else "\n".join(U.report_lines(assessment)))
            return 1 if assessment.blocking else 0
        try:
            result = U.prepare(conn, acknowledged=acknowledged, accepted_seeds=accepted_seeds, dry_run=args.dry_run)
        except (U.ConversionError, psycopg2.Error) as exc:
            print(f"STOPPED: {redact(exc, url)}", file=sys.stderr)
            print("Nothing was changed: the conversion runs in one transaction that was rolled back.", file=sys.stderr)
            return 1
        print("\n".join(U.report_lines(result.assessment)))
        print()
        print("\n".join(f"done: {s}" for s in result.steps))
        print(f"ledger rows: {result.ledger_rows}; evidence rows: {result.evidence_rows}")
        print("pending for scripts.migrate: " + ", ".join(result.pending))
        print("dry run: rolled back, nothing was written" if args.dry_run else "committed")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
