from __future__ import annotations

import os
import socket
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


LLM_ENV_PREFIX = "GUARDEDOPS_LLM_"
SECRET_ENV_VARS = ("DASHSCOPE_API_KEY",)
PROXY_ENV_SUFFIX = "_PROXY"
LOOPBACK_HOSTS = frozenset({"", "0.0.0.0", "127.0.0.1", "::1", "localhost", "::"})

_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex


class NetworkAccessBlockedError(RuntimeError):
    """Raised when a test tries to reach a host outside loopback."""


def _is_loopback_address(address: Any) -> bool:
    if not isinstance(address, tuple) or not address:
        return True
    host = address[0]
    if not isinstance(host, str):
        return False
    return host.strip().strip("[]").lower() in LOOPBACK_HOSTS


def _guarded_connect(self: socket.socket, address: Any, *args: Any, **kwargs: Any) -> Any:
    if not _is_loopback_address(address):
        raise NetworkAccessBlockedError(f"outbound network access is blocked in tests: {address!r}")
    return _REAL_CONNECT(self, address, *args, **kwargs)


def _guarded_connect_ex(self: socket.socket, address: Any, *args: Any, **kwargs: Any) -> Any:
    if not _is_loopback_address(address):
        raise NetworkAccessBlockedError(f"outbound network access is blocked in tests: {address!r}")
    return _REAL_CONNECT_EX(self, address, *args, **kwargs)


@pytest.fixture(autouse=True, scope="session")
def guarded_test_environment() -> Iterator[None]:
    """Keep the suite hermetic: no LLM env, no proxy, no outbound sockets.

    Loopback stays reachable because the Windows asyncio event loop used by
    ``TestClient`` builds its self-pipe with ``socket.socketpair``. Every
    HTTP proxy is therefore removed as well, otherwise a proxy listening on
    127.0.0.1 would still forward test traffic to the real internet.
    """

    with pytest.MonkeyPatch.context() as patcher:
        for name in list(os.environ):
            if (
                name.startswith(LLM_ENV_PREFIX)
                or name in SECRET_ENV_VARS
                or name.upper().endswith(PROXY_ENV_SUFFIX)
            ):
                patcher.delenv(name, raising=False)
        patcher.setenv("NO_PROXY", "*")
        patcher.setattr(socket.socket, "connect", _guarded_connect)
        patcher.setattr(socket.socket, "connect_ex", _guarded_connect_ex)
        yield
