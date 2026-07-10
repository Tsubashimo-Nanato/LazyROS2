# SPDX-License-Identifier: AGPL-3.0-or-later
"""Colcon command construction and package evidence collection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .paths import Workspace
from .process import CommandResult, run_command


CommandRunner = Callable[..., CommandResult]

RESERVED_ARGUMENTS = (
    "--base-paths",
    "--build-base",
    "--install-base",
    "--log-base",
    "--merge-install",
    "--test-result-base",
)


class ColconError(RuntimeError):
    """Raised when package metadata cannot be collected reliably."""

    def __init__(self, message: str, *, exit_code: int | None = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True, slots=True)
class DependencyEvidence:
    up_to_packages: tuple[str, ...]
    missing_dependencies: tuple[str, ...]


def build_argv(
    workspace: Workspace,
    packages: Sequence[str] = (),
    *,
    up_to: bool = False,
    passthrough: Sequence[str] = (),
) -> tuple[str, ...]:
    selected = _validated_packages(packages)
    extra = _validated_passthrough(passthrough)
    if up_to and not selected:
        raise ValueError("build --up-to requires at least one package")

    argv = [
        "colcon",
        "--log-base",
        str(workspace.log),
        "build",
        "--base-paths",
        str(workspace.root),
        "--build-base",
        str(workspace.build),
        "--install-base",
        str(workspace.install),
    ]
    if selected:
        argv.append("--packages-up-to" if up_to else "--packages-select")
        argv.extend(selected)
    argv.extend(extra)
    return tuple(argv)


def test_argv(
    workspace: Workspace,
    packages: Sequence[str] = (),
    *,
    passthrough: Sequence[str] = (),
) -> tuple[str, ...]:
    selected = _validated_packages(packages)
    extra = _validated_passthrough(passthrough)
    argv = [
        "colcon",
        "--log-base",
        str(workspace.log),
        "test",
        "--base-paths",
        str(workspace.root),
        "--build-base",
        str(workspace.build),
        "--install-base",
        str(workspace.install),
    ]
    if selected:
        argv.extend(("--packages-select", *selected))
    argv.extend(extra)
    return tuple(argv)


def test_result_argv(workspace: Workspace) -> tuple[str, ...]:
    return (
        "colcon",
        "test-result",
        "--test-result-base",
        str(workspace.build),
        "--verbose",
    )


def discover_packages(
    workspace: Workspace,
    env: Mapping[str, str],
    *,
    runner: CommandRunner = run_command,
    timeout: float = 2.0,
) -> tuple[str, ...]:
    argv = (
        "colcon",
        "list",
        "--names-only",
        "--base-paths",
        str(workspace.root),
    )
    result = runner(
        argv,
        cwd=workspace.root,
        env=env,
        timeout=timeout,
        capture_output=True,
    )
    if result.exit_code != 0:
        detail = result.stderr.strip()
        suffix = f": {detail}" if detail else ""
        raise ColconError(
            f"colcon list failed with exit {result.exit_code}{suffix}",
            exit_code=result.exit_code,
        )
    return _names_from_stdout(result.stdout)


def find_missing_workspace_dependencies(
    workspace: Workspace,
    targets: Sequence[str],
    env: Mapping[str, str],
    *,
    runner: CommandRunner = run_command,
    timeout: float = 2.0,
) -> DependencyEvidence:
    selected = _validated_packages(targets)
    if not selected:
        raise ValueError("dependency evidence requires at least one target package")
    argv = (
        "colcon",
        "list",
        "--names-only",
        "--base-paths",
        str(workspace.root),
        "--packages-up-to",
        *selected,
    )
    result = runner(
        argv,
        cwd=workspace.root,
        env=env,
        timeout=timeout,
        capture_output=True,
    )
    if result.exit_code != 0:
        detail = result.stderr.strip()
        suffix = f": {detail}" if detail else ""
        raise ColconError(
            f"cannot calculate --packages-up-to evidence "
            f"(exit {result.exit_code}){suffix}",
            exit_code=result.exit_code,
        )

    up_to = _names_from_stdout(result.stdout)
    installed = _installed_package_names(workspace.install)
    target_set = set(selected)
    missing = tuple(
        package
        for package in up_to
        if package not in target_set and package not in installed
    )
    return DependencyEvidence(up_to, missing)


def _validated_packages(packages: Sequence[str]) -> tuple[str, ...]:
    if isinstance(packages, (str, bytes)):
        raise TypeError("packages must be a sequence of package names")
    selected = tuple(packages)
    for index, package in enumerate(selected):
        if not isinstance(package, str):
            raise TypeError(
                f"packages[{index}] must be str, got {type(package).__name__}"
            )
        if (
            not package
            or package.startswith("-")
            or "\0" in package
            or any(character.isspace() for character in package)
        ):
            raise ValueError(f"invalid package name at packages[{index}]: {package!r}")
    return selected


def _validated_passthrough(arguments: Sequence[str]) -> tuple[str, ...]:
    if isinstance(arguments, (str, bytes)):
        raise TypeError("passthrough must be a sequence of arguments")
    extra = tuple(arguments)
    for index, argument in enumerate(extra):
        if not isinstance(argument, str):
            raise TypeError(
                f"passthrough[{index}] must be str, got {type(argument).__name__}"
            )
        if "\0" in argument:
            raise ValueError(f"passthrough[{index}] contains NUL")
        option = argument.split("=", 1)[0]
        if option.startswith("--packages-") or option in RESERVED_ARGUMENTS:
            raise ValueError(f"reserved colcon argument cannot be passed through: {option}")
    return extra


def _names_from_stdout(stdout: str) -> tuple[str, ...]:
    return tuple(sorted({line.strip() for line in stdout.splitlines() if line.strip()}))


def _installed_package_names(install: Path) -> set[str]:
    if not install.is_dir():
        return set()
    patterns = (
        "share/ament_index/resource_index/packages/*",
        "*/share/ament_index/resource_index/packages/*",
    )
    names: set[str] = set()
    for pattern in patterns:
        for marker in install.glob(pattern):
            if marker.is_file():
                names.add(marker.name)
    return names
