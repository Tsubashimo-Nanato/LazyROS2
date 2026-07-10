# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build-baseline and runtime-overlay environment handling."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
from typing import Mapping

from .paths import Workspace


PREFIX_VARIABLES = (
    "AMENT_PREFIX_PATH",
    "CMAKE_PREFIX_PATH",
    "COLCON_PREFIX_PATH",
    "LD_LIBRARY_PATH",
    "PATH",
    "PYTHONPATH",
)


class OverlayEnvironmentError(RuntimeError):
    """Raised when a safe workspace overlay cannot be constructed."""


@dataclass(frozen=True, slots=True)
class OverlayHit:
    variable: str
    path: Path


def find_self_overlay(
    workspace: Workspace,
    env: Mapping[str, str],
) -> tuple[OverlayHit, ...]:
    install = workspace.install.resolve(strict=False)
    hits: list[OverlayHit] = []
    seen: set[tuple[str, Path]] = set()
    for variable in PREFIX_VARIABLES:
        raw_value = env.get(variable, "")
        for raw_path in raw_value.split(os.pathsep):
            if not raw_path:
                continue
            candidate = Path(raw_path).expanduser()
            if not candidate.is_absolute():
                continue
            resolved = candidate.resolve(strict=False)
            if not _inside(resolved, install):
                continue
            key = (variable, resolved)
            if key in seen:
                continue
            seen.add(key)
            hits.append(OverlayHit(variable, resolved))
    return tuple(hits)


def capture_overlay_environment(
    workspace: Workspace,
    baseline: Mapping[str, str],
    *,
    bash: str = "bash",
    timeout: float = 10.0,
) -> dict[str, str]:
    hits = find_self_overlay(workspace, baseline)
    if hits:
        variables = ", ".join(sorted({hit.variable for hit in hits}))
        raise OverlayEnvironmentError(
            f"baseline already contains this workspace overlay in: {variables}"
        )

    setup = validated_setup_script(workspace, "sh")
    if setup is None:
        raise OverlayEnvironmentError(
            f"workspace overlay does not exist: {workspace.install / 'local_setup.sh'}"
        )
    if timeout <= 0:
        raise OverlayEnvironmentError(
            f"workspace setup timed out before it could start: {setup}"
        )
    command = (
        bash,
        "--noprofile",
        "--norc",
        "-c",
        'set -a\n. "$1" >/dev/null\ncommand -p env -0',
        "lazyros2-overlay",
        str(setup),
    )
    with tempfile.TemporaryFile() as stdout_stream, tempfile.TemporaryFile() as stderr_stream:
        try:
            process = subprocess.Popen(
                command,
                cwd=str(workspace.root),
                env=dict(baseline),
                stdout=stdout_stream,
                stderr=stderr_stream,
                shell=False,
                start_new_session=os.name == "posix",
            )
        except FileNotFoundError as error:
            raise OverlayEnvironmentError(f"bash executable not found: {bash}") from error
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            _stop_overlay_process(process)
            raise OverlayEnvironmentError(
                f"workspace setup timed out after {timeout:g}s: {setup}"
            ) from error

        stdout_stream.seek(0)
        stderr_stream.seek(0)
        stdout = stdout_stream.read()
        stderr = stderr_stream.read()

    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        suffix = f": {detail}" if detail else ""
        raise OverlayEnvironmentError(
            f"failed to source {setup} (exit {process.returncode}){suffix}"
        )
    return _parse_nul_environment(stdout)


def _stop_overlay_process(process: subprocess.Popen[bytes]) -> None:
    """Kill the setup group and reap its direct child without waiting on descendants."""
    group_signalled = False
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
            group_signalled = True
        except (ProcessLookupError, PermissionError):
            pass
    if not group_signalled:
        try:
            process.kill()
        except ProcessLookupError:
            pass

    try:
        process.wait(timeout=0.05)
        return
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=0.05)
    except subprocess.TimeoutExpired:
        threading.Thread(
            target=_reap_overlay_process,
            args=(process,),
            name="lazyros2-overlay-reaper",
            daemon=True,
        ).start()


def _reap_overlay_process(process: subprocess.Popen[bytes]) -> None:
    try:
        process.wait()
    except OSError:
        pass


def validated_setup_script(
    workspace: Workspace,
    shell: str,
) -> Path | None:
    """Resolve a shell-specific setup file and prove it stays in install/."""
    suffixes = {
        "bash": ("bash", "sh"),
        "zsh": ("zsh", "sh"),
        "sh": ("sh",),
    }
    try:
        candidates = suffixes[shell]
    except KeyError as error:
        raise ValueError(f"unsupported setup shell: {shell}") from error

    candidate = next(
        (
            workspace.install / f"local_setup.{suffix}"
            for suffix in candidates
            if (workspace.install / f"local_setup.{suffix}").is_file()
        ),
        None,
    )
    if candidate is None:
        return None
    resolved = candidate.resolve(strict=True)
    install = workspace.install.resolve(strict=False)
    root = workspace.root.resolve(strict=False)
    if not _inside(install, root):
        raise OverlayEnvironmentError(
            f"workspace install directory escapes workspace root: {workspace.install}"
        )
    if not _inside(resolved, install):
        raise OverlayEnvironmentError(f"workspace setup escapes install directory: {candidate}")
    return resolved


def _inside(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def _parse_nul_environment(payload: bytes) -> dict[str, str]:
    environment: dict[str, str] = {}
    for raw_entry in payload.split(b"\0"):
        if not raw_entry:
            continue
        entry = raw_entry.decode("utf-8", errors="surrogateescape")
        if "=" not in entry:
            raise OverlayEnvironmentError("overlay emitted an invalid environment entry")
        key, value = entry.split("=", 1)
        environment[key] = value
    return environment
