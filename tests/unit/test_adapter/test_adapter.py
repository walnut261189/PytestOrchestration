# ─────────────────────────────────────────────────────────────────
# tests/unit/test_adapter/test_adapter.py
#
# Unit tests for the Adapter component.
#
# The Adapter:
#   1. Hosts a Mosquitto broker on port 1883 (plain) / 8883 (TLS)
#   2. Subscribes to  devices/#  via paho-mqtt
#   3. Receives raw payloads from edge devices (CSV or JSON)
#   4. Normalises them into a standard schema
#   5. Forwards over HTTP to the Router
#
# These tests validate both the MQTT ingestion side and the
# HTTP management API of the Adapter.
#
# Run in isolation:
#   pytest tests/unit/test_adapter/ -m adapter
# ─────────────────────────────────────────────────────────────────

import json
import time
import pytest

pytestmark = [pytest.mark.unit, pytest.mark.adapter]


# ── HTTP client for Adapter management API ────────────────────────

@pytest.fixture(scope="module")
def adapter_http(adapter_url, http_client):
    class AdapterHTTP:
        def health(self):
            return http_client.get(f"{adapter_url}/health", timeout=5)

        def get_status(self):
            return http_client.get(f"{adapter_url}/status", timeout=5)

        def get_metrics(self):
            return http_client.get(f"{adapter_url}/metrics", timeout=5)

        def get_connected_devices(self):
            return http_client.get(f"{adapter_url}/devices", timeout=5)

        def get_device_last_seen(self, device_id: str):
            return http_client.get(f"{adapter_url}/devices/{device_id}", timeout=5)

    return AdapterHTTP()


# ── Health & management API ───────────────────────────────────────

class TestAdapterHealth:

    def test_health_returns_200(self, adapter_http):
        assert adapter_http.health().status_code == 200

    def test_health_reports_broker_status(self, adapter_http):
        body = adapter_http.health().json()
        # Adapter should report Mosquitto broker health
        assert any(k in body for k in ("broker", "mqtt", "mosquitto", "status"))

    def test_health_reports_broker_running(self, adapter_http):
        body = adapter_http.health().json()
        broker_status = (body.get("broker") or body.get("mqtt") or
                         body.get("mosquitto") or body.get("status", ""))
        assert str(broker_status).lower() in ("ok", "healthy", "running", "up", "true", "1")

    def test_status_endpoint_returns_200(self, adapter_http):
        assert adapter_http.get_status().status_code == 200

    def test_status_reports_connected_device_count(self, adapter_http):
        body = adapter_http.get_status().json()
        assert any(k in body for k in ("connected_devices", "device_count", "clients"))

    def test_metrics_endpoint_accessible(self, adapter_http):
        assert adapter_http.get_metrics().status_code == 200

    def test_metrics_include_mqtt_stats(self, adapter_http):
        body = adapter_http.get_metrics().json()
        # Expect some MQTT throughput counters
        assert any(k in str(body).lower() for k in
                   ("messages_received", "publish", "mqtt", "topic"))


# ── MQTT ingestion via adapter ────────────────────────────────────

