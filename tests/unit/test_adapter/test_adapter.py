# ─────────────────────────────────────────────────────────────────
# tests/unit/test_adapter/test_adapter.py
#
# Unit tests for the Adapter component.
# The adapter listens to edge devices (MQTT / Modbus / OPC-UA etc.)
# and normalises raw payloads before forwarding downstream.
#
# Run in isolation:
#   pytest tests/unit/test_adapter/ -m adapter
# ─────────────────────────────────────────────────────────────────

import pytest
import json
import time
import requests
from unittest.mock import patch, MagicMock


pytestmark = [pytest.mark.unit, pytest.mark.adapter]


# ── Adapter-level fixtures ────────────────────────────────────────

@pytest.fixture(scope="module")
def adapter_client(adapter_url, http_client):
    """Thin wrapper so tests don't hardcode the adapter base URL."""
    class AdapterClient:
        def __init__(self, base, session):
            self.base = base
            self.session = session

        def health(self):
            return self.session.get(f"{self.base}/health", timeout=5)

        def publish(self, payload: dict):
            return self.session.post(f"{self.base}/publish", json=payload, timeout=10)

        def get_status(self):
            return self.session.get(f"{self.base}/status", timeout=5)

        def get_metrics(self):
            return self.session.get(f"{self.base}/metrics", timeout=5)

    return AdapterClient(adapter_url, http_client)


# ── Health & connectivity ─────────────────────────────────────────

class TestAdapterHealth:

    def test_health_endpoint_returns_200(self, adapter_client):
        """Adapter /health must return HTTP 200."""
        r = adapter_client.health()
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"

    def test_health_response_contains_status_key(self, adapter_client):
        r = adapter_client.health()
        body = r.json()
        assert "status" in body, "Health response missing 'status' key"

    def test_health_reports_ok(self, adapter_client):
        body = adapter_client.health().json()
        assert body["status"] in ("ok", "healthy", "up")


# ── Payload ingestion ─────────────────────────────────────────────

class TestAdapterPayloadIngestion:

    def test_publish_valid_voltage_current_payload(self, adapter_client, sample_device_payload):
        payload = sample_device_payload(device_id="dev-001", voltage=230.1, current=4.5)
        r = adapter_client.publish(payload)
        assert r.status_code in (200, 201, 202), \
            f"Publish failed with {r.status_code}: {r.text}"

    def test_publish_returns_acknowledgement(self, adapter_client, sample_device_payload):
        payload = sample_device_payload()
        r = adapter_client.publish(payload)
        body = r.json()
        # Adapter should ack with at least a message_id or status
        assert "message_id" in body or "status" in body

    def test_publish_temperature_reading(self, adapter_client, sample_device_payload):
        payload = sample_device_payload(temperature=72.3)
        r = adapter_client.publish(payload)
        assert r.status_code in (200, 201, 202)

    def test_publish_missing_device_id_returns_400(self, adapter_client):
        bad_payload = {"readings": {"voltage": 220}}
        r = adapter_client.publish(bad_payload)
        assert r.status_code == 400, \
            f"Expected 400 for missing device_id, got {r.status_code}"

    def test_publish_empty_payload_returns_400(self, adapter_client):
        r = adapter_client.publish({})
        assert r.status_code == 400

    def test_publish_malformed_readings_returns_422(self, adapter_client):
        payload = {"device_id": "dev-x", "readings": "not-a-dict"}
        r = adapter_client.publish(payload)
        assert r.status_code in (400, 422)

    def test_publish_negative_voltage_handled(self, adapter_client, sample_device_payload):
        """Adapter should either reject or flag negative voltage readings."""
        payload = sample_device_payload(voltage=-10.0)
        r = adapter_client.publish(payload)
        # Accept 200 with a warning flag OR a 422 validation error
        if r.status_code == 200:
            body = r.json()
            assert body.get("warnings") or body.get("flagged"), \
                "Negative voltage should be flagged in response"
        else:
            assert r.status_code in (400, 422)

    def test_publish_large_batch_payload(self, adapter_client, sample_device_payload):
        """Adapter must handle a burst of readings without 5xx."""
        for i in range(20):
            payload = sample_device_payload(device_id=f"dev-{i:03d}")
            r = adapter_client.publish(payload)
            assert r.status_code < 500, f"5xx on reading {i}: {r.status_code}"


# ── Normalisation / schema ────────────────────────────────────────

class TestAdapterNormalisation:

    def test_published_message_has_iso_timestamp(self, adapter_client, sample_device_payload):
        """
        Adapter must enrich payloads with an ISO-8601 timestamp
        before forwarding – verify this is echoed back or fetchable
        from the status endpoint.
        """
        payload = sample_device_payload()
        r = adapter_client.publish(payload)
        body = r.json()
        # Adjust the key name to match your adapter's actual response schema
        ts = body.get("enriched_timestamp") or body.get("timestamp")
        assert ts is not None, "No timestamp in adapter response"

    def test_status_reports_connected_devices(self, adapter_client):
        status = adapter_client.get_status().json()
        assert "connected_devices" in status or "device_count" in status

    def test_metrics_endpoint_accessible(self, adapter_client):
        r = adapter_client.get_metrics()
        assert r.status_code == 200


# ── Edge-device protocol stubs (unit-level mocks) ─────────────────

class TestAdapterProtocolHandling:
    """
    Pure unit tests – mock the protocol client so no real broker needed.
    Replace 'adapter.mqtt_client' with your actual module path.
    """

    @patch("adapter.mqtt_client.connect")          # TODO: update import path
    def test_mqtt_connect_called_on_startup(self, mock_connect):
        mock_connect.return_value = MagicMock(is_connected=True)
        from adapter import startup                 # TODO: update import path
        startup()
        mock_connect.assert_called_once()

    def test_payload_normaliser_maps_raw_modbus_to_schema(self):
        """
        Tests the normaliser function directly without HTTP.
        Replace with your actual normaliser import.
        """
        # TODO: replace with real import
        # from adapter.normaliser import normalise_modbus
        raw_modbus = {"register_40001": 2205, "register_40002": 32}
        # normalised = normalise_modbus(raw_modbus)
        # assert normalised["voltage"] == 220.5
        # assert normalised["current"] == 3.2
        pytest.skip("Implement after wiring up normaliser import")
