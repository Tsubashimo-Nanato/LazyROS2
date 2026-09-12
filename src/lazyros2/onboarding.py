# SPDX-License-Identifier: AGPL-3.0-or-later
"""Find a workspace or guide an interactive first start."""

from __future__ import annotations

import os
from pathlib import Path
import sys

from .paths import PackageProbe, Workspace, WorkspaceError


def _find_workspace(path: Path, package_probe: PackageProbe | None) -> Workspace:
    if not path.is_dir():
        raise WorkspaceError(f"workspace directory does not exist: {path}")
    # Ancestor package probes can recursively scan an entire home or filesystem.
    # C++ packages also have src/, so package.xml excludes their local source
    # directory from the ancestor workspace markers.
    for candidate in (path, *path.parents):
        if (candidate / "src").is_dir() and not (candidate / "package.xml").exists():
            return Workspace.open(candidate, package_probe=package_probe)
    return Workspace.open(path, package_probe=package_probe)


def _read(prompt: str) -> str:
    print(prompt, end="", file=sys.stderr, flush=True)
    return input()


def _directory_candidates(text: str, base: Path) -> list[str]:
    parent, prefix = os.path.split(text)
    directory = Path(parent).expanduser() if parent else Path()
    if not directory.is_absolute():
        directory = base / directory
    try:
        return sorted(
            os.path.join(parent, child.name) + os.sep
            for child in directory.iterdir()
            if child.name.startswith(prefix) and child.is_dir()
        )
    except OSError:
        return []


def _read_directory(base: Path) -> str:
    try:
        import readline
    except ImportError:
        return _read("Directory (empty to cancel): ")

    previous_completer = readline.get_completer()
    previous_delimiters = readline.get_completer_delims()
    matches: list[str] = []

    def complete(text: str, state: int) -> str | None:
        nonlocal matches
        if state == 0:
            matches = _directory_candidates(text, base)
        return matches[state] if state < len(matches) else None

    try:
        readline.set_completer(complete)
        # A directory is one input value; spaces belong to its name.
        readline.set_completer_delims("\n")
        binding = "bind ^I rl_complete" if "libedit" in (readline.__doc__ or "") else "tab: complete"
        readline.parse_and_bind(binding)
        return _read("Directory (Tab to complete, empty to cancel): ")
    finally:
        readline.set_completer(previous_completer)
        readline.set_completer_delims(previous_delimiters)


def _create_workspace(root: Path, package_probe: PackageProbe | None) -> Workspace | None:
    if not root.is_dir():
        raise WorkspaceError(f"choose an existing directory first: {root}")
    src = root / "src"
    if src.exists() or src.is_symlink():
        raise WorkspaceError(f"cannot create {src}: a file or symlink already exists")
    if _read(f"Create {src} and use {root} as a workspace? [y/N] ").strip().lower() not in {"y", "yes"}:
        return None
    try:
        # No exist_ok: a path created during confirmation must not be reused.
        src.mkdir()
    except OSError as error:
        raise WorkspaceError(f"cannot create workspace source directory {src}: {error}") from error
    return Workspace.open(root, package_probe=package_probe)


def select_workspace(
    path: Path | str,
    package_probe: PackageProbe | None = None,
) -> Workspace | None:
    """Return a fixed workspace, or None when interactive setup is cancelled."""
    root = Path(path).expanduser().resolve(strict=False)
    try:
        while True:
            try:
                return _find_workspace(root, package_probe)
            except WorkspaceError as error:
                if not sys.stdin.isatty():
                    raise WorkspaceError(
                        f"{error}. Start lazy in a workspace with src/, or run lazy "
                        "in an interactive terminal to choose or create one."
                    ) from error
                print(f"lazy: {error}", file=sys.stderr)

            while True:
                choice = _read("[c] Create src here, [d] choose a directory, [q] quit: ").strip().lower()
                if choice in {"", "q", "quit"}:
                    return None
                if choice in {"c", "create"}:
                    try:
                        return _create_workspace(root, package_probe)
                    except WorkspaceError as error:
                        print(f"lazy: {error}", file=sys.stderr)
                    continue
                if choice in {"d", "directory"}:
                    selected = _read_directory(root)
                    if not selected:
                        return None
                    root = (root / Path(selected).expanduser()).resolve(strict=False)
                    break
                print("Choose c, d, or q.", file=sys.stderr)
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        return None
