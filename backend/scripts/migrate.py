"""Migration runner entrypoint.

    python -m scripts.migrate --dry-run          # print the pending order; writes nothing
    python -m scripts.migrate                    # apply pending migrations
    python -m scripts.migrate --init-fresh       # apply to an empty database and create the ledger

The connection string comes from MIGRATION_DATABASE_URL, which must name the migration operator login. It is never read
from the application's DATABASE_URL, and this script never prints it. Exit status is 0 on success and 1 when the runner
stops; the reason is printed to stderr."""
from __future__ import annotations

import argparse
import os
import pathlib
import sys

import psycopg2

BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from db.runner import runner  # noqa: E402


def _dsn() -> str:
    url = os.environ.get("MIGRATION_DATABASE_URL", "")
    if not url:
        raise SystemExit("MIGRATION_DATABASE_URL is not set")
    return url.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg2://", "postgresql://")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply cv_analyzer migrations through the ledger-backed runner.")
    parser.add_argument("--dry-run", action="store_true", help="print the pending order without writing")
    parser.add_argument("--init-fresh", action="store_true",
                        help="permit creating the ledger on a database with no migration objects")
    args = parser.parse_args(argv)

    conn = psycopg2.connect(_dsn())
    try:
        report = runner.run(conn, dry_run=args.dry_run, allow_fresh=args.init_fresh,
                            owner_role=os.environ.get("MIGRATION_OWNER_ROLE", "cv_migrator"))
    except runner.MigrationError as exc:
        print(f"STOPPED: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    for action in report.actions:
        print(f"{action.kind:<16} {action.file}  ({action.reason})")
    if report.dry_run:
        print(f"dry run: {len(report.pending())} file(s) would be applied; nothing was written")
    else:
        print(f"applied {len(report.applied)} file(s); recorded {len(report.superseded)} superseded original(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
