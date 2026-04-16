# ─────────────────────────────────────────────────────────────────
# conftest.py  –  Root-level shared fixtures
#
# Key design decisions reflected here:
#
#   1. Multiple adapter types — each with its own embedded Mosquitto.
#      The `adapter_config` fixture takes a type_name and returns
#      the right AdapterInstanceConfig.
#      The `mqtt_client_for` fixture returns a connected paho client
#      for any given adapter type.
#
#   2. Processor routing — PROCESSOR_TARGET=onprem routes to CrateDB,
#      PROCESSOR_TARGET=cloud routes to cloud DB. Tests use
#      `processor_target` fixture to know which DB to assert against.
#
#   3. All payloads carry a "type" field that identifies the adapter.
#      The `make_mqtt_payload` factory injects this automatically.
# ─────────────────────────────────────────────────────────────────

import os
import time
import json
import socket
import logging
import threading
import pytest
import requests

from config.env_config import get_config, EnvironmentConfig, AdapterInstanceConfig
from utils.adapter_registry import ADAPTER_REGISTRY, AdapterTypeSpec

logger = logging.getLogger(__name__)


# ── Active config ─────────────────────────────────────────────────

@pytest.fixture(scope="session")
def env_config() -> EnvironmentConfig:
    cfg = get_config()
    logger.info(f"Environment: {cfg.name} | Processor target: {cfg.processor_target}")
    return cfg


@pytest.fixture(scope="session")
def processor_target(env_config) -> str:
    """'onprem' or 'cloud' — governs which DB to assert against."""
    return env_config.processor_target


@pytest.fixture(scope="session")
def all_adapter_specs() -> list:
    """All registered adapter type specs from the registry."""
    return ADAPTER_REGISTRY


# ── Per-adapter config fixtures ───────────────────────────────────

@pytest.fixture(scope="session")
def adapter_config(env_config):
    """
    Factory: returns AdapterInstanceConfig for a given type_name.

    Usage:
        def test_something(adapter_config):
            cfg = adapter_config("voltage")
            # cfg.mqtt_port, cfg.topic_for("dev-001"), cfg.http_base ...
    """
    def _get(type_name: str) -> AdapterInstanceConfig:
        if type_name not in env_config.adapters:
            raise KeyError(
                f"Adapter type '{type_name}' not found in config. "
                f"Available: {list(env_config.adapters.keys())}"
            )
        return env_config.adapters[type_name]
    return _get


@pytest.fixture(scope="session")
def voltage_adapter_cfg(env_config):
    return env_config.adapters["voltage"]


@pytest.fixture(scope="session")
def current_adapter_cfg(env_config):
    return env_config.adapters["current"]


@pytest.fixture(scope="session")
def temperature_adapter_cfg(env_config):
    return env_config.adapters["temperature"]


# ── MQTT client factory ───────────────────────────────────────────

def _make_mqtt_client(cfg: AdapterInstanceConfig, client_id: str):
    """
    Internal helper: create and connect a paho client to the
    Mosquitto broker embedded in a specific adapter container.
    """
    import paho.mqtt.client as mqtt

    connected = threading.Event()
    client = mqtt.Client(client_id=client_id, clean_session=True)

    if cfg.mqtt_username:
        client.username_pw_set(cfg.mqtt_username, cfg.mqtt_password)
    if cfg.mqtt_tls:
        client.tls_set()

    client.on_connect = lambda c, u, f, rc: connected.set() if rc == 0 else None
    client.on_disconnect = lambda c, u, rc: logger.warning(
        f"MQTT client {client_id} disconnected rc={rc}"
    )

    client.connect(cfg.mqtt_host, cfg.mqtt_port, keepalive=cfg.mqtt_keepalive)
    client.loop_start()

    if not connected.wait(timeout=cfg.mqtt_connect_timeout):
        client.loop_stop()
        raise ConnectionError(
            f"Could not connect to Mosquitto for '{cfg.type_name}' adapter "
            f"at {cfg.mqtt_host}:{cfg.mqtt_port} within {cfg.mqtt_connect_timeout}s"
        )
    logger.info(f"MQTT client '{client_id}' connected → {cfg.type_name} adapter broker")
    return client


