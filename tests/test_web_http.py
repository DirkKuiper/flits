"""End-to-end tests that drive FLITS over real HTTP.

The rest of the web tests call the route functions directly, which skips
routing, status-code mapping, response serialization, middleware and the static
mount. These tests exercise the app through Starlette's TestClient so that layer
is covered too.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

pytest.importorskip("httpx", reason="httpx is required for the TestClient transport")

from fastapi.testclient import TestClient  # noqa: E402

# `flits.web` re-exports the FastAPI instance as `app`, which shadows the
# submodule under `import flits.web.app as ...`. Import it explicitly.
app_module = importlib.import_module("flits.web.app")


@pytest.fixture
def client(monkeypatch, synthetic_waterfall) -> TestClient:
    """A TestClient whose data directory contains the synthetic burst."""
    monkeypatch.setenv("FLITS_DATA_DIR", str(synthetic_waterfall.path.parent))
    monkeypatch.delenv("FLITS_ALLOW_OUTSIDE_DATA_DIR", raising=False)
    app_module.SESSIONS.clear()
    app_module.SESSION_SNAPSHOT_PATHS.clear()
    with TestClient(app_module.app) as test_client:
        yield test_client
    app_module.SESSIONS.clear()
    app_module.SESSION_SNAPSHOT_PATHS.clear()


def _create_session(client: TestClient, waterfall) -> str:
    response = client.post(
        "/api/sessions",
        json={"bfile": waterfall.path.name, "dm": 0.0, "sefd_jy": 10.0},
    )
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


def test_health_endpoint_serves_over_http(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_index_and_static_mount_serve_the_interface(client: TestClient) -> None:
    index = client.get("/")
    assert index.status_code == 200
    assert "text/html" in index.headers["content-type"]

    app_js = client.get("/static/app.js")
    assert app_js.status_code == 200


def test_file_listing_reports_the_synthetic_burst(client: TestClient, synthetic_waterfall) -> None:
    response = client.get("/api/files")
    assert response.status_code == 200
    assert synthetic_waterfall.path.name in response.json()["files"]


def test_session_lifecycle_over_http(client: TestClient, synthetic_waterfall) -> None:
    session_id = _create_session(client, synthetic_waterfall)

    view = client.get(f"/api/sessions/{session_id}")
    assert view.status_code == 200
    assert view.json()["view"]["meta"]["shape"] == [64, 256]

    action = client.post(
        f"/api/sessions/{session_id}/actions",
        json={"type": "time_factor", "payload": {"value": 2}},
    )
    assert action.status_code == 200
    assert action.json()["view"]["state"]["time_factor"] == 2

    snapshot = client.get(f"/api/sessions/{session_id}/snapshot")
    assert snapshot.status_code == 200
    assert "attachment" in snapshot.headers["content-disposition"]
    assert snapshot.json()["schema_version"]

    deleted = client.delete(f"/api/sessions/{session_id}")
    assert deleted.status_code == 200
    assert client.get(f"/api/sessions/{session_id}").status_code == 404


def test_export_artifact_downloads_with_attachment_headers(
    client: TestClient, synthetic_waterfall
) -> None:
    session_id = _create_session(client, synthetic_waterfall)
    client.post(
        f"/api/sessions/{session_id}/actions",
        json={"type": "compute_properties", "payload": {}},
    )

    export = client.post(
        f"/api/sessions/{session_id}/actions",
        json={"type": "export_results", "payload": {"include": ["json"]}},
    )
    assert export.status_code == 200, export.text
    manifest = export.json()["export_manifest"]
    assert manifest is not None

    export_id = manifest["export_id"]
    artifact_name = manifest["artifacts"][0]["name"]

    artifact = client.get(f"/api/sessions/{session_id}/exports/{export_id}/{artifact_name}")
    assert artifact.status_code == 200
    assert artifact_name in artifact.headers["content-disposition"]
    assert artifact.content


def test_unknown_session_returns_404(client: TestClient) -> None:
    response = client.get("/api/sessions/does-not-exist")
    assert response.status_code == 404


def test_unsupported_action_returns_400(client: TestClient, synthetic_waterfall) -> None:
    session_id = _create_session(client, synthetic_waterfall)
    response = client.post(
        f"/api/sessions/{session_id}/actions",
        json={"type": "not_a_real_action", "payload": {}},
    )
    assert response.status_code == 400
    assert "not_a_real_action" in response.json()["detail"]


def test_malformed_request_body_returns_422(client: TestClient) -> None:
    response = client.post("/api/sessions", json={"dm": "not-a-number"})
    assert response.status_code == 422


class TestDataDirectoryContainment:
    """The data directory is a containment boundary, not only a browsing root."""

    def test_absolute_path_outside_data_dir_is_rejected(self, client: TestClient) -> None:
        outside = Path(__file__).resolve()
        response = client.post("/api/detect", json={"bfile": str(outside)})
        assert response.status_code == 403
        assert "outside the FLITS data directory" in response.json()["detail"]

    def test_relative_traversal_outside_data_dir_is_rejected(self, client: TestClient) -> None:
        response = client.post("/api/detect", json={"bfile": "../../../../etc/hosts"})
        assert response.status_code == 403

    def test_containment_can_be_disabled_explicitly(
        self, client: TestClient, monkeypatch
    ) -> None:
        monkeypatch.setenv("FLITS_ALLOW_OUTSIDE_DATA_DIR", "1")
        outside = Path(__file__).resolve()
        response = client.post("/api/detect", json={"bfile": str(outside)})
        # The path is reachable again, so this fails on format detection rather
        # than on containment.
        assert response.status_code != 403

    def test_snapshot_import_cannot_escape_the_data_dir(
        self, client: TestClient, synthetic_waterfall, tmp_path_factory, monkeypatch
    ) -> None:
        """A snapshot naming a burst outside the data dir must not reopen it.

        Snapshot resolution prefers an identity-matching copy inside the data
        directory, so the burst is moved out of reach first: the data dir is
        repointed at an empty directory, leaving the snapshot's absolute path as
        the only candidate.
        """
        session_id = _create_session(client, synthetic_waterfall)
        snapshot = client.get(f"/api/sessions/{session_id}/snapshot").json()
        assert snapshot["source"]["source_path"] == str(synthetic_waterfall.path)

        empty_dir = tmp_path_factory.mktemp("elsewhere")
        monkeypatch.setenv("FLITS_DATA_DIR", str(empty_dir))

        response = client.post("/api/sessions/import", json={"snapshot": snapshot})
        assert response.status_code == 403
        assert "outside the FLITS data directory" in response.json()["detail"]


class TestCrossOriginPolicy:
    """Cross-origin access is opt-in rather than wide open."""

    @staticmethod
    def _reload_app(monkeypatch, origins: str | None):
        if origins is None:
            monkeypatch.delenv("FLITS_CORS_ORIGINS", raising=False)
        else:
            monkeypatch.setenv("FLITS_CORS_ORIGINS", origins)
        return importlib.reload(app_module)

    def test_no_cors_headers_by_default(self, monkeypatch, synthetic_waterfall) -> None:
        monkeypatch.setenv("FLITS_DATA_DIR", str(synthetic_waterfall.path.parent))
        module = self._reload_app(monkeypatch, None)
        try:
            with TestClient(module.app) as client:
                response = client.get(
                    "/api/health", headers={"Origin": "https://unrelated.example"}
                )
            assert response.status_code == 200
            assert "access-control-allow-origin" not in response.headers
        finally:
            self._reload_app(monkeypatch, None)

    def test_configured_origin_is_allowed(self, monkeypatch, synthetic_waterfall) -> None:
        monkeypatch.setenv("FLITS_DATA_DIR", str(synthetic_waterfall.path.parent))
        module = self._reload_app(monkeypatch, "https://trusted.example")
        try:
            with TestClient(module.app) as client:
                allowed = client.get(
                    "/api/health", headers={"Origin": "https://trusted.example"}
                )
                denied = client.get(
                    "/api/health", headers={"Origin": "https://unrelated.example"}
                )
            assert allowed.headers.get("access-control-allow-origin") == "https://trusted.example"
            assert "access-control-allow-origin" not in denied.headers
        finally:
            self._reload_app(monkeypatch, None)
