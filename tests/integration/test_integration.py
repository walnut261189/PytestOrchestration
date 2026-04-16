# ─────────────────────────────────────────────────────────────────
# tests/integration/test_integration.py
#
# Integration tests – verify data flows correctly across component
# boundaries.  Entry point is always MQTT publish to the broker
# (Mosquitto inside the Adapter), which then drives the rest of
# the pipeline over HTTP.
#
# Flow under test:
#   MQTT publish → Adapter (normalise) → Router → Proxy → Processor → CrateDB
#
# Run:
#   pytest tests/integration/ -m integration
# ─────────────────────────────────────────────────────────────────

import json
import time
import pytest

pytestmark = [pytest.mark.integration]

PROPAGATION_SEC = 4  # seconds to wait for a message to traverse the pipeline


@pytest.fixture(scope="module", autouse=True)
def all_services_ready(
    ensure_mqtt_ready,
    ensure_adapter_ready,
    ensure_router_ready,
    ensure_proxy_ready,
    ensure_processor_ready,
    ensure_landing_ready,
):
    """Block until every component is healthy before running any integration test."""
    pass


# ── MQTT → Adapter ────────────────────────────────────────────────

class TestMQTTToAdapter:

    def test_mqtt_publish_increments_adapter_message_count(
        self, mqtt_client, mqtt_config, adapter_url, http_client, make_raw_mqtt_payload
    ):
        before = http_client.get(f"{adapter_url}/metrics", timeout=5).json()
        before_count = _count(before, ("messages_received", "total_messages", "count"))

        mqtt_client.publish(
            mqtt_config.topic_for("integ-dev-001"),
            make_raw_mqtt_payload(device_id="integ-dev-001"),
            qos=mqtt_config.qos,
        )
        time.sleep(PROPAGATION_SEC)

        after = http_client.get(f"{adapter_url}/metrics", timeout=5).json()
        after_count = _count(after, ("messages_received", "total_messages", "count"))
        assert after_count > before_count, \
            "Adapter message count did not increase after MQTT publish"

    def test_raw_csv_publish_processed_by_adapter(
        self, mqtt_client, mqtt_config, adapter_url, http_client, make_raw_mqtt_payload
    ):
        """Legacy raw CSV payloads must also be processed without error."""
        before = http_client.get(f"{adapter_url}/metrics", timeout=5).json()
        before_count = _count(before, ("messages_received", "total_messages", "count"))

        mqtt_client.publish(
            mqtt_config.topic_for("integ-csv-001"),
            make_raw_mqtt_payload(voltage=218.0, current=3.0, raw=True),
            qos=mqtt_config.qos,
        )
        time.sleep(PROPAGATION_SEC)

        after = http_client.get(f"{adapter_url}/metrics", timeout=5).json()
        after_count = _count(after, ("messages_received", "total_messages", "count"))
        assert after_count > before_count


# ── Adapter → Router ──────────────────────────────────────────────

class TestAdapterToRouter:

    def test_mqtt_publish_reaches_router(
        self, mqtt_client, mqtt_config, router_url, http_client, make_raw_mqtt_payload
    ):
        before = http_client.get(f"{router_url}/metrics", timeout=5).json()
        before_count = _count(before, ("messages_routed", "total_messages", "count"))

        mqtt_client.publish(
            mqtt_config.topic_for("integ-router-001"),
            make_raw_mqtt_payload(device_id="integ-router-001", voltage=225.0),
            qos=mqtt_config.qos,
        )
        time.sleep(PROPAGATION_SEC)

        after = http_client.get(f"{router_url}/metrics", timeout=5).json()
        after_count = _count(after, ("messages_routed", "total_messages", "count"))
        assert after_count > before_count, \
            "Router message count did not increase after MQTT publish"


# ── Router → Proxy ────────────────────────────────────────────────

class TestRouterToProxy:

    def test_mqtt_publish_reaches_proxy(
        self, mqtt_client, mqtt_config, proxy_url, http_client, make_raw_mqtt_payload
    ):
        before = http_client.get(f"{proxy_url}/metrics", timeout=5).json()
        before_count = _count(before, ("requests_received", "forwarded", "count"))

        mqtt_client.publish(
            mqtt_config.topic_for("integ-proxy-001"),
            make_raw_mqtt_payload(device_id="integ-proxy-001"),
            qos=mqtt_config.qos,
        )
        time.sleep(PROPAGATION_SEC)

        after = http_client.get(f"{proxy_url}/metrics", timeout=5).json()
        after_count = _count(after, ("requests_received", "forwarded", "count"))
        assert after_count > before_count, \
            "Proxy did not receive a forwarded request after MQTT publish"


# ── Proxy → Processor ─────────────────────────────────────────────