@pytest.fixture(scope="session")
def mqtt_client_for(env_config):
    """
    Factory fixture: returns a connected paho client for any adapter type.
    Clients are cached per type_name for the session.

    Usage:
        def test_voltage(mqtt_client_for):
            client = mqtt_client_for("voltage")
            client.publish(...)
    """
    _clients = {}

    def _get(type_name: str):
        if type_name not in _clients:
            cfg = env_config.adapters[type_name]
            _clients[type_name] = _make_mqtt_client(
                cfg, f"pytest-{type_name}-client"
            )
        return _clients[type_name]

    yield _get

    for client in _clients.values():
        client.loop_stop()
        client.disconnect()


@pytest.fixture(scope="session")
def voltage_mqtt_client(mqtt_client_for):
    return mqtt_client_for("voltage")


@pytest.fixture(scope="session")
def current_mqtt_client(mqtt_client_for):
    return mqtt_client_for("current")


@pytest.fixture(scope="session")
def temperature_mqtt_client(mqtt_client_for):
    return mqtt_client_for("temperature")


# ── Message collector ─────────────────────────────────────────────

@pytest.fixture
def mqtt_message_collector(mqtt_client_for, env_config):
    """
    Factory: subscribe to a topic on a specific adapter's broker
    and collect messages into a list.

    Usage:
        def test_roundtrip(mqtt_message_collector):
            collector = mqtt_message_collector("voltage", topic="devices/#")
            # ... publish ...
            msgs = collector.wait(count=1, timeout=5)
    """
    _active_subscriptions = []

    class Collector:
        def __init__(self, type_name: str, topic: str):
            self.type_name = type_name
            self.topic = topic
            self.messages = []
            self._lock = threading.Lock()
            self._client = mqtt_client_for(type_name)

            def on_message(client, userdata, msg):
                with self._lock:
                    self.messages.append({
                        "topic": msg.topic,
                        "payload": msg.payload.decode("utf-8"),
                        "qos": msg.qos,
                        "retain": msg.retain,
                    })

            cfg = env_config.adapters[type_name]
            self._client.subscribe(topic, qos=cfg.mqtt_qos)
            self._client.on_message = on_message
            _active_subscriptions.append((self._client, topic))

        def wait(self, count: int = 1, timeout: int = 10) -> list:
            deadline = time.time() + timeout
            while time.time() < deadline:
                with self._lock:
                    if len(self.messages) >= count:
                        return list(self.messages)
                time.sleep(0.2)
            return list(self.messages)

        def clear(self):
            with self._lock:
                self.messages.clear()

    def _factory(type_name: str, topic: str = None):
        cfg = env_config.adapters[type_name]
        return Collector(type_name, topic or cfg.device_wildcard_topic)

    yield _factory

    for client, topic in _active_subscriptions:
        try:
            client.unsubscribe(topic)
        except Exception:
            pass


# ── Payload factories ─────────────────────────────────────────────

@pytest.fixture
def make_mqtt_payload():
    """
    Factory for MQTT payloads published by edge devices.
    Automatically injects the adapter "type" field.
    Supports both structured JSON and raw CSV (legacy devices).

    Structured JSON (default):
        {"device_id": "dev-001", "type": "voltage", "ts": 1700000000000,
         "v": 220.5, "i": 3.2, "t": 45.0}

    Raw CSV:
        "220.5,3.2,45.0"
    """
    def _make(adapter_type: str,
              device_id: str = "device-001",
              voltage: float = 220.5,
              current: float = 3.2,
              temperature: float = 45.0,
              raw_csv: bool = False) -> bytes:
        if raw_csv:
            return f"{voltage},{current},{temperature}".encode()
        payload = {
            "device_id": device_id,
            "type": adapter_type,          # ← identifies which adapter
            "ts": int(time.time() * 1000),
            "v": voltage,
            "i": current,
            "t": temperature,
        }
        return json.dumps(payload).encode()
    return _make