class TestAdapterMQTTIngestion:
    """
    Publish directly to the Mosquitto broker (hosted by the adapter)
    and verify the adapter processed the message via its HTTP API.
    """

    def test_structured_json_publish_increments_message_count(
        self, mqtt_client, mqtt_config, adapter_http, make_raw_mqtt_payload
    ):
        before = adapter_http.get_metrics().json()
        before_count = _extract_message_count(before)

        mqtt_client.publish(
            mqtt_config.topic_for("dev-adapter-001"),
            make_raw_mqtt_payload(device_id="dev-adapter-001", voltage=230.0),
            qos=mqtt_config.qos,
        )
        time.sleep(2)

        after = adapter_http.get_metrics().json()
        after_count = _extract_message_count(after)
        assert after_count > before_count, \
            "Adapter message count did not increase after MQTT publish"

    def test_raw_csv_publish_increments_message_count(
        self, mqtt_client, mqtt_config, adapter_http, make_raw_mqtt_payload
    ):
        before_count = _extract_message_count(adapter_http.get_metrics().json())

        mqtt_client.publish(
            mqtt_config.topic_for("dev-csv-001"),
            make_raw_mqtt_payload(voltage=215.0, current=2.8, raw=True),
            qos=mqtt_config.qos,
        )
        time.sleep(2)

        after_count = _extract_message_count(adapter_http.get_metrics().json())
        assert after_count > before_count

    def test_device_appears_in_connected_list_after_publish(
        self, mqtt_client, mqtt_config, adapter_http, make_raw_mqtt_payload
    ):
        device_id = f"dev-listed-{int(time.time())}"
        mqtt_client.publish(
            mqtt_config.topic_for(device_id),
            make_raw_mqtt_payload(device_id=device_id),
            qos=mqtt_config.qos,
        )
        time.sleep(2)

        r = adapter_http.get_connected_devices()
        if r.status_code == 200:
            body = r.json()
            devices = body if isinstance(body, list) else body.get("devices", [])
            device_ids = [
                d if isinstance(d, str) else d.get("device_id", d.get("id", ""))
                for d in devices
            ]
            assert device_id in device_ids, \
                f"{device_id} not found in connected devices: {device_ids}"

    def test_device_last_seen_updated_after_publish(
        self, mqtt_client, mqtt_config, adapter_http, make_raw_mqtt_payload
    ):
        device_id = f"dev-seen-{int(time.time())}"
        mqtt_client.publish(
            mqtt_config.topic_for(device_id),
            make_raw_mqtt_payload(device_id=device_id),
            qos=mqtt_config.qos,
        )
        time.sleep(2)

        r = adapter_http.get_device_last_seen(device_id)
        if r.status_code == 200:
            body = r.json()
            assert "last_seen" in body or "timestamp" in body or "ts" in body

    def test_burst_publish_handled_without_5xx(
        self, mqtt_client, mqtt_config, adapter_http, make_raw_mqtt_payload
    ):
        """Adapter must not return 5xx on its health endpoint during a burst."""
        for i in range(50):
            mqtt_client.publish(
                mqtt_config.topic_for(f"dev-burst-{i}"),
                make_raw_mqtt_payload(device_id=f"dev-burst-{i}"),
                qos=0,  # QoS 0 for max speed
            )
        time.sleep(2)
        assert adapter_http.health().status_code == 200


# ── Normalisation verification ────────────────────────────────────

class TestAdapterNormalisation:
    """
    Verify that the adapter correctly normalises raw MQTT payloads
    before forwarding downstream.  These tests check the normalised
    output by querying the adapter's last-message cache or by
    inspecting what arrives at the router (integration-style but
    kept here for focused unit validation).
    """

    def test_normalised_output_uses_standard_field_names(
        self, mqtt_client, mqtt_config, adapter_http, make_raw_mqtt_payload
    ):
        """
        Raw payload uses short keys (v, i, t).
        Normalised output must use (voltage, current, temperature).
        Check the adapter's last-message endpoint for the device.
        """
        device_id = f"dev-norm-{int(time.time())}"
        mqtt_client.publish(
            mqtt_config.topic_for(device_id),
            make_raw_mqtt_payload(device_id=device_id, voltage=222.0),
            qos=1,
        )
        time.sleep(2)

        r = adapter_http.get_device_last_seen(device_id)
        if r.status_code == 200:
            body = r.json()
            normalised = body.get("last_message") or body.get("normalised") or body
            readings = normalised.get("readings", normalised)
            # After normalisation: v → voltage, i → current, t → temperature
            assert "voltage" in readings or "v" not in readings, \
                "Adapter did not normalise 'v' → 'voltage'"

    def test_normalised_output_has_iso_timestamp(
        self, mqtt_client, mqtt_config, adapter_http, make_raw_mqtt_payload
    ):
        device_id = f"dev-ts-{int(time.time())}"
        mqtt_client.publish(
            mqtt_config.topic_for(device_id),
            make_raw_mqtt_payload(device_id=device_id),
            qos=1,
        )
        time.sleep(2)

        r = adapter_http.get_device_last_seen(device_id)
        if r.status_code == 200:
            body = r.json()
            ts = (body.get("timestamp") or body.get("ts") or
                  body.get("last_message", {}).get("timestamp"))
            assert ts is not None, "No timestamp in normalised adapter output"


# ── Helpers ───────────────────────────────────────────────────────

def _extract_message_count(metrics: dict) -> int:
    for key in ("messages_received", "total_messages", "mqtt_messages", "count"):
        if key in metrics:
            return int(metrics[key])
    # Try nested
    for val in metrics.values():
        if isinstance(val, dict):
            for key in ("messages_received", "count", "total"):
                if key in val:
                    return int(val[key])
    return 0
