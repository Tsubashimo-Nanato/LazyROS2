# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lazyros2.config import (  # noqa: E402
    PRESET_COLORS,
    ColorScheme,
    ConfigError,
    ConfigStore,
    LazyConfig,
    contrast_ratio,
)
from lazyros2.storage import ensure_private_directory  # noqa: E402


class ColorTests(unittest.TestCase):
    def test_loads_twelve_accessible_presets(self) -> None:
        self.assertEqual(len(PRESET_COLORS), 12)
        for name, colors in PRESET_COLORS.items():
            with self.subTest(name=name):
                self.assertGreaterEqual(contrast_ratio(colors.foreground, colors.background), 4.5)
                self.assertGreaterEqual(contrast_ratio(colors.accent, colors.background), 3.0)

    def test_normalizes_hex_colors(self) -> None:
        colors = ColorScheme("#14213d", "#e6edf3", "#7aa2f7")

        self.assertEqual(colors.background, "#14213D")

    def test_rejects_invalid_or_low_contrast_colors(self) -> None:
        with self.assertRaisesRegex(ValueError, "#RRGGBB"):
            ColorScheme("navy", "#FFFFFF", "#000000")
        with self.assertRaisesRegex(ValueError, "foreground/background"):
            ColorScheme("#111111", "#222222", "#FFFFFF")
        with self.assertRaisesRegex(ValueError, "accent/background"):
            ColorScheme("#000000", "#FFFFFF", "#111111")


class StorageTests(unittest.TestCase):
    def test_private_directory_rejects_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            actual = root / "actual"
            actual.mkdir()
            link = root / "private"
            try:
                link.symlink_to(actual, target_is_directory=True)
            except OSError:
                self.skipTest("directory symlinks are unavailable")

            with self.assertRaisesRegex(OSError, "must not be a symlink"):
                ensure_private_directory(link)


class ConfigStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.path = root / "config" / "config.json"
        self.workspace = root / "robot_ws"
        self.workspace.mkdir()
        self.store = ConfigStore(self.path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_missing_file_loads_empty_schema_v1(self) -> None:
        config = self.store.load()

        self.assertEqual(config, LazyConfig())

    def test_round_trips_workspace_color_and_terminal_atomically(self) -> None:
        custom = ColorScheme("#000000", "#FFFFFF", "#00AACC")

        self.store.set_color(self.workspace, "navy", custom)
        self.store.set_terminal(self.workspace, "kitty")
        workspace_config = self.store.get_workspace(self.workspace)

        self.assertEqual(workspace_config.colors["navy"], custom)
        self.assertEqual(workspace_config.terminal, "kitty")
        self.assertFalse((self.path.parent / "config.json.tmp").exists())
        if os.name != "nt":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_reset_colors_keeps_terminal_choice(self) -> None:
        self.store.set_color(
            self.workspace,
            "navy",
            ColorScheme("#000000", "#FFFFFF", "#00AACC"),
        )
        self.store.set_terminal(self.workspace, "gnome-terminal")

        self.store.reset_colors(self.workspace)

        config = self.store.get_workspace(self.workspace)
        self.assertEqual(config.colors, {})
        self.assertEqual(config.terminal, "gnome-terminal")

    def test_rejects_unknown_schema_without_rewriting_file(self) -> None:
        self.path.parent.mkdir(parents=True)
        original = '{"schema_version": 99, "workspaces": {}}\n'
        self.path.write_text(original, encoding="utf-8")

        with self.assertRaisesRegex(ConfigError, "schema_version"):
            self.store.load()

        self.assertEqual(self.path.read_text(encoding="utf-8"), original)

    def test_rejects_malformed_workspace_entry(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "workspaces": {
                        "relative/path": {"colors": {}, "terminal": None}
                    },
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ConfigError, "absolute"):
            self.store.load()

    def test_save_rejects_invalid_in_memory_schema(self) -> None:
        invalid = LazyConfig(workspaces={"relative/path": self.store.get_workspace(self.workspace)})

        with self.assertRaisesRegex(ConfigError, "absolute"):
            self.store.save(invalid)

        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