@pytest.fixture
def make_normalised_payload():
    """
    Factory for normalised HTTP payloads (post-adapter, router/proxy/processor layer).
    """
    def _make(adapter_type: str,
              device_id: str = "device-001",
              voltage: float = 220.5,
              current: float = 3.2,
              temperature: float = 45.0,
              extra: dict = None):
        payload = {
            "device_id": device_id,
            "type": adapter_type,
            "timestamp": int(time.time() * 1000),
            "readings": {
                "voltage": voltage,
                "current": current,
                "temperature": temperature,
                "power": round(voltage * current, 3),
            },
        }
        if extra:
            payload.update(extra)
        return payload
    return _make


# ── HTTP client ───────────────────────────────────────────────────

@pytest.fixture(scope="session")
def http_client():
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    yield session
    session.close()


# ── Component URL fixtures ────────────────────────────────────────

@pytest.fixture(scope="session")
def router_url(env_config):
    c = env_config.router
    return f"http://{c.host}:{c.port}"


@pytest.fixture(scope="session")
def proxy_url(env_config):
    c = env_config.proxy
    return f"http://{c.host}:{c.port}"


@pytest.fixture(scope="session")
def processor_url(env_config):
    c = env_config.processor
    return f"http://{c.host}:{c.port}"


@pytest.fixture(scope="session")
def cratedb_config(env_config):
    return env_config.cratedb


@pytest.fixture(scope="session")
def cloud_db_config(env_config):
    return env_config.cloud_db


@pytest.fixture(scope="session")
def landing_url(env_config):
    c = env_config.landing
    return f"http://{c.host}:{c.port}"


# ── Readiness helpers ─────────────────────────────────────────────

def wait_for_http(url: str, timeout: int = 60, interval: int = 3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(url, timeout=5).status_code == 200:
                logger.info(f"Ready: {url}")
                return
        except requests.RequestException:
            pass
        time.sleep(interval)
    raise TimeoutError(f"Not ready after {timeout}s: {url}")


def wait_for_tcp(host: str, port: int, timeout: int = 30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=3):
                logger.info(f"TCP ready: {host}:{port}")
                return
        except (ConnectionRefusedError, OSError):
            time.sleep(2)
    raise TimeoutError(f"TCP not ready at {host}:{port} after {timeout}s")


@pytest.fixture(scope="session", autouse=False)
def ensure_adapter_ready(adapter_config, env_config):
    """Waits for ALL adapter HTTP APIs and their brokers to be ready."""
    for type_name, cfg in env_config.adapters.items():
        wait_for_tcp(cfg.mqtt_host, cfg.mqtt_port)
        wait_for_http(f"{cfg.http_base}/health")


@pytest.fixture(scope="session", autouse=False)
def ensure_router_ready(router_url, env_config):
    wait_for_http(f"{router_url}{env_config.router.health_endpoint}")


@pytest.fixture(scope="session", autouse=False)
def ensure_proxy_ready(proxy_url, env_config):
    wait_for_http(f"{proxy_url}{env_config.proxy.health_endpoint}")


@pytest.fixture(scope="session", autouse=False)
def ensure_processor_ready(processor_url, env_config):
    wait_for_http(f"{processor_url}{env_config.processor.health_endpoint}")


@pytest.fixture(scope="session", autouse=False)
def ensure_landing_ready(landing_url, env_config):
    wait_for_http(f"{landing_url}{env_config.landing.health_endpoint}")


# ── Auto-skip by environment ──────────────────────────────────────

def pytest_collection_modifyitems(config, items):
    current_env = os.environ.get("ENV", "onprem")
    current_target = os.environ.get("PROCESSOR_TARGET", "onprem")
    for item in items:
        if "onprem" in item.keywords and current_env != "onprem":
            item.add_marker(pytest.mark.skip(reason="Skipped: not an on-prem run"))
        if "cloud" in item.keywords and current_env != "cloud":
            item.add_marker(pytest.mark.skip(reason="Skipped: not a cloud run"))
        if "processor_onprem" in item.keywords and current_target != "onprem":
            item.add_marker(pytest.mark.skip(reason="Skipped: PROCESSOR_TARGET != onprem"))
        if "processor_cloud" in item.keywords and current_target != "cloud":
            item.add_marker(pytest.mark.skip(reason="Skipped: PROCESSOR_TARGET != cloud"))
