"""Entry point of the backend under test, started as its own process by the E2E stack.

Environment (set by stack.py): DATABASE_URL, DB_SCHEMA, JWT_SECRET. Arguments: --port, --origin (the frontend origin allowed by CORS),
--files-root. The real application runs; only the external services in fakes.py are replaced."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for _path in (REPO / "backend", REPO):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--files-root", required=True)
    args = parser.parse_args()
    os.environ.update({
        "CORS_ORIGINS": json.dumps([args.origin]),
        "APP_BASE_URL": args.origin,
        "API_BASE_URL": f"http://127.0.0.1:{args.port}",
    })
    from e2e.support.fakes import patch_external
    patch_external(args.files_root)
    import uvicorn
    from main import app
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
