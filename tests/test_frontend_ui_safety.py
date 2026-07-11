from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "flits" / "web_static" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "flits" / "web_static" / "index.html").read_text(encoding="utf-8")
STYLES_CSS = (ROOT / "flits" / "web_static" / "styles.css").read_text(encoding="utf-8")


def test_session_save_persists_note_draft_first() -> None:
    save_function = re.search(
        r"async function saveSessionSnapshot\(.*?\n}\n",
        APP_JS,
        re.S,
    )
    assert save_function is not None
    source = save_function.group(0)
    assert "await persistNotesDraft()" in source
    assert source.index("await persistNotesDraft()") < source.index("/snapshot/save")
    assert 'notesInput.addEventListener("input"' in APP_JS
    assert "state.notesDraftDirty" in APP_JS
    assert re.search(
        r"async function downloadSessionSnapshot\(\).*?await persistNotesDraft\(\).*?/snapshot",
        APP_JS,
        re.S,
    )
    assert re.search(
        r'if \(type === "export_results"\)\s*{\s*await persistNotesDraft\(\)',
        APP_JS,
    )


def test_destructive_session_changes_have_unsaved_work_guards() -> None:
    assert "function hasUnsavedWork()" in APP_JS
    assert "function confirmDiscardUnsaved(actionLabel)" in APP_JS
    assert 'confirmDiscardUnsaved("Importing a session")' in APP_JS
    assert 'confirmDiscardUnsaved("Opening a saved session")' in APP_JS
    assert 'confirmDiscardUnsaved("Loading another file")' in APP_JS
    assert 'window.addEventListener("beforeunload"' in APP_JS


def test_detection_requests_are_cancelled_and_versioned() -> None:
    assert "let detectionRequestId = 0" in APP_JS
    assert "let detectionAbortController = null" in APP_JS
    assert "new AbortController()" in APP_JS
    assert "requestId !== detectionRequestId" in APP_JS
    assert "selectedBurstPath() !== normalizedPath" in APP_JS


def test_plot_actions_are_locked_while_session_mutates() -> None:
    assert re.search(
        r"function handleViewerPlotClick\(event\)\s*{\s*if \(state\.busyAction\)",
        APP_JS,
    )
    assert 'viewerPlot.classList.toggle("is-busy", isBusy)' in APP_JS
    assert 'viewerPlot.setAttribute("aria-busy", String(isBusy))' in APP_JS


def test_all_plot_selection_modes_have_exact_value_controls() -> None:
    assert 'id="exactSelectionMode"' in INDEX_HTML
    assert 'id="exactStartInput"' in INDEX_HTML
    assert 'id="exactEndInput"' in INDEX_HTML
    for mode in (
        "event",
        "crop",
        "offpulse",
        "region",
        "add-peak",
        "remove-peak",
        "mask-channel",
        "mask-range",
        "spec-extent",
    ):
        assert f'<option value="{mode}">' in INDEX_HTML
        assert f'{mode}: "' in APP_JS or f'"{mode}": "' in APP_JS


def test_tabs_and_modes_expose_accessible_selected_state() -> None:
    assert 'aria-controls="analysisPreparePanel"' in INDEX_HTML
    assert 'aria-labelledby="analysisPrepareTab"' in INDEX_HTML
    assert 'button.setAttribute("aria-pressed", String(isActive))' in APP_JS
    assert 'button.addEventListener("keydown"' in APP_JS
    assert 'button.tabIndex = isActive ? 0 : -1' in APP_JS


def test_desktop_inspector_remains_persistent_at_laptop_widths() -> None:
    marker = "@media (max-width: 1200px)"
    assert marker in STYLES_CSS
    source = STYLES_CSS.split(marker, 1)[1].split("@media (max-width: 720px)", 1)[0]
    assert "grid-template-columns: 300px minmax(0, 1fr)" in source
    assert ".sidebar" in source
    assert "position: static" not in source
    assert "order: 2" not in source


def test_ui_has_no_external_font_dependency_and_fitting_remains_expert_capable() -> None:
    assert "fonts.googleapis.com" not in INDEX_HTML
    assert 'id="fitStructureHeading"' in INDEX_HTML
    assert 'id="fitParametersHeading"' in INDEX_HTML
    assert "Advanced Fit Setup" in INDEX_HTML
    for field in (
        "fitWeightingModeSelect",
        "fitIterationsInput",
        "fitMaxEvaluationsInput",
        "fitTimeUpsampleInput",
        "fitFreqUpsampleInput",
        "fitRefFreqInput",
    ):
        assert f'id="{field}"' in INDEX_HTML
