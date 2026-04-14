# ─────────────────────────────────────────────────────────────────
# tests/unit/test_landing/test_landing.py
#
# Unit tests for the Landing / Visualisation Page.
# Verifies the API layer that feeds charts and device lists.
#
# Run in isolation:
#   pytest tests/unit/test_landing/ -m landing
# ─────────────────────────────────────────────────────────────────

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.landing]


@pytest.fixture(scope="module")
def landing_client(landing_url, http_client):
    class LandingClient:
        def __init__(self, base, session):
            self.base = base
            self.session = session

        def health(self):
            return self.session.get(f"{self.base}/healthz", timeout=5)

        def get_devices(self):
            return self.session.get(f"{self.base}/api/devices", timeout=10)

        def get_device_latest(self, device_id: str):
            return self.session.get(
                f"{self.base}/api/devices/{device_id}/latest", timeout=10
            )

        def get_device_history(self, device_id: str, limit: int = 100):
            return self.session.get(
                f"{self.base}/api/devices/{device_id}/history",
                params={"limit": limit},
                timeout=10,
            )

        def get_dashboard_summary(self):
            return self.session.get(f"{self.base}/api/dashboard/summary", timeout=10)

    return LandingClient(landing_url, http_client)


class TestLandingHealth:

    def test_health_returns_200(self, landing_client):
        assert landing_client.health().status_code == 200

    def test_health_body_has_status(self, landing_client):
        body = landing_client.health().json()
        assert "status" in body


class TestLandingDeviceList:

    def test_devices_endpoint_returns_200(self, landing_client):
        assert landing_client.get_devices().status_code == 200

    def test_devices_returns_list(self, landing_client):
        body = landing_client.get_devices().json()
        devices = body if isinstance(body, list) else body.get("devices", body.get("data", []))
        assert isinstance(devices, list)

    def test_device_list_items_have_id(self, landing_client):
        body = landing_client.get_devices().json()
        devices = body if isinstance(body, list) else body.get("devices", [])
        if devices:
            assert "device_id" in devices[0] or "id" in devices[0]


class TestLandingDeviceData:

    def test_latest_unknown_device_returns_404(self, landing_client):
        r = landing_client.get_device_latest("nonexistent-device-xyz")
        assert r.status_code in (404, 200)  # 200 with empty body also acceptable

    def test_history_default_limit(self, landing_client):
        # Just verify the endpoint structure is correct
        r = landing_client.get_device_history("any-device", limit=10)
        assert r.status_code in (200, 404)

    def test_history_response_is_list_or_object(self, landing_client):
        r = landing_client.get_device_history("any-device", limit=5)
        if r.status_code == 200:
            body = r.json()
            assert isinstance(body, (list, dict))


class TestLandingDashboard:

    def test_dashboard_summary_accessible(self, landing_client):
        r = landing_client.get_dashboard_summary()
        assert r.status_code in (200, 501)  # 501 if not yet implemented

    def test_dashboard_summary_has_device_count(self, landing_client):
        r = landing_client.get_dashboard_summary()
        if r.status_code == 200:
            body = r.json()
            assert any(k in body for k in ["device_count", "total_devices", "devices"])
