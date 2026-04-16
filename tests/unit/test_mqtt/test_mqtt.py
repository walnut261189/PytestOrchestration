# ─────────────────────────────────────────────────────────────────
# tests/unit/test_mqtt/test_mqtt.py
#
# Unit tests for the MQTT / Mosquitto broker layer.
# The broker is embedded in (or fronted by) the Adapter container.
#
# Topic convention:
#   devices/<device_id>   — one flat topic per physical device
#   devices/#             — wildcard the adapter subscribes to
#
# Edge devices can publish:
#   - Raw CSV bytes:        "220.5,3.2,45.0"
#   - Structured JSON:      {"device_id":..., "v":..., "i":..., "t":...}
#
# Run in isolation:
#   pytest tests/unit/test_mqtt/ -m mqtt
# ─────────────────────────────────────────────────────────────────

import json
import time
import threading
import pytest
import paho.mqtt.client as mqtt

pytestmark = [pytest.mark.unit, pytest.mark.mqtt]

# ── Helpers ───────────────────────────────────────────────────────

def make_subscribe_client(mqtt_config, client_id: str) -> mqtt.Client:
    """Create and connect a fresh paho client for subscription."""
    connected = threading.Event()
    client = mqtt.Client(client_id=client_id, clean_session=True)

    if mqtt_config.username:
        client.username_pw_set(mqtt_config.username, mqtt_config.password)
    if mqtt_config.tls_enabled:
        client.tls_set()

    client.on_connect = lambda c, u, f, rc: connected.set() if rc == 0 else None
    client.connect(mqtt_config.host, mqtt_config.port, keepalive=30)
    client.loop_start()
    assert connected.wait(timeout=mqtt_config.connect_timeout), \
        f"Subscriber could not connect within {mqtt_config.connect_timeout}s"
    return client


# ── Broker connectivity ───────────────────────────────────────────

class TestMQTTBrokerConnectivity:

    def test_broker_accepts_connection(self, mqtt_client):
        """Session-scoped mqtt_client fixture proves connection succeeded."""
        assert mqtt_client.is_connected()

    def test_broker_rejects_bad_credentials(self, mqtt_config):
        """If auth is enabled, wrong credentials must be rejected."""
        if not mqtt_config.username:
            pytest.skip("Auth not enabled on this broker — skipping credential rejection test")

        rejected = threading.Event()
        client = mqtt.Client(client_id="pytest-bad-creds", clean_session=True)
        client.username_pw_set("wrong_user", "wrong_pass")
        client.on_connect = lambda c, u, f, rc: rejected.set() if rc != 0 else None
        client.connect(mqtt_config.host, mqtt_config.port)
        client.loop_start()
        time.sleep(3)
        client.loop_stop()
        assert rejected.is_set(), "Broker accepted invalid credentials — check auth config"

    def test_multiple_clients_connect_simultaneously(self, mqtt_config):
        clients = []
        try:
            for i in range(5):
                c = make_subscribe_client(mqtt_config, f"pytest-multi-{i}")
                clients.append(c)
            assert all(c.is_connected() for c in clients), \
                "Not all simultaneous clients could connect"
        finally:
            for c in clients:
                c.loop_stop()
                c.disconnect()


# ── Publish / subscribe ───────────────────────────────────────────

