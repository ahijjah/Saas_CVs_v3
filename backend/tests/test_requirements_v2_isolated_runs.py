"""Runs the two requirements-v2 modules that import the REAL sqlalchemy and openai, in a fresh interpreter.

Why a subprocess: other modules of this suite replace `sqlalchemy` and `openai` with stand-ins while they are being collected (for example
test_duplicate_detection.py and test_intake_notifications.py), so these modules cannot be collected in the same process without breaking the
run. In the fresh interpreter nothing is stand-in: the real PostgreSQL engine, the real exception classes and the real services are used.
The modules are named isolated_*.py so that the main run does not collect them a second time.
"""
import pathlib
import re
import subprocess
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
MODULES = [
    BACKEND / "tests" / "isolated_requirements_v2_extraction_service.py",
    BACKEND / "tests" / "isolated_requirements_v2_extraction_postgres.py",
]


def test_the_real_dependency_modules_pass_in_a_fresh_interpreter():
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-W", "ignore", *map(str, MODULES)],
                          capture_output=True, text=True, timeout=1500, cwd=str(BACKEND))
    summary = (proc.stdout.strip().splitlines() or ["(no output)"])[-1]
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-2000:]
    assert re.search(r"\d+ passed", summary) and not re.search(r"failed|error|skipped", summary), summary
