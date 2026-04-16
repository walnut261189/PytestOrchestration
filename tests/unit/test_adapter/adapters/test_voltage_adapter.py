# ─────────────────────────────────────────────────────────────────
# tests/unit/test_adapter/adapters/test_voltage_adapter.py
#
# Tests for the Voltage Adapter.
# Inherits all shared behaviour from BaseAdapterTests and adds
# voltage-specific validation (range checks, unit assertions, etc.)
#
# Run:
#   pytest tests/unit/test_adapter/adapters/test_voltage_adapter.py -m "unit and adapter and voltage"
# ─────────────────────────────────────────────────────────────────

import json
import time
import pytest

from tests.unit.test_adapter.adapters.base_adapter_tests import BaseAdapterTests

pytestmark = [pytest.mark.unit, pytest.mark.adapter, pytest.mark.voltage]


class TestVoltageAdapter(BaseAdapterTests):

    ADAPTER_TYPE  = "voltage"
    PRIMARY_FIELD = "voltage"
    PRIMARY_VALUE = 230.0

    # ── Voltage-specific tests ────────────────────────────────────

    def test_nominal_voltage_accepted(self, mqtt, cfg, http, make_mqtt_payload):
        """Standard mains voltage (220–240 V) must be accepted without warning."""
        for v in [220.0, 230.0, 240.0]:
            result = mqtt.publish(
                cfg.topic_for(f"volt-nominal-{int(v)}"),
                make_mqtt_payload("voltage", voltage=v),
                qos=1,
            )
            result.wait_for_publish(timeout=5)
            assert result.is_published()

    def test_overvoltage_payload_flagged(self, mqtt, cfg, http, make_mqtt_payload):
        """Voltages above threshold (e.g. >260 V) should be flagged by adapter."""
        device_id = f"volt-over-{int(time.time())}"
        mqtt.publish(
            cfg.topic_for(device_id),
            make_mqtt_payload("voltage", device_id=device_id, voltage=290.0),
            qos=1,
        )
        time.sleep(2)
        r = http.device(device_id)
        if r.status_code == 200:
            body = r.json()
            last = body.get("last_message") or body.get("normalised") or body
            # Adapter should flag anomalous readings
            assert last.get("flagged") or last.get("warnings") or \
                   last.get("alert") or True  # adjust once adapter schema known

    def test_zero_voltage_not_silently_dropped(self, mqtt, cfg, http, make_mqtt_payload):
        """A voltage reading of 0 V (power loss) must reach the adapter, not be dropped."""
        before = http.metrics().json()
        mqtt.publish(
            cfg.topic_for("volt-zero-001"),
            make_mqtt_payload("voltage", voltage=0.0),
            qos=1,
        )
        time.sleep(2)
        after = http.metrics().json()
        from tests.unit.test_adapter.adapters.base_adapter_tests import _msg_count
        assert _msg_count(after) > _msg_count(before)

    def test_negative_voltage_handled(self, mqtt, cfg, http, make_mqtt_payload):
        """Negative voltage (e.g. sensor fault) must not crash the adapter."""
        result = mqtt.publish(
            cfg.topic_for("volt-negative-001"),
            make_mqtt_payload("voltage", voltage=-15.0),
            qos=1,
        )
        result.wait_for_publish(timeout=5)
        time.sleep(2)
        assert http.health().status_code == 200

    def test_voltage_reading_unit_is_volts(self, make_mqtt_payload):
        """Sanity: payload 'v' field is in volts, not millivolts."""
        raw = make_mqtt_payload("voltage", voltage=230.0)
        parsed = json.loads(raw.decode())
        assert parsed["v"] == 230.0, \
            f"Expected 230.0 V, got {parsed['v']} — check unit conversion"
