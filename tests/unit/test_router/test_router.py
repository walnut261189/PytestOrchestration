# ─────────────────────────────────────────────────────────────────
# tests/unit/test_router/test_router.py
#
# Unit tests for the Router component.
# The router receives normalised messages from the adapter and
# dispatches them to the correct downstream service.
#
# Run in isolation:
#   pytest tests/unit/test_router/ -m router
# ─────────────────────────────────────────────────────────────────

import pytest
import requests

pytestmark = [pytest.mark.unit, pytest.mark.router]


@pytest.fixture(scope="module")
def router_client(router_url, http_client):
    class RouterClient:
        def __init__(self, base, session):
            self.base = base
            self.session = session

        def health(self):
            return self.session.get(f"{self.base}/health", timeout=5)

        def get_routes(self):
            return self.session.get(f"{self.base}/routes", timeout=5)

        def route_message(self, payload: dict):
            return self.session.post(f"{self.base}/route", json=payload, timeout=10)

        def get_metrics(self):
            return self.session.get(f"{self.base}/metrics", timeout=5)

    return RouterClient(router_url, http_client)


class TestRouterHealth:

    def test_health_returns_200(self, router_client):
        r = router_client.health()
        assert r.status_code == 200

    def test_health_payload_structure(self, router_client):
        body = router_client.health().json()
        assert "status" in body


class TestRouterRouteTable:

    def test_routes_endpoint_accessible(self, router_client):
        r = router_client.get_routes()
        assert r.status_code == 200

    def test_routes_returns_list(self, router_client):
        routes = router_client.get_routes().json()
        assert isinstance(routes, (list, dict)), "Routes should return a list or map"

    def test_proxy_route_exists(self, router_client):
        """At minimum, a route to the proxy should be registered."""
        routes = router_client.get_routes().json()
        # Adjust based on your router's route schema
        if isinstance(routes, list):
            destinations = [r.get("destination", "") for r in routes]
        else:
            destinations = list(routes.keys())
        assert any("proxy" in d.lower() for d in destinations), \
            "No proxy route found in router table"


class TestRouterDispatch:

    def test_route_valid_payload(self, router_client, sample_device_payload):
        payload = sample_device_payload()
        r = router_client.route_message(payload)
        assert r.status_code in (200, 201, 202)

    def test_route_unknown_device_type(self, router_client):
        payload = {"device_id": "unknown-999", "type": "unknown_sensor",
                   "readings": {"value": 1}}
        r = router_client.route_message(payload)
        # Should route to a default/dead-letter queue, not crash
        assert r.status_code < 500

    def test_route_empty_payload_rejected(self, router_client):
        r = router_client.route_message({})
        assert r.status_code in (400, 422)

    def test_route_returns_routing_metadata(self, router_client, sample_device_payload):
        r = router_client.route_message(sample_device_payload())
        body = r.json()
        # Router should tell us where it sent the message
        assert "routed_to" in body or "destination" in body or "status" in body

    def test_high_frequency_routing(self, router_client, sample_device_payload):
        """Simulate rapid bursts – router must not drop 5xx."""
        for i in range(30):
            r = router_client.route_message(
                sample_device_payload(device_id=f"dev-burst-{i}")
            )
            assert r.status_code < 500, f"Failed on message {i}: {r.status_code}"


class TestRouterMetrics:

    def test_metrics_endpoint_returns_200(self, router_client):
        assert router_client.get_metrics().status_code == 200

    def test_metrics_contain_message_count(self, router_client):
        body = router_client.get_metrics().json()
        assert "messages_routed" in body or "total_messages" in body or "count" in body
