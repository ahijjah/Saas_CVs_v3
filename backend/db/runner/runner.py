"""Deterministic, forward-only migration runner for the cv_analyzer chain.

Rules, all enforced before any migration runs unless stated otherwise:

* The manifest (db/runner/manifest.py) lists every file under db/migrations exactly once, in execution order.
* A superseded original is never executed. Its replacement runs at the original's position and, in the same transaction,
  records the original as `superseded` with replaced_by set to the replacement.
* Each file runs in its own transaction, together with its ledger row. Any SQL error rolls that transaction back and stops
  the run. A file whose transaction failed is never recorded.
* A recorded file whose bytes differ from the recorded SHA-256 stops the run.
* A superseded original whose replacement is not recorded as applied stops the run.
* The database state is classified explicitly. A missing ledger is accepted only for a database with none of the
  migration-created evidence tables, and only when the caller asks for fresh initialisation. Anything else raises
  UnknownBaseline. A ledger gap (a pending file followed by a recorded one) also raises UnknownBaseline.
* Apply mode holds a session advisory lock for the whole run. A second runner gets LockHeld and changes nothing.
* Dry-run runs every read inside a READ ONLY transaction and never creates the ledger or takes the lock.
"""
from __future__ import annotations

import hashlib
import pathlib
import re
from dataclasses import dataclass, field

import psycopg2
from psycopg2 import sql

from .ledger import LEDGER_DDL, LEDGER_READ, LEDGER_TABLE
from .manifest import MANIFEST, Entry
from .sqltext import SqlTextError, migration_body

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[1] / "migrations"

# Key of the session advisory lock held by an apply-mode run. Two runners on one database cannot both hold it.
LOCK_KEY = 0x43564D49

# Tables created by migrations (not by schema.sql). Their presence without a ledger means the state is unknown.
FRESH_EVIDENCE_TABLES = ("ai_model_registry", "application_intake_log", "application_workflow_history", "job_campaigns")


class MigrationError(RuntimeError):
    def __init__(self, message: str, file: str | None = None):
        super().__init__(message)
        self.file = file


class ManifestError(MigrationError):
    pass


class ChecksumMismatch(MigrationError):
    pass


class MissingReplacement(MigrationError):
    pass


class UnknownBaseline(MigrationError):
    pass


class LockHeld(MigrationError):
    pass


@dataclass(frozen=True)
class Action:
    file: str
    kind: str       # 'apply' | 'skip-applied' | 'skip-superseded' | 'skip-replaced'
    reason: str


@dataclass
class Report:
    dry_run: bool
    actions: list[Action] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)
    superseded: list[str] = field(default_factory=list)

    def pending(self) -> list[str]:
        return [a.file for a in self.actions if a.kind == "apply"]


@dataclass(frozen=True)
class _Row:
    sha256: str
    status: str
    replaced_by: str | None


def sha256_of(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_manifest(entries: tuple[Entry, ...], directory: pathlib.Path) -> None:
    files = [e.file for e in entries]
    if len(set(files)) != len(files):
        raise ManifestError("manifest lists a file more than once")
    on_disk = sorted(p.name for p in directory.glob("*.sql"))
    if sorted(files) != on_disk:
        missing = sorted(set(on_disk) - set(files))
        extra = sorted(set(files) - set(on_disk))
        raise ManifestError(f"manifest does not match the directory: not in manifest {missing}, not on disk {extra}")
    for i, e in enumerate(entries):
        if e.replaces and e.superseded_by:
            raise ManifestError(f"{e.file} cannot both replace and be superseded")
        if e.superseded_by:
            nxt = entries[i + 1] if i + 1 < len(entries) else None
            if nxt is None or nxt.file != e.superseded_by or nxt.replaces != e.file:
                raise ManifestError(f"{e.superseded_by} must be listed immediately after {e.file}")
        if e.replaces:
            prev = entries[i - 1] if i > 0 else None
            if prev is None or prev.file != e.replaces or prev.superseded_by != e.file:
                raise ManifestError(f"{e.file} must be listed immediately after the file it replaces")


def _read_state(cur) -> tuple[bool, dict[str, _Row], bool]:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (LEDGER_TABLE,))
    ledger_exists = cur.fetchone()[0]
    rows: dict[str, _Row] = {}
    if ledger_exists:
        cur.execute(LEDGER_READ)
        for filename, sha256, status, replaced_by in cur.fetchall():
            rows[filename] = _Row(sha256=sha256, status=status, replaced_by=replaced_by)
    cur.execute(
        "SELECT count(*) FROM unnest(%s::text[]) AS t(name) WHERE to_regclass('cv_analyzer.' || t.name) IS NOT NULL",
        (list(FRESH_EVIDENCE_TABLES),),
    )
    evidence = cur.fetchone()[0] > 0
    return ledger_exists, rows, evidence