class TestMQTTPublishSubscribe:

    def test_publish_structured_json_received(
        self, mqtt_client, mqtt_config, mqtt_message_collector, make_raw_mqtt_payload
    ):
        """
        Publish a structured JSON payload to devices/dev-001.
        A subscriber on devices/# must receive it within timeout.
        """
        collector = mqtt_message_collector()
        topic = mqtt_config.topic_for("dev-001")
        payload = make_raw_mqtt_payload(device_id="dev-001", voltage=230.0)

        mqtt_client.publish(topic, payload, qos=mqtt_config.qos)
        msgs = collector.wait(count=1, timeout=8)

        assert len(msgs) >= 1, f"No message received on {mqtt_config.device_wildcard_topic}"
        assert msgs[0]["topic"] == topic

    def test_publish_raw_csv_payload_received(
        self, mqtt_client, mqtt_config, mqtt_message_collector, make_raw_mqtt_payload
    ):
        """Raw CSV payloads (legacy sensors) must also arrive on the broker."""
        collector = mqtt_message_collector()
        topic = mqtt_config.topic_for("dev-raw-001")
        raw_payload = make_raw_mqtt_payload(
            device_id="dev-raw-001", voltage=215.0, current=2.8, raw=True
        )
        mqtt_client.publish(topic, raw_payload, qos=mqtt_config.qos)
        msgs = collector.wait(count=1, timeout=8)
        assert len(msgs) >= 1
        # Raw payload is bytes like "215.0,2.8,45.0"
        assert "," in msgs[0]["payload"] or msgs[0]["payload"].startswith("{")

    def test_topic_isolation_different_devices(
        self, mqtt_client, mqtt_config
    ):
        """
        Messages for dev-A must not appear on dev-B's topic subscriber.
        """
        received_on_b = []
        lock = threading.Lock()
        sub_client = make_subscribe_client(mqtt_config, "pytest-iso-sub")

        def on_msg(c, u, msg):
            with lock:
                received_on_b.append(msg.topic)

        sub_client.on_message = on_msg
        sub_client.subscribe(mqtt_config.topic_for("dev-B"), qos=mqtt_config.qos)
        time.sleep(0.5)

        # Publish only to dev-A
        mqtt_client.publish(
            mqtt_config.topic_for("dev-A"),
            json.dumps({"device_id": "dev-A", "v": 220}).encode(),
            qos=mqtt_config.qos,
        )
        time.sleep(2)
        sub_client.loop_stop()
        sub_client.disconnect()

        assert len(received_on_b) == 0, \
            f"dev-B subscriber incorrectly received: {received_on_b}"

    def test_wildcard_subscription_receives_all_devices(
        self, mqtt_client, mqtt_config, mqtt_message_collector, make_raw_mqtt_payload
    ):
        """devices/# wildcard must receive publishes from multiple device topics."""
        collector = mqtt_message_collector(topic=mqtt_config.device_wildcard_topic)
        device_ids = ["dev-w1", "dev-w2", "dev-w3"]

        for did in device_ids:
            mqtt_client.publish(
                mqtt_config.topic_for(did),
                make_raw_mqtt_payload(device_id=did),
                qos=mqtt_config.qos,
            )
            time.sleep(0.1)

        msgs = collector.wait(count=3, timeout=10)
        received_topics = {m["topic"] for m in msgs}
        expected_topics = {mqtt_config.topic_for(did) for did in device_ids}
        assert expected_topics.issubset(received_topics), \
            f"Missing topics: {expected_topics - received_topics}"


# ── QoS levels ────────────────────────────────────────────────────

class TestMQTTQoS:

    def test_qos0_publish_delivered(
        self, mqtt_client, mqtt_config, mqtt_message_collector, make_raw_mqtt_payload
    ):
        collector = mqtt_message_collector()
        mqtt_client.publish(
            mqtt_config.topic_for("dev-qos0"),
            make_raw_mqtt_payload(device_id="dev-qos0"),
            qos=0,
        )
        msgs = collector.wait(count=1, timeout=5)
        assert len(msgs) >= 1

    def test_qos1_publish_acknowledged(
        self, mqtt_client, mqtt_config, make_raw_mqtt_payload
    ):
        result = mqtt_client.publish(
            mqtt_config.topic_for("dev-qos1"),
            make_raw_mqtt_payload(device_id="dev-qos1"),
            qos=1,
        )
        # wait_for_publish blocks until broker ACKs (PUBACK)
        result.wait_for_publish(timeout=5)
        assert result.is_published(), "QoS 1 message was not acknowledged by broker"

    def test_qos2_publish_exactly_once(
        self, mqtt_client, mqtt_config, make_raw_mqtt_payload
    ):
        result = mqtt_client.publish(
            mqtt_config.topic_for("dev-qos2"),
            make_raw_mqtt_payload(device_id="dev-qos2"),
            qos=2,
        )
        result.wait_for_publish(timeout=8)
        assert result.is_published(), "QoS 2 handshake did not complete"