class TestProxyToProcessor:

    def test_mqtt_publish_reaches_processor(
        self, mqtt_client, mqtt_config, processor_url, http_client, make_raw_mqtt_payload
    ):
        before = http_client.get(f"{processor_url}/stats", timeout=5).json()
        before_count = _count(before, ("records_processed", "ingested", "count"))

        mqtt_client.publish(
            mqtt_config.topic_for("integ-proc-001"),
            make_raw_mqtt_payload(device_id="integ-proc-001", voltage=232.0),
            qos=mqtt_config.qos,
        )
        time.sleep(PROPAGATION_SEC)

        after = http_client.get(f"{processor_url}/stats", timeout=5).json()
        after_count = _count(after, ("records_processed", "ingested", "count"))
        assert after_count > before_count, \
            "Processor ingest count did not increase after MQTT publish"


# ── Processor → CrateDB ───────────────────────────────────────────

class TestProcessorToDatabase:

    def test_mqtt_publish_writes_to_cratedb(
        self, mqtt_client, mqtt_config, db_config, http_client, make_raw_mqtt_payload
    ):
        device_id = f"integ-db-{int(time.time())}"
        mqtt_client.publish(
            mqtt_config.topic_for(device_id),
            make_raw_mqtt_payload(device_id=device_id, voltage=219.5),
            qos=1,
        )
        time.sleep(PROPAGATION_SEC + 2)  # extra for CrateDB refresh

        cratedb_url = f"http://{db_config.host}:{db_config.http_port}/_sql"
        result = http_client.post(
            cratedb_url,
            json={"stmt": "SELECT voltage FROM doc.device_readings "
                          "WHERE device_id = ? LIMIT 1",
                  "args": [device_id]},
            timeout=10,
        )
        rows = result.json().get("rows", [])
        assert len(rows) > 0, \
            f"No CrateDB row found for device {device_id} after MQTT publish"
        assert rows[0][0] == pytest.approx(219.5, rel=0.01)

    def test_raw_csv_payload_correctly_stored(
        self, mqtt_client, mqtt_config, db_config, http_client, make_raw_mqtt_payload
    ):
        """Raw CSV from a legacy device must be normalised and stored."""
        device_id = f"integ-csv-{int(time.time())}"
        # Publish raw "voltage,current,temperature"
        raw = f"221.0,3.5,46.0".encode()
        mqtt_client.publish(mqtt_config.topic_for(device_id), raw, qos=1)
        time.sleep(PROPAGATION_SEC + 2)

        cratedb_url = f"http://{db_config.host}:{db_config.http_port}/_sql"
        result = http_client.post(
            cratedb_url,
            json={"stmt": "SELECT COUNT(*) FROM doc.device_readings "
                          "WHERE device_id = ?",
                  "args": [device_id]},
            timeout=10,
        )
        count = result.json()["rows"][0][0]
        assert count >= 1, \
            f"Raw CSV payload for {device_id} was not persisted to CrateDB"


# ── Full MQTT → CrateDB pipeline ──────────────────────────────────

class TestFullMQTTPipeline:

    def test_full_pipeline_single_device(
        self, mqtt_client, mqtt_config, db_config, http_client, make_raw_mqtt_payload
    ):
        device_id = f"integ-full-{int(time.time())}"
        mqtt_client.publish(
            mqtt_config.topic_for(device_id),
            make_raw_mqtt_payload(device_id=device_id, voltage=240.0),
            qos=1,
        )
        time.sleep(PROPAGATION_SEC + 3)

        cratedb_url = f"http://{db_config.host}:{db_config.http_port}/_sql"
        result = http_client.post(
            cratedb_url,
            json={"stmt": "SELECT COUNT(*) FROM doc.device_readings "
                          "WHERE device_id = ?",
                  "args": [device_id]},
            timeout=10,
        )
        assert result.json()["rows"][0][0] >= 1

    def test_full_pipeline_multiple_concurrent_devices(
        self, mqtt_client, mqtt_config, db_config, http_client, make_raw_mqtt_payload
    ):
        """Five devices publishing simultaneously — all must reach CrateDB."""
        device_ids = [f"integ-multi-{i}-{int(time.time())}" for i in range(5)]

        for did in device_ids:
            mqtt_client.publish(
                mqtt_config.topic_for(did),
                make_raw_mqtt_payload(device_id=did),
                qos=1,
            )

        time.sleep(PROPAGATION_SEC + 4)

        cratedb_url = f"http://{db_config.host}:{db_config.http_port}/_sql"
        for did in device_ids:
            result = http_client.post(
                cratedb_url,
                json={"stmt": "SELECT COUNT(*) FROM doc.device_readings "
                              "WHERE device_id = ?",
                      "args": [did]},
                timeout=10,
            )
            count = result.json()["rows"][0][0]
            assert count >= 1, f"No CrateDB row for device {did}"


# ── Helpers ───────────────────────────────────────────────────────

def _count(d: dict, keys: tuple) -> int:
    for k in keys:
        if k in d:
            return int(d[k])
    return 0
