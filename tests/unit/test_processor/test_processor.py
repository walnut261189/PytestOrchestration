# ─────────────────────────────────────────────────────────────────
# tests/unit/test_processor/test_processor.py
#
# Unit tests for the Streamlit Processor component.
# This component receives data, processes/transforms it,
# persists it to CrateDB, and feeds the landing page.
#
# Run in isolation:
#   pytest tests/unit/test_processor/ -m processor
# ─────────────────────────────────────────────────────────────────

import pytest
import time

pytestmark = [pytest.mark.unit, pytest.mark.processor]


@pytest.fixture(scope="module")
def processor_client(processor_url, http_client):
    class ProcessorClient:
        def __init__(self, base, session):
            self.base = base
            self.session = session

        def health(self):
            return self.session.get(f"{self.base}/health", timeout=5)

        def ingest(self, payload: dict):
            return self.session.post(f"{self.base}/ingest", json=payload, timeout=15)

        def get_latest(self, device_id: str = None):
            params = {"device_id": device_id} if device_id else {}
            return self.session.get(f"{self.base}/data/latest", params=params, timeout=10)

        def get_aggregates(self, device_id: str, window: str = "1h"):
            return self.session.get(
                f"{self.base}/data/aggregates",
                params={"device_id": device_id, "window": window},
                timeout=10
            )

        def get_pipeline_stats(self):
            return self.session.get(f"{self.base}/stats", timeout=5)

    return ProcessorClient(processor_url, http_client)


class TestProcessorHealth:

    def test_health_returns_200(self, processor_client):
        assert processor_client.health().status_code == 200

    def test_health_reports_db_connection(self, processor_client):
        body = processor_client.health().json()
        assert "db" in body or "cratedb" in body or "database" in body


class TestProcessorIngest:

    def test_ingest_valid_payload(self, processor_client, sample_device_payload):
        r = processor_client.ingest(sample_device_payload())
        assert r.status_code in (200, 201, 202)

    def test_ingest_returns_record_id(self, processor_client, sample_device_payload):
        r = processor_client.ingest(sample_device_payload())
        body = r.json()
        assert "record_id" in body or "id" in body or "inserted" in body

    def test_ingest_empty_payload_rejected(self, processor_client):
        assert processor_client.ingest({}).status_code in (400, 422)

    def test_ingest_missing_readings_rejected(self, processor_client):
        r = processor_client.ingest({"device_id": "dev-001"})
        assert r.status_code in (400, 422)

    def test_ingest_multiple_readings(self, processor_client, sample_device_payload):
        for i in range(5):
            r = processor_client.ingest(
                sample_device_payload(device_id="dev-multi", voltage=220 + i)
            )
            assert r.status_code in (200, 201, 202)


class TestProcessorDataRetrieval:

    def test_get_latest_returns_200(self, processor_client, sample_device_payload):
        # Seed a reading first
        device_id = "dev-retrieval-test"
        processor_client.ingest(sample_device_payload(device_id=device_id))
        time.sleep(1)  # Allow write to propagate

        r = processor_client.get_latest(device_id=device_id)
        assert r.status_code == 200

    def test_latest_reading_has_expected_fields(self, processor_client, sample_device_payload):
        device_id = "dev-fields-test"
        processor_client.ingest(sample_device_payload(device_id=device_id, voltage=235.0))
        time.sleep(1)

        body = processor_client.get_latest(device_id=device_id).json()
        reading = body if isinstance(body, dict) else body[0]
        assert "voltage" in reading or "readings" in reading

    def test_aggregates_endpoint(self, processor_client, sample_device_payload):
        device_id = "dev-agg-test"
        for _ in range(3):
            processor_client.ingest(sample_device_payload(device_id=device_id))
        time.sleep(1)

        r = processor_client.get_aggregates(device_id=device_id, window="1h")
        assert r.status_code == 200

    def test_aggregates_contain_min_max_avg(self, processor_client, sample_device_payload):
        device_id = "dev-minmax-test"
        for v in [200.0, 220.0, 240.0]:
            processor_client.ingest(sample_device_payload(device_id=device_id, voltage=v))
        time.sleep(1)

        body = processor_client.get_aggregates(device_id=device_id).json()
        assert any(k in str(body).lower() for k in ["min", "max", "avg", "mean"])


class TestProcessorStats:

    def test_stats_endpoint_returns_200(self, processor_client):
        assert processor_client.get_pipeline_stats().status_code == 200

    def test_stats_include_throughput(self, processor_client):
        body = processor_client.get_pipeline_stats().json()
        assert any(k in body for k in ["throughput", "records_processed", "ingested"])
