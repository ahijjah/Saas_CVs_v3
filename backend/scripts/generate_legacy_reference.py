"""Generate db/legacy/reference.json: what each migration of the legacy production lineage produces, read from a clean build.

    cd backend && python -m scripts.generate_legacy_reference > db/legacy/reference.json

Two clean builds on a disposable PostgreSQL 16 cluster (tests/realdb_helper.py), never a shared database:

  base    db/schema.sql and the migration manifest up to and including 092 (replacements 095, 096 and 097 run at the position of
          the originals 010, 070 and 087, as the manifest says). After every file, the catalogue fingerprint is taken, and the
          identities the file added or changed are recorded.
  legacy  the sixteen legacy production migrations 093 to 108 (db/legacy/lineage_63f3bd7/migrations, byte-identical to the files at
          production source commit 63f3bd7) applied on top of the base build, in order, with the same per-file record.

The output is deterministic. A test regenerates it and fails when the committed file differs, so the reference cannot drift from the
files it describes."""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import uuid

import psycopg2

BACKEND = pathlib.Path(__file__).resolve().parents[1]
for extra in (BACKEND, BACKEND / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import realdb_helper as H  # noqa: E402
from db.legacy import fingerprint as F  # noqa: E402
from db.runner import runner  # noqa: E402
from db.runner.manifest import MANIFEST  # noqa: E402

LEGACY_DIR = BACKEND / "db" / "legacy" / "lineage_63f3bd7"
LEGACY_COMMIT = "63f3bd7a3932d3c335d679cecf615ad592bd8423"
BASE_CUT = "093_requirements_v2_storage_schema.sql"      # the first file of the target chain that production does not have


def _delta(before: dict[str, str], after: dict[str, str]) -> dict:
    return {"set": {k: v for k, v in sorted(after.items()) if before.get(k) != v},
            "removed": sorted(k for k in before if k not in after)}


def generate() -> dict:
    cluster = H.Cluster()
    try:
        name = "ref_" + uuid.uuid4().hex[:10]
        admin = psycopg2.connect(cluster.dsn())
        admin.autocommit = True
        admin.cursor().execute(f"CREATE DATABASE {name} ENCODING 'UTF8' TEMPLATE template0")
        conn = psycopg2.connect(cluster.dsn(name))
        conn.autocommit = True
        conn.set_client_encoding("UTF8")
        cur = conn.cursor()
        cur.execute(H.schema_sql())
        cur.execute("SET search_path = cv_analyzer, public")
        schema_snapshot = F.snapshot(cur)

        files = [e.file for e in MANIFEST]
        cut = files.index(BASE_CUT)
        previous = schema_snapshot
        base = [{"file": "schema.sql", "superseded_by": None, "replaces": None, **_delta({}, schema_snapshot)}]
        for entry in MANIFEST[:cut]:
            nxt = files[files.index(entry.file) + 1]
            runner.run(conn, allow_fresh=True, stop_before=nxt) if entry.file == files[0] else runner.run(conn, stop_before=nxt)
            cur.execute("SET search_path = cv_analyzer, public")
            now = F.snapshot(cur)
            base.append({"file": entry.file, "superseded_by": entry.superseded_by, "replaces": entry.replaces, **_delta(previous, now)})
            previous = now

        legacy = []
        for path in sorted((LEGACY_DIR / "migrations").glob("*.sql")):
            cur.execute(path.read_text(encoding="utf-8"))
            cur.execute("SET search_path = cv_analyzer, public")
            now = F.snapshot(cur)
            legacy.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), **_delta(previous, now)})
            previous = now
        conn.close()
        admin.cursor().execute(f"DROP DATABASE {name} WITH (FORCE)")

        # The schema.sql of the legacy lineage against the schema.sql of this release: the structure must be the same, so the
        # base reference describes production's schema as well as main's.
        old = "oldschema_" + uuid.uuid4().hex[:10]
        admin.cursor().execute(f"CREATE DATABASE {old} ENCODING 'UTF8' TEMPLATE template0")
        oc = psycopg2.connect(cluster.dsn(old))
        oc.autocommit = True
        oc.set_client_encoding("UTF8")
        lines = (LEGACY_DIR / "schema.sql").read_text(encoding="utf-8").split("\n")
        import re
        for n in range(0, 96):
            lines[n] = re.sub(r"\s+REFERENCES users \(user_id\) ON DELETE SET NULL", "", lines[n])
        oc.cursor().execute("\n".join(lines))
        old_snapshot = F.snapshot(oc.cursor())
        oc.close()
        admin.cursor().execute(f"DROP DATABASE {old} WITH (FORCE)")
        admin.close()
        missing, different, extra = F.diff(schema_snapshot, old_snapshot)
        schema_equivalence = {"missing_in_legacy": missing, "different": different, "extra_in_legacy": extra}
    finally:
        cluster.stop()
    return {
        "legacy_source_commit": LEGACY_COMMIT,
        "legacy_schema_sha256": hashlib.sha256((LEGACY_DIR / "schema.sql").read_bytes()).hexdigest(),
        "base_cut": BASE_CUT,
        "schema_equivalence": schema_equivalence,
        "base": base,
        "legacy": legacy,
    }


def main() -> int:
    reason = H.unavailable_reason()
    if reason:
        print(reason, file=sys.stderr)
        return 1
    json.dump(generate(), sys.stdout, indent=1, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