def _classify_state(ledger_exists: bool, rows: dict[str, _Row], evidence: bool, allow_fresh: bool) -> None:
    if not ledger_exists:
        if evidence:
            raise UnknownBaseline("migration objects exist but the ledger is absent: an explicit baseline is required")
        if not allow_fresh:
            raise UnknownBaseline("ledger is absent: run the ops bootstrap, or use fresh initialisation on an empty database")
    elif not rows and evidence:
        raise UnknownBaseline("the ledger is empty but migration objects exist: an explicit baseline is required")


def _check_rows(rows: dict[str, _Row], shas: dict[str, str]) -> None:
    for filename, row in rows.items():
        if filename not in shas:
            raise ManifestError(f"ledger records {filename}, which is not in the repository", file=filename)
        if row.sha256 != shas[filename]:
            raise ChecksumMismatch(f"{filename}: recorded sha256 differs from the file on disk", file=filename)
        if row.status == "superseded":
            replacement = rows.get(row.replaced_by or "")
            if replacement is None or replacement.status != "applied":
                raise MissingReplacement(
                    f"{filename} is superseded but its replacement {row.replaced_by} is not recorded as applied",
                    file=filename,
                )
        if row.status not in ("applied", "superseded"):
            raise UnknownBaseline(f"{filename}: unknown ledger status {row.status!r}", file=filename)


def _plan(entries: tuple[Entry, ...], rows: dict[str, _Row]) -> list[Action]:
    actions: list[Action] = []
    for e in entries:
        row = rows.get(e.file)
        if e.superseded_by:
            if row is None:
                actions.append(Action(e.file, "skip-replaced", f"replaced by {e.superseded_by}"))
            elif row.status == "applied":
                actions.append(Action(e.file, "skip-applied", "recorded applied before its replacement"))
            else:
                actions.append(Action(e.file, "skip-superseded", f"superseded by {e.superseded_by}"))
        elif e.replaces:
            if row is not None and row.status == "applied":
                actions.append(Action(e.file, "skip-applied", "replacement already applied"))
            elif row is not None:
                raise ManifestError(f"{e.file} is a replacement recorded as {row.status}", file=e.file)
            else:
                actions.append(Action(e.file, "apply", f"replaces {e.replaces}"))
        else:
            if row is None:
                actions.append(Action(e.file, "apply", "pending"))
            elif row.status == "applied":
                actions.append(Action(e.file, "skip-applied", "recorded applied"))
            else:
                raise MissingReplacement(f"{e.file} is recorded superseded but has no replacement entry", file=e.file)
    _check_gap(actions)
    return actions


def _check_gap(actions: list[Action]) -> None:
    first_pending: str | None = None
    for a in actions:
        if a.kind == "apply" and first_pending is None:
            first_pending = a.file
        elif first_pending is not None and a.kind in ("skip-applied", "skip-superseded"):
            raise UnknownBaseline(
                f"ledger gap: {a.file} is recorded but {first_pending} before it is pending",
                file=a.file,
            )


def _plan_run(cur, entries, directory, shas, allow_fresh):
    ledger_exists, rows, evidence = _read_state(cur)
    _classify_state(ledger_exists, rows, evidence, allow_fresh)
    _check_rows(rows, shas)
    actions = _plan(entries, rows)
    return ledger_exists, rows, actions


def _shas(directory: pathlib.Path, entries: tuple[Entry, ...]) -> dict[str, str]:
    return {e.file: sha256_of(directory / e.file) for e in entries}


