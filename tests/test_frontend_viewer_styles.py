from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "flits" / "web_static" / "app.js").read_text(encoding="utf-8")
STYLES_CSS = (ROOT / "flits" / "web_static" / "styles.css").read_text(encoding="utf-8")


def test_spectral_window_uses_dedicated_high_contrast_color() -> None:
    assert 'spectral: "#D55E00"' in APP_JS
    assert APP_JS.count("plotTheme.spectral") == 4
    assert "--signal-spectral: #d55e00;" in STYLES_CSS


def test_spectral_window_does_not_reuse_heatmap_accent() -> None:
    spectral_shape_lines = [
        line
        for line in APP_JS.splitlines()
        if "horizontalLine(view.state.spectral_extent_mhz" in line
    ]
    assert len(spectral_shape_lines) == 4
    assert all("plotTheme.spectral" in line for line in spectral_shape_lines)
