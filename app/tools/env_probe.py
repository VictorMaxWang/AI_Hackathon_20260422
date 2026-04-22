from __future__ import annotations

from app.executors import BaseExecutor
from app.models import CommandResult, EnvironmentSnapshot


COMMAND_PROBES: dict[str, list[str]] = {
    "df": ["df", "--version"],
    "find": ["find", "--version"],
    "ps": ["ps", "--version"],
    "ss": ["ss", "-V"],
    "lsof": ["lsof", "-v"],
    "getent": ["getent", "--help"],
    "useradd": ["useradd", "--help"],
    "userdel": ["userdel", "--help"],
    "sudo": ["sudo", "-V"],
}

MISSING_COMMAND_EXIT_CODES = {-1, 126, 127}


def env_probe_tool(executor: BaseExecutor) -> EnvironmentSnapshot:
    """Collect a read-only snapshot of the current execution environment."""

    hostname = _first_line(_run(executor, ["hostname"], timeout=3), default="unknown")
    distro = _detect_distro(executor)
    kernel = _first_line(_run(executor, ["uname", "-r"], timeout=3), default="unknown")
    current_user = _first_line(_run(executor, ["id", "-un"], timeout=3), default="unknown")
    is_root = _is_root(executor)
    sudo_available = _sudo_available(executor)
    available_commands = _available_commands(executor)
    connection_mode = _connection_mode(executor)

    return EnvironmentSnapshot(
        hostname=hostname,
        distro=distro,
        kernel=kernel,
        current_user=current_user,
        is_root=is_root,
        sudo_available=sudo_available,
        available_commands=available_commands,
        connection_mode=connection_mode,
    )


def _run(
    executor: BaseExecutor,
    argv: list[str],
    *,
    timeout: int,
) -> CommandResult | None:
    try:
        return executor.run(argv, timeout=timeout)
    except Exception:
        return None


def _first_line(result: CommandResult | None, *, default: str) -> str:
    if result is None or not result.success:
        return default

    for line in result.stdout.splitlines():
        value = line.strip()
        if value:
            return value
    return default


def _detect_distro(executor: BaseExecutor) -> str:
    result = _run(executor, ["cat", "/etc/os-release"], timeout=3)
    if result is None or not result.success:
        return "unknown"

    values = _parse_os_release(result.stdout)
    if values.get("PRETTY_NAME"):
        return values["PRETTY_NAME"]

    name = values.get("NAME")
    version = values.get("VERSION_ID")
    if name and version:
        return f"{name} {version}"
    if name:
        return name
    return "unknown"


def _parse_os_release(content: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _is_root(executor: BaseExecutor) -> bool:
    result = _run(executor, ["id", "-u"], timeout=3)
    if result is None or not result.success:
        return False
    return result.stdout.strip() == "0"


def _sudo_available(executor: BaseExecutor) -> bool:
    result = _run(executor, ["sudo", "-n", "true"], timeout=3)
    return bool(result is not None and result.success)


def _available_commands(executor: BaseExecutor) -> list[str]:
    available: list[str] = []
    for command, argv in COMMAND_PROBES.items():
        result = _run(executor, argv, timeout=3)
        if result is None or result.timed_out:
            continue
        if result.exit_code in MISSING_COMMAND_EXIT_CODES:
            continue
        available.append(command)
    return available


def _connection_mode(executor: BaseExecutor) -> str:
    for attr_name in ("connection_mode", "mode"):
        value = getattr(executor, attr_name, None)
        if isinstance(value, str) and value.lower() in {"local", "ssh"}:
            return value.lower()

    executor_type = type(executor)
    name = executor_type.__name__.lower()
    module = executor_type.__module__.lower()
    if "ssh" in name or module.endswith(".ssh") or ".ssh" in module:
        return "ssh"
    return "local"
