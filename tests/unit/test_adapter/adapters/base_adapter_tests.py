# ─────────────────────────────────────────────────────────────────
# tests/unit/test_adapter/adapters/base_adapter_tests.py
#
# Base test class for all adapter types.
# Every adapter (voltage, current, temperature, ...) inherits from
# BaseAdapterTests, which covers the behaviour that is identical
# across all adapters:
#   - Health / status / metrics HTTP API
#   - MQTT broker embedded in the adapter responds correctly
#   - Payload publish increments message count
#   - Payload carries the correct "type" field
#   - Normalisation maps raw fields to standard schema
#   - Burst resilience
#
# Each concrete subclass sets:
#   ADAPTER_TYPE   : str   e.g. "voltage"
#   PRIMARY_FIELD  : str   e.g. "voltage"  (the reading this adapter owns)
#   PRIMARY_VALUE  : float e.g. 230.0
# ─────────────────────────────────────────────────────────────────

import json
import time
import pytest


class BaseAdapterTests:
    """
    Inherit this class in each adapter-type test module.

    Required class attributes:
        ADAPTER_TYPE  = "voltage" | "current" | "temperature" | ...
        PRIMARY_FIELD = "voltage" | "current" | "temperature" | ...
        PRIMARY_VALUE = a representative float reading value
    """

    ADAPTER_TYPE: str = None
    PRIMARY_FIELD: str = None
    PRIMARY_VALUE: float = None

    # ── Fixtures resolved at runtime via adapter_config ───────────

    @pytest.fixture(scope="class")
    def cfg(self, adapter_config):
        assert self.ADAPTER_TYPE, "Subclass must set ADAPTER_TYPE"
        return adapter_config(self.ADAPTER_TYPE)

    @pytest.fixture(scope="class")
    def mqtt(self, mqtt_client_for):
        return mqtt_client_for(self.ADAPTER_TYPE)

    @pytest.fixture(scope="class")
    def http(self, http_client, adapter_config):
        cfg = adapter_config(self.ADAPTER_TYPE)
        class AdapterHTTP:
            def health(self):
                return http_client.get(f"{cfg.http_base}/health", timeout=5)
            def status(self):
                return http_client.get(f"{cfg.http_base}/status", timeout=5)
            def metrics(self):
                return http_client.get(f"{cfg.http_base}/metrics", timeout=5)
            def devices(self):
                return http_client.get(f"{cfg.http_base}/devices", timeout=5)
            def device(self, device_id):
                return http_client.get(f"{cfg.http_base}/devices/{device_id}", timeout=5)
        return AdapterHTTP()

    # ── Health ────────────────────────────────────────────────────

    def test_health_returns_200(self, http):
        assert http.health().status_code == 200

    def test_health_reports_broker_running(self, http):
        body = http.health().json()
        broker = (body.get("broker") or body.get("mqtt") or
                  body.get("mosquitto") or body.get("status", ""))
        assert str(broker).lower() in ("ok", "healthy", "running", "up", "true", "1")

    def test_health_reports_adapter_type(self, http):
        """Health response should identify which adapter type this is."""
        body = http.health().json()
        reported_type = (body.get("type") or body.get("adapter_type") or
                         body.get("name", ""))
        if reported_type:
            assert reported_type == self.ADAPTER_TYPE

    def test_status_returns_200(self, http):
        assert http.status().status_code == 200

    def test_metrics_returns_200(self, http):
        assert http.metrics().status_code == 200

    # ── MQTT broker (embedded in adapter) ─────────────────────────

    def test_mqtt_broker_accepts_connection(self, mqtt):
        assert mqtt.is_connected(), \
            f"{self.ADAPTER_TYPE} adapter broker did not accept connection"

    def test_mqtt_publish_increments_message_count(
        self, mqtt, cfg, http, make_mqtt_payload
    ):
        before = _msg_count(http.metrics().json())
        mqtt.publish(
            cfg.topic_for(f"{self.ADAPTER_TYPE}-dev-001"),
            make_mqtt_payload(self.ADAPTER_TYPE, device_id=f"{self.ADAPTER_TYPE}-dev-001"),
            qos=cfg.mqtt_qos,
        )
        time.sleep(2)
        after = _msg_count(http.metrics().json())
        assert after > before, \
            f"{self.ADAPTER_TYPE} adapter message count did not increase"

    def test_mqtt_publish_uses_correct_type_field(self, make_mqtt_payload):
        """Every payload must carry the correct 'type' field for this adapter."""
        raw = make_mqtt_payload(self.ADAPTER_TYPE)
        parsed = json.loads(raw.decode())
        assert parsed.get("type") == self.ADAPTER_TYPE, \
            f"Expected type='{self.ADAPTER_TYPE}', got '{parsed.get('type')}'"

    def test_topic_format_correct(self, cfg):
        topic = cfg.topic_for("dev-test-001")
        assert topic == f"devices/dev-test-001"
        assert " " not in topic

    def test_wildcard_topic_correct(self, cfg):
        assert cfg.device_wildcard_topic == "devices/#"

    # ── Normalisation ─────────────────────────────────────────────

    def test_normalised_output_uses_standard_field_names(
        self, mqtt, cfg, http, make_mqtt_payload
    ):
        """
        Raw payload uses short keys (v, i, t).
        Normalised output must use full names (voltage, current, temperature).
        """
        device_id = f"{self.ADAPTER_TYPE}-norm-{int(time.time())}"
        mqtt.publish(
            cfg.topic_for(device_id),
            make_mqtt_payload(self.ADAPTER_TYPE, device_id=device_id,
                              **{self.PRIMARY_FIELD: self.PRIMARY_VALUE}),
            qos=1,
        )
        time.sleep(2)
        r = http.device(device_id)
        if r.status_code == 200:
            body = r.json()
            last = body.get("last_message") or body.get("normalised") or body
            readings = last.get("readings", last)
            assert self.PRIMARY_FIELD in readings, \
                f"Normalised output missing '{self.PRIMARY_FIELD}' field"

    def test_raw_csv_publish_processed(self, mqtt, cfg, http, make_mqtt_payload):
        """Legacy raw CSV payloads must also be processed."""
        before = _msg_count(http.metrics().json())
        mqtt.publish(
            cfg.topic_for(f"{self.ADAPTER_TYPE}-csv-001"),
            make_mqtt_payload(self.ADAPTER_TYPE, raw_csv=True),
            qos=cfg.mqtt_qos,
        )
        time.sleep(2)
        assert _msg_count(http.metrics().json()) > before

    # ── Resilience ────────────────────────────────────────────────

    def test_burst_50_messages_no_health_degradation(
        self, mqtt, cfg, http, make_mqtt_payload
    ):
        for i in range(50):
            mqtt.publish(
                cfg.topic_for(f"{self.ADAPTER_TYPE}-burst-{i}"),
                make_mqtt_payload(self.ADAPTER_TYPE,
                                  device_id=f"{self.ADAPTER_TYPE}-burst-{i}"),
                qos=0,
            )
        time.sleep(2)
        assert http.health().status_code == 200


# ── Helper ────────────────────────────────────────────────────────

def _msg_count(metrics: dict) -> int:
    for k in ("messages_received", "total_messages", "mqtt_messages", "count"):
        if k in metrics:
            return int(metrics[k])
    for v in metrics.values():
        if isinstance(v, dict):
            for k in ("messages_received", "count", "total"):
                if k in v:
                    return int(v[k])
    return 0
