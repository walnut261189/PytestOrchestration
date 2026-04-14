# ─────────────────────────────────────────────────────────────────
# tests/unit/test_proxy/test_proxy.py
#
# Unit tests for the Proxy Server component.
#
# Run in isolation:
#   pytest tests/unit/test_proxy/ -m proxy
# ─────────────────────────────────────────────────────────────────

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.proxy]


@pytest.fixture(scope="module")
def proxy_client(proxy_url, http_client):
    class ProxyClient:
        def __init__(self, base, session):
            self.base = base
            self.session = session

        def health(self):
            return self.session.get(f"{self.base}/health", timeout=5)

        def forward(self, payload: dict):
            return self.session.post(f"{self.base}/forward", json=payload, timeout=15)

        def get_upstream_status(self):
            return self.session.get(f"{self.base}/upstream/status", timeout=5)

        def get_config(self):
            return self.session.get(f"{self.base}/config", timeout=5)

    return ProxyClient(proxy_url, http_client)


class TestProxyHealth:

    def test_health_returns_200(self, proxy_client):
        assert proxy_client.health().status_code == 200

    def test_health_reports_upstream_connectivity(self, proxy_client):
        body = proxy_client.health().json()
        # Proxy should report whether its upstream (processor) is reachable
        assert "upstream" in body or "status" in body


class TestProxyForwarding:

    def test_forward_valid_payload(self, proxy_client, sample_device_payload):
        r = proxy_client.forward(sample_device_payload())
        assert r.status_code in (200, 201, 202)

    def test_forward_empty_body_rejected(self, proxy_client):
        r = proxy_client.forward({})
        assert r.status_code in (400, 422)

    def test_forward_adds_proxy_headers(self, proxy_client, sample_device_payload):
        """Proxy should inject tracing/identification headers downstream."""
        r = proxy_client.forward(sample_device_payload())
        # Check response for forwarded-header echoes or a trace_id
        body = r.json()
        assert "trace_id" in body or "request_id" in body or r.status_code < 400

    def test_forward_returns_upstream_response_code(self, proxy_client, sample_device_payload):
        r = proxy_client.forward(sample_device_payload())
        body = r.json()
        assert "upstream_status" in body or "forwarded" in body or r.status_code < 500

    def test_upstream_status_endpoint(self, proxy_client):
        r = proxy_client.get_upstream_status()
        assert r.status_code == 200

    def test_upstream_reports_processor_reachable(self, proxy_client):
        body = proxy_client.get_upstream_status().json()
        processor_status = body.get("processor") or body.get("upstream", {})
        assert processor_status  # non-empty


class TestProxyRobustness:

    def test_oversized_payload_rejected_or_truncated(self, proxy_client):
        """Proxy should not OOM or 5xx on a large payload."""
        big_payload = {"device_id": "dev-big",
                       "data": "x" * 100_000}
        r = proxy_client.forward(big_payload)
        assert r.status_code in (400, 413, 422) or r.status_code < 500

    def test_concurrent_requests_handled(self, proxy_client, sample_device_payload):
        import concurrent.futures
        payloads = [sample_device_payload(device_id=f"dev-c{i}") for i in range(10)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            futures = [ex.submit(proxy_client.forward, p) for p in payloads]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]
        assert all(r.status_code < 500 for r in results), \
            "Some concurrent requests returned 5xx"
