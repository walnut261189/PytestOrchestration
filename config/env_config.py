# ─────────────────────────────────────────────────────────────────
# config/env_config.py
#
# Single source of truth for all environment-specific settings.
#
# Architecture:
#   - Multiple adapter types (voltage, current, temperature, ...)
#   - Each adapter is its own Docker container with its own
#     embedded Mosquitto broker on a unique port
#   - All adapters publish to devices/# with a "type" field
#   - Streamlit processor routes to on-prem CrateDB OR cloud DB
#     based on PROCESSOR_TARGET env variable
#
# Toggle:
#   ENV=onprem (default) | cloud
#   PROCESSOR_TARGET=onprem (default) | cloud
# ─────────────────────────────────────────────────────────────────

import os
from dataclasses import dataclass, field
from typing import Optional, Dict


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


ENVIRONMENT = _env("ENV", "onprem")
PROCESSOR_TARGET = _env("PROCESSOR_TARGET", "onprem")  # onprem | cloud


# ── Per-adapter-instance config ───────────────────────────────────

@dataclass
class AdapterInstanceConfig:
    """
    Config for one specific adapter instance (e.g. the voltage adapter).
    Each adapter embeds its own Mosquitto broker.

    topic_prefix  : "devices"  →  topic: devices/<device_id>
    type_name     : value of the "type" field in every payload
                    e.g. "voltage", "current", "temperature"
    """
    type_name: str              # e.g. "voltage"
    host: str                   # HTTP management API host
    port: int                   # HTTP management API port
    mqtt_host: str              # Mosquitto broker host (same container)
    mqtt_port: int              # Mosquitto plain port (1883 = default)
    mqtt_ws_port: int           # Mosquitto WebSocket port
    topic_prefix: str = "devices"
    mqtt_username: Optional[str] = None
    mqtt_password: Optional[str] = None
    mqtt_tls: bool = False
    mqtt_qos: int = 1
    mqtt_keepalive: int = 60
    mqtt_connect_timeout: int = 10

    @property
    def device_wildcard_topic(self) -> str:
        return f"{self.topic_prefix}/#"

    def topic_for(self, device_id: str) -> str:
        return f"{self.topic_prefix}/{device_id}"

    @property
    def http_base(self) -> str:
        scheme = "https" if self.mqtt_tls else "http"
        return f"{scheme}://{self.host}:{self.port}"


# ── Other component configs ───────────────────────────────────────

@dataclass
class RouterConfig:
    host: str
    port: int
    health_endpoint: str = "/health"
    route_endpoint: str = "/route"
    metrics_endpoint: str = "/metrics"


@dataclass
class ProxyConfig:
    host: str
    port: int
    health_endpoint: str = "/health"
    forward_endpoint: str = "/forward"
    upstream_timeout: int = 30


@dataclass
class ProcessorConfig:
    """
    Streamlit processor.
    Routes ingested data to on-prem CrateDB OR cloud DB
    based on the PROCESSOR_TARGET env variable set at startup.
    """
    host: str
    port: int
    target: str                     # "onprem" | "cloud"  (read from PROCESSOR_TARGET)
    ingest_endpoint: str = "/ingest"
    health_endpoint: str = "/health"
    stats_endpoint: str = "/stats"
    routing_endpoint: str = "/routing/status"


@dataclass
class CrateDBConfig:
    host: str
    port: int
    username: str
    password: str
    schema: str = "doc"
    http_port: int = 4200


@dataclass
class CloudDBConfig:
    """
    Cloud database target (e.g. Azure SQL, CosmosDB, ADX).
    Update field names to match your actual cloud DB.
    """
    host: str
    port: int
    database: str
    username: str
    password: str
    db_type: str = "azure_sql"      # azure_sql | cosmosdb | adx


@dataclass
class LandingConfig:
    host: str
    port: int
    health_endpoint: str = "/healthz"


# ── Composite environment config ──────────────────────────────────

@dataclass
class EnvironmentConfig:
    name: str
    processor_target: str                       # "onprem" | "cloud"
    adapters: Dict[str, AdapterInstanceConfig]  # keyed by type_name
    router: RouterConfig
    proxy: ProxyConfig
    processor: ProcessorConfig
    cratedb: CrateDBConfig
    cloud_db: CloudDBConfig
    landing: LandingConfig


def _adapter(type_name: str,
             env_key: str,
             default_host: str,
             default_port: int,
             default_mqtt_port: int,
             default_mqtt_ws_port: int,
             tls: bool = False) -> AdapterInstanceConfig:
    """Helper: build an AdapterInstanceConfig from env vars."""
    prefix = env_key  # e.g. ADAPTER_VOLTAGE
    return AdapterInstanceConfig(
        type_name=type_name,
        host=_env(f"{prefix}_HOST", default_host),
        port=int(_env(f"{prefix}_PORT", str(default_port))),
        mqtt_host=_env(f"{prefix}_MQTT_HOST", default_host),
        mqtt_port=int(_env(f"{prefix}_MQTT_PORT", str(default_mqtt_port))),
        mqtt_ws_port=int(_env(f"{prefix}_MQTT_WS_PORT", str(default_mqtt_ws_port))),
        topic_prefix=_env("MQTT_TOPIC_PREFIX", "devices"),
        mqtt_username=_env(f"{prefix}_MQTT_USERNAME", "") or None,
        mqtt_password=_env(f"{prefix}_MQTT_PASSWORD", "") or None,
        mqtt_tls=tls,
        mqtt_qos=int(_env("MQTT_QOS", "1")),
    )


