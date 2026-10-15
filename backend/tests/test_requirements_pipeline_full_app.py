"""Full-application verification of the requirements-v2 editor (real FastAPI app + real SPA JobDetails page + disposable PostgreSQL 16 + Chromium; see
../tests/requirementsV2/browser/verify_full_app.py). Runs the script in its own process; SKIPS (never passes silently) when the system PostgreSQL 16 binaries,
runuser, Playwright + Chromium or the node tooling (`npm install --no-save tailwindcss@3`) are missing."""
import pathlib
import subprocess
import sys

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "tests" / "requirementsV2" / "browser" / "verify_full_app.py"


def test_editor_in_the_full_application(tmp_path):
    proc = subprocess.run([sys.executable, str(SCRIPT), "--out", str(tmp_path)], cwd=SCRIPT.parents[3] / "backend", capture_output=True, text=True, timeout=1500)
    if proc.returncode == 77:
        pytest.skip(proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "prerequisites missing")
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-2000:]
    report = (tmp_path / "REPORT.md").read_text(encoding="utf-8")
    assert "checks passed" in report and "**FAIL**" not in report
