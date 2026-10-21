"""Browser helpers: the real UI (Vite) with its API calls routed to the live backend, traces and failure artifacts."""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import Page, expect

from e2e.support.api import FIXTURE

CHECKS: list[str] = []          # every named check that passed, in order; written to artifacts/checks.txt at session end


def check(name: str) -> None:
    CHECKS.append(name)


class UiSession:
    """Opens pages as a given user. Every context records a Playwright trace; on failure the traces and screenshots are kept."""

    def __init__(self, browser, stack, artifacts: Path, test_name: str, api):
        self.browser, self.stack, self.artifacts, self.test_name, self.api = browser, stack, artifacts, test_name, api
        self._contexts: list = []

    def open(self, role: str, job: str, *, lang: str = "en", token_user=None, path: str | None = None) -> Page:
        token, user = token_user or self.api.login(role)
        context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        context.tracing.start(screenshots=True, snapshots=True)
        self._contexts.append(context)
        context.add_init_script(f"localStorage.setItem('token', {json.dumps(token)});"
                                f"localStorage.setItem('user', {json.dumps(json.dumps(user))});"
                                f"localStorage.setItem('app_lang', '{lang}');")
        api_base = self.api.base
        ui_api = self.stack.ui_api_origin
        context.route(f"{ui_api}/**", lambda route, request: route.continue_(url=request.url.replace(ui_api, api_base)))
        page = context.new_page()
        page.goto(f"{self.stack.frontend_base}{path or f'/jobs/{job}'}")
        return page

    def finish(self, failed: bool) -> None:
        self.artifacts.mkdir(parents=True, exist_ok=True)
        for index, context in enumerate(self._contexts):
            name = f"{self.test_name}-{index}"
            if failed:
                for page_index, page in enumerate(context.pages):
                    try:
                        page.screenshot(path=str(self.artifacts / f"{name}-page{page_index}.png"), full_page=True)
                    except Exception:  # noqa: BLE001 - a page that already closed has no screenshot; the trace still exists
                        pass
                context.tracing.stop(path=str(self.artifacts / f"{name}.zip"))
            else:
                context.tracing.stop()
            context.close()
        self._contexts.clear()


def panel(page: Page):
    return page.locator("section[aria-labelledby='requirements-heading']")


def readiness(page: Page) -> str:
    return panel(page).get_by_test_id("readiness-state").inner_text()


def wait_revision(page: Page, revision: int) -> None:
    expect(panel(page).locator("header").get_by_text(f"Revision {revision}")).to_be_visible(timeout=15000)


def upload_cv_through_ui(page: Page) -> None:
    """The candidate upload the recruiter performs: the page's own file input, then the success message."""
    page.get_by_text("Manual Upload", exact=True).first.click()
    page.set_input_files("#cv-file-input", str(FIXTURE))
    expect(page.get_by_text("CV(s) uploaded successfully.")).to_be_visible(timeout=15000)
