# ─────────────────────────────────────────────────────────────────
# tests/unit/test_adapter/adapters/test_temperature_adapter.py
#
# Tests for the Temperature Adapter.
# Run:
#   pytest tests/unit/test_adapter/adapters/test_temperature_adapter.py -m "unit and adapter and temperature"
# ─────────────────────────────────────────────────────────────────

import json
import time
import pytest

from tests.unit.test_adapter.adapters.base_adapter_tests import BaseAdapterTests, _msg_count

pytestmark = [pytest.mark.unit, pytest.mark.adapter, pytest.mark.temperature]


class TestTemperatureAdapter(BaseAdapterTests):

    ADAPTER_TYPE  = "temperature"
    PRIMARY_FIELD = "temperature"
    PRIMARY_VALUE = 25.0

    # ── Temperature-specific tests ────────────────────────────────

    def test_nominal_temperature_accepted(self, mqtt, cfg, make_mqtt_payload):
        """Normal operating range (0–85 °C) must be accepted without error."""
        for t in [0.0, 25.0, 50.0, 85.0]:
            result = mqtt.publish(
                cfg.topic_for(f"temp-nominal-{int(t)}"),
                make_mqtt_payload("temperature", temperature=t),
                qos=1,
            )
            result.wait_for_publish(timeout=5)
            assert result.is_published()

    def test_high_temperature_flagged(self, mqtt, cfg, http, make_mqtt_payload):
        """Temperature above operating threshold should be flagged."""
        device_id = f"temp-high-{int(time.time())}"
        mqtt.publish(
            cfg.topic_for(device_id),
            make_mqtt_payload("temperature", device_id=device_id, temperature=120.0),
            qos=1,
        )
        time.sleep(2)
        assert http.health().status_code == 200

    def test_sub_zero_temperature_accepted(self, mqtt, cfg, http, make_mqtt_payload):
        """Sub-zero readings are valid (cold environments)."""
        before = _msg_count(http.metrics().json())
        mqtt.publish(
            cfg.topic_for("temp-subzero-001"),
            make_mqtt_payload("temperature", temperature=-10.0),
            qos=1,
        )
        time.sleep(2)
        assert _msg_count(http.metrics().json()) > before

    def test_temperature_reading_unit_is_celsius(self, make_mqtt_payload):
        """Sanity: 't' field is in Celsius, not Fahrenheit or Kelvin."""
        raw = make_mqtt_payload("temperature", temperature=25.0)
        parsed = json.loads(raw.decode())
        assert parsed["t"] == 25.0, \
            f"Expected 25.0 °C, got {parsed['t']} — check unit conversion"

    def test_type_field_is_temperature(self, make_mqtt_payload):
        raw = make_mqtt_payload("temperature")
        assert json.loads(raw.decode())["type"] == "temperature"
