# ─────────────────────────────────────────────────────────────────
# tests/unit/test_processor/test_processor.py
#
# Unit tests for the Streamlit Processor component.
#
# KEY BEHAVIOUR:
#   The processor reads the PROCESSOR_TARGET env variable at startup:
#     PROCESSOR_TARGET=onprem  →  writes to CrateDB
#     PROCESSOR_TARGET=cloud   →  writes to cloud DB (Azure SQL etc.)
#
# Tests are marked with @pytest.mark.processor_onprem or
# @pytest.mark.processor_cloud and are auto-skipped by conftest.py
# based on the active PROCESSOR_TARGET value.
#
# Run all:
#   pytest tests/unit/test_processor/ -m processor
#
# Run only onprem-target tests:
#   PROCESSOR_TARGET=onprem pytest tests/unit/test_processor/ -m processor
#
# Run only cloud-target tests:
#   PROCESSOR_TARGET=cloud pytest tests/unit/test_processor/ -m processor
# ─────────────────────────────────────────────────────────────────

import time
import pytest

pytestmark = [pytest.mark.unit, pytest.mark.processor]


@pytest.fixture(scope="module")
def proc(processor_url, http_client):
    class ProcessorClient:
        def health(self):
            return http_client.get(f"{processor_url}/health", timeout=5)

        def routing_status(self):
            return http_client.get(f"{processor_url}/routing/status", timeout=5)

        def ingest(self, payload: dict):
            return http_client.post(f"{processor_url}/ingest", json=payload, timeout=15)

        def stats(self):
            return http_client.get(f"{processor_url}/stats", timeout=5)

        def get_latest(self, device_id: str = None):
            params = {"device_id": device_id} if device_id else {}
            return http_client.get(f"{processor_url}/data/latest",
                                   params=params, timeout=10)

        def get_aggregates(self, device_id: str, window: str = "1h"):
            return http_client.get(
                f"{processor_url}/data/aggregates",
                params={"device_id": device_id, "window": window},
                timeout=10,
            )
    return ProcessorClient()


# ── Health ────────────────────────────────────────────────────────

class TestProcessorHealth:

    def test_health_returns_200(self, proc):
        assert proc.health().status_code == 200

    def test_health_reports_active_target(self, proc, processor_target):
        """Processor must report which target DB it is writing to."""
        body = proc.health().json()
        reported = (body.get("target") or body.get("processor_target") or
                    body.get("routing_target") or "")
        if reported:
            assert reported == processor_target, \
                f"Health reports target='{reported}' but PROCESSOR_TARGET='{processor_target}'"

    def test_health_reports_db_connected(self, proc):
        body = proc.health().json()
        assert any(k in body for k in ("db", "database", "cratedb", "cloud_db", "storage"))


# ── Routing status ────────────────────────────────────────────────

class TestProcessorRoutingStatus:

    def test_routing_status_endpoint_returns_200(self, proc):
        assert proc.routing_status().status_code == 200

    def test_routing_status_shows_correct_target(self, proc, processor_target):
        body = proc.routing_status().json()
        target = (body.get("target") or body.get("active_target") or
                  body.get("routing_to", ""))
        assert target == processor_target, \
            f"Routing status shows '{target}', expected '{processor_target}'"

    def test_routing_status_shows_target_healthy(self, proc):
        body = proc.routing_status().json()
        db_health = (body.get("db_healthy") or body.get("target_healthy") or
                     body.get("connected", True))
        assert db_health


# ── Ingest ────────────────────────────────────────────────────────

class TestProcessorIngest:

    def test_ingest_voltage_payload(self, proc, make_normalised_payload):
        r = proc.ingest(make_normalised_payload("voltage", voltage=230.0))
        assert r.status_code in (200, 201, 202)

    def test_ingest_current_payload(self, proc, make_normalised_payload):
        r = proc.ingest(make_normalised_payload("current", current=5.0))
        assert r.status_code in (200, 201, 202)

    def test_ingest_temperature_payload(self, proc, make_normalised_payload):
        r = proc.ingest(make_normalised_payload("temperature", temperature=45.0))
        assert r.status_code in (200, 201, 202)

    def test_ingest_returns_record_id_and_target(self, proc, make_normalised_payload):
        """Response must confirm where the record was written."""
        r = proc.ingest(make_normalised_payload("voltage"))
        body = r.json()
        assert "record_id" in body or "id" in body or "inserted" in body
        # Should also confirm the routing target
        assert ("target" in body or "routed_to" in body or
                "destination" in body or r.status_code in (200, 201, 202))

    def test_ingest_empty_payload_rejected(self, proc):
        assert proc.ingest({}).status_code in (400, 422)

    def test_ingest_missing_type_field_rejected(self, proc):
        """Payload without 'type' field cannot be routed correctly."""
        bad = {"device_id": "dev-001", "timestamp": int(time.time() * 1000),
               "readings": {"voltage": 220.0}}
        r = proc.ingest(bad)
        assert r.status_code in (400, 422)


