# IoT Pipeline – Test Orchestration Reference Guide

> **Purpose:** Step-by-step reference for setting up, running, extending, and maintaining the pytest-based test orchestration suite for the IoT data pipeline. Covers on-prem (Docker Compose & Kubernetes) and Azure Cloud environments, wired into Azure DevOps.

---

## Table of Contents

1. [Architecture overview](#1-architecture-overview)
2. [Project structure explained](#2-project-structure-explained)
3. [Prerequisites](#3-prerequisites)
4. [First-time setup](#4-first-time-setup)
5. [Running tests locally](#5-running-tests-locally)
6. [Azure DevOps integration](#6-azure-devops-integration)
7. [Environment configuration](#7-environment-configuration)
8. [Docker Compose setup (on-prem)](#8-docker-compose-setup-on-prem)
9. [Kubernetes setup (on-prem)](#9-kubernetes-setup-on-prem)
10. [Switching to Azure Cloud](#10-switching-to-azure-cloud)
11. [Adding a new component](#11-adding-a-new-component)
12. [Test layer reference](#12-test-layer-reference)
13. [Pytest marker cheat sheet](#13-pytest-marker-cheat-sheet)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. Architecture overview

The pipeline under test consists of six components, each running in its own Docker container:

```
Edge Devices (voltage, current, temperature, ...)
       │
       ▼
  [ Adapter ]          Listens to edge devices via MQTT / Modbus / OPC-UA.
       │               Normalises raw payloads into a standard schema.
       ▼
  [ Router ]           Receives normalised messages and dispatches them
       │               to the correct downstream service based on routing rules.
       ▼
  [ Proxy Server ]     Forwards requests, adds tracing headers,
       │               and manages upstream timeouts.
       ▼
  [ Streamlit          Ingests data, applies transformations,
    Processor ]        and persists records to CrateDB.
       │
       ▼
  [ CrateDB ]          Time-series database. Stores all device readings.
       │
       ▼
  [ Landing Page ]     Visualisation layer. Reads from CrateDB and
                       exposes device data via a REST API consumed by the UI.
```

The test suite mirrors this topology exactly. Tests are organised into three layers:

| Layer | What it tests | Docker needed? |
|---|---|---|
| **Unit** | Each component in isolation via its HTTP API | No |
| **Integration** | Data flowing across component boundaries | Yes |
| **E2E** | Full path from adapter publish to landing page visibility | Yes |
| **Smoke** | Fastest possible post-deployment sanity check | Yes (services up) |

---

## 2. Project structure explained

```
iot-pipeline-tests/
│
├── azure-pipelines/
│   ├── azure-pipelines.yml           # Main CI/CD pipeline definition
│   └── manual-component-test.yml     # Manual on-demand pipeline with UI dropdowns
│
├── config/
│   └── env_config.py                 # Single source of truth for all host/port config.
│                                     # Reads from environment variables.
│                                     # ENV=onprem uses on-prem defaults.
│                                     # ENV=cloud uses AZURE_* variables.
│
├── docker/
│   └── docker-compose.test.yml       # Brings up all six containers for integration
│                                     # and E2E testing. Healthchecks ensure correct
│                                     # startup order.
│
├── k8s/
│   └── test-runner-job.yaml          # Kubernetes Job that runs the test suite as a
│                                     # one-shot pod inside the cluster. Includes a
│                                     # ConfigMap for test_marker and environment.
│
├── tests/
│   ├── conftest.py                   # Root-level fixtures shared across ALL tests:
│   │                                 #   - env_config, per-component URLs
│   │                                 #   - http_client (requests.Session)
│   │                                 #   - wait_for_service helpers
│   │                                 #   - sample_device_payload factory
│   │                                 #   - auto-skip logic for onprem/cloud markers
│   │
│   ├── unit/
│   │   ├── test_adapter/
│   │   │   └── test_adapter.py       # Health, payload ingestion, normalisation,
│   │   │                             # protocol stubs (MQTT mock), burst tests
│   │   ├── test_router/
│   │   │   └── test_router.py        # Health, route table, dispatch, metrics,
│   │   │                             # high-frequency routing
│   │   ├── test_proxy/
│   │   │   └── test_proxy.py         # Health, forwarding, header injection,
│   │   │                             # oversized payload, concurrent requests
│   │   ├── test_processor/
│   │   │   └── test_processor.py     # Health, ingest, data retrieval, aggregates,
│   │   │                             # pipeline stats
│   │   ├── test_db/
│   │   │   └── test_db.py            # CrateDB connectivity, schema validation,
│   │   │                             # CRUD, aggregation queries, bulk insert perf
│   │   └── test_landing/
│   │       └── test_landing.py       # Health, device list, latest reading,
│   │                                 # time-series history, dashboard summary
│   │
│   ├── integration/
│   │   └── test_integration.py       # Adapter→Router, Router→Proxy, Proxy→Processor,
│   │                                 # Processor→CrateDB, full adapter-to-DB pipeline
│   │
│   └── e2e/
│       └── test_e2e.py               # Landing page health, data visibility after
│                                     # publish, multi-device, time-series, smoke suite
│
├── reports/                          # Auto-generated after every run:
│                                     #   junit.xml  → Azure DevOps test results
│                                     #   report.html → Human-readable HTML report
│
├── .env.example                      # Template for local environment variables
├── pytest.ini                        # Markers, test paths, output settings, timeout
├── requirements-test.txt             # All Python test dependencies
└── README.md                         # Quick-start guide
```

---

## 3. Prerequisites

### For running unit tests (no containers)

- Python 3.11 or higher
- pip
- The component under test must be reachable at the configured host/port

### For running integration and E2E tests

- Docker Engine 24+
- Docker Compose v2 (`docker compose` not `docker-compose`)
- All six container images built and accessible (local or registry)

### For Kubernetes

- `kubectl` configured against your cluster
- The namespace `iot-pipeline` created, or update `k8s/test-runner-job.yaml` to your namespace
- Test runner image pushed to your container registry

### For Azure DevOps

- An Azure DevOps project with a repository containing this test project
- Agent pool with Docker installed (for integration/E2E stages)
- Pipeline variables or variable groups configured (see Section 6)

---

## 4. First-time setup

Follow these steps in order the very first time you set up this project.

### Step 1 — Clone or copy the project

Place the `iot-pipeline-tests/` directory at the root of your repository, or as a sibling directory to your pipeline services.

```
your-repo/
├── adapter/
├── router/
├── proxy/
├── processor/
├── landing/
└── iot-pipeline-tests/        ← place it here
```

### Step 2 — Create a Python virtual environment

```bash
cd iot-pipeline-tests
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
```

### Step 3 — Install test dependencies

```bash
pip install -r requirements-test.txt
```

This installs: `pytest`, `pytest-html`, `pytest-timeout`, `pytest-xdist`, `pytest-rerunfailures`, `requests`, `python-dotenv`.

### Step 4 — Create your local .env file

```bash
cp .env.example .env
```

Open `.env` and fill in the actual host/port values for your local setup. The defaults assume Docker Compose with all services on `localhost`.

```bash
# Minimum required for unit tests against running services
ENV=onprem
ADAPTER_HOST=localhost
ADAPTER_PORT=5000
ROUTER_HOST=localhost
ROUTER_PORT=6000
PROXY_HOST=localhost
PROXY_PORT=7000
PROCESSOR_HOST=localhost
PROCESSOR_PORT=8501
CRATEDB_HOST=localhost
CRATEDB_PORT=5432
CRATEDB_HTTP_PORT=4200
LANDING_HOST=localhost
LANDING_PORT=3000
```

### Step 5 — Update endpoint paths in test files

Each test file uses common endpoint paths (`/health`, `/publish`, `/route`, `/forward`, `/ingest`). If your components use different paths, update them in the relevant `test_<component>.py` file. Look for the client class at the top of each file — all paths are defined there in one place.

For example in `tests/unit/test_adapter/test_adapter.py`:

```python
class AdapterClient:
    def health(self):
        return self.session.get(f"{self.base}/health")      # ← change path here

    def publish(self, payload):
        return self.session.post(f"{self.base}/publish", json=payload)   # ← and here
```

### Step 6 — Update response schema assertions

Each test checks for specific keys in response bodies. Update these to match your actual API responses. For example, if your adapter returns `{"ack": true, "msg_id": "abc"}` instead of `{"message_id": "abc"}`, update:

```python
# In test_adapter.py — TestAdapterPayloadIngestion
def test_publish_returns_acknowledgement(self, adapter_client, sample_device_payload):
    body = r.json()
    assert "msg_id" in body or "ack" in body    # ← update to your schema
```

### Step 7 — Update container image names in Docker Compose

Open `docker/docker-compose.test.yml` and replace the placeholder image names:

```yaml
adapter:
  image: your-registry/iot-adapter:latest      # ← replace with your actual image
router:
  image: your-registry/iot-router:latest       # ← replace
proxy:
  image: your-registry/iot-proxy:latest        # ← replace
processor:
  image: your-registry/iot-processor:latest    # ← replace
landing:
  image: your-registry/iot-landing:latest      # ← replace
```

### Step 8 — Verify setup with a dry run

Run the test collection (no execution) to confirm everything is wired correctly:

```bash
pytest tests/ --collect-only
```

You should see all test files and test functions listed without any import errors.

---

## 5. Running tests locally

### Unit tests — no containers required

Services must be running (started manually or via Docker), but the tests do not manage containers themselves.

```bash
# All unit tests across all components
pytest tests/unit/ -m unit

# Single component
pytest tests/unit/test_adapter/    -m "unit and adapter"
pytest tests/unit/test_router/     -m "unit and router"
pytest tests/unit/test_proxy/      -m "unit and proxy"
pytest tests/unit/test_processor/  -m "unit and processor"
pytest tests/unit/test_db/         -m "unit and db"
pytest tests/unit/test_landing/    -m "unit and landing"
```

### Integration tests — Docker Compose required

```bash
# Step 1: Start all containers
docker compose -f docker/docker-compose.test.yml up -d

# Step 2: Wait for all healthchecks to pass (usually 30–60 seconds)
docker compose -f docker/docker-compose.test.yml ps

# Step 3: Run integration tests
pytest tests/integration/ -m integration

# Step 4: Tear down containers and volumes when done
docker compose -f docker/docker-compose.test.yml down -v
```

To run only a specific cross-component boundary:

```bash
# Only adapter→router boundary tests
pytest tests/integration/ -m "integration and adapter and router"
```

### E2E tests — all services must be up

```bash
pytest tests/e2e/ -m e2e
```

### Smoke tests — fastest post-deployment check

```bash
pytest tests/e2e/ -m smoke
```

Smoke tests run in under 30 seconds and verify that all six services respond and a single payload traverses the full pipeline.

### Useful flags

```bash
# Run in parallel across 4 workers (speeds up unit tests significantly)
pytest tests/unit/ -m unit -n 4

# Stop after first failure
pytest tests/unit/ -m unit -x

# Show full traceback on failure
pytest tests/unit/ -m unit --tb=long

# Rerun flaky tests up to 2 times
pytest tests/ --reruns 2 --reruns-delay 3

# Generate HTML report to a custom path
pytest tests/ --html=my-report.html --self-contained-html

# Verbose output with live log streaming
pytest tests/ -v --log-cli-level=DEBUG
```

---

## 6. Azure DevOps integration

### Step 1 — Add the pipeline YAML to your repository

Commit both files from `azure-pipelines/` into your repository root or a `pipelines/` folder:

```
your-repo/
├── azure-pipelines/
│   ├── azure-pipelines.yml
│   └── manual-component-test.yml
└── iot-pipeline-tests/
```

### Step 2 — Create the main CI/CD pipeline

1. Go to **Azure DevOps → Pipelines → New Pipeline**
2. Select your repository source (Azure Repos Git, GitHub, etc.)
3. Choose **Existing Azure Pipelines YAML file**
4. Set the path to `azure-pipelines/azure-pipelines.yml`
5. Click **Save** (do not run yet)

### Step 3 — Create the manual component pipeline

Repeat Step 2 using `azure-pipelines/manual-component-test.yml`. Name it something like `IoT – Manual Component Test`.

### Step 4 — Configure pipeline variables

Go to **Pipelines → your pipeline → Edit → Variables** and add the following. Each variable has a default — only override what you need.

| Variable | Default | Description |
|---|---|---|
| `RUN_UNIT` | `true` | Enable the unit test stage |
| `RUN_INTEGRATION` | `false` | Enable the integration test stage |
| `RUN_E2E` | `false` | Enable the E2E test stage |
| `RUN_SMOKE` | `false` | Enable the smoke test stage |
| `TEST_COMPONENT` | `all` | Target a single component: `adapter`, `router`, `proxy`, `processor`, `db`, `landing`, or `all` |
| `ENV` | `onprem` | Target environment: `onprem` or `cloud` |

**To run unit tests only on every PR (recommended CI baseline):**

```
RUN_UNIT=true
RUN_INTEGRATION=false
RUN_E2E=false
RUN_SMOKE=false
```

**To run unit + integration on merge to main (full CI):**

```
RUN_UNIT=true
RUN_INTEGRATION=true
```

**To run smoke tests after every deployment (CD):**

Create a separate pipeline or add a deployment gate that sets:

```
RUN_SMOKE=true
ENV=onprem        # or cloud, depending on deployment target
```

### Step 5 — Configure service host variables for integration and E2E stages

When `RUN_INTEGRATION=true` or `RUN_E2E=true`, the pipeline needs to know where the services are. In the integration stage, Docker Compose brings everything up on `localhost` automatically. For E2E against a live environment, add these to your variable group:

```
ADAPTER_HOST=<your-adapter-host>
ROUTER_HOST=<your-router-host>
PROXY_HOST=<your-proxy-host>
PROCESSOR_HOST=<your-processor-host>
CRATEDB_HOST=<your-cratedb-host>
LANDING_HOST=<your-landing-host>
```

Store sensitive values (passwords, tokens) as **secret variables** in Azure DevOps or reference them from Azure Key Vault via a variable group link.

### Step 6 — Using the manual trigger pipeline

1. Go to **Pipelines → IoT – Manual Component Test → Run Pipeline**
2. A form appears with four dropdowns:
   - **Component to test:** `adapter`, `router`, `proxy`, `processor`, `db`, `landing`, `integration`, `e2e`, `smoke`, or `all`
   - **Test level:** `unit`, `integration`, `e2e`, `smoke`, or `all`
   - **Target environment:** `onprem` or `cloud`
   - **Spin up Docker Compose?:** tick if you want the pipeline to bring up containers itself
3. Click **Run**

This is the recommended way to test a single component after modifying it, or to debug a failing test in isolation.

### Step 7 — View test results

After each pipeline run:

1. Click the run in the pipeline list
2. Go to the **Tests** tab — Azure DevOps renders the JUnit XML as a full test results dashboard with pass/fail counts, durations, and history
3. Go to **Artifacts** to download the HTML report (`unit-test-reports`, `integration-test-reports`, etc.)

---

## 7. Environment configuration

All configuration lives in `config/env_config.py`. The file uses a simple pattern: read from environment variables with safe defaults.

### How environment switching works

```
ENV=onprem  →  Uses ADAPTER_HOST, ROUTER_HOST, ... (on-prem defaults)
ENV=cloud   →  Uses AZURE_ADAPTER_HOST, AZURE_ROUTER_HOST, ... (cloud overrides)
```

Set `ENV` as an environment variable before running tests:

```bash
# On-prem (default)
ENV=onprem pytest tests/ -m smoke

# Azure Cloud
ENV=cloud pytest tests/ -m smoke
```

Or in `.env`:

```bash
ENV=cloud
```

### Adding a new config field

Open `config/env_config.py` and add the field to the relevant dataclass:

```python
@dataclass
class AdapterConfig:
    host: str
    port: int
    protocol: str
    topic_prefix: str
    connect_timeout: int = 10
    tls_enabled: bool = False
    my_new_field: str = "default_value"    # ← add here
```

Then add it to both `ONPREM_CONFIG` and `CLOUD_CONFIG` lower in the file, reading from an environment variable:

```python
ONPREM_CONFIG = EnvironmentConfig(
    adapter=AdapterConfig(
        ...
        my_new_field=_env("MY_NEW_FIELD", "default_value"),
    ),
    ...
)
```

---

## 8. Docker Compose setup (on-prem)

The file `docker/docker-compose.test.yml` defines all six services on a shared bridge network (`pipeline-net`) so they can resolve each other by service name.

### Startup order

Docker Compose `depends_on` with `condition: service_healthy` ensures this order:

```
cratedb  →  processor  →  proxy  →  router  →  adapter
                       →  landing
```

Each service has a `healthcheck` that polls its `/health` endpoint. Services will not start until their dependency is healthy.

### Step 1 — Set image names via environment variables

Rather than hardcoding image names, use environment variables so the same Compose file works across branches and registries:

```bash
export ADAPTER_IMAGE=myregistry.azurecr.io/iot-adapter:dev
export ROUTER_IMAGE=myregistry.azurecr.io/iot-router:dev
export PROXY_IMAGE=myregistry.azurecr.io/iot-proxy:dev
export PROCESSOR_IMAGE=myregistry.azurecr.io/iot-processor:dev
export LANDING_IMAGE=myregistry.azurecr.io/iot-landing:dev
docker compose -f docker/docker-compose.test.yml up -d
```

Or put them in a `.env` file (Docker Compose reads it automatically):

```bash
ADAPTER_IMAGE=myregistry.azurecr.io/iot-adapter:dev
ROUTER_IMAGE=myregistry.azurecr.io/iot-router:dev
# ...etc
```

### Step 2 — Check all services are healthy

```bash
docker compose -f docker/docker-compose.test.yml ps
```

All services should show `healthy` in the STATUS column. If any shows `unhealthy` or `starting`, check logs:

```bash
docker compose -f docker/docker-compose.test.yml logs adapter
docker compose -f docker/docker-compose.test.yml logs cratedb
```

### Step 3 — Run tests

```bash
pytest tests/integration/ -m integration
pytest tests/e2e/ -m e2e
pytest tests/e2e/ -m smoke
```

### Step 4 — Tear down

```bash
# Stop containers and remove volumes (clears CrateDB data)
docker compose -f docker/docker-compose.test.yml down -v

# Stop containers but keep volumes (preserve CrateDB data between runs)
docker compose -f docker/docker-compose.test.yml down
```

---

## 9. Kubernetes setup (on-prem)

Use this approach when your pipeline services are deployed to a Kubernetes cluster (on-prem or AKS) and you want to run tests from inside the cluster for network proximity.

### Step 1 — Build the test runner image

Create a `Dockerfile.test` at the root of your test project:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements-test.txt .
RUN pip install --no-cache-dir -r requirements-test.txt
COPY . .
ENTRYPOINT ["pytest"]
```

Build and push:

```bash
docker build -f Dockerfile.test -t myregistry.azurecr.io/iot-test-runner:latest .
docker push myregistry.azurecr.io/iot-test-runner:latest
```

### Step 2 — Update the Kubernetes Job manifest

Open `k8s/test-runner-job.yaml` and update:

**Image name:**
```yaml
image: myregistry.azurecr.io/iot-test-runner:latest   # ← your image
```

**Service DNS names** — follow the pattern `<service-name>.<namespace>.svc.cluster.local`:
```yaml
- name: ADAPTER_HOST
  value: "adapter-svc.iot-pipeline.svc.cluster.local"   # ← your service name and namespace
- name: ROUTER_HOST
  value: "router-svc.iot-pipeline.svc.cluster.local"
# ... repeat for all components
```

**Namespace:**
```yaml
metadata:
  namespace: iot-pipeline    # ← your namespace
```

### Step 3 — Configure the test run via ConfigMap

Edit the ConfigMap at the bottom of the YAML before applying:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: iot-test-config
  namespace: iot-pipeline
data:
  environment: "onprem"      # onprem | cloud
  test_marker: "smoke"       # smoke | unit | integration | e2e
```

### Step 4 — Apply and monitor

```bash
# Create the namespace if it doesn't exist
kubectl create namespace iot-pipeline --dry-run=client -o yaml | kubectl apply -f -

# Apply the Job (also creates the ConfigMap)
kubectl apply -f k8s/test-runner-job.yaml

# Watch the pod come up
kubectl get pods -n iot-pipeline -w

# Stream logs
kubectl logs -f job/iot-pipeline-test-runner -n iot-pipeline

# Check exit code (0 = all tests passed)
kubectl get job iot-pipeline-test-runner -n iot-pipeline -o jsonpath='{.status.conditions}'
```

### Step 5 — Clean up

```bash
kubectl delete job iot-pipeline-test-runner -n iot-pipeline
```

The Job is also configured with `ttlSecondsAfterFinished: 600` so it auto-deletes 10 minutes after completion.

### Step 6 — Integrate with Azure DevOps (Kubernetes path)

Add a script step in your Azure DevOps pipeline after deployment:

```yaml
- script: |
    # Update the ConfigMap with the desired test marker
    kubectl patch configmap iot-test-config -n iot-pipeline \
      --patch '{"data": {"test_marker": "smoke", "environment": "onprem"}}'

    # Delete any previous run
    kubectl delete job iot-pipeline-test-runner -n iot-pipeline --ignore-not-found

    # Apply the new Job
    kubectl apply -f k8s/test-runner-job.yaml

    # Wait for completion (timeout 5 minutes)
    kubectl wait job/iot-pipeline-test-runner -n iot-pipeline \
      --for=condition=complete --timeout=300s
  displayName: "Run smoke tests on cluster"
```

---

## 10. Switching to Azure Cloud

The cloud environment uses the same test files — only the host/port config changes.

### Step 1 — Populate Azure cloud variables

In `.env` (local) or Azure DevOps variable group (pipeline):

```bash
ENV=cloud
AZURE_ADAPTER_HOST=adapter.yourdomain.azure.com
AZURE_ADAPTER_PORT=443
AZURE_ROUTER_HOST=router.yourdomain.azure.com
AZURE_ROUTER_PORT=443
AZURE_PROXY_HOST=proxy.yourdomain.azure.com
AZURE_PROXY_PORT=443
AZURE_PROCESSOR_HOST=processor.yourdomain.azure.com
AZURE_PROCESSOR_PORT=443
AZURE_CRATEDB_HOST=cratedb.yourdomain.azure.com
AZURE_CRATEDB_PORT=5432
AZURE_CRATEDB_HTTP_PORT=4200
AZURE_CRATEDB_USER=crate
AZURE_CRATEDB_PASSWORD=<from Key Vault>
AZURE_LANDING_HOST=landing.yourdomain.azure.com
AZURE_LANDING_PORT=443
```

### Step 2 — Enable TLS for the adapter

The cloud adapter config has `tls_enabled: True` by default, which causes the test client to use `https://`. Verify your adapter's cloud certificate is valid and the port is correct.

### Step 3 — Run against cloud

```bash
ENV=cloud pytest tests/e2e/ -m smoke
```

### Step 4 — Mark cloud-only tests

If you write tests that only make sense in cloud (e.g. Azure Event Hub integration), mark them:

```python
@pytest.mark.cloud
def test_event_hub_integration(self, ...):
    ...
```

These will be automatically skipped when `ENV=onprem` and vice-versa for `@pytest.mark.onprem`. The skip logic lives in `conftest.py → pytest_collection_modifyitems`.

---

## 11. Adding a new component

Follow these steps every time a new service joins the pipeline.

### Step 1 — Add config

In `config/env_config.py`, add a new dataclass and wire it into both `ONPREM_CONFIG` and `CLOUD_CONFIG`:

```python
@dataclass
class MyNewServiceConfig:
    host: str
    port: int
    health_endpoint: str = "/health"

# Add to EnvironmentConfig dataclass:
@dataclass
class EnvironmentConfig:
    ...
    my_new_service: MyNewServiceConfig

# Add to ONPREM_CONFIG:
ONPREM_CONFIG = EnvironmentConfig(
    ...
    my_new_service=MyNewServiceConfig(
        host=_env("MY_NEW_SERVICE_HOST", "localhost"),
        port=int(_env("MY_NEW_SERVICE_PORT", "9000")),
    ),
)

# Add to CLOUD_CONFIG similarly with AZURE_MY_NEW_SERVICE_* env vars
```

### Step 2 — Add URL fixture to conftest.py

```python
@pytest.fixture(scope="session")
def my_new_service_url(env_config):
    c = env_config.my_new_service
    return f"http://{c.host}:{c.port}"
```

### Step 3 — Create test directory and files

```bash
mkdir tests/unit/test_my_new_service
touch tests/unit/test_my_new_service/__init__.py
touch tests/unit/test_my_new_service/test_my_new_service.py
```

### Step 4 — Write the test file

Use this template:

```python
import pytest
pytestmark = [pytest.mark.unit, pytest.mark.my_new_service]

@pytest.fixture(scope="module")
def my_client(my_new_service_url, http_client):
    class MyClient:
        def health(self):
            return http_client.get(f"{my_new_service_url}/health", timeout=5)
    return MyClient()

class TestMyNewServiceHealth:
    def test_health_returns_200(self, my_client):
        assert my_client.health().status_code == 200
```

### Step 5 — Register the marker

Add to `pytest.ini` under `markers`:

```ini
markers =
    ...
    my_new_service: Tests for My New Service
```

### Step 6 — Add to Docker Compose

```yaml
my-new-service:
  image: ${MY_NEW_SERVICE_IMAGE:-your-registry/my-new-service:latest}
  container_name: test-my-new-service
  ports:
    - "9000:9000"
  networks:
    - pipeline-net
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:9000/health"]
    interval: 10s
    timeout: 5s
    retries: 10
```

### Step 7 — Add to Azure DevOps manual pipeline

In `azure-pipelines/manual-component-test.yml`, add to the `component` parameter's `values` list:

```yaml
parameters:
  - name: component
    values:
      - adapter
      - router
      - ...
      - my_new_service    # ← add here
```

### Step 8 — Add to .env.example

```bash
MY_NEW_SERVICE_HOST=localhost
MY_NEW_SERVICE_PORT=9000
```

---

## 12. Test layer reference

### Unit tests

- **Location:** `tests/unit/test_<component>/`
- **Marker:** `@pytest.mark.unit`
- **Requires:** The component's HTTP API to be reachable. No other services needed.
- **What they cover:**
  - Health endpoint returns 200 with expected body structure
  - Valid payload accepted, invalid payload rejected with correct status codes
  - Response schemas contain expected fields
  - Edge cases (empty payloads, negative values, oversized inputs, concurrent requests)
  - Metrics and status endpoints accessible

### Integration tests

- **Location:** `tests/integration/test_integration.py`
- **Marker:** `@pytest.mark.integration`
- **Requires:** All containers running via Docker Compose or on a shared network.
- **What they cover:**
  - A publish to the adapter increments the router's message counter
  - A router dispatch increments the proxy's request counter
  - A proxy forward triggers an ingest on the processor
  - A processor ingest writes a record to CrateDB (verified by direct SQL query)
  - Full adapter-to-database pipeline: one publish, verify row in CrateDB

### E2E tests

- **Location:** `tests/e2e/test_e2e.py`
- **Marker:** `@pytest.mark.e2e`
- **Requires:** All services up, including the landing page.
- **What they cover:**
  - Data published via adapter is visible on the landing page API after pipeline propagation
  - Multiple devices all appear on the landing page
  - Time-series history returns ≥ N points after N publishes
  - Landing page health and device list endpoints

### Smoke tests

- **Location:** `tests/e2e/test_e2e.py` — class `TestSmoke`
- **Marker:** `@pytest.mark.smoke`
- **Requires:** All services up.
- **What they cover:**
  - All six services return 200 on their health endpoints
  - One payload traverses the full pipeline without a 5xx error
- **Target runtime:** Under 30 seconds

---

## 13. Pytest marker cheat sheet

```bash
# By test layer
pytest -m unit
pytest -m integration
pytest -m e2e
pytest -m smoke

# By component
pytest -m adapter
pytest -m router
pytest -m proxy
pytest -m processor
pytest -m db
pytest -m landing

# Combined (AND)
pytest -m "unit and adapter"
pytest -m "integration and db"
pytest -m "e2e and not smoke"

# Combined (OR)
pytest -m "adapter or router"

# Exclude a marker
pytest -m "unit and not db"

# Environment-specific
pytest -m onprem      # only runs when ENV=onprem
pytest -m cloud       # only runs when ENV=cloud
```

---

## 14. Troubleshooting

### "Connection refused" on a unit test

The service is not running or is on the wrong host/port. Check:

```bash
# Confirm the service is up
curl http://localhost:5000/health

# Check your .env values
cat .env | grep ADAPTER

# Check if the port is actually bound
lsof -i :5000
```

### Docker Compose service stuck in "starting" or "unhealthy"

```bash
# View healthcheck logs for a specific service
docker inspect test-adapter | python3 -m json.tool | grep -A 10 Health

# View full container logs
docker compose -f docker/docker-compose.test.yml logs --tail=50 adapter

# Restart a single service
docker compose -f docker/docker-compose.test.yml restart adapter
```

### CrateDB tests failing with "table not found"

The processor creates the `device_readings` table on first startup. If the processor hasn't started or ingested any data yet, the table may not exist. Trigger at least one ingest:

```bash
curl -X POST http://localhost:8501/ingest \
  -H "Content-Type: application/json" \
  -d '{"device_id":"seed-dev","timestamp":1700000000000,"readings":{"voltage":220,"current":3,"temperature":45}}'
```

### Azure DevOps pipeline: integration stage fails immediately

The Docker agent may not have access to your container registry. Add a Docker login step before the compose up:

```yaml
- task: Docker@2
  inputs:
    command: login
    containerRegistry: <your-service-connection-name>
  displayName: "Login to container registry"
```

### Tests are flaky (pass sometimes, fail others)

Increase the propagation wait time in integration and E2E tests. Look for `time.sleep(2)` or `time.sleep(5)` calls and increase them. For a more robust approach, use the `wait_for_service` helper from `conftest.py` with a polling loop instead of a fixed sleep.

### Running tests in parallel causes failures

Some tests share CrateDB state. If running with `pytest -n auto`, isolate each test's data with a unique `device_id` per test (the fixtures already do this with timestamps). If failures persist, run CrateDB tests sequentially:

```bash
pytest tests/unit/test_db/ -n 0
pytest tests/unit/test_adapter/ -n 4
```

---

*Last updated: refer to your repository commit history for the latest revision.*
