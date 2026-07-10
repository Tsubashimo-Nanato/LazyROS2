# SPDX-License-Identifier: AGPL-3.0-or-later
"""ROS-aware completion snapshots with atomic stale-cache handling."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Callable, Mapping

from .colcon import ColconError, discover_packages
from .paths import Workspace
from .process import CommandResult, run_command
from .storage import atomic_write_json, exclusive_lock, read_json


CACHE_SCHEMA_VERSION = 1
COMPLETION_CACHE_DOMAINS = frozenset({"packages", "runtime", "launch"})


class CompletionError(RuntimeError):
    """Raised when fresh completion data cannot be collected or decoded."""

    def __init__(self, message: str, *, exit_code: int | None = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True, slots=True)
class CompletionData:
    packages: tuple[str, ...] = ()
    executables: dict[str, tuple[str, ...]] = field(default_factory=dict)
    launches: dict[str, tuple[str, ...]] = field(default_factory=dict)
    ambiguous_launches: dict[str, dict[str, tuple[str, ...]]] = field(
        default_factory=dict
    )
    rviz_files: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "packages", _clean_names(self.packages))
        object.__setattr__(self, "rviz_files", _clean_names(self.rviz_files))
        object.__setattr__(self, "executables", _clean_name_map(self.executables))
        object.__setattr__(self, "launches", _clean_name_map(self.launches))
        object.__setattr__(
            self,
            "ambiguous_launches",
            _clean_ambiguities(self.ambiguous_launches),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "packages": list(self.packages),
            "executables": {
                package: list(names) for package, names in self.executables.items()
            },
            "launches": {
                package: list(names) for package, names in self.launches.items()
            },
            "ambiguous_launches": {
                package: {
                    basename: list(paths) for basename, paths in basenames.items()
                }
                for package, basenames in self.ambiguous_launches.items()
            },
            "rviz_files": list(self.rviz_files),
        }


@dataclass(frozen=True, slots=True)
class CachedCompletion:
    data: CompletionData
    updated_at: float
    stale: bool = False
    dirty: bool = False
    error: str | None = None
    error_code: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": CACHE_SCHEMA_VERSION,
            "updated_at": self.updated_at,
            "stale": self.stale,
            "dirty": self.dirty,
            "error": self.error,
            "error_code": self.error_code,
            "data": self.data.to_json(),
        }


class CompletionCollector:
    def __init__(
        self,
        runner: Callable[..., CommandResult] = run_command,
        timeout: float = 1.75,
    ) -> None:
        if timeout <= 0:
            raise ValueError("completion timeout must be positive")
        self.runner = runner
        self.timeout = timeout

    def collect(
        self,
        workspace: Workspace,
        env: Mapping[str, str],
        *,
        deadline: float | None = None,
    ) -> CompletionData:
        deadline = _effective_deadline(deadline, self.timeout)
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="lazy-complete") as pool:
            package_future = pool.submit(
                self.collect_packages,
                workspace,
                env,
                deadline=deadline,
            )
            executable_future = pool.submit(
                self.collect_executables,
                workspace,
                env,
                deadline=deadline,
            )
            launches, ambiguities = self.collect_launches(
                workspace,
                env,
                deadline=deadline,
            )
            rviz_files = self.collect_rviz_files(workspace, deadline=deadline)
            try:
                packages = package_future.result(timeout=_remaining(deadline))
                executables = executable_future.result(timeout=_remaining(deadline))
            except FutureTimeoutError as error:
                raise CompletionError(
                    "completion collection exceeded its deadline",
                    exit_code=124,
                ) from error
        return CompletionData(
            packages=packages,
            executables=executables,
            launches=launches,
            ambiguous_launches=ambiguities,
            rviz_files=rviz_files,
        )

    def collect_packages(
        self,
        workspace: Workspace,
        env: Mapping[str, str],
        *,
        deadline: float | None = None,
    ) -> tuple[str, ...]:
        timeout = _command_timeout(deadline, self.timeout)
        try:
            return discover_packages(
                workspace,
                env,
                runner=self.runner,
                timeout=timeout,
            )
        except ColconError as error:
            raise CompletionError(str(error), exit_code=error.exit_code) from error

    def collect_executables(
        self,
        workspace: Workspace,
        env: Mapping[str, str],
        *,
        deadline: float | None = None,
    ) -> dict[str, tuple[str, ...]]:
        timeout = _command_timeout(deadline, self.timeout)
        argv = ("ros2", "pkg", "executables")
        result = self.runner(
            argv,
            cwd=workspace.root,
            env=env,
            timeout=timeout,
            capture_output=True,
        )
        if result.exit_code != 0:
            detail = result.stderr.strip()
            suffix = f": {detail}" if detail else ""
            raise CompletionError(
                f"ros2 pkg executables failed with exit {result.exit_code}{suffix}",
                exit_code=result.exit_code,
            )

        names: dict[str, set[str]] = {}
        for line_number, raw_line in enumerate(result.stdout.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            fields = line.split()
            if len(fields) != 2:
                raise CompletionError(
                    "ros2 pkg executables returned malformed output "
                    f"at line {line_number}: {raw_line!r}"
                )
            package, executable = fields
            names.setdefault(package, set()).add(executable)
        return {
            package: tuple(sorted(executables))
            for package, executables in sorted(names.items())
        }

    def collect_launches(
        self,
        workspace: Workspace,
        env: Mapping[str, str],
        *,
        deadline: float | None = None,
    ) -> tuple[
        dict[str, tuple[str, ...]],
        dict[str, dict[str, tuple[str, ...]]],
    ]:
        return _collect_launches(workspace, env, deadline=deadline)

    def collect_rviz_files(
        self,
        workspace: Workspace,
        *,
        deadline: float | None = None,
    ) -> tuple[str, ...]:
        return _collect_rviz_files(workspace, deadline=deadline)


class CompletionCache:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = Path(cache_dir)

    def key(
        self,
        workspace: Workspace,
        env: Mapping[str, str],
        ros2_path: str | None,
        colcon_path: str | None,
        *,
        domain: str,
    ) -> str:
        if domain not in COMPLETION_CACHE_DOMAINS:
            raise ValueError(f"unknown completion cache domain: {domain}")
        identity = {
            "domain": domain,
            "workspace": str(workspace.root),
            "ros2": ros2_path or "",
            "colcon": colcon_path or "",
            "ros_distro": env.get("ROS_DISTRO", ""),
            "ament_prefix_path": env.get("AMENT_PREFIX_PATH", ""),
        }
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return hashlib.sha256(encoded).hexdigest()

    def read(self, key: str) -> CachedCompletion | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            raw = read_json(path)
        except (OSError, json.JSONDecodeError) as error:
            raise CompletionError(f"cannot read completion cache {path}: {error}") from error
        return _parse_cached_completion(raw, path)

    def refresh(
        self,
        key: str,
        collect: Callable[[], CompletionData],
        *,
        lock_timeout: float = 0.2,
    ) -> CachedCompletion:
        if lock_timeout < 0:
            raise ValueError("completion cache lock timeout cannot be negative")
        path = self._path(key)
        lock_path = path.with_suffix(".lock")
        with exclusive_lock(lock_path, timeout=lock_timeout):
            previous = self.read(key)
            try:
                data = collect()
                if not isinstance(data, CompletionData):
                    raise TypeError(
                        f"completion collector returned {type(data).__name__}, "
                        "expected CompletionData"
                    )
            except Exception as error:
                if previous is None:
                    if isinstance(error, CompletionError):
                        raise
                    raise CompletionError(str(error)) from error
                stale = replace(
                    previous,
                    stale=True,
                    dirty=False,
                    error=str(error),
                    error_code=getattr(error, "exit_code", None),
                )
                atomic_write_json(path, stale.to_json())
                return stale

            fresh = CachedCompletion(data=data, updated_at=time.time())
            atomic_write_json(path, fresh.to_json())
            return fresh

    def mark_dirty(self, key: str) -> None:
        path = self._path(key)
        lock_path = path.with_suffix(".lock")
        with exclusive_lock(lock_path, timeout=0.2):
            cached = self.read(key)
            if cached is None:
                return
            atomic_write_json(path, replace(cached, dirty=True).to_json())

    def _path(self, key: str) -> Path:
        if (
            len(key) != 64
            or any(character not in "0123456789abcdef" for character in key)
        ):
            raise ValueError("completion cache key must be a lowercase SHA-256 digest")
        return self.cache_dir / f"{key}.json"


def _collect_launches(
    workspace: Workspace,
    env: Mapping[str, str],
    *,
    deadline: float | None = None,
) -> tuple[
    dict[str, tuple[str, ...]],
    dict[str, dict[str, tuple[str, ...]]],
]:
    launch_roots = _launch_roots(workspace, env)
    package_files: dict[str, dict[str, list[str]]] = {}
    seen_packages: set[str] = set()
    for package, launch_root in launch_roots:
        _check_deadline(deadline)
        if package in seen_packages:
            continue
        seen_packages.add(package)
        basenames: dict[str, list[str]] = {}
        for path in launch_root.rglob("*"):
            _check_deadline(deadline)
            if not path.is_file() or not _is_launch_file(path.name):
                continue
            relative = path.relative_to(launch_root).as_posix()
            basenames.setdefault(path.name, []).append(relative)
        if basenames:
            package_files[package] = basenames

    launches: dict[str, tuple[str, ...]] = {}
    ambiguities: dict[str, dict[str, tuple[str, ...]]] = {}
    for package, basenames in sorted(package_files.items()):
        unique = sorted(name for name, paths in basenames.items() if len(set(paths)) == 1)
        ambiguous = {
            name: tuple(sorted(set(paths)))
            for name, paths in sorted(basenames.items())
            if len(set(paths)) > 1
        }
        if unique:
            launches[package] = tuple(unique)
        if ambiguous:
            ambiguities[package] = ambiguous
    return launches, ambiguities


def ambiguous_launch_paths(
    workspace: Workspace,
    env: Mapping[str, str],
    package: str,
    basename: str,
) -> tuple[str, ...]:
    """Return every installed launch path sharing *basename* in *package*."""
    if not package or "\0" in package:
        raise ValueError("launch package must be non-empty and contain no NUL")
    if not basename or "\0" in basename or Path(basename).name != basename:
        raise ValueError("launch file must be a basename")
    _, ambiguities = _collect_launches(workspace, env)
    return ambiguities.get(package, {}).get(basename, ())


def _launch_roots(
    workspace: Workspace,
    env: Mapping[str, str],
) -> tuple[tuple[str, Path], ...]:
    raw_prefixes = [str(workspace.install)]
    raw_prefixes.extend(
        path for path in env.get("AMENT_PREFIX_PATH", "").split(os.pathsep) if path
    )
    prefixes: list[Path] = []
    seen_prefixes: set[Path] = set()
    for raw_prefix in raw_prefixes:
        prefix = Path(raw_prefix).expanduser().resolve(strict=False)
        if prefix in seen_prefixes:
            continue
        seen_prefixes.add(prefix)
        prefixes.append(prefix)

    roots: list[tuple[str, Path]] = []
    for prefix in prefixes:
        for launch_root in sorted(prefix.glob("share/*/launch")):
            if launch_root.is_dir():
                roots.append((launch_root.parent.name, launch_root))
        if prefix != workspace.install.resolve(strict=False):
            continue
        for launch_root in sorted(prefix.glob("*/share/*/launch")):
            if launch_root.is_dir():
                roots.append((launch_root.parent.name, launch_root))
    return tuple(roots)


def _is_launch_file(name: str) -> bool:
    return name.endswith((".launch.py", ".launch.xml", ".launch.yaml", ".launch.yml")) or name.endswith(
        (".xml", ".yaml", ".yml")
    )


def _collect_rviz_files(
    workspace: Workspace,
    *,
    deadline: float | None = None,
) -> tuple[str, ...]:
    files: list[str] = []
    excluded_top_level = {
        workspace.build.name,
        workspace.install.name,
        workspace.log.name,
    }
    for raw_root, directories, filenames in os.walk(
        workspace.root,
        topdown=True,
        followlinks=False,
    ):
        _check_deadline(deadline)
        root = Path(raw_root)
        if root == workspace.root:
            directories[:] = [
                name for name in directories if name not in excluded_top_level
            ]
        for filename in filenames:
            if not filename.endswith(".rviz"):
                continue
            path = root / filename
            files.append(path.relative_to(workspace.root).as_posix())
    return tuple(sorted(set(files)))


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _effective_deadline(deadline: float | None, timeout: float) -> float:
    own_deadline = time.monotonic() + timeout
    return own_deadline if deadline is None else min(deadline, own_deadline)


def _command_timeout(deadline: float | None, timeout: float) -> float:
    if deadline is None:
        return timeout
    _check_deadline(deadline)
    return min(timeout, _remaining(deadline))


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise CompletionError(
            "completion collection exceeded its deadline",
            exit_code=124,
        )


def _clean_names(names: Any) -> tuple[str, ...]:
    if isinstance(names, (str, bytes)):
        raise TypeError("completion names must be a sequence")
    clean: set[str] = set()
    for index, name in enumerate(names):
        if not isinstance(name, str):
            raise TypeError(
                f"completion names[{index}] must be str, got {type(name).__name__}"
            )
        if not name or any(_is_control(character) for character in name):
            raise ValueError(f"invalid completion name at index {index}: {name!r}")
        clean.add(name)
    return tuple(sorted(clean))


def _clean_name_map(raw: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(raw, Mapping):
        raise TypeError("completion mapping must be an object")
    clean: dict[str, tuple[str, ...]] = {}
    for package, names in sorted(raw.items()):
        if (
            not isinstance(package, str)
            or not package
            or any(_is_control(character) for character in package)
        ):
            raise ValueError(f"invalid completion package: {package!r}")
        clean[package] = _clean_names(names)
    return clean


def _clean_ambiguities(raw: Any) -> dict[str, dict[str, tuple[str, ...]]]:
    if not isinstance(raw, Mapping):
        raise TypeError("ambiguous launch mapping must be an object")
    clean: dict[str, dict[str, tuple[str, ...]]] = {}
    for package, basenames in sorted(raw.items()):
        if (
            not isinstance(package, str)
            or not package
            or any(_is_control(character) for character in package)
        ):
            raise ValueError(f"invalid launch package: {package!r}")
        if not isinstance(basenames, Mapping):
            raise TypeError(f"ambiguous launches for {package} must be an object")
        package_ambiguities: dict[str, tuple[str, ...]] = {}
        for basename, paths in basenames.items():
            if (
                not isinstance(basename, str)
                or not basename
                or any(_is_control(character) for character in basename)
            ):
                raise ValueError(
                    f"invalid ambiguous launch basename for {package}: {basename!r}"
                )
            package_ambiguities[basename] = _clean_names(paths)
        clean[package] = dict(sorted(package_ambiguities.items()))
    return clean


def _is_control(character: str) -> bool:
    codepoint = ord(character)
    return codepoint < 0x20 or 0x7F <= codepoint <= 0x9F


def _parse_cached_completion(raw: Any, path: Path) -> CachedCompletion:
    if not isinstance(raw, dict) or raw.get("schema_version") != CACHE_SCHEMA_VERSION:
        raise CompletionError(f"unsupported completion cache schema: {path}")
    try:
        raw_data = raw["data"]
        data = CompletionData(
            packages=raw_data["packages"],
            executables=raw_data["executables"],
            launches=raw_data["launches"],
            ambiguous_launches=raw_data["ambiguous_launches"],
            rviz_files=raw_data["rviz_files"],
        )
        updated_at = float(raw["updated_at"])
        stale = raw["stale"]
        dirty = raw["dirty"]
        error = raw.get("error")
        error_code = raw.get("error_code")
    except (KeyError, TypeError, ValueError) as parse_error:
        raise CompletionError(f"invalid completion cache {path}: {parse_error}") from parse_error
    if not isinstance(stale, bool) or not isinstance(dirty, bool):
        raise CompletionError(f"invalid completion cache flags: {path}")
    if error is not None and not isinstance(error, str):
        raise CompletionError(f"invalid completion cache error: {path}")
    if error_code is not None and (
        not isinstance(error_code, int) or not 0 <= error_code <= 255
    ):
        raise CompletionError(f"invalid completion cache error code: {path}")
    return CachedCompletion(data, updated_at, stale, dirty, error, error_code)
