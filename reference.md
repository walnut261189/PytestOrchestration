# IoT Pipeline – Test Orchestration Reference Guide

> **Purpose:** Step-by-step reference for the pytest-based test orchestration suite. Covers the multi-adapter architecture, per-adapter Mosquitto brokers, processor dual-target routing, on-prem (Docker Compose & Kubernetes) and Azure Cloud environments, all wired into Azure DevOps.

---

## Table of Contents

1. [Architecture overview](#1-architecture-overview)
2. [Multi-adapter design](#2-multi-adapter-design)
3. [Processor routing (on-prem vs cloud)](#3-processor-routing-on-prem-vs-cloud)
4. [Project structure explained](#4-project-structure-explained)
5. [Prerequisites](#5-prerequisites)
6. [First-time setup](#6-first-time-setup)
7. [Running tests locally](#7-running-tests-locally)
8. [Azure DevOps integration](#8-azure-devops-integration)
9. [Environment configuration](#9-environment-configuration)
10. [Docker Compose setup (on-prem)](#10-docker-compose-setup-on-prem)
11. [Kubernetes setup (on-prem)](#11-kubernetes-setup-on-prem)
12. [Switching to Azure Cloud](#12-switching-to-azure-cloud)
13. [Adding a new adapter type](#13-adding-a-new-adapter-type)
14. [Test layer reference](#14-test-layer-reference)
15. [Pytest marker cheat sheet](#15-pytest-marker-cheat-sheet)
16. [Troubleshooting](#16-troubleshooting)

---

## 1. Architecture overview

```
Edge Devices (voltage sensors, current probes, temp sensors, ...)
       │
       │  MQTT publish → topic: devices/<device_id>
       │  payload contains "type" field: "voltage" | "current" | "temperature" | ...
       │
   ┌───┴──────────────────────────────────────────┐
   │                                              │
   ▼                                              ▼
[ Voltage Adapter ]                    [ Current Adapter ]   [ Temperature Adapter ] ...
  + Mosquitto broker                     + Mosquitto broker    + Mosquitto broker
  port 1884 (MQTT)                       port 1885             port 1886
  port 5001 (HTTP API)                   port 5002             port 5003
   │                                              │                    │
   └──────────────────────┬───────────────────────┘                    │
                          │  HTTP POST (normalised payload)            │
                          ▼                                            │
                     [ Router ]  ◄──────────────────────────────────────
                          │
                          ▼
                     [ Proxy Server ]
                          │
                          ▼
                  [ Streamlit Processor ]
                  PROCESSOR_TARGET=onprem → CrateDB (on-prem)
                  PROCESSOR_TARGET=cloud  → Cloud DB (Azure SQL etc.)
                          │
               ┌──────────┴──────────┐
               ▼                     ▼
          [ CrateDB ]          [ Cloud DB ]
               │
               ▼
          [ Landing Page ]  (visualisation, reads from CrateDB)
```

---

## 2. Multi-adapter design

### Core rules

- Each adapter type (voltage, current, temperature, ...) is a **separate Docker container**
- Each adapter **embeds its own Mosquitto broker** on a unique port — they do not share a broker
- All adapters publish to the **same topic prefix** `devices/<device_id>` but include a `"type"` field in every payload that identifies the adapter
- All adapters expose the **same HTTP management API** shape (`/health`, `/status`, `/metrics`, `/devices/<id>`)
- The test suite uses a `BaseAdapterTests` class that all adapter types inherit — shared behaviour is tested once; type-specific behaviour (voltage ranges, unit checks) is tested in each subclass

### Topic and payload convention

**Topic:** `devices/<device_id>` — identical across all adapter types

**Structured JSON payload (default):**
```json
{
  "device_id": "device-001",
  "type": "voltage",
  "ts": 1700000000000,
  "v": 230.0,
  "i": 3.2,
  "t": 45.0
}
```

**Raw CSV payload (legacy devices):**
```
230.0,3.2,45.0
```
Order is always `voltage,current,temperature`. The adapter infers type from its own identity, not the payload.

**Normalised output (post-adapter, HTTP downstream):**
```json
{
  "device_id": "device-001",
  "type": "voltage",
  "timestamp": 1700000000000,
  "readings": {
    "voltage": 230.0,
    "current": 3.2,
    "temperature": 45.0,
    "power": 736.0
  }
}
```

### Adapter registry

`utils/adapter_registry.py` is the single place that lists all known adapter types with their ports and payload fields. When you add a new adapter type, this is the first file to update — the rest of the test infrastructure reads from it.

```python
# Current registry entries:
AdapterTypeSpec(type_name="voltage",     mqtt_port=1884, default_port=5001, ...)
AdapterTypeSpec(type_name="current",     mqtt_port=1885, default_port=5002, ...)
AdapterTypeSpec(type_name="temperature", mqtt_port=1886, default_port=5003, ...)
# Add new types here
```

### Per-adapter MQTT client fixtures

`conftest.py` provides convenient named fixtures AND a generic factory:

```python
# Named (for most tests):
def test_voltage(voltage_mqtt_client, voltage_adapter_cfg): ...
def test_current(current_mqtt_client, current_adapter_cfg): ...
def test_temp(temperature_mqtt_client, temperature_adapter_cfg): ...

# Factory (for parametrised or registry-driven tests):
def test_all_adapters(mqtt_client_for, adapter_config):
    for type_name in ("voltage", "current", "temperature"):
        client = mqtt_client_for(type_name)
        cfg    = adapter_config(type_name)
```

---

## 3. Processor routing (on-prem vs cloud)

The Streamlit processor reads a single env variable at startup:

```
PROCESSOR_TARGET=onprem   →  writes to CrateDB (on-prem)
PROCESSOR_TARGET=cloud    →  writes to cloud DB (Azure SQL / CosmosDB / ADX)
```

### How this affects tests

Two custom pytest markers auto-skip tests that don't apply to the current target:

| Marker | Runs when | Skipped when |
|---|---|---|
| `@pytest.mark.processor_onprem` | `PROCESSOR_TARGET=onprem` | `PROCESSOR_TARGET=cloud` |
| `@pytest.mark.processor_cloud` | `PROCESSOR_TARGET=cloud` | `PROCESSOR_TARGET=onprem` |

This means the same test suite handles both environments — you don't maintain separate test files.

### Running processor tests for each target

```bash
# Test on-prem routing (data should land in CrateDB)
PROCESSOR_TARGET=onprem pytest tests/unit/test_processor/ -m processor

# Test cloud routing (data should land in cloud DB)
PROCESSOR_TARGET=cloud pytest tests/unit/test_processor/ -m processor

# Run both in the same pipeline (Azure DevOps):
# Stage 1: PROCESSOR_TARGET=onprem
# Stage 2: PROCESSOR_TARGET=cloud
```

### What the routing tests verify

**On-prem tests (`processor_onprem`):**
- Routing status endpoint reports `target=onprem`
- An ingested record is queryable in CrateDB within 3 seconds
- All three adapter types (voltage, current, temperature) land in CrateDB
- Cloud DB is not written to

**Cloud tests (`processor_cloud`):**
- Routing status endpoint reports `target=cloud`
- An ingested record does NOT appear in CrateDB
- All three adapter types are accepted and return 200

---

## 4. Project structure explained

```
iot-pipeline-tests/
│
├── utils/
│   └── adapter_registry.py           # ★ Central registry of all adapter types.
│                                     #   AdapterTypeSpec: type_name, ports, fields.
│                                     #   Add new adapters here first.
│
├── config/
│   └── env_config.py                 # All host/port config.
│                                     #   AdapterInstanceConfig per adapter type.
│                                     #   ProcessorConfig with target field.
│                                     #   CrateDBConfig + CloudDBConfig (both).
│                                     #   PROCESSOR_TARGET env var controls routing.
│
├── docker/
│   └── docker-compose.test.yml       # ★ One service per adapter type:
│                                     #   adapter-voltage  (MQTT 1884, HTTP 5001)
│                                     #   adapter-current  (MQTT 1885, HTTP 5002)
│                                     #   adapter-temperature (MQTT 1886, HTTP 5003)
│                                     #   processor gets PROCESSOR_TARGET injected.
│
├── tests/
│   ├── conftest.py                   # Root fixtures:
│   │                                 #   adapter_config(type_name) factory
│   │                                 #   mqtt_client_for(type_name) factory
│   │                                 #   voltage/current/temperature named shortcuts
│   │                                 #   mqtt_message_collector(type_name, topic)
│   │                                 #   make_mqtt_payload(type, ..., raw_csv=False)
│   │                                 #   make_normalised_payload(type, ...)
│   │                                 #   processor_target fixture
│   │                                 #   Auto-skip for processor_onprem/processor_cloud
│   │
│   ├── unit/
│   │   ├── test_adapter/
│   │   │   ├── test_adapter.py       # Legacy/generic adapter HTTP tests
│   │   │   └── adapters/
│   │   │       ├── base_adapter_tests.py       # ★ Shared test class all adapters inherit
│   │   │       ├── test_voltage_adapter.py     # Voltage-specific: range, overvoltage, units
│   │   │       ├── test_current_adapter.py     # Current-specific: overcurrent, zero load
│   │   │       └── test_temperature_adapter.py # Temp-specific: sub-zero, high-temp
│   │   │
│   │   ├── test_processor/
│   │   │   └── test_processor.py     # ★ Routing tests:
│   │   │                             #   TestProcessorOnPremRouting (processor_onprem)
│   │   │                             #   TestProcessorCloudRouting (processor_cloud)
│   │   │                             #   All three adapter types tested against each target
│   │   │
│   │   ├── test_mqtt/                # Mosquitto broker tests (generic, used by all adapters)
│   │   ├── test_router/              # Router HTTP tests
│   │   ├── test_proxy/               # Proxy HTTP tests
│   │   ├── test_db/                  # CrateDB schema + CRUD tests
│   │   └── test_landing/             # Landing page API tests
│   │
│   ├── integration/
│   │   └── test_integration.py       # Entry point: MQTT publish on each adapter type
│   │
│   └── e2e/
│       └── test_e2e.py               # Smoke: all adapters + both routing targets
│
├── azure-pipelines/
│   ├── azure-pipelines.yml           # CI/CD with PROCESSOR_TARGET variable
│   └── manual-component-test.yml     # Manual trigger with adapter type + routing target dropdowns
│
├── .env.example                      # All ADAPTER_VOLTAGE_*, ADAPTER_CURRENT_*,
│                                     # ADAPTER_TEMPERATURE_*, PROCESSOR_TARGET vars
└── pytest.ini                        # Markers: voltage, current, temperature,
                                      #          processor_onprem, processor_cloud
```

---

## 5. Prerequisites

### Unit tests (no containers)

- Python 3.11+
- `pip install -r requirements-test.txt`
- Each component running and reachable at configured host/port

### Integration and E2E

- Docker Engine 24+
- Docker Compose v2
- All adapter images + shared service images built and accessible

### For processor cloud routing tests

- A reachable cloud DB (Azure SQL / CosmosDB / ADX)
- `CLOUD_DB_*` vars set in `.env` or Azure DevOps variable group

---

## 6. First-time setup

### Step 1 — Place in your repo

```
your-repo/
├── adapter-voltage/
├── adapter-current/
├── adapter-temperature/
├── router/
├── proxy/
├── processor/
├── landing/
└── iot-pipeline-tests/
```

### Step 2 — Create virtual environment and install

```bash
cd iot-pipeline-tests
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-test.txt
```

### Step 3 — Create .env

```bash
cp .env.example .env
```

Minimum values for unit testing:

```bash
ENV=onprem
PROCESSOR_TARGET=onprem      # or cloud
MQTT_TOPIC_PREFIX=devices

ADAPTER_VOLTAGE_HOST=localhost
ADAPTER_VOLTAGE_PORT=5001
ADAPTER_VOLTAGE_MQTT_PORT=1884

ADAPTER_CURRENT_HOST=localhost
ADAPTER_CURRENT_PORT=5002
ADAPTER_CURRENT_MQTT_PORT=1885

ADAPTER_TEMPERATURE_HOST=localhost
ADAPTER_TEMPERATURE_PORT=5003
ADAPTER_TEMPERATURE_MQTT_PORT=1886
```

### Step 4 — Update adapter registry if your ports differ

Open `utils/adapter_registry.py` and update `mqtt_port`, `default_port`, `mqtt_ws_port` to match your actual container port assignments.

### Step 5 — Update response schema assertions

In each adapter test file (`test_voltage_adapter.py` etc.) and the processor test, update field name checks to match your actual API response shapes. Look for `body.get(...)` calls in each test method.

### Step 6 — Update Docker Compose image names

In `docker/docker-compose.test.yml`:
```yaml
adapter-voltage:
  image: ${ADAPTER_VOLTAGE_IMAGE:-your-registry/iot-adapter-voltage:latest}  # ← update
adapter-current:
  image: ${ADAPTER_CURRENT_IMAGE:-your-registry/iot-adapter-current:latest}  # ← update
adapter-temperature:
  image: ${ADAPTER_TEMPERATURE_IMAGE:-your-registry/iot-adapter-temperature:latest}  # ← update
```

### Step 7 — Dry run

```bash
pytest tests/ --collect-only
```

---

## 7. Running tests locally

### MQTT tests — per adapter broker

Each adapter type has its own Mosquitto. Tests connect to the specific broker for that type.

```bash
# All MQTT broker tests (connects to each adapter's embedded broker)
pytest tests/unit/test_mqtt/ -m mqtt

# Unit tests for a specific adapter type
pytest tests/unit/test_adapter/adapters/test_voltage_adapter.py     -m "unit and adapter and voltage"
pytest tests/unit/test_adapter/adapters/test_current_adapter.py     -m "unit and adapter and current"
pytest tests/unit/test_adapter/adapters/test_temperature_adapter.py -m "unit and adapter and temperature"

# All adapter tests at once
pytest tests/unit/test_adapter/ -m "unit and adapter"
```

### Processor routing tests

```bash
# Test on-prem routing (CrateDB must be up)
PROCESSOR_TARGET=onprem pytest tests/unit/test_processor/ -m processor

# Test cloud routing (cloud DB must be configured)
PROCESSOR_TARGET=cloud pytest tests/unit/test_processor/ -m processor

# Test only routing assertions (skip ingest tests)
pytest tests/unit/test_processor/ -m "processor and (processor_onprem or processor_cloud)"
```

### Integration tests — all containers required

```bash
# Start everything (three adapters + shared services)
docker compose -f docker/docker-compose.test.yml up -d

# Verify all healthy
docker compose -f docker/docker-compose.test.yml ps

# Run integration tests
PROCESSOR_TARGET=onprem pytest tests/integration/ -m integration

# Tear down
docker compose -f docker/docker-compose.test.yml down -v
```

### Smoke tests

```bash
pytest tests/e2e/ -m smoke
```

Smoke tests verify:
- All three adapter brokers accept connections
- All service health endpoints return 200
- One QoS-1 publish per adapter type is acknowledged by each broker

---

## 8. Azure DevOps integration

### Variables to add

In addition to the previous set, add:

| Variable | Default | Description |
|---|---|---|
| `PROCESSOR_TARGET` | `onprem` | `onprem` or `cloud` — controls processor routing tests |
| `TEST_ADAPTER_TYPE` | `all` | `all`, `voltage`, `current`, `temperature` |
| `ADAPTER_VOLTAGE_IMAGE` | — | Full image path for voltage adapter |
| `ADAPTER_CURRENT_IMAGE` | — | Full image path for current adapter |
| `ADAPTER_TEMPERATURE_IMAGE` | — | Full image path for temperature adapter |

### Running processor routing tests in CI

Add two stages in `azure-pipelines.yml` — one per target:

```yaml
# In your pipeline variables, set PROCESSOR_TARGET=onprem for stage 1
# and PROCESSOR_TARGET=cloud for stage 2.
# The processor_onprem / processor_cloud markers handle skipping automatically.
```

### Manual trigger pipeline

The `manual-component-test.yml` has a dropdown for:
- **Component:** `voltage`, `current`, `temperature`, `adapter` (all), `processor`, `router`, `proxy`, `db`, `landing`, `integration`, `e2e`, `smoke`, `all`
- **Processor target:** `onprem` or `cloud`
- **Environment:** `onprem` or `cloud`

---

## 9. Environment configuration

### Key variables

| Variable | Example | Description |
|---|---|---|
| `PROCESSOR_TARGET` | `onprem` | Controls processor DB routing |
| `ADAPTER_VOLTAGE_MQTT_PORT` | `1884` | Mosquitto port for voltage adapter |
| `ADAPTER_CURRENT_MQTT_PORT` | `1885` | Mosquitto port for current adapter |
| `ADAPTER_TEMPERATURE_MQTT_PORT` | `1886` | Mosquitto port for temperature adapter |
| `CLOUD_DB_HOST` | `server.database.windows.net` | Cloud DB host |
| `CLOUD_DB_TYPE` | `azure_sql` | `azure_sql`, `cosmosdb`, or `adx` |

### How `adapter_config` fixture resolves

```python
# In conftest.py, adapter_config("voltage") reads:
ADAPTER_VOLTAGE_HOST, ADAPTER_VOLTAGE_PORT
ADAPTER_VOLTAGE_MQTT_HOST, ADAPTER_VOLTAGE_MQTT_PORT
ADAPTER_VOLTAGE_MQTT_WS_PORT
ADAPTER_VOLTAGE_MQTT_USERNAME, ADAPTER_VOLTAGE_MQTT_PASSWORD
```

Pattern: `ADAPTER_{TYPE_NAME_UPPER}_{FIELD}`.

---

## 10. Docker Compose setup (on-prem)

### Port map

| Service | HTTP port | MQTT port | WS port |
|---|---|---|---|
| adapter-voltage | 5001 | 1884 | 9002 |
| adapter-current | 5002 | 1885 | 9003 |
| adapter-temperature | 5003 | 1886 | 9004 |
| router | 6000 | — | — |
| proxy | 7000 | — | — |
| processor | 8501 | — | — |
| cratedb | 5432 / 4200 | — | — |
| landing | 3000 | — | — |

### Startup order

```
cratedb → processor → proxy → router → adapter-* (all three in parallel) → landing
```

### Testing processor routing

```bash
# On-prem routing
PROCESSOR_TARGET=onprem docker compose -f docker/docker-compose.test.yml up -d
PROCESSOR_TARGET=onprem pytest tests/ -m "integration or smoke"

# Cloud routing (processor sends to cloud DB, not CrateDB)
PROCESSOR_TARGET=cloud docker compose -f docker/docker-compose.test.yml up -d
PROCESSOR_TARGET=cloud pytest tests/unit/test_processor/ -m "processor_cloud"
```

### Checking a specific adapter's broker

```bash
# Voltage adapter's Mosquitto (port 1884)
mosquitto_pub -h localhost -p 1884 -t "devices/test-001" \
  -m '{"device_id":"test-001","type":"voltage","ts":1700000000000,"v":230}' -q 1

# Current adapter's Mosquitto (port 1885)
mosquitto_pub -h localhost -p 1885 -t "devices/test-002" \
  -m '{"device_id":"test-002","type":"current","ts":1700000000000,"i":5}' -q 1
```

---

## 11. Kubernetes setup (on-prem)

### Key changes for multi-adapter

In `k8s/test-runner-job.yaml`, add env vars for each adapter:

```yaml
- name: ADAPTER_VOLTAGE_MQTT_HOST
  value: "voltage-adapter-svc.iot-pipeline.svc.cluster.local"
- name: ADAPTER_VOLTAGE_MQTT_PORT
  value: "1883"   # internal port, not host-mapped
- name: ADAPTER_CURRENT_MQTT_HOST
  value: "current-adapter-svc.iot-pipeline.svc.cluster.local"
- name: ADAPTER_CURRENT_MQTT_PORT
  value: "1883"
- name: ADAPTER_TEMPERATURE_MQTT_HOST
  value: "temperature-adapter-svc.iot-pipeline.svc.cluster.local"
- name: ADAPTER_TEMPERATURE_MQTT_PORT
  value: "1883"
- name: PROCESSOR_TARGET
  value: "onprem"   # or cloud
```

Inside the cluster each adapter uses its own internal port 1883. The host-mapped ports (1884, 1885, 1886) are only needed for local development.

---

## 12. Switching to Azure Cloud

```bash
ENV=cloud
PROCESSOR_TARGET=cloud

AZURE_ADAPTER_VOLTAGE_HOST=voltage-adapter.yourdomain.azure.com
AZURE_ADAPTER_VOLTAGE_MQTT_PORT=8883   # TLS
AZURE_ADAPTER_CURRENT_HOST=current-adapter.yourdomain.azure.com
AZURE_ADAPTER_CURRENT_MQTT_PORT=8883
AZURE_ADAPTER_TEMPERATURE_HOST=temperature-adapter.yourdomain.azure.com
AZURE_ADAPTER_TEMPERATURE_MQTT_PORT=8883

AZURE_CLOUD_DB_HOST=yourserver.database.windows.net
AZURE_CLOUD_DB_NAME=iot_pipeline
AZURE_CLOUD_DB_USER=<from Key Vault>
AZURE_CLOUD_DB_PASSWORD=<from Key Vault>
```

Cloud adapter configs have `mqtt_tls=True` automatically. The `mqtt_client_for` fixture calls `client.tls_set()` for each cloud adapter connection.

---

## 13. Adding a new adapter type

### Step 1 — Register it

In `utils/adapter_registry.py`:
```python
AdapterTypeSpec(
    type_name="pressure",
    env_key="ADAPTER_PRESSURE",
    default_port=5004,
    mqtt_port=1887,
    mqtt_ws_port=9005,
    payload_fields=["pressure"],
    description="Pressure sensor — measures in bar",
),
```

### Step 2 — Add config

In `config/env_config.py`, add to `ONPREM_CONFIG.adapters` and `CLOUD_CONFIG.adapters`:
```python
"pressure": _adapter("pressure", "ADAPTER_PRESSURE", "localhost", 5004, 1887, 9005),
```

### Step 3 — Create test file

```bash
touch tests/unit/test_adapter/adapters/test_pressure_adapter.py
```

```python
from tests.unit.test_adapter.adapters.base_adapter_tests import BaseAdapterTests
import pytest

pytestmark = [pytest.mark.unit, pytest.mark.adapter, pytest.mark.pressure]

class TestPressureAdapter(BaseAdapterTests):
    ADAPTER_TYPE  = "pressure"
    PRIMARY_FIELD = "pressure"
    PRIMARY_VALUE = 1.013          # 1 atm in bar

    def test_nominal_pressure_accepted(self, mqtt, cfg, make_mqtt_payload):
        # pressure-specific tests here
        ...
```

### Step 4 — Register the marker in `pytest.ini`

```ini
markers =
    ...
    pressure: Pressure adapter tests
```

### Step 5 — Add to Docker Compose

```yaml
adapter-pressure:
  image: ${ADAPTER_PRESSURE_IMAGE:-your-registry/iot-adapter-pressure:latest}
  ports:
    - "5004:5001"
    - "1887:1883"
    - "9005:9001"
  environment:
    - ADAPTER_TYPE=pressure
```

### Step 6 — Add to `.env.example`

```bash
ADAPTER_PRESSURE_HOST=localhost
ADAPTER_PRESSURE_PORT=5004
ADAPTER_PRESSURE_MQTT_HOST=localhost
ADAPTER_PRESSURE_MQTT_PORT=1887
ADAPTER_PRESSURE_MQTT_WS_PORT=9005
```

### Step 7 — Add a named fixture to `conftest.py` (optional but convenient)

```python
@pytest.fixture(scope="session")
def pressure_mqtt_client(mqtt_client_for):
    return mqtt_client_for("pressure")

@pytest.fixture(scope="session")
def pressure_adapter_cfg(env_config):
    return env_config.adapters["pressure"]
```

---

## 14. Test layer reference

### Base adapter tests (`base_adapter_tests.py`)

All adapter types inherit `BaseAdapterTests`. Covers:
- HTTP health, status, metrics (all must return 200)
- Health response reports broker running and adapter type
- MQTT publish increments message count
- Payload carries correct `"type"` field
- Topic format is `devices/<device_id>`
- Normalised output uses standard field names
- Raw CSV payloads are processed
- 50-message burst does not degrade health endpoint

### Adapter-type-specific tests

Each subclass adds domain-specific validations:

| Adapter | Extra tests |
|---|---|
| Voltage | Nominal range (220–240 V), overvoltage flag, zero voltage not dropped, unit is volts |
| Current | Nominal range (1–16 A), overcurrent flag, zero current valid, unit is amperes |
| Temperature | Range (0–85 °C), high-temp flag, sub-zero valid, unit is Celsius |

### Processor routing tests

| Test class | Marker | When it runs |
|---|---|---|
| `TestProcessorOnPremRouting` | `processor_onprem` | `PROCESSOR_TARGET=onprem` |
| `TestProcessorCloudRouting` | `processor_cloud` | `PROCESSOR_TARGET=cloud` |

### Integration tests

Entry point is always MQTT publish on a specific adapter's broker. Verifies each boundary: MQTT→Adapter count, Adapter→Router count, Router→Proxy count, Proxy→Processor count, Processor→CrateDB row.

### Smoke tests

- All three adapter brokers connected
- All 7 service health endpoints return 200
- One QoS-1 publish per adapter type is broker-acknowledged

---

## 15. Pytest marker cheat sheet

```bash
# By adapter type
pytest -m "unit and voltage"
pytest -m "unit and current"
pytest -m "unit and temperature"
pytest -m "unit and adapter"          # all adapter types

# Processor routing
PROCESSOR_TARGET=onprem pytest -m processor_onprem
PROCESSOR_TARGET=cloud  pytest -m processor_cloud
pytest -m processor                   # all processor tests (auto-skips wrong target)

# By layer
pytest -m unit
pytest -m integration
pytest -m e2e
pytest -m smoke

# Combined
pytest -m "unit and adapter and not temperature"
pytest -m "integration and (voltage or current)"
pytest -m "processor and processor_onprem"

# Environment
pytest -m onprem
pytest -m cloud
```

---

## 16. Troubleshooting

### Wrong adapter broker connecting

Each adapter type connects to its own broker on a unique port. If a test is connecting to the wrong broker, check:
1. `ADAPTER_VOLTAGE_MQTT_PORT`, `ADAPTER_CURRENT_MQTT_PORT`, `ADAPTER_TEMPERATURE_MQTT_PORT` in `.env`
2. The `mqtt_client_for("voltage")` fixture — it reads `env_config.adapters["voltage"].mqtt_port`

```bash
# Verify which port each adapter's broker is on
cat .env | grep MQTT_PORT
```

### Processor routing test failing: data in wrong DB

1. Confirm `PROCESSOR_TARGET` matches what the container was started with:
   ```bash
   docker inspect test-processor | grep PROCESSOR_TARGET
   ```
2. Confirm the routing status endpoint agrees:
   ```bash
   curl http://localhost:8501/routing/status
   ```
3. If you changed `PROCESSOR_TARGET`, restart the processor container — it reads this at startup.

### "Adapter type X not found in config"

You added a new adapter to `utils/adapter_registry.py` but forgot to add it to `config/env_config.py`. Add an entry in `ONPREM_CONFIG.adapters` and `CLOUD_CONFIG.adapters`.

### Docker Compose: adapter container unhealthy

Each adapter's healthcheck hits its own HTTP port (`5001` inside the container, mapped to `5001/5002/5003` on the host). The healthcheck uses the internal container port:

```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:5001/health"]  # internal port always 5001
```

Check logs:
```bash
docker compose -f docker/docker-compose.test.yml logs adapter-voltage
```

### MQTT tests: `ConnectionError: Could not connect within 10s`

The embedded Mosquitto in the adapter may not be ready. The `ensure_adapter_ready` fixture polls both the TCP port and the HTTP health endpoint. If running tests without this fixture:

```python
# Add to your test module's autouse fixture:
@pytest.fixture(autouse=True)
def wait_ready(ensure_adapter_ready): pass
```

### Processor cloud tests skipped unexpectedly

Check `PROCESSOR_TARGET` is set before running:
```bash
echo $PROCESSOR_TARGET   # must print "cloud"
PROCESSOR_TARGET=cloud pytest tests/unit/test_processor/ -m processor_cloud
```

---

*Last updated: refer to your repository commit history for the latest revision.*
