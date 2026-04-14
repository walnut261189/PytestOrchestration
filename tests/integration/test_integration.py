# ─────────────────────────────────────────────────────────────────
# tests/integration/test_integration.py
#
# Integration tests – verify data flows correctly across component
# boundaries.  All Docker containers must be up before running.
#
# Run:
#   pytest tests/integration/ -m integration
# ─────────────────────────────────────────────────────────────────

import pytest
import time
import requests

pytestmark = [pytest.mark.integration]


# ── Fixtures: ensure all services are up ─────────────────────────

@pytest.fixture(scope="module", autouse=True)
def all_services_ready(
    ensure_adapter_ready,
    ensure_router_ready,
    ensure_proxy_ready,
    ensure_processor_ready,
    ensure_landing_ready,
):
    """Block until every component passes its health check."""
    pass


# ── Adapter → Router ──────────────────────────────────────────────

class TestAdapterToRouter:

    def test_payload_published_to_adapter_appears_in_router_metrics(
        self, adapter_url, router_url, http_client, sample_device_payload
    ):
        before = http_client.get(f"{router_url}/metrics", timeout=5).json()
        before_count = before.get("messages_routed", before.get("count", 0))

        http_client.post(f"{adapter_url}/publish", json=sample_device_payload(), timeout=10)
        time.sleep(2)

        after = http_client.get(f"{router_url}/metrics", timeout=5).json()
        after_count = after.get("messages_routed", after.get("count", 0))

        assert after_count > before_count, \
            "Router message count did not increase after adapter publish"

    def test_router_routes_to_proxy_on_valid_payload(
        self, adapter_url, proxy_url, http_client, sample_device_payload
    ):
        before = http_client.get(f"{proxy_url}/metrics", timeout=5).json()
        before_count = before.get("requests_received", before.get("count", 0))

        http_client.post(f"{adapter_url}/publish", json=sample_device_payload(), timeout=10)
        time.sleep(2)

        after = http_client.get(f"{proxy_url}/metrics", timeout=5).json()
        after_count = after.get("requests_received", after.get("count", 0))

        assert after_count > before_count, \
            "Proxy did not receive a request after adapter publish"


# ── Router → Proxy ────────────────────────────────────────────────

class TestRouterToProxy:

    def test_router_dispatch_triggers_proxy_forward(
        self, router_url, proxy_url, http_client, sample_device_payload
    ):
        before = http_client.get(f"{proxy_url}/metrics", timeout=5).json()
        before_count = before.get("requests_received", 0)

        http_client.post(
            f"{router_url}/route",
            json=sample_device_payload(device_id="integ-router-proxy"),
            timeout=10
        )
        time.sleep(2)

        after = http_client.get(f"{proxy_url}/metrics", timeout=5).json()
        assert after.get("requests_received", 0) > before_count


# ── Proxy → Processor ─────────────────────────────────────────────

class TestProxyToProcessor:

    def test_proxy_forward_triggers_processor_ingest(
        self, proxy_url, processor_url, http_client, sample_device_payload
    ):
        before = http_client.get(f"{processor_url}/stats", timeout=5).json()
        before_count = before.get("records_processed", before.get("ingested", 0))

        http_client.post(
            f"{proxy_url}/forward",
            json=sample_device_payload(device_id="integ-proxy-proc"),
            timeout=10
        )
        time.sleep(2)

        after = http_client.get(f"{processor_url}/stats", timeout=5).json()
        assert after.get("records_processed", after.get("ingested", 0)) > before_count


# ── Processor → CrateDB ───────────────────────────────────────────

class TestProcessorToDatabase:

    def test_ingest_writes_to_cratedb(
        self, processor_url, db_config, http_client, sample_device_payload
    ):
        device_id = f"integ-db-{int(time.time())}"
        r = http_client.post(
            f"{processor_url}/ingest",
            json=sample_device_payload(device_id=device_id, voltage=218.5),
            timeout=10
        )
        assert r.status_code in (200, 201, 202)
        time.sleep(2)  # CrateDB refresh interval

        # Query CrateDB directly
        cratedb_url = f"http://{db_config.host}:{db_config.http_port}/_sql"
        result = http_client.post(
            cratedb_url,
            json={"stmt": "SELECT voltage FROM doc.device_readings "
                          "WHERE device_id = ? LIMIT 1",
                  "args": [device_id]},
            timeout=10
        )
        rows = result.json().get("rows", [])
        assert len(rows) > 0, f"No rows found in CrateDB for device {device_id}"
        assert rows[0][0] == pytest.approx(218.5, rel=0.01)

    def test_ingest_timestamp_persisted(
        self, processor_url, db_config, http_client, sample_device_payload
    ):
        device_id = f"integ-ts-{int(time.time())}"
        http_client.post(
            f"{processor_url}/ingest",
            json=sample_device_payload(device_id=device_id),
            timeout=10
        )
        time.sleep(2)

        cratedb_url = f"http://{db_config.host}:{db_config.http_port}/_sql"
        result = http_client.post(
            cratedb_url,
            json={"stmt": "SELECT timestamp FROM doc.device_readings "
                          "WHERE device_id = ? LIMIT 1",
                  "args": [device_id]},
            timeout=10
        )
        rows = result.json().get("rows", [])
        assert len(rows) > 0
        assert rows[0][0] is not None


# ── Full adapter-to-DB pipeline ───────────────────────────────────

class TestAdapterToDatabasePipeline:

    def test_full_pipeline_data_appears_in_db(
        self, adapter_url, db_config, http_client, sample_device_payload
    ):
        """
        Publish via adapter → router → proxy → processor → CrateDB.
        Confirm the row lands in the DB.
        """
        device_id = f"e2e-pipe-{int(time.time())}"
        r = http_client.post(
            f"{adapter_url}/publish",
            json=sample_device_payload(device_id=device_id, voltage=240.0),
            timeout=10
        )
        assert r.status_code in (200, 201, 202), f"Adapter publish failed: {r.status_code}"

        # Allow pipeline propagation time
        time.sleep(5)

        cratedb_url = f"http://{db_config.host}:{db_config.http_port}/_sql"
        result = http_client.post(
            cratedb_url,
            json={"stmt": "SELECT COUNT(*) FROM doc.device_readings "
                          "WHERE device_id = ?",
                  "args": [device_id]},
            timeout=10
        )
        count = result.json()["rows"][0][0]
        assert count >= 1, f"Expected ≥1 row in DB for {device_id}, found {count}"
