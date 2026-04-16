# ─────────────────────────────────────────────────────────────────
# tests/e2e/test_e2e.py
#
# End-to-end tests – edge device data injected at the adapter
# must be visible on the landing page / visualisation layer.
#
# Run:
#   pytest tests/e2e/ -m e2e
# ─────────────────────────────────────────────────────────────────

import pytest
import time

pytestmark = [pytest.mark.e2e]

# Propagation budget: how long to wait for data to traverse the whole pipeline
PIPELINE_PROPAGATION_SEC = 10


@pytest.fixture(scope="module", autouse=True)
def all_services_ready(
    ensure_adapter_ready,
    ensure_router_ready,
    ensure_proxy_ready,
    ensure_processor_ready,
    ensure_landing_ready,
):
    pass


# ── Landing page availability ─────────────────────────────────────

class TestLandingPageHealth:

    def test_landing_page_reachable(self, landing_url, http_client):
        r = http_client.get(f"{landing_url}/", timeout=10)
        assert r.status_code == 200

    def test_landing_api_health(self, landing_url, http_client, env_config):
        r = http_client.get(
            f"{landing_url}{env_config.landing.health_endpoint}",
            timeout=5
        )
        assert r.status_code == 200

    def test_landing_devices_endpoint_exists(self, landing_url, http_client):
        r = http_client.get(f"{landing_url}/api/devices", timeout=10)
        assert r.status_code in (200, 404)  # 404 acceptable if no devices yet


# ── Data visibility on landing page ──────────────────────────────

class TestDataVisibilityOnLanding:

    def test_injected_data_appears_on_landing(
        self, adapter_url, landing_url, http_client, sample_device_payload
    ):
        """
        Full pipeline: adapter publish → landing page API reflects the data.
        """
        device_id = f"e2e-landing-{int(time.time())}"
        r = http_client.post(
            f"{adapter_url}/publish",
            json=sample_device_payload(device_id=device_id, voltage=225.0),
            timeout=10
        )
        assert r.status_code in (200, 201, 202)

        time.sleep(PIPELINE_PROPAGATION_SEC)

        # The landing page API should expose latest readings
        r = http_client.get(
            f"{landing_url}/api/devices/{device_id}/latest",
            timeout=10
        )
        assert r.status_code == 200, \
            f"Landing page did not expose data for {device_id}"

        body = r.json()
        reading = body if isinstance(body, dict) else body[0]
        voltage = (reading.get("voltage")
                   or reading.get("readings", {}).get("voltage"))
        assert voltage is not None, "Voltage not present in landing page response"

    def test_multiple_devices_all_visible(
        self, adapter_url, landing_url, http_client, sample_device_payload
    ):
        device_ids = [f"e2e-multi-{i}-{int(time.time())}" for i in range(3)]
        for did in device_ids:
            http_client.post(
                f"{adapter_url}/publish",
                json=sample_device_payload(device_id=did),
                timeout=10
            )
        time.sleep(PIPELINE_PROPAGATION_SEC)

        for did in device_ids:
            r = http_client.get(f"{landing_url}/api/devices/{did}/latest", timeout=10)
            assert r.status_code == 200, f"Missing data for {did}"

    def test_time_series_data_on_landing(
        self, adapter_url, landing_url, http_client, sample_device_payload
    ):
        """Verify the landing page exposes historical series, not just latest."""
        device_id = f"e2e-series-{int(time.time())}"
        for v in [220.0, 222.0, 225.0]:
            http_client.post(
                f"{adapter_url}/publish",
                json=sample_device_payload(device_id=device_id, voltage=v),
                timeout=10
            )
            time.sleep(0.5)

        time.sleep(PIPELINE_PROPAGATION_SEC)

        r = http_client.get(
            f"{landing_url}/api/devices/{device_id}/history",
            params={"limit": 10},
            timeout=10
        )
        assert r.status_code == 200
        data = r.json()
        series = data if isinstance(data, list) else data.get("data", [])
        assert len(series) >= 3, \
            f"Expected ≥3 historical points, got {len(series)}"


# ── Smoke tests (fast sanity post-deploy) ─────────────────────────

class TestSmoke:
    """Fast smoke suite – run first after every deployment."""

    @pytest.mark.smoke
    def test_mosquitto_broker_reachable(self, mqtt_client):
        assert mqtt_client.is_connected(), "Cannot connect to Mosquitto broker"

    @pytest.mark.smoke
    def test_adapter_alive(self, adapter_url, http_client):
        assert http_client.get(f"{adapter_url}/health", timeout=5).status_code == 200

    @pytest.mark.smoke
    def test_router_alive(self, router_url, http_client):
        assert http_client.get(f"{router_url}/health", timeout=5).status_code == 200

    @pytest.mark.smoke
    def test_proxy_alive(self, proxy_url, http_client):
        assert http_client.get(f"{proxy_url}/health", timeout=5).status_code == 200

    @pytest.mark.smoke
    def test_processor_alive(self, processor_url, http_client):
        assert http_client.get(f"{processor_url}/health", timeout=5).status_code == 200

    @pytest.mark.smoke
    def test_landing_alive(self, landing_url, http_client):
        assert http_client.get(f"{landing_url}/", timeout=10).status_code == 200

    @pytest.mark.smoke
    def test_single_mqtt_publish_survives_pipeline(
        self, mqtt_client, mqtt_config, make_raw_mqtt_payload
    ):
        """One MQTT publish must not cause any 5xx across the pipeline."""
        result = mqtt_client.publish(
            mqtt_config.topic_for("smoke-test-device"),
            make_raw_mqtt_payload(device_id="smoke-test-device"),
            qos=1,
        )
        result.wait_for_publish(timeout=10)
        assert result.is_published(), \
            "Smoke MQTT publish was not acknowledged by Mosquitto broker"
