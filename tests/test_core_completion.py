# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lazyros2.completion import (  # noqa: E402
    CompletionCache,
    CompletionCollector,
    CompletionData,
    CompletionError,
)
from lazyros2.paths import Workspace  # noqa: E402
from lazyros2.process import CommandResult  # noqa: E402


class CompletionCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        (root / "src").mkdir()
        self.workspace = Workspace.open(root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_collects_packages_executables_launches_and_rviz_files(self) -> None:
        prefix = self.workspace.install
        nested_launch = prefix / "share" / "nav_pkg" / "launch" / "nested"
        nested_launch.mkdir(parents=True)
        (nested_launch / "bringup.launch.py").write_text("", encoding="utf-8")
        (self.workspace.src / "robot.rviz").write_text("", encoding="utf-8")
        (self.workspace.install / "generated.rviz").write_text("", encoding="utf-8")

        def runner(argv: object, **kwargs: object) -> CommandResult:
            command = tuple(argv)  # type: ignore[arg-type]
            if command[0] == "colcon":
                return CommandResult(command, 0, "nav_pkg\nempty_pkg\n", "")
            return CommandResult(
                command,
                0,
                "nav_pkg navigator\nnav_pkg mapper\n",
                "",
            )

        collector = CompletionCollector(runner=runner)
        data = collector.collect(
            self.workspace,
            {"AMENT_PREFIX_PATH": str(prefix)},
        )

        self.assertEqual(data.packages, ("empty_pkg", "nav_pkg"))
        self.assertEqual(data.executables["nav_pkg"], ("mapper", "navigator"))
        self.assertEqual(data.launches["nav_pkg"], ("bringup.launch.py",))
        self.assertEqual(data.ambiguous_launches, {})
        self.assertEqual(data.rviz_files, ("src/robot.rviz",))

    def test_marks_duplicate_launch_basenames_ambiguous(self) -> None:
        launch_root = self.workspace.install / "share" / "nav_pkg" / "launch"
        (launch_root / "a").mkdir(parents=True)
        (launch_root / "b").mkdir(parents=True)
        (launch_root / "a" / "bringup.launch.py").touch()
        (launch_root / "b" / "bringup.launch.py").touch()

        def runner(argv: object, **kwargs: object) -> CommandResult:
            command = tuple(argv)  # type: ignore[arg-type]
            stdout = "nav_pkg\n" if command[0] == "colcon" else ""
            return CommandResult(command, 0, stdout, "")

        data = CompletionCollector(runner=runner).collect(
            self.workspace,
            {"AMENT_PREFIX_PATH": str(self.workspace.install)},
        )

        self.assertNotIn("nav_pkg", data.launches)
        self.assertEqual(
            data.ambiguous_launches["nav_pkg"]["bringup.launch.py"],
            ("a/bringup.launch.py", "b/bringup.launch.py"),
        )

    def test_ros2_failure_is_distinct_from_empty_result(self) -> None:
        def runner(argv: object, **kwargs: object) -> CommandResult:
            command = tuple(argv)  # type: ignore[arg-type]
            if command[0] == "colcon":
                return CommandResult(command, 0, "pkg\n", "")
            return CommandResult(command, 1, "", "daemon unavailable")

        with self.assertRaisesRegex(CompletionError, "ros2 pkg executables"):
            CompletionCollector(runner=runner).collect(self.workspace, {})

    def test_rejects_control_characters_in_line_protocol_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid completion name"):
            CompletionData(packages=("valid", "bad\npackage"))
        with self.assertRaisesRegex(ValueError, "invalid completion package"):
            CompletionData(executables={"bad\x1bpackage": ("node",)})

    def test_collector_subcommands_use_the_shared_deadline_remaining_time(self) -> None:
        observed_timeouts: list[float] = []

        def runner(argv: object, **kwargs: object) -> CommandResult:
            observed_timeouts.append(float(kwargs["timeout"]))
            command = tuple(argv)  # type: ignore[arg-type]
            return CommandResult(command, 0, "pkg\n", "")

        deadline = time.monotonic() + 0.25
        packages = CompletionCollector(runner=runner).collect_packages(
            self.workspace,
            {},
            deadline=deadline,
        )

        self.assertEqual(packages, ("pkg",))
        self.assertEqual(len(observed_timeouts), 1)
        self.assertGreater(observed_timeouts[0], 0)
        self.assertLessEqual(observed_timeouts[0], 0.25)


class CompletionCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cache = CompletionCache(Path(self.temp_dir.name))
        self.workspace_root = Path(self.temp_dir.name) / "robot_ws"
        self.workspace_root.mkdir()
        (self.workspace_root / "src").mkdir()
        self.workspace = Workspace.open(self.workspace_root)
        self.key = self.cache.key(
            self.workspace,
            {"ROS_DISTRO": "jazzy", "AMENT_PREFIX_PATH": "/opt/ros/jazzy"},
            "/opt/ros/jazzy/bin/ros2",
            "/usr/bin/colcon",
            domain="runtime",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_successful_empty_refresh_replaces_old_data(self) -> None:
        old = CompletionData(packages=("old_pkg",))
        self.cache.refresh(self.key, lambda: old)

        refreshed = self.cache.refresh(self.key, CompletionData)

        self.assertEqual(refreshed.data, CompletionData())
        self.assertFalse(refreshed.stale)

    def test_failed_refresh_keeps_previous_snapshot_and_marks_stale(self) -> None:
        data = CompletionData(packages=("nav_pkg",))
        self.cache.refresh(self.key, lambda: data)

        def fail() -> CompletionData:
            raise CompletionError("timed out")

        cached = self.cache.refresh(self.key, fail)

        self.assertEqual(cached.data, data)
        self.assertTrue(cached.stale)
        self.assertEqual(cached.error, "timed out")

    def test_failed_first_refresh_does_not_create_invalid_cache(self) -> None:
        def fail() -> CompletionData:
            raise CompletionError("timed out")

        with self.assertRaisesRegex(CompletionError, "timed out"):
            self.cache.refresh(self.key, fail)

        self.assertIsNone(self.cache.read(self.key))

    def test_mark_dirty_preserves_snapshot(self) -> None:
        data = CompletionData(packages=("nav_pkg",))
        self.cache.refresh(self.key, lambda: data)

        self.cache.mark_dirty(self.key)

        cached = self.cache.read(self.key)
        self.assertIsNotNone(cached)
        assert cached is not None
        self.assertEqual(cached.data, data)
        self.assertTrue(cached.dirty)

    def test_refresh_honors_a_short_caller_lock_deadline(self) -> None:
        lock_path = (self.cache.cache_dir / f"{self.key}.json").with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("held\n", encoding="ascii")

        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            self.cache.refresh(
                self.key,
                CompletionData,
                lock_timeout=0.03,
            )
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.15)

    def test_failed_atomic_replace_reports_error_and_preserves_old_snapshot(self) -> None:
        original = CompletionData(packages=("old_pkg",))
        self.cache.refresh(self.key, lambda: original)

        with mock.patch(
            "lazyros2.completion.atomic_write_json",
            side_effect=OSError("disk full"),
        ), self.assertRaisesRegex(OSError, "disk full"):
            self.cache.refresh(
                self.key,
                lambda: CompletionData(packages=("new_pkg",)),
            )

        cached = self.cache.read(self.key)
        self.assertIsNotNone(cached)
        assert cached is not None
        self.assertEqual(cached.data, original)

    def test_cache_key_changes_with_ros_environment(self) -> None:
        other = self.cache.key(
            self.workspace,
            {"ROS_DISTRO": "humble", "AMENT_PREFIX_PATH": "/opt/ros/humble"},
            "/opt/ros/humble/bin/ros2",
            "/usr/bin/colcon",
            domain="runtime",
        )

        self.assertNotEqual(self.key, other)

    def test_cache_domains_cannot_alias_in_the_same_environment(self) -> None:
        environment = {"ROS_DISTRO": "jazzy", "AMENT_PREFIX_PATH": ""}
        keys = {
            self.cache.key(
                self.workspace,
                environment,
                "/usr/bin/ros2",
                "/usr/bin/colcon",
                domain=domain,
            )
            for domain in ("packages", "runtime", "launch")
        }

        self.assertEqual(len(keys), 3)


if __name__ == "__main__":
    unittest.main()
