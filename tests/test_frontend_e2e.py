"""Browser tests that drive the FLITS interface end to end.

The other frontend tests assert that certain strings appear in app.js. That
breaks on any refactor and proves nothing about what the interface does. These
tests start a real server, open a real browser, and work through the interface
the way a user does.

Skipped unless Playwright and its browser are installed:

    python -m pip install pytest-playwright
    playwright install chromium
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

playwright_api = pytest.importorskip("playwright.sync_api", reason="Playwright is required for the browser tests")

sync_playwright = playwright_api.sync_playwright
expect = playwright_api.expect


pytestmark = pytest.mark.e2e


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture(scope="function")
def flits_server(monkeypatch, synthetic_waterfall) -> str:
    """Run a real FLITS server against a directory holding the synthetic burst."""
    import uvicorn

    monkeypatch.setenv("FLITS_DATA_DIR", str(synthetic_waterfall.path.parent))
    monkeypatch.delenv("FLITS_CORS_ORIGINS", raising=False)

    import importlib

    app_module = importlib.import_module("flits.web.app")
    app_module.SESSIONS.clear()

    port = _free_port()
    config = uvicorn.Config(app_module.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 30
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("FLITS server did not start in time")
        time.sleep(0.05)

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        app_module.SESSIONS.clear()


@pytest.fixture(scope="function")
def page(flits_server: str):
    """A browser page with the FLITS interface already open."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = context.new_page()

        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))

        page.goto(flits_server, wait_until="networkidle")
        page.errors = errors  # type: ignore[attr-defined]

        yield page

        browser.close()


def _load_session(page, waterfall) -> None:
    """Work through the load panel the way a user does."""
    page.select_option("#fileSelect", label=waterfall.path.name)
    page.fill("#dmInput", "0")

    # SEFD lives under the collapsed "Acquisition Overrides" section.
    page.evaluate("document.querySelector('#acquisitionOverridesDetails').open = true")
    page.fill("#sefdInput", "10")

    page.click("#loadButton")
    # The viewer only appears once the session exists and the waterfall renders.
    page.wait_for_selector("#viewerPlot.js-plotly-plot", timeout=60_000)


def test_interface_loads_without_javascript_errors(page) -> None:
    expect(page.locator("#loadButton")).to_be_visible()
    assert page.errors == [], f"page raised JavaScript errors: {page.errors}"


def test_file_listing_offers_the_burst(page, synthetic_waterfall) -> None:
    options = page.locator("#fileSelect option").all_text_contents()
    assert any(synthetic_waterfall.path.name in option for option in options)


def test_loading_a_burst_renders_the_waterfall(page, synthetic_waterfall) -> None:
    _load_session(page, synthetic_waterfall)

    expect(page.locator("#viewerPlot.js-plotly-plot")).to_be_visible()
    expect(page.locator("#burstTitle")).to_contain_text(synthetic_waterfall.path.stem[:8])
    assert page.errors == [], f"page raised JavaScript errors: {page.errors}"


def test_analysis_tabs_switch_panels(page, synthetic_waterfall) -> None:
    _load_session(page, synthetic_waterfall)

    for tab, panel in (
        ("#analysisDmTab", "#analysisDmPanel"),
        ("#analysisTemporalTab", "#analysisTemporalPanel"),
        ("#analysisExportTab", "#analysisExportPanel"),
        ("#analysisPrepareTab", "#analysisPreparePanel"),
    ):
        page.click(tab)
        expect(page.locator(panel)).to_be_visible()

    assert page.errors == [], f"page raised JavaScript errors: {page.errors}"


def test_auto_localize_then_measure_reports_results(page, synthetic_waterfall) -> None:
    """The core workflow: localize the burst, measure it, see numbers come back."""
    _load_session(page, synthetic_waterfall)

    page.click("#autoLocalizeButton")
    page.wait_for_timeout(1_000)

    page.click("#computeButton")

    # Measurements land in the results panel; wait for it to be populated.
    results = page.locator("#resultsContent")
    expect(results).not_to_be_empty(timeout=60_000)
    expect(results).to_contain_text("S/N", timeout=60_000)

    assert page.errors == [], f"page raised JavaScript errors: {page.errors}"


def test_export_panel_can_build_a_bundle(page, synthetic_waterfall) -> None:
    _load_session(page, synthetic_waterfall)

    page.click("#autoLocalizeButton")
    page.wait_for_timeout(500)
    page.click("#computeButton")
    page.wait_for_timeout(1_500)

    page.click("#analysisExportTab")
    expect(page.locator("#analysisExportPanel")).to_be_visible()

    page.click("#buildExportButton")
    page.wait_for_selector("#exportManifestContent", timeout=60_000)

    expect(page.locator("#exportManifestContent")).not_to_be_empty()
    assert page.errors == [], f"page raised JavaScript errors: {page.errors}"
