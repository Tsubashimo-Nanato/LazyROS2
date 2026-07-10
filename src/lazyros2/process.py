# SPDX-License-Identifier: AGPL-3.0-or-later
"""Literal subprocess execution shared by LazyROS2 commands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def exit_code(self) -> int:
        return normalize_returncode(self.returncode)


def normalize_returncode(returncode: int) -> int:
    """Map subprocess signals to the shell convention and clamp to one byte."""
    if returncode < 0:
        return min(255, 128 + abs(returncode))
    return min(255, returncode)


def run_command(
    argv: Sequence[str],
    *,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    capture_output: bool = False,
) -> CommandResult:
    command = _validated_argv(argv)
    command_env = _validated_env(env)
    try:
        completed = subprocess.run(
            command,
            cwd=None if cwd is None else str(cwd),
            env=command_env,
            timeout=timeout,
            capture_output=capture_output,
            text=True,
            check=False,
            shell=False,
        )
    except FileNotFoundError:
        return CommandResult(command, 127, "", f"command not found: {command[0]}")
    except PermissionError:
        return CommandResult(command, 126, "", f"command is not executable: {command[0]}")
    except subprocess.TimeoutExpired as error:
        stdout = _timeout_text(error.stdout)
        stderr = _timeout_text(error.stderr)
        message = f"command timed out after {timeout:g}s" if timeout is not None else "command timed out"
        stderr = f"{stderr.rstrip()}\n{message}".lstrip()
        return CommandResult(command, 124, stdout, stderr)

    return CommandResult(
        argv=command,
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def _validated_argv(argv: Sequence[str]) -> tuple[str, ...]:
    if isinstance(argv, (str, bytes)):
        raise TypeError("argv must be a sequence of strings, not a shell command")
    command = tuple(argv)
    if not command:
        raise ValueError("argv must contain a command")
    for index, argument in enumerate(command):
        if not isinstance(argument, str):
            raise TypeError(
                f"argv[{index}] must be str, got {type(argument).__name__}"
            )
        if "\0" in argument:
            raise ValueError(f"argv[{index}] contains NUL")
    return command


def _validated_env(env: Mapping[str, str] | None) -> dict[str, str] | None:
    if env is None:
        return None
    clean: dict[str, str] = {}
    for key, value in env.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise TypeError("environment keys and values must be strings")
        if not key or "=" in key or "\0" in key or "\0" in value:
            raise ValueError(f"invalid environment entry: {key!r}")
        clean[key] = value
    return clean


def _timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
