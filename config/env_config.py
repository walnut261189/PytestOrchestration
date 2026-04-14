# ─────────────────────────────────────────────────────────────────
# config/env_config.py
#
# Single source of truth for all environment-specific settings.
# Toggle ENV=onprem|cloud via environment variable or .env file.
# On-prem values are defaults; cloud values are loaded when
# ENV=cloud is set (e.g. in Azure Pipelines variable group).
# ─────────────────────────────────────────────────────────────────

import os
from dataclasses import dataclass, field
from typing import Optional


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


# ── Active environment ────────────────────────────────────────────
ENVIRONMENT = _env("ENV", "onprem")   # "onprem" | "cloud"


@dataclass
class AdapterConfig:
    host: str
    port: int
    protocol: str           # mqtt | modbus | opcua | http
    topic_prefix: str
    connect_timeout: int = 10
    tls_enabled: bool = False


@dataclass
class RouterConfig:
    host: str
    port: int
    health_endpoint: str = "/health"
    route_table_endpoint: str = "/routes"


@dataclass
class ProxyConfig:
    host: str
    port: int
    health_endpoint: str = "/health"
    upstream_timeout: int = 30


@dataclass
class ProcessorConfig:
    host: str
    port: int
    ingest_endpoint: str = "/ingest"
    health_endpoint: str = "/health"


@dataclass
class DBConfig:
    host: str
    port: int
    username: str
    password: str
    schema: str = "doc"
    http_port: int = 4200


@dataclass
class LandingConfig:
    host: str
    port: int
    health_endpoint: str = "/healthz"


@dataclass
class EnvironmentConfig:
    name: str
    adapter: AdapterConfig
    router: RouterConfig
    proxy: ProxyConfig
    processor: ProcessorConfig
    db: DBConfig
    landing: LandingConfig


# ── On-prem defaults (Docker Compose / Kubernetes on local infra) ─
ONPREM_CONFIG = EnvironmentConfig(
    name="onprem",
    adapter=AdapterConfig(
        host=_env("ADAPTER_HOST", "localhost"),
        port=int(_env("ADAPTER_PORT", "5000")),
        protocol=_env("ADAPTER_PROTOCOL", "mqtt"),
        topic_prefix=_env("ADAPTER_TOPIC_PREFIX", "iot/devices"),
    ),
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
    ),
    db=DBConfig(
        host=_env("CRATEDB_HOST", "localhost"),
        port=int(_env("CRATEDB_PORT", "5432")),
        username=_env("CRATEDB_USER", "crate"),
        password=_env("CRATEDB_PASSWORD", ""),
    ),
    landing=LandingConfig(
        host=_env("LANDING_HOST", "localhost"),
        port=int(_env("LANDING_PORT", "3000")),
    ),
)

# ── Azure Cloud overrides ─────────────────────────────────────────
# Populated via Azure Pipelines variable groups or Key Vault refs.
CLOUD_CONFIG = EnvironmentConfig(
    name="cloud",
    adapter=AdapterConfig(
        host=_env("AZURE_ADAPTER_HOST", "adapter.yourdomain.azure.com"),
        port=int(_env("AZURE_ADAPTER_PORT", "443")),
        protocol=_env("ADAPTER_PROTOCOL", "mqtt"),
        topic_prefix=_env("ADAPTER_TOPIC_PREFIX", "iot/devices"),
        tls_enabled=True,
    ),
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
    ),
    db=DBConfig(
        host=_env("AZURE_CRATEDB_HOST", "cratedb.yourdomain.azure.com"),
        port=int(_env("AZURE_CRATEDB_PORT", "5432")),
        username=_env("AZURE_CRATEDB_USER", "crate"),
        password=_env("AZURE_CRATEDB_PASSWORD", ""),
    ),
    landing=LandingConfig(
        host=_env("AZURE_LANDING_HOST", "landing.yourdomain.azure.com"),
        port=int(_env("AZURE_LANDING_PORT", "443")),
    ),
)


def get_config() -> EnvironmentConfig:
    """Return the active config based on the ENV variable."""
    if ENVIRONMENT == "cloud":
        return CLOUD_CONFIG
    return ONPREM_CONFIG
