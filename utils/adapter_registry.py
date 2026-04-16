# ─────────────────────────────────────────────────────────────────
# utils/adapter_registry.py
#
# Central registry of all known adapter types.
#
# Each adapter:
#   - Is its own Docker container
#   - Embeds its own Mosquitto broker
#   - Publishes to devices/# with a "type" field in the payload
#   - Has its own HTTP management API (health, metrics, status)
#
# To add a new adapter type:
#   1. Add an entry to ADAPTER_REGISTRY below
#   2. Add its config to env_config.py (AdapterInstanceConfig)
#   3. Add a Docker Compose service entry
#   4. Create a test file in tests/unit/test_adapter/adapters/
# ─────────────────────────────────────────────────────────────────

from dataclasses import dataclass
from typing import List


@dataclass
class AdapterTypeSpec:
    """
    Describes a single adapter type.

    type_name     : value of the "type" field in MQTT payloads
    env_key       : env variable prefix (e.g. ADAPTER_VOLTAGE_HOST)
    default_port  : HTTP management API port
    mqtt_port     : Mosquitto broker plain port (embedded)
    mqtt_ws_port  : Mosquitto WebSocket port (embedded)
    payload_fields: required reading fields for this device type
    description   : human-readable description
    """
    type_name: str
    env_key: str
    default_port: int
    mqtt_port: int
    mqtt_ws_port: int
    payload_fields: List[str]
    description: str


# ── Registry ──────────────────────────────────────────────────────
# Add new adapter types here. Ports must be unique across all adapters.

ADAPTER_REGISTRY: List[AdapterTypeSpec] = [
    AdapterTypeSpec(
        type_name="voltage",
        env_key="ADAPTER_VOLTAGE",
        default_port=5001,
        mqtt_port=1884,
        mqtt_ws_port=9002,
        payload_fields=["voltage"],
        description="Voltage sensor adapter — measures AC/DC voltage in volts",
    ),
    AdapterTypeSpec(
        type_name="current",
        env_key="ADAPTER_CURRENT",
        default_port=5002,
        mqtt_port=1885,
        mqtt_ws_port=9003,
        payload_fields=["current"],
        description="Current sensor adapter — measures current draw in amperes",
    ),
    AdapterTypeSpec(
        type_name="temperature",
        env_key="ADAPTER_TEMPERATURE",
        default_port=5003,
        mqtt_port=1886,
        mqtt_ws_port=9004,
        payload_fields=["temperature"],
        description="Temperature sensor adapter — measures temp in Celsius",
    ),
    # ── Add new adapter types below ───────────────────────────────
    # AdapterTypeSpec(
    #     type_name="pressure",
    #     env_key="ADAPTER_PRESSURE",
    #     default_port=5004,
    #     mqtt_port=1887,
    #     mqtt_ws_port=9005,
    #     payload_fields=["pressure"],
    #     description="Pressure sensor adapter — measures pressure in bar",
    # ),
]

# Fast lookup by type_name
ADAPTER_BY_TYPE = {a.type_name: a for a in ADAPTER_REGISTRY}


def get_adapter(type_name: str) -> AdapterTypeSpec:
    if type_name not in ADAPTER_BY_TYPE:
        raise KeyError(
            f"Unknown adapter type '{type_name}'. "
            f"Known types: {list(ADAPTER_BY_TYPE.keys())}"
        )
    return ADAPTER_BY_TYPE[type_name]
