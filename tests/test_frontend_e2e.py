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
    # The listing is fetched after load, so let the assertion retry rather than
    # racing the request on a slow runner.
    expect(page.locator("#fileSelect")).to_contain_text(synthetic_waterfall.path.name, timeout=30_000)


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
        ("#analysisDriftTab", "#analysisDriftPanel"),
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


def _apply_exact_selection(page, mode: str, start_ms: float, end_ms: float) -> None:
    """Set a selection through the keyboard-accessible controls.

    Clicking coordinates in the plot is what a user does, but it makes the
    selection depend on the rendered geometry. These tests need an exact,
    repeatable event window and off-pulse span, so they use the numeric path.
    """
    page.evaluate("document.querySelector('#exactSelectionDetails').open = true")
    page.select_option("#exactSelectionMode", mode)
    page.fill("#exactStartInput", str(start_ms))
    page.fill("#exactEndInput", str(end_ms))
    page.click("#applyExactSelectionButton")
    page.wait_for_timeout(500)


def test_measuring_the_sub_burst_drift_rate(drifting_waterfall, page) -> None:
    """The whole point of #94: a drifting burst in, MHz/ms out, with the DM caveat.

    `drifting_waterfall` is requested before `page` so the file exists in the
    served directory before the interface fetches its listing.
    """
    _load_session(page, drifting_waterfall)

    centre_ms = drifting_waterfall.centre_time_ms
    span_ms = drifting_waterfall.ntime * drifting_waterfall.tsamp_s * 1e3
    _apply_exact_selection(page, "event", centre_ms - 12.0, centre_ms + 12.0)
    _apply_exact_selection(page, "offpulse", 2.0, centre_ms - 25.0)
    _apply_exact_selection(page, "offpulse", centre_ms + 25.0, span_ms - 2.0)

    page.click("#analysisDriftTab")
    expect(page.locator("#analysisDriftPanel")).to_be_visible()

    page.fill("#driftTrialsInput", "8")
    page.click("#runDriftButton")

    content = page.locator("#driftContent")
    expect(content).to_contain_text("Drift Rate", timeout=90_000)
    expect(content).to_contain_text("MHz/ms")
    # No DM uncertainty was supplied, so the bar must stay statistical-only.
    expect(content).to_contain_text("Statistical only")
    expect(content).to_contain_text("Missing DM uncertainty")
    expect(content).to_contain_text("Equivalent DM Error")
    expect(page.locator("#driftAcfPlot")).to_be_visible()

    tile = page.locator('.result-tile:has(.results-label:text-matches("^Drift Rate")) strong').first
    measured = float(tile.inner_text().split()[0])
    assert measured == pytest.approx(drifting_waterfall.drift_mhz_per_ms, rel=0.25), measured

    # Supplying a DM uncertainty is what promotes the measurement.
    page.fill("#driftDmUncertaintyInput", "0.05")
    page.click("#runDriftButton")
    expect(content).to_contain_text("Formal 1σ", timeout=90_000)
    expect(content).not_to_contain_text("Missing DM uncertainty")

    # The result must survive leaving the tab and coming back.
    page.click("#analysisPrepareTab")
    page.click("#analysisDriftTab")
    expect(content).to_contain_text("Drift Rate")
    expect(page.locator("#driftAcfPlot")).to_be_visible()

    assert page.errors == [], f"page raised JavaScript errors: {page.errors}"


def test_polarization_panel_explains_a_stokes_i_only_burst(page, synthetic_waterfall) -> None:
    _load_session(page, synthetic_waterfall)

    page.click("#analysisPolarizationTab")
    expect(page.locator("#analysisPolarizationPanel")).to_be_visible()

    status = page.locator("#sessionPolarizationStatus")
    expect(status).to_contain_text("fewer than four polarization products", timeout=30_000)
    expect(page.locator("#sessionPolRunButton")).to_be_disabled()

    assert page.errors == [], f"page raised JavaScript errors: {page.errors}"


def test_measuring_the_rotation_measure_from_the_session(full_stokes_waterfall, page) -> None:
    """The whole point of #92: file in, Faraday depth out, without leaving FLITS.

    `full_stokes_waterfall` is requested before `page` so the file exists in the
    served directory before the interface fetches its listing.
    """
    page.select_option("#fileSelect", label=full_stokes_waterfall.path.name)
    # Preset detection is asynchronous, and the NRT preset is what establishes
    # the polarization basis for a filterbank. Let it land before loading.
    expect(page.locator("#telescopeInput")).to_have_value("nrt", timeout=30_000)
    _load_session(page, full_stokes_waterfall)

    burst_ms = full_stokes_waterfall.burst_time_idx * full_stokes_waterfall.tsamp_s * 1e3
    span_ms = full_stokes_waterfall.ntime * full_stokes_waterfall.tsamp_s * 1e3
    _apply_exact_selection(page, "event", burst_ms - 10.0, burst_ms + 10.0)
    _apply_exact_selection(page, "offpulse", 10.0, burst_ms - 60.0)
    _apply_exact_selection(page, "offpulse", burst_ms + 60.0, span_ms - 10.0)

    page.click("#analysisPolarizationTab")
    expect(page.locator("#analysisPolarizationPanel")).to_be_visible()

    status = page.locator("#sessionPolarizationStatus")
    expect(status).to_contain_text("4 polarization products", timeout=30_000)
    expect(status).to_contain_text("coherency linear")

    page.fill("#sessionPolMinSnrInput", "3")
    page.click("#sessionPolRunButton")

    content = page.locator("#rmContent")
    expect(content).to_contain_text("Peak Faraday Depth", timeout=60_000)
    expect(content).to_contain_text("Measured from the session")
    expect(content).to_contain_text("calibration is unconfirmed")
    expect(page.locator("#rmPlot")).to_be_visible()

    # The session result owns the panel: leaving the tab and coming back must
    # not replace it with the (empty) imported-spectrum view.
    page.click("#analysisPrepareTab")
    page.click("#analysisPolarizationTab")
    expect(content).to_contain_text("Measured from the session")
    expect(content).to_contain_text("Peak Faraday Depth")

    assert page.errors == [], f"page raised JavaScript errors: {page.errors}"
