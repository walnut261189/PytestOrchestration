# IoT Pipeline – Test Orchestration

Pytest-based test suite for the full IoT data pipeline, wired into Azure DevOps.
Covers unit, integration, and E2E layers across on-prem (Docker Compose / Kubernetes)
and Azure Cloud environments.

---

## Project structure

```
iot-pipeline-tests/
├── azure-pipelines/
│   ├── azure-pipelines.yml          # Main CI/CD pipeline (toggle via variables)
│   └── manual-component-test.yml    # Manual trigger – single component on demand
│
├── config/
│   └── env_config.py                # All host/port config; ENV= switches on-prem↔cloud
│
├── docker/
│   └── docker-compose.test.yml      # Spins up all containers for integration tests
│
├── k8s/
│   └── test-runner-job.yaml         # K8s Job to run tests inside the cluster
│
├── tests/
│   ├── conftest.py                  # Root fixtures (shared across all tests)
│   ├── unit/
│   │   ├── test_adapter/            # Adapter unit tests
│   │   ├── test_router/             # Router unit tests
│   │   ├── test_proxy/              # Proxy unit tests
│   │   ├── test_processor/          # Streamlit processor unit tests
│   │   ├── test_db/                 # CrateDB unit tests
│   │   └── test_landing/            # Landing page unit tests
│   ├── integration/
│   │   └── test_integration.py      # Cross-component flow tests
│   └── e2e/
│       └── test_e2e.py              # Full pipeline + smoke tests
│
├── reports/                         # Auto-generated test reports (JUnit + HTML)
├── .env.example                     # Copy to .env for local dev
├── pytest.ini                       # Markers, output settings
└── requirements-test.txt
```

---

## Running tests locally

```bash
# 1. Install dependencies
pip install -r requirements-test.txt

# 2. Copy and fill in environment config
cp .env.example .env

# 3a. Unit tests only (no Docker needed)
pytest tests/unit/ -m unit

# 3b. Single component
pytest tests/unit/test_adapter/ -m "unit and adapter"

# 3c. Integration tests (start containers first)
docker compose -f docker/docker-compose.test.yml up -d
pytest tests/integration/ -m integration
docker compose -f docker/docker-compose.test.yml down -v

# 3d. Smoke tests (services must already be running)
pytest tests/e2e/ -m smoke

# 3e. Full E2E
pytest tests/e2e/ -m e2e
```

---

## Azure DevOps – pipeline toggle guide

Both pipeline YAMLs live in `azure-pipelines/`.

### Main pipeline (`azure-pipelines.yml`)

| Variable          | Default   | Effect                                      |
|-------------------|-----------|---------------------------------------------|
| `RUN_UNIT`        | `true`    | Run unit test stage                         |
| `RUN_INTEGRATION` | `false`   | Run integration test stage (needs Docker)   |
| `RUN_E2E`         | `false`   | Run E2E test stage                          |
| `RUN_SMOKE`       | `false`   | Run smoke test stage                        |
| `TEST_COMPONENT`  | `all`     | `all` or one of: adapter router proxy processor db landing |
| `ENV`             | `onprem`  | `onprem` or `cloud`                         |

Set variables at: **Azure DevOps → Pipeline → Edit → Variables** or via a variable group.

### Manual trigger pipeline (`manual-component-test.yml`)

Shows a dropdown UI when you click **Run Pipeline**:
- Pick the component, test level, environment, and whether to spin up Docker Compose.

---

## Adding a new component

1. Create `tests/unit/test_<component>/test_<component>.py`
2. Add `pytestmark = [pytest.mark.unit, pytest.mark.<component>]`
3. Add `<component>` to the `markers` list in `pytest.ini`
4. Add `<component>` to the `parameters.values` list in `manual-component-test.yml`
5. Add service config to `config/env_config.py`
6. Add the container to `docker/docker-compose.test.yml`

---

## Switching to Azure Cloud

```bash
ENV=cloud pytest tests/ -m smoke
```

Or set `ENV=cloud` in the Azure DevOps pipeline variable.
All `AZURE_*` env vars in `.env` (or the pipeline variable group) are used automatically.

---

## Kubernetes

```bash
# Edit k8s/test-runner-job.yaml: set test_marker and environment in the ConfigMap
kubectl apply -f k8s/test-runner-job.yaml
kubectl logs -f job/iot-pipeline-test-runner
```
