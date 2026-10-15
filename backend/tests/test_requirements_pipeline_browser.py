"""Real-page verification of the requirements-v2 editor (see ../tests/requirementsV2/browser/verify_ui.py): the real editor component in Chromium against the real
API and a disposable PostgreSQL. Runs the script in its own process; SKIPS (does not pass silently) when pgserver, playwright + Chromium, fastapi/uvicorn or the
node tooling (vite, tailwindcss@3 via `npm install --no-save`) are missing."""
import pathlib
import subprocess
import sys

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "tests" / "requirementsV2" / "browser" / "verify_ui.py"


def test_editor_against_the_real_api_in_a_real_browser(tmp_path):
    proc = subprocess.run([sys.executable, str(SCRIPT), "--out", str(tmp_path)], cwd=SCRIPT.parents[3] / "backend", capture_output=True, text=True, timeout=900)
    if proc.returncode == 77:
        pytest.skip(proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "prerequisites missing")
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-2000:]
    report = (tmp_path / "REPORT.md").read_text(encoding="utf-8")
    assert "checks passed" in report and "**FAIL**" not in report
