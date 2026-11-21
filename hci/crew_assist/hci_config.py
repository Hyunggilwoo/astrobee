"""Names and defaults for the HCI environment variables.

Shell scripts and hci/docker/compose.yml use these same names. Defaults here
match that compose file. Nothing in this module starts a process or opens a
port.
"""
from __future__ import annotations

import os

# Phone page. Bound on the host by compose so a phone on the same LAN can
# open it. Not a public service.
WEB_BACKEND = "ros1"
WEB_BIND = "0.0.0.0"
WEB_PORT = 8090
HEARTBEAT_S = 2.0

# Typed relay between the Noetic sim and the Jazzy client. Localhost only.
BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 11511
BRIDGE_LOG = "/hci/logs/bridge_latency.jsonl"


def getenv(name: str, default: str) -> str:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value


def getenv_int(name: str, default: int) -> int:
    return int(getenv(name, str(default)))


def getenv_float(name: str, default: float) -> float:
    return float(getenv(name, str(default)))


def web_backend() -> str:
    return getenv("HCI_WEB_BACKEND", WEB_BACKEND)


def web_bind() -> str:
    return getenv("HCI_WEB_BIND", WEB_BIND)


def web_port() -> int:
    return getenv_int("HCI_WEB_PORT", WEB_PORT)


def heartbeat_s() -> float:
    return getenv_float("HCI_HEARTBEAT_S", HEARTBEAT_S)


def pairing_code():
    value = os.environ.get("HCI_PAIRING_CODE")
    return value if value else None


def bridge_host() -> str:
    return getenv("HCI_BRIDGE_HOST", BRIDGE_HOST)


def bridge_port() -> int:
    return getenv_int("HCI_BRIDGE_PORT", BRIDGE_PORT)


def bridge_log(default: str = BRIDGE_LOG) -> str:
    return getenv("HCI_BRIDGE_LOG", default)


def log_dir(default: str) -> str:
    return getenv("HCI_LOG_DIR", default)


def image_dir(default: str) -> str:
    return getenv("HCI_IMAGE_DIR", default)


def vosk_model(default: str) -> str:
    return getenv("HCI_VOSK_MODEL", default)