# ── On-prem config ────────────────────────────────────────────────
ONPREM_CONFIG = EnvironmentConfig(
    name="onprem",
    processor_target=PROCESSOR_TARGET,
    adapters={
        "voltage": _adapter(
            "voltage", "ADAPTER_VOLTAGE", "localhost", 5001, 1884, 9002
        ),
        "current": _adapter(
            "current", "ADAPTER_CURRENT", "localhost", 5002, 1885, 9003
        ),
        "temperature": _adapter(
            "temperature", "ADAPTER_TEMPERATURE", "localhost", 5003, 1886, 9004
        ),
        # ── Add new adapter types here ───────────────────────────
        # "pressure": _adapter("pressure", "ADAPTER_PRESSURE", "localhost", 5004, 1887, 9005),
    },
    router=RouterConfig(
        host=_env("ROUTER_HOST", "localhost"),
        port=int(_env("ROUTER_PORT", "6000")),
    ),
    proxy=ProxyConfig(
        host=_env("PROXY_HOST", "localhost"),
        port=int(_env("PROXY_PORT", "7000")),
    ),
    processor=ProcessorConfig(
        host=_env("PROCESSOR_HOST", "localhost"),
        port=int(_env("PROCESSOR_PORT", "8501")),
        target=PROCESSOR_TARGET,
    ),
    cratedb=CrateDBConfig(
        host=_env("CRATEDB_HOST", "localhost"),
        port=int(_env("CRATEDB_PORT", "5432")),
        username=_env("CRATEDB_USER", "crate"),
        password=_env("CRATEDB_PASSWORD", ""),
    ),
    cloud_db=CloudDBConfig(
        host=_env("CLOUD_DB_HOST", ""),
        port=int(_env("CLOUD_DB_PORT", "1433")),
        database=_env("CLOUD_DB_NAME", "iot_pipeline"),
        username=_env("CLOUD_DB_USER", ""),
        password=_env("CLOUD_DB_PASSWORD", ""),
        db_type=_env("CLOUD_DB_TYPE", "azure_sql"),
    ),
    landing=LandingConfig(
        host=_env("LANDING_HOST", "localhost"),
        port=int(_env("LANDING_PORT", "3000")),
    ),
)

# ── Azure Cloud config ────────────────────────────────────────────
CLOUD_CONFIG = EnvironmentConfig(
    name="cloud",
    processor_target=PROCESSOR_TARGET,
    adapters={
        "voltage": _adapter(
            "voltage", "AZURE_ADAPTER_VOLTAGE",
            "voltage-adapter.yourdomain.azure.com", 443, 8883, 9443, tls=True
        ),
        "current": _adapter(
            "current", "AZURE_ADAPTER_CURRENT",
            "current-adapter.yourdomain.azure.com", 443, 8883, 9443, tls=True
        ),
        "temperature": _adapter(
            "temperature", "AZURE_ADAPTER_TEMPERATURE",
            "temperature-adapter.yourdomain.azure.com", 443, 8883, 9443, tls=True
        ),
    },
    router=RouterConfig(
        host=_env("AZURE_ROUTER_HOST", "router.yourdomain.azure.com"),
        port=int(_env("AZURE_ROUTER_PORT", "443")),
    ),
    proxy=ProxyConfig(
        host=_env("AZURE_PROXY_HOST", "proxy.yourdomain.azure.com"),
        port=int(_env("AZURE_PROXY_PORT", "443")),
    ),
    processor=ProcessorConfig(
        host=_env("AZURE_PROCESSOR_HOST", "processor.yourdomain.azure.com"),
        port=int(_env("AZURE_PROCESSOR_PORT", "443")),
        target=PROCESSOR_TARGET,
    ),
    cratedb=CrateDBConfig(
        host=_env("AZURE_CRATEDB_HOST", "cratedb.yourdomain.azure.com"),
        port=int(_env("AZURE_CRATEDB_PORT", "5432")),
        username=_env("AZURE_CRATEDB_USER", "crate"),
        password=_env("AZURE_CRATEDB_PASSWORD", ""),
    ),
    cloud_db=CloudDBConfig(
        host=_env("AZURE_CLOUD_DB_HOST", "yourserver.database.windows.net"),
        port=int(_env("AZURE_CLOUD_DB_PORT", "1433")),
        database=_env("AZURE_CLOUD_DB_NAME", "iot_pipeline"),
        username=_env("AZURE_CLOUD_DB_USER", ""),
        password=_env("AZURE_CLOUD_DB_PASSWORD", ""),
        db_type=_env("CLOUD_DB_TYPE", "azure_sql"),
    ),
    landing=LandingConfig(
        host=_env("AZURE_LANDING_HOST", "landing.yourdomain.azure.com"),
        port=int(_env("AZURE_LANDING_PORT", "443")),
    ),
)


def get_config() -> EnvironmentConfig:
    return CLOUD_CONFIG if ENVIRONMENT == "cloud" else ONPREM_CONFIG
