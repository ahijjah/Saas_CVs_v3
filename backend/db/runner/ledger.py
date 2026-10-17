"""Migration ledger: one row per migration file that the runner has classified.

The ledger is owned by the migration owner role. Runtime roles receive no privilege on it. Its DDL is also reproduced in
db/ops/0001_roles_and_ledger.sql.template; tests assert the two stay identical."""
from __future__ import annotations

LEDGER_TABLE = "cv_analyzer.schema_migrations"

LEDGER_DDL = """
CREATE TABLE cv_analyzer.schema_migrations (
    filename    text PRIMARY KEY,
    sha256      text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    status      text NOT NULL CHECK (status IN ('applied', 'superseded')),
    replaced_by text,
    applied_at  timestamptz NOT NULL DEFAULT now(),
    applied_by  text NOT NULL DEFAULT session_user,
    CONSTRAINT schema_migrations_replacement CHECK ((status = 'superseded') = (replaced_by IS NOT NULL))
)
"""

LEDGER_READ = "SELECT filename, sha256, status, replaced_by FROM cv_analyzer.schema_migrations ORDER BY filename"
