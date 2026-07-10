# SPDX-License-Identifier: AGPL-3.0-or-later
"""Small private-file primitives for JSON state."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Iterator


def ensure_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise OSError(f"private state directory must not be a symlink: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise OSError(f"private state path is not a directory: {path}")
    if os.name != "nt" and path.stat().st_uid != os.getuid():
        raise PermissionError(f"private state directory is owned by another user: {path}")
    try:
        path.chmod(0o700)
    except OSError:
        if os.name != "nt":
            raise


@contextmanager
def exclusive_lock(
    lock_path: Path,
    *,
    timeout: float = 5.0,
    stale_after: float = 30.0,
) -> Iterator[None]:
    ensure_private_directory(lock_path.parent)
    deadline = time.monotonic() + timeout
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(
                lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            _remove_stale_lock(lock_path, stale_after)
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for state lock: {lock_path}")
            time.sleep(0.02)

    try:
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def atomic_write_json(path: Path, payload: Any) -> None:
    ensure_private_directory(path.parent)
    descriptor, raw_temp_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp_path = Path(raw_temp_path)
    try:
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            if os.name != "nt":
                raise
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
        try:
            path.chmod(0o600)
        except OSError:
            if os.name != "nt":
                raise
        _sync_directory(path.parent)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _remove_stale_lock(path: Path, stale_after: float) -> None:
    try:
        age = time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return
    if age <= stale_after:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _sync_directory(path: Path) -> None:
    if os.name == "nt" or not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
