"""Fixtures for the browser E2E suite. Run it with ./e2e/run.sh from the repository root.

Session scope: one disposable PostgreSQL 16 cluster, one backend process and one frontend process, and one Chromium. Every process
group and the cluster are stopped at the end of the session, also when a test or setup fails. Per test: one UI session that keeps a
Playwright trace for each browser context; the traces and screenshots of a failed test are kept in e2e/artifacts/."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"
for _path in (REPO, BACKEND, BACKEND / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from e2e.support import ui as ui_support  # noqa: E402
from e2e.support.api import Api  # noqa: E402
from e2e.support.stack import running_stack  # noqa: E402

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
CHROMIUM = Path(os.environ.get("E2E_CHROMIUM", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed:
        item.session.e2e_failed = True
    if report.when == "call":
        item._e2e_failed = report.failed


@pytest.fixture(scope="session")
def run_dir():
    path = Path(tempfile.mkdtemp(prefix="e2e_run_"))
    try:
        yield path
    finally:
        if os.environ.get("E2E_KEEP_LOGS") != "1":
            shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(scope="session")
def stack(run_dir, request):
    with running_stack(run_dir) as running:
        yield running
    if getattr(request.session, "e2e_failed", False):
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        for log in running.logs:
            if log.exists():
                shutil.copy(log, ARTIFACTS / log.name)


@pytest.fixture(scope="session")
def api(stack):
    return Api(stack)


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        launch = {"args": ["--no-sandbox"]}
        if CHROMIUM.exists():
            launch["executable_path"] = str(CHROMIUM)
        chromium = playwright.chromium.launch(**launch)
        try:
            yield chromium
        finally:
            chromium.close()


@pytest.fixture
def ui(browser, stack, api, request):
    session = ui_support.UiSession(browser, stack, ARTIFACTS, request.node.name.split("[")[0], api)
    try:
        yield session
    finally:
        session.finish(failed=getattr(request.node, "_e2e_failed", False))


def pytest_sessionfinish(session, exitstatus):
    if ui_support.CHECKS:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        (ARTIFACTS / "checks.txt").write_text("\n".join(ui_support.CHECKS) + "\n", encoding="utf-8")
