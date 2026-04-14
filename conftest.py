# ─────────────────────────────────────────────────────────────────
# conftest.py  –  Root-level shared fixtures
#
# All fixtures here are available to every test in the project.
# Component-specific fixtures live in tests/<layer>/conftest.py.
# ─────────────────────────────────────────────────────────────────

import os
import time
import pytest
import requests
import logging

from config.env_config import get_config, EnvironmentConfig

logger = logging.getLogger(__name__)


# ── Active config fixture (session-scoped) ────────────────────────

@pytest.fixture(scope="session")
def env_config() -> EnvironmentConfig:
    """Provides the full environment config for the active ENV."""
    cfg = get_config()
    logger.info(f"Running tests against environment: {cfg.name}")
    return cfg


# ── Per-component base-URL fixtures ──────────────────────────────

@pytest.fixture(scope="session")
def adapter_url(env_config):
    c = env_config.adapter
    scheme = "https" if c.tls_enabled else "http"
    return f"{scheme}://{c.host}:{c.port}"


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
def db_config(env_config):
    return env_config.db


@pytest.fixture(scope="session")
def landing_url(env_config):
    c = env_config.landing
    return f"http://{c.host}:{c.port}"


# ── HTTP client fixture ───────────────────────────────────────────

@pytest.fixture(scope="session")
def http_client():
    """A requests.Session with sensible defaults."""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    yield session
    session.close()


# ── Readiness helpers ─────────────────────────────────────────────

def wait_for_service(url: str, timeout: int = 60, interval: int = 3) -> bool:
    """Poll a URL until it returns 200 or timeout is reached."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                logger.info(f"Service ready: {url}")
                return True
        except requests.RequestException:
            pass
        time.sleep(interval)
    raise TimeoutError(f"Service not ready after {timeout}s: {url}")


@pytest.fixture(scope="session", autouse=False)
def ensure_adapter_ready(adapter_url):
    wait_for_service(f"{adapter_url}/health")


@pytest.fixture(scope="session", autouse=False)
def ensure_router_ready(router_url, env_config):
    wait_for_service(f"{router_url}{env_config.router.health_endpoint}")


@pytest.fixture(scope="session", autouse=False)
def ensure_proxy_ready(proxy_url, env_config):
    wait_for_service(f"{proxy_url}{env_config.proxy.health_endpoint}")


@pytest.fixture(scope="session", autouse=False)
def ensure_processor_ready(processor_url, env_config):
    wait_for_service(f"{processor_url}{env_config.processor.health_endpoint}")


@pytest.fixture(scope="session", autouse=False)
def ensure_landing_ready(landing_url, env_config):
    wait_for_service(f"{landing_url}{env_config.landing.health_endpoint}")


# ── Sample IoT payload factory ────────────────────────────────────

@pytest.fixture
def sample_device_payload():
    """Returns a factory for generating realistic edge-device payloads."""
    def _make(device_id: str = "device-001",
              voltage: float = 220.5,
              current: float = 3.2,
              temperature: float = 45.0,
              extra: dict = None):
        payload = {
            "device_id": device_id,
            "timestamp": int(time.time() * 1000),
            "readings": {
                "voltage": voltage,
                "current": current,
                "temperature": temperature,
                "power": round(voltage * current, 3),
            }
        }
        if extra:
            payload.update(extra)
        return payload
    return _make


# ── Environment-skip helpers ──────────────────────────────────────

def pytest_configure(config):
    """Register custom markers so --strict-markers doesn't error."""
    pass   # markers are declared in pytest.ini


def pytest_collection_modifyitems(config, items):
    """
    Auto-skip tests marked @pytest.mark.onprem when ENV=cloud,
    and vice-versa. Tests with neither marker run in both.
    """
    current_env = os.environ.get("ENV", "onprem")
    for item in items:
        if "onprem" in item.keywords and current_env != "onprem":
            item.add_marker(pytest.mark.skip(reason="Skipped: not an on-prem run"))
        if "cloud" in item.keywords and current_env != "cloud":
            item.add_marker(pytest.mark.skip(reason="Skipped: not a cloud run"))