_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _assume_owner(cur, owner_role: str | None) -> None:
    """The operator logs in as cv_migration_operator and assumes the owner role for the whole run. The name is validated
    before it is used, and the identifier is quoted by psycopg2."""
    if owner_role is None:
        return
    if not _ROLE_NAME.match(owner_role):
        raise ManifestError(f"owner role name {owner_role!r} is not a plain role identifier")
    cur.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(owner_role)))


def run(conn, *, dry_run: bool = False, allow_fresh: bool = False, directory: pathlib.Path = MIGRATIONS_DIR,
        entries: tuple[Entry, ...] = MANIFEST, stop_before: str | None = None,
        owner_role: str | None = None) -> Report:
    """Apply pending migrations, or with dry_run describe them without writing anything.

    `owner_role`, when given, is assumed with SET ROLE before any read or write, so created objects are owned by it.
    `stop_before` truncates the plan before the named file. It exists for tests that need an intermediate state."""
    validate_manifest(entries, directory)
    shas = _shas(directory, entries)
    report = Report(dry_run=dry_run)
    cur = conn.cursor()
    if dry_run:
        conn.autocommit = False
        try:
            cur.execute("SET TRANSACTION READ ONLY")
            try:
                _assume_owner(cur, owner_role)
            except psycopg2.Error as exc:
                raise MigrationError(f"could not assume owner role {owner_role}: {str(exc).splitlines()[0]}") from exc
            _, _, actions = _plan_run(cur, entries, directory, shas, allow_fresh)
        finally:
            conn.rollback()
            conn.autocommit = True
        report.actions = _truncate(actions, stop_before)
        return report

    conn.autocommit = True
    try:
        _assume_owner(cur, owner_role)
    except psycopg2.Error as exc:
        raise MigrationError(f"could not assume owner role {owner_role}: {str(exc).splitlines()[0]}") from exc
    cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,))
    if not cur.fetchone()[0]:
        raise LockHeld("another migration run holds the advisory lock; nothing was changed")
    try:
        conn.autocommit = False
        ledger_exists, rows, actions = _plan_run(cur, entries, directory, shas, allow_fresh)
        conn.rollback()
        actions = _truncate(actions, stop_before)
        report.actions = actions
        if not ledger_exists:
            conn.autocommit = False
            cur.execute(LEDGER_DDL)
            conn.commit()
        _apply(conn, cur, entries, directory, shas, rows, actions, report)
    finally:
        if conn.autocommit is False:
            conn.rollback()
        conn.autocommit = True
        cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
    return report


def _truncate(actions: list[Action], stop_before: str | None) -> list[Action]:
    if stop_before is None:
        return actions
    names = [a.file for a in actions]
    if stop_before not in names:
        raise ManifestError(f"stop_before {stop_before} is not in the manifest")
    return actions[: names.index(stop_before)]


def _apply(conn, cur, entries, directory, shas, rows, actions, report) -> None:
    by_file = {e.file: e for e in entries}
    for action in actions:
        if action.kind != "apply":
            continue
        entry = by_file[action.file]
        raw = (directory / entry.file).read_bytes()
        try:
            body = migration_body(raw.decode("utf-8"))
        except SqlTextError as exc:
            raise MigrationError(f"{entry.file}: {exc}", file=entry.file) from exc
        conn.autocommit = False
        try:
            cur.execute(body.sql)
            if entry.replaces and entry.replaces not in rows:
                cur.execute(
                    "INSERT INTO cv_analyzer.schema_migrations (filename, sha256, status, replaced_by, applied_by) "
                    "VALUES (%s, %s, 'superseded', %s, session_user)",
                    (entry.replaces, shas[entry.replaces], entry.file),
                )
                report.superseded.append(entry.replaces)
            cur.execute(
                "INSERT INTO cv_analyzer.schema_migrations (filename, sha256, status, replaced_by, applied_by) "
                "VALUES (%s, %s, 'applied', NULL, session_user)",
                (entry.file, shas[entry.file]),
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise MigrationError(f"{entry.file} failed and was rolled back: {str(exc).splitlines()[0]}",
                                 file=entry.file) from exc
        report.applied.append(entry.file)
        rows[entry.file] = _Row(sha256=shas[entry.file], status="applied", replaced_by=None)
        if entry.replaces:
            rows[entry.replaces] = _Row(sha256=shas[entry.replaces], status="superseded", replaced_by=entry.file)
    conn.autocommit = True
