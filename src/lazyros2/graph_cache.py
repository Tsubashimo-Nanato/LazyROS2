# SPDX-License-Identifier: AGPL-3.0-or-later
"""Short-lived ROS graph snapshots shared by completion and live lists."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Callable, Mapping, Sequence

from .completion import CompletionError
from .storage import atomic_write_json, exclusive_lock, read_json


GRAPH_TTL_SECONDS = 3.0
GRAPH_IDENTITY_VARIABLES = (
    "PATH", "AMENT_PREFIX_PATH", "CMAKE_PREFIX_PATH", "PYTHONPATH", "ROS_DISTRO",
    "ROS_DOMAIN_ID", "RMW_IMPLEMENTATION", "ROS_LOCALHOST_ONLY",
    "ROS_AUTOMATIC_DISCOVERY_RANGE", "ROS_STATIC_PEERS", "ROS_DISCOVERY_SERVER",
    "CYCLONEDDS_URI", "FASTRTPS_DEFAULT_PROFILES_FILE", "FASTDDS_DEFAULT_PROFILES_FILE",
)


@dataclass(frozen=True)
class GraphSnapshot:
    # None means collection has never succeeded; () is a successful empty graph.
    values: tuple[str, ...] | None
    updated_at: float
    attempted_at: float
    error: str | None = None
    error_code: int | None = None

    def fresh(self, now: float, ttl: float) -> bool:
        # Backwards clock changes must not make an old snapshot fresh indefinitely.
        return 0 <= now - self.attempted_at < ttl

    def require_values(self) -> tuple[str, ...]:
        if self.values is None:
            raise CompletionError(self.error or "graph refresh is in progress", exit_code=self.error_code)
        return self.values


class GraphCache:
    def __init__(self, directory: Path, *, ttl: float = GRAPH_TTL_SECONDS) -> None:
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("graph cache TTL must be positive and finite")
        self.directory = Path(directory)
        self.ttl = ttl

    @staticmethod
    def key(domain: str, workspace: Path, env: Mapping[str, str], ros2_path: str | None) -> str:
        setup = workspace / "install" / "local_setup.sh"
        try:
            stamp = setup.stat().st_mtime_ns
        except FileNotFoundError:
            stamp = None
        identity = {
            "domain": domain,
            "workspace": str(workspace.resolve()),
            "ros2": ros2_path,
            "setup_mtime_ns": stamp,
            "environment": {name: env.get(name, "") for name in GRAPH_IDENTITY_VARIABLES},
        }
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    def read(self, key: str) -> GraphSnapshot | None:
        path = self._path(key)
        try:
            raw = read_json(path)
        except FileNotFoundError:
            return None
        except json.JSONDecodeError as error:
            raise CompletionError(f"invalid graph cache {path}: {error}") from error
        try:
            if not isinstance(raw, dict) or raw.get("schema_version") != 1:
                raise ValueError("unsupported schema")
            values = None if raw["values"] is None else _values(raw["values"])
            updated = float(raw["updated_at"])
            attempted = float(raw["attempted_at"])
            error, code = raw["error"], raw["error_code"]
            if not all(math.isfinite(value) for value in (updated, attempted)):
                raise ValueError("non-finite timestamp")
            if error is not None and not isinstance(error, str):
                raise ValueError("invalid error")
            if code is not None and (type(code) is not int or not 0 <= code <= 255):
                raise ValueError("invalid exit code")
            return GraphSnapshot(values, updated, attempted, error, code)
        except (KeyError, TypeError, ValueError) as error:
            raise CompletionError(f"invalid graph cache {path}: {error}") from error

    def get(self, key: str, collect: Callable[[], Sequence[str]]) -> GraphSnapshot:
        previous = self._read_for_refresh(key)
        if previous is not None and previous.fresh(time.time(), self.ttl):
            return previous
        try:
            # Never make a second Tab wait behind another terminal's ROS discovery.
            with exclusive_lock(self._path(key).with_suffix(".lock"), timeout=0):
                previous = self._read_for_refresh(key)
                if previous is not None and previous.fresh(time.time(), self.ttl):
                    return previous
                try:
                    values = _values(collect())
                    now = time.time()
                    snapshot = GraphSnapshot(values, now, now)
                except (CompletionError, OSError, TimeoutError) as error:
                    snapshot = GraphSnapshot(
                        None if previous is None else previous.values,
                        0.0 if previous is None else previous.updated_at,
                        time.time(),
                        str(error),
                        getattr(error, "exit_code", None),
                    )
                atomic_write_json(self._path(key), {
                    "schema_version": 1,
                    "values": snapshot.values,
                    "updated_at": snapshot.updated_at,
                    "attempted_at": snapshot.attempted_at,
                    "error": snapshot.error,
                    "error_code": snapshot.error_code,
                })
                return snapshot
        except TimeoutError:
            return previous or GraphSnapshot(None, 0.0, 0.0, "graph refresh is in progress")

    def _read_for_refresh(self, key: str) -> GraphSnapshot | None:
        try:
            return self.read(key)
        except CompletionError:
            # Derived data can be rebuilt. Replacement still happens under the
            # refresh lock, so a corrupt file never needs an unsafe unlink race.
            return None

    def _path(self, key: str) -> Path:
        if len(key) != 64 or any(character not in "0123456789abcdef" for character in key):
            raise ValueError("graph cache key must be a lowercase SHA-256 digest")
        return self.directory / f"{key}.json"


def _values(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise CompletionError("graph values must be a sequence of lines")
    for value in values:
        if not isinstance(value, str) or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
            raise CompletionError("graph output contains invalid text")
    return tuple(values)