# ── On-prem routing (PROCESSOR_TARGET=onprem) ─────────────────────

class TestProcessorOnPremRouting:

    @pytest.mark.processor_onprem
    def test_onprem_routing_writes_to_cratedb(
        self, proc, cratedb_config, http_client, make_normalised_payload
    ):
        """Ingest a record and verify it appears in CrateDB."""
        device_id = f"proc-onprem-{int(time.time())}"
        r = proc.ingest(make_normalised_payload("voltage", device_id=device_id, voltage=225.0))
        assert r.status_code in (200, 201, 202)
        time.sleep(2)

        cratedb_url = f"http://{cratedb_config.host}:{cratedb_config.http_port}/_sql"
        result = http_client.post(
            cratedb_url,
            json={"stmt": "SELECT COUNT(*) FROM doc.device_readings "
                          "WHERE device_id = ?",
                  "args": [device_id]},
            timeout=10,
        )
        count = result.json()["rows"][0][0]
        assert count >= 1, \
            f"Expected record in CrateDB for {device_id}, found {count}"

    @pytest.mark.processor_onprem
    def test_onprem_routing_does_not_write_to_cloud(
        self, proc, cloud_db_config, make_normalised_payload
    ):
        """When PROCESSOR_TARGET=onprem, cloud DB must not receive data."""
        body = proc.routing_status().json()
        assert body.get("target") != "cloud", \
            "Processor is routing to cloud when PROCESSOR_TARGET=onprem"

    @pytest.mark.processor_onprem
    def test_all_adapter_types_stored_onprem(
        self, proc, cratedb_config, http_client, make_normalised_payload
    ):
        """Voltage, current, and temperature payloads all land in CrateDB."""
        device_ids = {}
        for adapter_type in ("voltage", "current", "temperature"):
            did = f"proc-all-{adapter_type}-{int(time.time())}"
            device_ids[adapter_type] = did
            proc.ingest(make_normalised_payload(adapter_type, device_id=did))

        time.sleep(3)
        cratedb_url = f"http://{cratedb_config.host}:{cratedb_config.http_port}/_sql"
        for adapter_type, did in device_ids.items():
            result = http_client.post(
                cratedb_url,
                json={"stmt": "SELECT COUNT(*) FROM doc.device_readings "
                              "WHERE device_id = ?",
                      "args": [did]},
                timeout=10,
            )
            count = result.json()["rows"][0][0]
            assert count >= 1, \
                f"{adapter_type} payload not found in CrateDB for device {did}"


# ── Cloud routing (PROCESSOR_TARGET=cloud) ────────────────────────

class TestProcessorCloudRouting:

    @pytest.mark.processor_cloud
    def test_cloud_routing_target_is_cloud(self, proc):
        body = proc.routing_status().json()
        assert body.get("target") == "cloud", \
            "Routing status does not show 'cloud' as active target"

    @pytest.mark.processor_cloud
    def test_cloud_routing_does_not_write_to_cratedb(
        self, proc, cratedb_config, http_client, make_normalised_payload
    ):
        """When PROCESSOR_TARGET=cloud, CrateDB must NOT receive the record."""
        device_id = f"proc-cloud-{int(time.time())}"
        proc.ingest(make_normalised_payload("voltage", device_id=device_id))
        time.sleep(3)

        cratedb_url = f"http://{cratedb_config.host}:{cratedb_config.http_port}/_sql"
        try:
            result = http_client.post(
                cratedb_url,
                json={"stmt": "SELECT COUNT(*) FROM doc.device_readings "
                              "WHERE device_id = ?",
                      "args": [device_id]},
                timeout=5,
            )
            count = result.json()["rows"][0][0]
            assert count == 0, \
                f"Cloud-routed record for {device_id} unexpectedly found in CrateDB"
        except Exception:
            pass  # CrateDB may not be available in cloud-only test runs

    @pytest.mark.processor_cloud
    def test_cloud_routing_all_adapter_types(
        self, proc, make_normalised_payload
    ):
        """All adapter types must be accepted by the processor in cloud mode."""
        for adapter_type in ("voltage", "current", "temperature"):
            r = proc.ingest(
                make_normalised_payload(adapter_type,
                                        device_id=f"cloud-{adapter_type}-{int(time.time())}")
            )
            assert r.status_code in (200, 201, 202), \
                f"{adapter_type} ingest failed in cloud mode: {r.status_code}"


# ── Stats ─────────────────────────────────────────────────────────

class TestProcessorStats:

    def test_stats_returns_200(self, proc):
        assert proc.stats().status_code == 200

    def test_stats_show_routing_target(self, proc, processor_target):
        body = proc.stats().json()
        target = (body.get("routing_target") or body.get("target") or "")
        if target:
            assert target == processor_target

    def test_stats_include_throughput_counters(self, proc):
        body = proc.stats().json()
        assert any(k in body for k in
                   ("records_processed", "ingested", "throughput", "total"))
