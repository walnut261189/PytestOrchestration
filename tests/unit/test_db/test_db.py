# ─────────────────────────────────────────────────────────────────
# tests/unit/test_db/test_db.py
#
# Unit tests for CrateDB.
# Uses the CrateDB HTTP endpoint (port 4200) for SQL queries.
#
# Run in isolation:
#   pytest tests/unit/test_db/ -m db
# ─────────────────────────────────────────────────────────────────

import pytest
import time
import requests

pytestmark = [pytest.mark.unit, pytest.mark.db]


@pytest.fixture(scope="module")
def cratedb_http(db_config):
    """CrateDB HTTP SQL endpoint base URL."""
    return f"http://{db_config.host}:{db_config.http_port}"


@pytest.fixture(scope="module")
def sql(cratedb_http, http_client):
    """Execute a SQL statement via CrateDB's HTTP endpoint."""
    def _run(statement: str, args: list = None):
        body = {"stmt": statement}
        if args:
            body["args"] = args
        return http_client.post(
            f"{cratedb_http}/_sql",
            json=body,
            timeout=10
        )
    return _run


class TestCrateDBConnectivity:

    def test_cratedb_http_reachable(self, cratedb_http, http_client):
        r = http_client.get(f"{cratedb_http}/", timeout=5)
        assert r.status_code == 200

    def test_cratedb_cluster_info(self, cratedb_http, http_client):
        r = http_client.get(f"{cratedb_http}/", timeout=5)
        body = r.json()
        assert "name" in body or "cluster_name" in body or "ok" in body

    def test_simple_select_works(self, sql):
        r = sql("SELECT 1 AS val")
        assert r.status_code == 200
        body = r.json()
        assert body["rows"][0][0] == 1


class TestCrateDBSchema:

    def test_device_readings_table_exists(self, sql):
        """The processor should have created the readings table."""
        r = sql(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name = 'device_readings' AND table_schema = 'doc'"
        )
        assert r.status_code == 200
        assert len(r.json()["rows"]) > 0, "Table 'device_readings' not found"

    def test_device_readings_has_required_columns(self, sql):
        r = sql(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'device_readings' AND table_schema = 'doc'"
        )
        body = r.json()
        columns = [row[0] for row in body["rows"]]
        required = {"device_id", "timestamp", "voltage", "current", "temperature"}
        missing = required - set(columns)
        assert not missing, f"Missing columns: {missing}"


class TestCrateDBReadWrite:

    @pytest.fixture(autouse=True)
    def cleanup(self, sql):
        """Remove test rows after each test."""
        yield
        sql("DELETE FROM doc.device_readings WHERE device_id LIKE 'pytest-%'")

    def test_insert_reading(self, sql):
        ts = int(time.time() * 1000)
        r = sql(
            "INSERT INTO doc.device_readings "
            "(device_id, timestamp, voltage, current, temperature) "
            "VALUES (?, ?, ?, ?, ?)",
            args=["pytest-dev-001", ts, 220.5, 3.2, 45.0]
        )
        assert r.status_code == 200
        assert r.json().get("rowcount", 0) == 1

    def test_query_inserted_row(self, sql):
        ts = int(time.time() * 1000)
        sql(
            "INSERT INTO doc.device_readings "
            "(device_id, timestamp, voltage, current, temperature) "
            "VALUES (?, ?, ?, ?, ?)",
            args=["pytest-dev-query", ts, 230.0, 4.0, 50.0]
        )
        time.sleep(1)  # CrateDB write refresh

        r = sql(
            "SELECT voltage FROM doc.device_readings "
            "WHERE device_id = 'pytest-dev-query' LIMIT 1"
        )
        rows = r.json()["rows"]
        assert len(rows) > 0
        assert rows[0][0] == 230.0

    def test_aggregation_query(self, sql):
        ts = int(time.time() * 1000)
        for v in [200.0, 220.0, 240.0]:
            sql(
                "INSERT INTO doc.device_readings "
                "(device_id, timestamp, voltage, current, temperature) "
                "VALUES (?, ?, ?, ?, ?)",
                args=["pytest-dev-agg", ts, v, 3.0, 45.0]
            )
        time.sleep(1)

        r = sql(
            "SELECT AVG(voltage), MIN(voltage), MAX(voltage) "
            "FROM doc.device_readings WHERE device_id = 'pytest-dev-agg'"
        )
        row = r.json()["rows"][0]
        assert row[0] == pytest.approx(220.0, rel=0.01)
        assert row[1] == 200.0
        assert row[2] == 240.0

    def test_delete_removes_rows(self, sql):
        ts = int(time.time() * 1000)
        sql(
            "INSERT INTO doc.device_readings "
            "(device_id, timestamp, voltage, current, temperature) "
            "VALUES (?, ?, ?, ?, ?)",
            args=["pytest-dev-del", ts, 210.0, 2.5, 40.0]
        )
        time.sleep(1)
        r = sql("DELETE FROM doc.device_readings WHERE device_id = 'pytest-dev-del'")
        assert r.json().get("rowcount", 0) >= 1


class TestCrateDBPerformance:

    def test_bulk_insert_completes_within_threshold(self, sql):
        """500 inserts must complete in under 10 seconds."""
        start = time.time()
        ts = int(time.time() * 1000)
        for i in range(500):
            sql(
                "INSERT INTO doc.device_readings "
                "(device_id, timestamp, voltage, current, temperature) "
                "VALUES (?, ?, ?, ?, ?)",
                args=[f"pytest-bulk-{i}", ts + i, 220.0, 3.0, 45.0]
            )
        elapsed = time.time() - start
        # Cleanup
        sql("DELETE FROM doc.device_readings WHERE device_id LIKE 'pytest-bulk-%'")
        assert elapsed < 10, f"Bulk insert took {elapsed:.1f}s (threshold: 10s)"
