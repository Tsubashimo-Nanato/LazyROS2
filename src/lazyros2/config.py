# SPDX-License-Identifier: AGPL-3.0-or-later
"""Versioned per-workspace preferences and task-window colors."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from .storage import atomic_write_json, exclusive_lock, read_json


SCHEMA_VERSION = 1
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
KNOWN_TERMINALS = {
    "xdg-terminal-exec",
    "gnome-terminal",
    "konsole",
    "kitty",
    "ghostty",
    "alacritty",
}


class ConfigError(RuntimeError):
    """Raised when persistent configuration is invalid or unreadable."""


def contrast_ratio(first: str, second: str) -> float:
    first_luminance = _relative_luminance(_parse_hex(first))
    second_luminance = _relative_luminance(_parse_hex(second))
    lighter = max(first_luminance, second_luminance)
    darker = min(first_luminance, second_luminance)
    return (lighter + 0.05) / (darker + 0.05)


def _parse_hex(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or HEX_COLOR.fullmatch(value) is None:
        raise ValueError(f"color must use #RRGGBB: {value!r}")
    red = int(value[1:3], 16)
    green = int(value[3:5], 16)
    blue = int(value[5:7], 16)
    return red, green, blue


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    channels = []
    for value in rgb:
        normalized = value / 255.0
        channels.append(
            normalized / 12.92
            if normalized <= 0.04045
            else math.pow((normalized + 0.055) / 1.055, 2.4)
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


@dataclass(frozen=True, slots=True)
class ColorScheme:
    background: str
    foreground: str
    accent: str

    def __post_init__(self) -> None:
        for field_name in ("background", "foreground", "accent"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or HEX_COLOR.fullmatch(value) is None:
                raise ValueError(f"{field_name} must use #RRGGBB")
            object.__setattr__(self, field_name, value.upper())

        foreground_ratio = contrast_ratio(self.foreground, self.background)
        if foreground_ratio < 4.5:
            raise ValueError(
                "foreground/background contrast must be at least 4.5:1 "
                f"(got {foreground_ratio:.2f}:1)"
            )
        accent_ratio = contrast_ratio(self.accent, self.background)
        if accent_ratio < 3.0:
            raise ValueError(
                "accent/background contrast must be at least 3.0:1 "
                f"(got {accent_ratio:.2f}:1)"
            )

    def to_json(self) -> dict[str, str]:
        return {
            "background": self.background,
            "foreground": self.foreground,
            "accent": self.accent,
        }


PRESET_COLORS: dict[str, ColorScheme] = {
    "navy": ColorScheme("#14213D", "#E6EDF3", "#7AA2F7"),
    "forest": ColorScheme("#16251F", "#E8F0EB", "#7FBF9A"),
    "plum": ColorScheme("#251B2E", "#F0E8F5", "#B99CD8"),
    "slate": ColorScheme("#20242C", "#ECEFF4", "#88C0D0"),
    "earth": ColorScheme("#2A2118", "#F2ECE4", "#D8A657"),
    "burgundy": ColorScheme("#2B1A1E", "#F5E9EC", "#D98B9A"),
    "teal": ColorScheme("#10272A", "#E4F2F2", "#6FB7B7"),
    "indigo": ColorScheme("#1E1C35", "#ECEAF7", "#9A92D0"),
    "olive": ColorScheme("#262719", "#F0F1E6", "#B3B76D"),
    "steel": ColorScheme("#18242B", "#E6F0F4", "#76A9C0"),
    "cocoa": ColorScheme("#2A1F1B", "#F5ECE8", "#C89B84"),
    "aubergine": ColorScheme("#2A1826", "#F4E8F1", "#C887B7"),
}


@dataclass(slots=True)
class WorkspaceConfig:
    colors: dict[str, ColorScheme] = field(default_factory=dict)
    terminal: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "colors": {
                name: colors.to_json() for name, colors in sorted(self.colors.items())
            },
            "terminal": self.terminal,
        }


@dataclass(slots=True)
class LazyConfig:
    schema_version: int = SCHEMA_VERSION
    workspaces: dict[str, WorkspaceConfig] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "workspaces": {
                path: config.to_json()
                for path, config in sorted(self.workspaces.items())
            },
        }


class ConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(f"{self.path.name}.lock")

    def load(self) -> LazyConfig:
        if not self.path.exists():
            return LazyConfig()
        try:
            raw = read_json(self.path)
        except (OSError, json.JSONDecodeError) as error:
            raise ConfigError(f"cannot read configuration {self.path}: {error}") from error
        return _parse_config(raw)

    def save(self, config: LazyConfig) -> None:
        if not isinstance(config, LazyConfig):
            raise TypeError(f"config must be LazyConfig, got {type(config).__name__}")
        if config.schema_version != SCHEMA_VERSION:
            raise ConfigError(
                f"unsupported schema_version {config.schema_version}; expected {SCHEMA_VERSION}"
            )
        # Validate caller-built dataclasses before they cross the persistent boundary.
        validated = _parse_config(config.to_json())
        with exclusive_lock(self.lock_path):
            atomic_write_json(self.path, validated.to_json())

    def get_workspace(self, workspace: Path | str) -> WorkspaceConfig:
        key = _workspace_key(workspace)
        config = self.load().workspaces.get(key)
        if config is None:
            return WorkspaceConfig()
        return WorkspaceConfig(colors=dict(config.colors), terminal=config.terminal)

    def set_color(
        self,
        workspace: Path | str,
        name: str,
        colors: ColorScheme,
    ) -> None:
        if name not in PRESET_COLORS:
            raise ValueError(f"unknown color slot: {name}")
        if not isinstance(colors, ColorScheme):
            raise TypeError(f"colors must be ColorScheme, got {type(colors).__name__}")

        def change(config: LazyConfig, key: str) -> None:
            entry = config.workspaces.setdefault(key, WorkspaceConfig())
            entry.colors[name] = colors

        self._update(workspace, change)

    def reset_colors(self, workspace: Path | str) -> None:
        def change(config: LazyConfig, key: str) -> None:
            entry = config.workspaces.get(key)
            if entry is not None:
                entry.colors.clear()

        self._update(workspace, change)

    def set_terminal(self, workspace: Path | str, name: str | None) -> None:
        if name is not None and name not in KNOWN_TERMINALS:
            raise ValueError(f"unknown terminal adapter: {name}")

        def change(config: LazyConfig, key: str) -> None:
            entry = config.workspaces.setdefault(key, WorkspaceConfig())
            entry.terminal = name

        self._update(workspace, change)

    def effective_colors(self, workspace: Path | str) -> dict[str, ColorScheme]:
        colors = dict(PRESET_COLORS)
        colors.update(self.get_workspace(workspace).colors)
        return colors

    def _update(
        self,
        workspace: Path | str,
        change: Callable[[LazyConfig, str], None],
    ) -> None:
        key = _workspace_key(workspace)
        with exclusive_lock(self.lock_path):
            config = self.load()
            change(config, key)
            atomic_write_json(self.path, config.to_json())


def _workspace_key(workspace: Path | str) -> str:
    return str(Path(workspace).expanduser().resolve(strict=False))


def _parse_config(raw: Any) -> LazyConfig:
    if not isinstance(raw, dict):
        raise ConfigError("configuration root must be an object")
    if set(raw) - {"schema_version", "workspaces"}:
        unknown = ", ".join(sorted(set(raw) - {"schema_version", "workspaces"}))
        raise ConfigError(f"unknown configuration fields: {unknown}")
    version = raw.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ConfigError(
            f"unsupported schema_version {version!r}; expected {SCHEMA_VERSION}"
        )
    raw_workspaces = raw.get("workspaces")
    if not isinstance(raw_workspaces, dict):
        raise ConfigError("workspaces must be an object")

    workspaces: dict[str, WorkspaceConfig] = {}
    for raw_path, raw_entry in raw_workspaces.items():
        if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
            raise ConfigError(f"workspace key must be an absolute path: {raw_path!r}")
        workspaces[raw_path] = _parse_workspace_config(raw_path, raw_entry)
    return LazyConfig(workspaces=workspaces)


def _parse_workspace_config(path: str, raw: Any) -> WorkspaceConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"workspace configuration must be an object: {path}")
    unknown = set(raw) - {"colors", "terminal"}
    if unknown:
        raise ConfigError(f"unknown workspace fields for {path}: {', '.join(sorted(unknown))}")
    raw_colors = raw.get("colors", {})
    if not isinstance(raw_colors, dict):
        raise ConfigError(f"workspace colors must be an object: {path}")
    colors: dict[str, ColorScheme] = {}
    for name, raw_scheme in raw_colors.items():
        if name not in PRESET_COLORS:
            raise ConfigError(f"unknown color slot for {path}: {name}")
        colors[name] = _parse_color_scheme(path, name, raw_scheme)

    terminal = raw.get("terminal")
    if terminal is not None and terminal not in KNOWN_TERMINALS:
        raise ConfigError(f"unknown terminal adapter for {path}: {terminal!r}")
    return WorkspaceConfig(colors=colors, terminal=terminal)


def _parse_color_scheme(path: str, name: str, raw: Any) -> ColorScheme:
    if not isinstance(raw, Mapping):
        raise ConfigError(f"color slot {name} for {path} must be an object")
    if set(raw) != {"background", "foreground", "accent"}:
        raise ConfigError(
            f"color slot {name} for {path} requires background, foreground, and accent"
        )
    try:
        return ColorScheme(
            raw["background"],
            raw["foreground"],
            raw["accent"],
        )
    except (TypeError, ValueError) as error:
        raise ConfigError(f"invalid color slot {name} for {path}: {error}") from error
