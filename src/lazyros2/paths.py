# SPDX-License-Identifier: AGPL-3.0-or-later
"""Filesystem layout and ROS workspace boundaries."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
from typing import Callable, Mapping, Sequence


class WorkspaceError(ValueError):
    """Raised when a directory cannot be used as a LazyROS2 workspace."""


PackageProbe = Callable[[Path], Sequence[str]]


@dataclass(frozen=True, slots=True)
class XdgPaths:
    """LazyROS2 roots under the XDG base directories."""

    config: Path
    state: Path
    cache: Path
    runtime: Path

    @classmethod
    def from_environment(
        cls,
        env: Mapping[str, str] | None = None,
        home: Path | None = None,
        uid: int | None = None,
    ) -> "XdgPaths":
        source = os.environ if env is None else env
        home_dir = (Path.home() if home is None else Path(home)).expanduser()
        user_id = _current_uid() if uid is None else uid

        config = _xdg_root(source, "XDG_CONFIG_HOME", home_dir / ".config")
        state = _xdg_root(
            source,
            "XDG_STATE_HOME",
            home_dir / ".local" / "state",
        )
        cache = _xdg_root(source, "XDG_CACHE_HOME", home_dir / ".cache")
        runtime = _runtime_root(source, user_id)
        return cls(
            config=config / "lazyros2",
            state=state / "lazyros2",
            cache=cache / "lazyros2",
            runtime=runtime,
        )

    @property
    def config_file(self) -> Path:
        return self.config / "config.json"

    @property
    def history(self) -> Path:
        return self.state / "history"

    @property
    def completion(self) -> Path:
        return self.cache / "completion"


@dataclass(frozen=True, slots=True)
class Workspace:
    """Canonical paths belonging to one colcon workspace."""

    root: Path
    src: Path
    build: Path
    install: Path
    log: Path

    @classmethod
    def open(
        cls,
        path: Path | str,
        package_probe: PackageProbe | None = None,
    ) -> "Workspace":
        root = Path(path).expanduser().resolve(strict=False)
        if not root.is_dir():
            raise WorkspaceError(f"workspace directory does not exist: {root}")

        src = root / "src"
        if not src.is_dir():
            probe = _default_package_probe if package_probe is None else package_probe
            try:
                package_names = tuple(probe(root))
            except OSError as error:
                raise WorkspaceError(
                    f"cannot inspect workspace packages in {root}: {error}"
                ) from error
            if not package_names:
                raise WorkspaceError(
                    f"not a ROS 2 workspace: {root} has no src directory "
                    "and colcon found no packages"
                )

        return cls(
            root=root,
            src=src,
            build=root / "build",
            install=root / "install",
            log=root / "log",
        )


def _xdg_root(env: Mapping[str, str], name: str, fallback: Path) -> Path:
    raw_value = env.get(name, "")
    if not raw_value:
        return fallback

    candidate = Path(raw_value).expanduser()
    if candidate.is_absolute() or raw_value.startswith("/"):
        return candidate
    return fallback


def _runtime_root(env: Mapping[str, str], uid: int) -> Path:
    raw_value = env.get("XDG_RUNTIME_DIR", "")
    if raw_value:
        candidate = Path(raw_value).expanduser()
        if candidate.is_absolute() or raw_value.startswith("/"):
            return candidate / "lazyros2"
    return Path(tempfile.gettempdir()) / f"lazyros2-{uid}"


def _current_uid() -> int:
    getuid = getattr(os, "getuid", None)
    return int(getuid()) if getuid is not None else 0


def _default_package_probe(root: Path) -> Sequence[str]:
    # Workspace.open remains useful for non-standard layouts while keeping the
    # subprocess boundary literal and short-lived.
    from .process import run_command

    result = run_command(
        ("colcon", "list", "--names-only", "--base-paths", str(root)),
        cwd=root,
        timeout=2.0,
        capture_output=True,
    )
    if result.exit_code != 0:
        return ()
    return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())
