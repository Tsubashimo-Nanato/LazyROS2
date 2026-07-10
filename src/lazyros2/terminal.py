# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixed argv adapters for supported graphical terminals."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
import shutil
from typing import Callable, Mapping, Sequence


class TerminalKind(str, Enum):
    XDG = "xdg-terminal-exec"
    GNOME = "gnome-terminal"
    KONSOLE = "konsole"
    KITTY = "kitty"
    GHOSTTY = "ghostty"
    ALACRITTY = "alacritty"


class TerminalSupport(str, Enum):
    SUPPORTED = "supported"
    EXPERIMENTAL = "experimental"


@dataclass(frozen=True, slots=True)
class TerminalAdapter:
    kind: TerminalKind
    executable: str

    @property
    def name(self) -> str:
        return self.kind.value

    @property
    def support(self) -> TerminalSupport:
        if self.kind is TerminalKind.GNOME:
            return TerminalSupport.SUPPORTED
        return TerminalSupport.EXPERIMENTAL

    def command(self, title: str, program: Sequence[str]) -> tuple[str, ...]:
        if not isinstance(title, str) or not title or "\0" in title:
            raise ValueError("terminal title must be a non-empty string without NUL")
        child = _validated_program(program)
        if self.kind is TerminalKind.GNOME:
            return (self.executable, f"--title={title}", "--", *child)
        if self.kind is TerminalKind.XDG:
            return (self.executable, f"--title={title}", "--", *child)
        if self.kind is TerminalKind.KONSOLE:
            return (
                self.executable,
                "--separate",
                "-p",
                f"tabtitle={title}",
                "-e",
                *child,
            )
        if self.kind is TerminalKind.KITTY:
            return (self.executable, "--title", title, *child)
        if self.kind is TerminalKind.GHOSTTY:
            return (self.executable, f"--title={title}", "-e", *child)
        return (self.executable, "--title", title, "-e", *child)


def adapter_for(kind: TerminalKind | str, executable: str | None = None) -> TerminalAdapter:
    try:
        terminal_kind = kind if isinstance(kind, TerminalKind) else TerminalKind(kind)
    except ValueError as error:
        raise ValueError(f"unknown terminal adapter: {kind}") from error
    path = executable or shutil.which(terminal_kind.value)
    if not path:
        raise FileNotFoundError(f"terminal executable not found: {terminal_kind.value}")
    return TerminalAdapter(terminal_kind, path)


def detect_terminal(
    env: Mapping[str, str] | None = None,
    *,
    preferred: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> TerminalAdapter | None:
    source = os.environ if env is None else env
    if not source.get("DISPLAY") and not source.get("WAYLAND_DISPLAY"):
        return None

    if preferred is not None:
        try:
            preferred_kind = TerminalKind(preferred)
        except ValueError as error:
            raise ValueError(f"unknown terminal adapter: {preferred}") from error
        path = which(preferred_kind.value)
        return TerminalAdapter(preferred_kind, path) if path else None

    order = (
        TerminalKind.GNOME,
        TerminalKind.XDG,
        TerminalKind.KONSOLE,
        TerminalKind.KITTY,
        TerminalKind.GHOSTTY,
        TerminalKind.ALACRITTY,
    )
    for kind in order:
        path = which(kind.value)
        if path:
            return TerminalAdapter(kind, path)
    return None


def _validated_program(program: Sequence[str]) -> tuple[str, ...]:
    if isinstance(program, (str, bytes)):
        raise TypeError("terminal program must be an argv sequence")
    child = tuple(program)
    if not child:
        raise ValueError("terminal program must contain a command")
    for index, argument in enumerate(child):
        if not isinstance(argument, str):
            raise TypeError(
                f"terminal program[{index}] must be str, got {type(argument).__name__}"
            )
        if "\0" in argument:
            raise ValueError(f"terminal program[{index}] contains NUL")
    return child