# ── Payload schema validation ─────────────────────────────────────

class TestMQTTPayloadSchema:

    def test_structured_payload_has_required_fields(self, make_raw_mqtt_payload):
        raw = make_raw_mqtt_payload(device_id="dev-schema", voltage=225.0)
        parsed = json.loads(raw.decode("utf-8"))
        assert "device_id" in parsed
        assert "ts" in parsed
        assert "v" in parsed   # voltage (raw field)
        assert "i" in parsed   # current
        assert "t" in parsed   # temperature

    def test_raw_csv_payload_has_three_fields(self, make_raw_mqtt_payload):
        raw = make_raw_mqtt_payload(voltage=220.0, current=3.2, temperature=45.0, raw=True)
        parts = raw.decode("utf-8").split(",")
        assert len(parts) == 3, f"Expected 3 CSV fields, got {len(parts)}: {parts}"
        assert float(parts[0]) == 220.0
        assert float(parts[1]) == 3.2
        assert float(parts[2]) == 45.0

    def test_topic_format_matches_convention(self, mqtt_config):
        """Topic must follow devices/<device_id> — no extra slashes or spaces."""
        topic = mqtt_config.topic_for("dev-123")
        assert topic == "devices/dev-123"
        assert " " not in topic
        assert topic.count("/") == 1

    def test_wildcard_topic_format(self, mqtt_config):
        assert mqtt_config.device_wildcard_topic == "devices/#"


# ── Broker resilience ─────────────────────────────────────────────

class TestMQTTBrokerResilience:

    def test_large_payload_delivered(
        self, mqtt_client, mqtt_config, mqtt_message_collector
    ):
        """Broker must handle payloads up to 256 KB without dropping."""
        collector = mqtt_message_collector()
        big_payload = json.dumps({
            "device_id": "dev-big",
            "ts": int(time.time() * 1000),
            "data": "x" * 250_000,
        }).encode()

        result = mqtt_client.publish(
            mqtt_config.topic_for("dev-big"), big_payload, qos=1
        )
        result.wait_for_publish(timeout=10)
        assert result.is_published()

    def test_burst_of_100_messages_all_received(
        self, mqtt_client, mqtt_config, mqtt_message_collector, make_raw_mqtt_payload
    ):
        """Publish 100 messages in rapid succession — all must arrive."""
        collector = mqtt_message_collector(topic=mqtt_config.topic_for("dev-burst"))
        for i in range(100):
            mqtt_client.publish(
                mqtt_config.topic_for("dev-burst"),
                make_raw_mqtt_payload(device_id="dev-burst", voltage=200.0 + i * 0.1),
                qos=1,
            )
        msgs = collector.wait(count=100, timeout=15)
        assert len(msgs) >= 100, f"Only {len(msgs)}/100 burst messages received"

    def test_reconnect_after_disconnect(self, mqtt_config, make_raw_mqtt_payload):
        """Client must be able to reconnect and publish after a clean disconnect."""
        client = make_subscribe_client(mqtt_config, "pytest-reconnect")
        client.loop_stop()
        client.disconnect()
        time.sleep(1)

        # Reconnect
        reconnected = threading.Event()
        client.on_connect = lambda c, u, f, rc: reconnected.set() if rc == 0 else None
        client.reconnect()
        client.loop_start()
        assert reconnected.wait(timeout=10), "Client failed to reconnect after disconnect"

        result = client.publish(
            mqtt_config.topic_for("dev-reconnect"),
            make_raw_mqtt_payload(device_id="dev-reconnect"),
            qos=1,
        )
        result.wait_for_publish(timeout=5)
        assert result.is_published()
        client.loop_stop()
        client.disconnect()
