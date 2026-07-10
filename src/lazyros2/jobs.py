# SPDX-License-Identifier: AGPL-3.0-or-later
"""Private runtime registry for LazyROS2 task windows."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Sequence

from .storage import atomic_write_json, ensure_private_directory, exclusive_lock, read_json


JOB_SCHEMA_VERSION = 1
STARTING_GRACE_SECONDS = 30.0


class JobRegistryError(RuntimeError):
    """Raised when runtime task state is missing or invalid."""


class JobState(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    IDLE = "idle"
    EXITED = "exited"


@dataclass(frozen=True, slots=True)
class JobRecord:
    id: str
    number: int
    workspace: Path
    kind: str
    target: str
    command: tuple[str, ...]
    color_slot: int
    state: JobState
    pid: int
    exit_code: int | None
    created_at: float
    updated_at: float

    @property
    def title(self) -> str:
        return f"{self.workspace.name} · #{self.number:02d} · {self.kind} · {self.target}"

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "number": self.number,
            "workspace": str(self.workspace),
            "kind": self.kind,
            "target": self.target,
            "command": list(self.command),
            "color_slot": self.color_slot,
            "state": self.state.value,
            "pid": self.pid,
            "exit_code": self.exit_code,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class JobRegistry:
    def __init__(self, runtime_dir: Path) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.path = self.runtime_dir / "jobs.json"
        self.lock_path = self.runtime_dir / "jobs.lock"
        ensure_private_directory(self.runtime_dir)

    def register(
        self,
        workspace: Path | str,
        kind: str,
        target: str,
        command: Sequence[str],
        *,
        color_slot: int | None = None,
        pid: int = 0,
    ) -> JobRecord:
        workspace_path = _workspace_path(workspace)
        clean_kind = _plain_text(kind, "kind")
        clean_target = _plain_text(target, "target")
        child = _command_argv(command)
        if pid < 0:
            raise ValueError("pid must be zero or positive")
        if color_slot is not None and not 0 <= color_slot < 12:
            raise ValueError("color_slot must be between 0 and 11")

        with exclusive_lock(self.lock_path):
            raw_state = self._load_state()
            workspace_key = str(workspace_path)
            number = int(raw_state["next_numbers"].get(workspace_key, 1))
            raw_state["next_numbers"][workspace_key] = number + 1
            now = time.time()
            record = JobRecord(
                id=_job_id(workspace_path, number),
                number=number,
                workspace=workspace_path,
                kind=clean_kind,
                target=clean_target,
                command=child,
                color_slot=(number - 1) % 12 if color_slot is None else color_slot,
                state=JobState.STARTING,
                pid=pid,
                exit_code=None,
                created_at=now,
                updated_at=now,
            )
            raw_state["jobs"].append(record.to_json())
            atomic_write_json(self.path, raw_state)
            return record

    def update(
        self,
        workspace: Path | str,
        number: int,
        state: JobState,
        *,
        exit_code: int | None = None,
        pid: int | None = None,
    ) -> JobRecord:
        workspace_path = _workspace_path(workspace)
        if not isinstance(state, JobState):
            raise TypeError(f"state must be JobState, got {type(state).__name__}")
        if pid is not None and pid <= 0:
            raise ValueError("claimed task-shell pid must be positive")
        if exit_code is not None and not 0 <= exit_code <= 255:
            raise ValueError("exit_code must be between 0 and 255")

        with exclusive_lock(self.lock_path):
            raw_state = self._load_state()
            records = [_parse_record(raw) for raw in raw_state["jobs"]]
            for index, record in enumerate(records):
                if record.workspace != workspace_path or record.number != number:
                    continue
                updated = replace(
                    record,
                    state=state,
                    pid=record.pid if pid is None else pid,
                    exit_code=exit_code,
                    updated_at=time.time(),
                )
                raw_state["jobs"][index] = updated.to_json()
                atomic_write_json(self.path, raw_state)
                return updated
        raise JobRegistryError(f"task not found: {workspace_path} #{number}")

    def get(self, workspace: Path | str, number: int) -> JobRecord:
        workspace_path = _workspace_path(workspace)
        for record in self.list(prune=False):
            if record.workspace == workspace_path and record.number == number:
                return record
        raise JobRegistryError(f"task not found: {workspace_path} #{number}")

    def get_by_id(self, job_id: str) -> JobRecord:
        _plain_text(job_id, "job_id")
        for record in self.list(prune=False):
            if record.id == job_id:
                return record
        raise JobRegistryError(f"task not found: {job_id}")

    def list(
        self,
        workspace: Path | str | None = None,
        *,
        prune: bool = True,
    ) -> tuple[JobRecord, ...]:
        workspace_path = None if workspace is None else _workspace_path(workspace)
        with exclusive_lock(self.lock_path):
            raw_state = self._load_state()
            records = [_parse_record(raw) for raw in raw_state["jobs"]]
            if prune:
                kept = [record for record in records if not _is_stale(record)]
                if len(kept) != len(records):
                    raw_state["jobs"] = [record.to_json() for record in kept]
                    atomic_write_json(self.path, raw_state)
                records = kept
        if workspace_path is not None:
            records = [record for record in records if record.workspace == workspace_path]
        return tuple(sorted(records, key=lambda record: (str(record.workspace), record.number)))

    def remove(self, workspace: Path | str, number: int) -> None:
        workspace_path = _workspace_path(workspace)
        with exclusive_lock(self.lock_path):
            raw_state = self._load_state()
            before = len(raw_state["jobs"])
            raw_state["jobs"] = [
                raw
                for raw in raw_state["jobs"]
                if not (
                    Path(raw.get("workspace", "")) == workspace_path
                    and raw.get("number") == number
                )
            ]
            if len(raw_state["jobs"]) == before:
                raise JobRegistryError(f"task not found: {workspace_path} #{number}")
            atomic_write_json(self.path, raw_state)

    def has_active_jobs(self) -> bool:
        return bool(self.list())

    def _load_state(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "schema_version": JOB_SCHEMA_VERSION,
                "next_numbers": {},
                "jobs": [],
            }
        try:
            raw = read_json(self.path)
        except (OSError, json.JSONDecodeError) as error:
            raise JobRegistryError(f"cannot read task registry {self.path}: {error}") from error
        if not isinstance(raw, dict) or raw.get("schema_version") != JOB_SCHEMA_VERSION:
            raise JobRegistryError(f"unsupported task registry schema: {self.path}")
        if not isinstance(raw.get("next_numbers"), dict) or not isinstance(raw.get("jobs"), list):
            raise JobRegistryError(f"invalid task registry structure: {self.path}")
        return raw


def _job_id(workspace: Path, number: int) -> str:
    workspace_hash = hashlib.sha256(str(workspace).encode("utf-8")).hexdigest()[:12]
    return f"{workspace_hash}-{number:06d}"


def _workspace_path(workspace: Path | str) -> Path:
    path = Path(workspace).expanduser().resolve(strict=False)
    if not path.is_absolute():
        raise ValueError(f"workspace must resolve to an absolute path: {workspace}")
    return path


def _plain_text(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be str, got {type(value).__name__}")
    if not value or any(_is_control(character) for character in value):
        raise ValueError(f"{name} must be non-empty text without control characters")
    return value


def _is_control(character: str) -> bool:
    codepoint = ord(character)
    return codepoint < 0x20 or 0x7F <= codepoint <= 0x9F


def _command_argv(command: Sequence[str]) -> tuple[str, ...]:
    if isinstance(command, (str, bytes)):
        raise TypeError("command must be an argv sequence")
    argv = tuple(command)
    if not argv:
        raise ValueError("command must contain an executable")
    for index, argument in enumerate(argv):
        if not isinstance(argument, str):
            raise TypeError(
                f"command[{index}] must be str, got {type(argument).__name__}"
            )
        if "\0" in argument:
            raise ValueError(f"command[{index}] contains NUL")
    return argv


def _parse_record(raw: Any) -> JobRecord:
    if not isinstance(raw, dict):
        raise JobRegistryError("task registry record must be an object")
    try:
        workspace = Path(raw["workspace"])
        record = JobRecord(
            id=raw["id"],
            number=int(raw["number"]),
            workspace=workspace,
            kind=raw["kind"],
            target=raw["target"],
            command=_command_argv(raw["command"]),
            color_slot=int(raw["color_slot"]),
            state=JobState(raw["state"]),
            pid=int(raw["pid"]),
            exit_code=None if raw.get("exit_code") is None else int(raw["exit_code"]),
            created_at=float(raw["created_at"]),
            updated_at=float(raw["updated_at"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise JobRegistryError(f"invalid task registry record: {error}") from error
    if not record.workspace.is_absolute():
        raise JobRegistryError(
            f"task registry workspace must be absolute: {record.workspace}"
        )
    if record.number <= 0:
        raise JobRegistryError(f"task registry number must be positive: {record.number}")
    if record.pid < 0:
        raise JobRegistryError(f"task registry pid cannot be negative: {record.pid}")
    if not 0 <= record.color_slot < 12:
        raise JobRegistryError(
            f"task registry color slot is out of range: {record.color_slot}"
        )
    if record.exit_code is not None and not 0 <= record.exit_code <= 255:
        raise JobRegistryError(
            f"task registry exit code is out of range: {record.exit_code}"
        )
    if record.id != _job_id(record.workspace, record.number):
        raise JobRegistryError(f"task registry id does not match workspace: {record.id}")
    return record


def _is_stale(record: JobRecord) -> bool:
    if record.pid > 0:
        return not _pid_exists(record.pid)
    if record.state is not JobState.STARTING:
        return True
    return time.time() - record.created_at > STARTING_GRACE_SECONDS


def _pid_exists(pid: int) -> bool:
    if os.name == "nt":
        return _windows_pid_exists(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_pid_exists(pid: int) -> bool:
    import ctypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)
