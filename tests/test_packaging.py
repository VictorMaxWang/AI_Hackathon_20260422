from __future__ import annotations

import importlib.metadata
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_TIMEOUT_SECONDS = 600
MINIMUM_SETUPTOOLS_VERSION = (77,)
DIST_INFO_RE = re.compile(r"^guardedops-[^/]+\.dist-info$")

REQUIRED_UI_MEMBERS = frozenset(
    {
        "app/ui/index.html",
        "app/ui/app.js",
        "app/ui/style.css",
    }
)
REQUIRED_TEMPLATE_MEMBERS = frozenset(
    {
        "app/evolution/templates/diagnose_port_owner.json",
        "app/evolution/templates/safe_disk_triage.json",
        "app/evolution/templates/safe_file_search.json",
        "app/evolution/templates/safe_user_lifecycle.json",
    }
)
BUILD_INPUT_FILES = ("pyproject.toml", "README.md", "LICENSE")


@pytest.fixture(scope="module")
def wheel_namelist(tmp_path_factory: pytest.TempPathFactory) -> frozenset[str]:
    """Build a wheel from the tracked sources and return its member names."""

    workspace = tmp_path_factory.mktemp("packaging")
    source_dir = workspace / "src"
    wheel_dir = workspace / "wheels"
    wheel_dir.mkdir()
    _copy_build_inputs(source_dir)

    command = _wheel_build_command(source_dir, wheel_dir)
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=BUILD_TIMEOUT_SECONDS,
        cwd=str(workspace),
    )
    if completed.returncode != 0:
        raise AssertionError(
            "wheel build failed\n"
            f"command: {command}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )

    wheels = sorted(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {[path.name for path in wheels]}"

    with zipfile.ZipFile(wheels[0]) as archive:
        return frozenset(archive.namelist())


def test_wheel_ships_the_operator_panel_assets(wheel_namelist: frozenset[str]) -> None:
    missing = sorted(REQUIRED_UI_MEMBERS - wheel_namelist)

    assert not missing, f"wheel is missing Operator Panel assets: {missing}"


def test_wheel_ships_every_workflow_template(wheel_namelist: frozenset[str]) -> None:
    missing = sorted(REQUIRED_TEMPLATE_MEMBERS - wheel_namelist)

    assert not missing, f"wheel is missing workflow templates: {missing}"

    shipped = {name for name in wheel_namelist if name.startswith("app/evolution/templates/")}
    assert shipped == set(REQUIRED_TEMPLATE_MEMBERS)


def test_wheel_templates_match_the_templates_in_the_source_tree(
    wheel_namelist: frozenset[str],
) -> None:
    source_dir = REPO_ROOT / "app" / "evolution" / "templates"
    source_members = {
        f"app/evolution/templates/{path.name}" for path in source_dir.glob("*.json")
    }

    assert source_members == set(REQUIRED_TEMPLATE_MEMBERS)
    for path in sorted(source_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["workflow_id"] == path.stem


def test_wheel_has_no_top_level_member_beyond_the_app_package(
    wheel_namelist: frozenset[str],
) -> None:
    top_level = {name.split("/", 1)[0] for name in wheel_namelist if name}
    unexpected = sorted(
        name for name in top_level if name != "app" and not DIST_INFO_RE.match(name)
    )

    assert not unexpected, f"wheel leaks top-level members: {unexpected}"
    assert "app" in top_level
    assert any(DIST_INFO_RE.match(name) for name in top_level)


def _copy_build_inputs(destination: Path) -> None:
    destination.mkdir(parents=True)
    shutil.copytree(
        REPO_ROOT / "app",
        destination / "app",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    for name in BUILD_INPUT_FILES:
        shutil.copy2(REPO_ROOT / name, destination / name)


def _wheel_build_command(source_dir: Path, wheel_dir: Path) -> list[str]:
    if _is_distribution_installed("build"):
        return [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(wheel_dir),
            str(source_dir),
        ]

    if not _is_distribution_installed("pip"):
        pytest.skip("no PEP 517 front end available: neither build nor pip is importable")

    setuptools_version = _installed_setuptools_version()
    if setuptools_version is None:
        pytest.skip("setuptools is not installed, so the build backend cannot run offline")
    if setuptools_version < MINIMUM_SETUPTOOLS_VERSION:
        pytest.skip(
            "installed setuptools "
            f"{'.'.join(str(part) for part in setuptools_version)} is older than the "
            f"{'.'.join(str(part) for part in MINIMUM_SETUPTOOLS_VERSION)} this project builds with"
        )

    return [
        sys.executable,
        "-m",
        "pip",
        "wheel",
        "--no-deps",
        "--no-index",
        "--no-build-isolation",
        "--wheel-dir",
        str(wheel_dir),
        str(source_dir),
    ]


def _is_distribution_installed(name: str) -> bool:
    """Ask the installed-distribution metadata, never the import path.

    ``importlib.util.find_spec`` is not usable here: a stray ``build/``
    directory left behind by an earlier wheel build shadows the real ``build``
    front end as an implicit namespace package.
    """

    try:
        importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


def _installed_setuptools_version() -> tuple[int, ...] | None:
    try:
        raw_version = importlib.metadata.version("setuptools")
    except importlib.metadata.PackageNotFoundError:
        return None

    parts: list[int] = []
    for chunk in raw_version.split("."):
        if not chunk.isdigit():
            break
        parts.append(int(chunk))
    return tuple(parts) or None
