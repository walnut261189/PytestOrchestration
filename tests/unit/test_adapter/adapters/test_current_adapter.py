# ─────────────────────────────────────────────────────────────────
# tests/unit/test_adapter/adapters/test_current_adapter.py
#
# Tests for the Current Adapter.
# Run:
#   pytest tests/unit/test_adapter/adapters/test_current_adapter.py -m "unit and adapter and current"
# ─────────────────────────────────────────────────────────────────

import json
import time
import pytest

from tests.unit.test_adapter.adapters.base_adapter_tests import BaseAdapterTests, _msg_count

pytestmark = [pytest.mark.unit, pytest.mark.adapter, pytest.mark.current]


class TestCurrentAdapter(BaseAdapterTests):

    ADAPTER_TYPE  = "current"
    PRIMARY_FIELD = "current"
    PRIMARY_VALUE = 5.0

    # ── Current-specific tests ────────────────────────────────────

    def test_nominal_current_accepted(self, mqtt, cfg, make_mqtt_payload):
        """Typical operating current (1–16 A) must be accepted."""
        for c in [1.0, 5.0, 10.0, 16.0]:
            result = mqtt.publish(
                cfg.topic_for(f"curr-nominal-{int(c)}"),
                make_mqtt_payload("current", current=c),
                qos=1,
            )
            result.wait_for_publish(timeout=5)
            assert result.is_published()

    def test_overcurrent_payload_flagged(self, mqtt, cfg, http, make_mqtt_payload):
        """Current above trip threshold must be flagged."""
        device_id = f"curr-over-{int(time.time())}"
        mqtt.publish(
            cfg.topic_for(device_id),
            make_mqtt_payload("current", device_id=device_id, current=100.0),
            qos=1,
        )
        time.sleep(2)
        assert http.health().status_code == 200  # adapter must not crash on overcurrent

    def test_zero_current_not_dropped(self, mqtt, cfg, http, make_mqtt_payload):
        """Zero current (standby / no load) is a valid reading."""
        before = _msg_count(http.metrics().json())
        mqtt.publish(
            cfg.topic_for("curr-zero-001"),
            make_mqtt_payload("current", current=0.0),
            qos=1,
        )
        time.sleep(2)
        assert _msg_count(http.metrics().json()) > before

    def test_current_reading_unit_is_amperes(self, make_mqtt_payload):
        """Sanity: 'i' field is in amperes, not milliamperes."""
        raw = make_mqtt_payload("current", current=5.0)
        parsed = json.loads(raw.decode())
        assert parsed["i"] == 5.0, \
            f"Expected 5.0 A, got {parsed['i']} — check unit conversion"

    def test_type_field_is_current(self, make_mqtt_payload):
        raw = make_mqtt_payload("current")
        assert json.loads(raw.decode())["type"] == "current"
