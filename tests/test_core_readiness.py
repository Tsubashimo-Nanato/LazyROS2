# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lazyros2 import cli
from lazyros2.completion import CompletionData, CompletionError
from lazyros2.graph_cache import GRAPH_TTL_SECONDS
from lazyros2.graph_cache import GraphCache
from lazyros2.jobs import JobRegistry
from lazyros2.paths import Workspace, XdgPaths
from lazyros2.process import CommandResult


class GraphCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cache = GraphCache(self.root, ttl=3)
        self.key = "a" * 64

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_successful_empty_is_cached_without_another_collection(self) -> None:
        collect = mock.Mock(return_value=())
        first = self.cache.get(self.key, collect)
        second = self.cache.get(self.key, collect)
        self.assertEqual(first.require_values(), ())
        self.assertIsNone(first.error)
        self.assertEqual(second, first)
        collect.assert_called_once()

    def test_failed_cold_collection_has_a_retry_cooldown_and_no_successful_value(self) -> None:
        collect = mock.Mock(side_effect=CompletionError("unavailable", exit_code=127))
        first = self.cache.get(self.key, collect)
        second = self.cache.get(self.key, collect)
        self.assertIsNone(first.values)
        self.assertEqual(second.error_code, 127)
        with self.assertRaisesRegex(CompletionError, "unavailable"):
            second.require_values()
        collect.assert_called_once()

    def test_timeout_keeps_last_successful_snapshot_and_its_timestamp(self) -> None:
        with mock.patch("lazyros2.graph_cache.time.time", return_value=10):
            original = self.cache.get(self.key, lambda: ("/scan",))
        with mock.patch("lazyros2.graph_cache.time.time", return_value=14):
            stale = self.cache.get(self.key, mock.Mock(side_effect=CompletionError("timed out", exit_code=124)))
        self.assertEqual(stale.values, ("/scan",))
        self.assertEqual(stale.updated_at, original.updated_at)
        self.assertEqual(stale.attempted_at, 14)
        self.assertEqual(stale.error_code, 124)

    def test_concurrent_request_uses_stale_data_without_duplicate_collector(self) -> None:
        with mock.patch("lazyros2.graph_cache.time.time", return_value=1):
            self.cache.get(self.key, lambda: ("/old",))
        entered, release = threading.Event(), threading.Event()
        errors = []

        def collect() -> tuple[str, ...]:
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test collector was not released")
            return ("/new",)

        def refresh() -> None:
            try:
                self.cache.get(self.key, collect)
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=refresh)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            duplicate = mock.Mock(side_effect=AssertionError("duplicate ROS discovery"))
            started = time.monotonic()
            snapshot = self.cache.get(self.key, duplicate)
            self.assertLess(time.monotonic() - started, 0.1)
            self.assertEqual(snapshot.values, ("/old",))
            duplicate.assert_not_called()
        finally:
            release.set()
            worker.join(3)
        self.assertEqual(errors, [])
        self.assertEqual(self.cache.read(self.key).values, ("/new",))

    def test_cache_identity_separates_graph_domains_and_discovery_settings(self) -> None:
        baseline = {"ROS_DISTRO": "jazzy", "ROS_DOMAIN_ID": "1"}
        first = GraphCache.key("topic", self.root, baseline, "/bin/ros2")
        for domain, env in (
            ("node", baseline),
            ("topic", {**baseline, "ROS_DOMAIN_ID": "2"}),
            ("topic", {**baseline, "RMW_IMPLEMENTATION": "rmw_cyclonedds_cpp"}),
        ):
            self.assertNotEqual(first, GraphCache.key(domain, self.root, env, "/bin/ros2"))
        self.assertNotEqual(first, GraphCache.key("topic", self.root / "other", baseline, "/bin/ros2"))

    def test_corrupt_snapshot_is_rebuilt_under_the_refresh_lock(self) -> None:
        for content in ("{invalid", '{"schema_version":99}'):
            with self.subTest(content=content):
                (self.root / f"{self.key}.json").write_text(content, encoding="utf-8")
                with self.assertRaises(CompletionError):
                    self.cache.read(self.key)
                snapshot = self.cache.get(self.key, lambda: ("/scan",))
                self.assertEqual(snapshot.values, ("/scan",))
                self.assertEqual(self.cache.read(self.key), snapshot)


class ReadinessIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        (self.root / "src").mkdir()
        self.workspace = Workspace.open(self.root)
        self.paths = XdgPaths(self.root / "config", self.root / "state", self.root / "cache", self.root / "runtime")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_saved_baseline_replaces_stale_runtime_overlay_on_each_call(self) -> None:
        self.workspace.install.mkdir()
        (self.workspace.install / "local_setup.sh").touch()
        baseline = {"PATH": "/usr/bin", "ROS_DISTRO": "jazzy"}
        baseline_file = self.root / "baseline.json"
        baseline_file.write_text(json.dumps(baseline), encoding="utf-8")
        inherited = {"LAZYROS_BASELINE_FILE": str(baseline_file), "AMENT_PREFIX_PATH": str(self.workspace.install / "old_pkg")}
        with mock.patch.object(cli, "capture_overlay_environment", side_effect=[{"NEW": "one"}, {"NEW": "two"}]) as capture:
            self.assertEqual(cli._runtime_environment(self.workspace, inherited), {"NEW": "one"})
            self.assertEqual(cli._runtime_environment(self.workspace, inherited), {"NEW": "two"})
        for call in capture.call_args_list:
            self.assertEqual(call.args[1], baseline)

    def test_warm_graph_completion_skips_ros_and_overlay_capture(self) -> None:
        result = CommandResult(("ros2", "topic", "list"), 0, "/scan\n/tf\n")
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(cli, "_xdg", return_value=self.paths), mock.patch.object(
            cli, "_runtime_environment", return_value={}
        ) as overlay, mock.patch.object(cli, "run_command", return_value=result) as run:
            first = cli._collect_completion_objects("topic", self.workspace, time.monotonic() + 1.5)
            for _ in range(30):
                values = cli._collect_completion_objects("topic", self.workspace, time.monotonic() + 1.5)
                self.assertEqual(values, first)
        self.assertEqual(first, ("/scan", "/tf"))
        run.assert_called_once()
        overlay.assert_called_once()

    @unittest.skipUnless(os.name == "posix", "launcher latency fixture requires POSIX executables")
    def test_separate_completion_processes_share_one_ros_collection(self) -> None:
        tools = self.root / "tools"
        tools.mkdir()
        ros2 = tools / "ros2"
        ros2.write_text(
            '#!/bin/sh\nprintf "call\\n" >> "$LAZYROS_TEST_GRAPH_CALLS"\nprintf "/scan\\n/tf\\n"\n',
            encoding="utf-8",
        )
        ros2.chmod(0o755)
        calls = self.root / "calls"
        env = {key: value for key, value in os.environ.items() if not key.startswith("LAZYROS_")}
        env.update({
            "PATH": str(tools) + os.pathsep + env.get("PATH", ""),
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "LAZYROS_WORKSPACE": str(self.root),
            "LAZYROS_TEST_GRAPH_CALLS": str(calls),
            "XDG_CACHE_HOME": str(self.paths.cache),
            "XDG_RUNTIME_DIR": str(self.paths.runtime),
        })
        command = [sys.executable, "-m", "lazyros2", "__complete", "--shell", "bash", "--cursor", "3", "--", "lazy", "topic", "echo", ""]
        latencies = []
        overall_started = time.monotonic()
        for iteration in range(11):
            started = time.monotonic()
            result = subprocess.run(command, cwd=self.root, env=env, capture_output=True, text=True, timeout=2.0, check=False)
            elapsed = time.monotonic() - started
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "/scan\n/tf\n")
            self.assertEqual(result.stderr, "")
            if iteration:
                latencies.append(elapsed)
        elapsed = time.monotonic() - overall_started
        collection_count = len(calls.read_text().splitlines())
        # A slow runner may legitimately cross the TTL during this process-level
        # probe. Deterministic unit tests above enforce the fresh-cache no-spawn rule.
        self.assertLessEqual(collection_count, int(elapsed / GRAPH_TTL_SECONDS) + 1)
        # Report the full interpreter startup cost; shared CI machines vary too
        # much for a 100 ms wall-clock assertion to be a reliable unit-test gate.
        print(f"Graph completion repeated-process p95: {sorted(latencies)[-1] * 1000:.1f} ms ({collection_count} ROS collections)")

    def test_runtime_completion_cache_reads_do_not_recapture_the_overlay(self) -> None:
        self.workspace.install.mkdir()
        (self.workspace.install / "local_setup.sh").touch()
        baseline = self.root / "baseline.json"
        baseline.write_text(json.dumps({"PATH": os.environ.get("PATH", "")}), encoding="utf-8")
        inherited = {"LAZYROS_BASELINE_FILE": str(baseline), "AMENT_PREFIX_PATH": str(self.workspace.install)}
        data = CompletionData(packages=("demo",), executables={"demo": ("talker",)})
        with mock.patch.dict(os.environ, inherited, clear=True), mock.patch.object(cli, "_xdg", return_value=self.paths), mock.patch.object(
            cli, "capture_overlay_environment", return_value={"AMENT_PREFIX_PATH": str(self.workspace.install)}
        ) as capture, mock.patch.object(cli.CompletionCollector, "collect", return_value=data), mock.patch.object(
            cli.CompletionCollector, "collect_launches", return_value=({"demo": ("bringup.launch.py",)}, {})
        ), mock.patch.object(cli.CompletionCollector, "collect_packages", return_value=("demo",)):
            for command in ("run", "launch", "build"):
                first = cli._completion_data_for_command(command, self.workspace)
                for _ in range(5):
                    self.assertEqual(cli._completion_data_for_command(command, self.workspace), first)
            self.assertEqual(capture.call_count, 2)
            cli._write_private_json(cli._overlay_generation_file(self.workspace, self.paths), 123)
            cli._completion_data_for_command("run", self.workspace)
            cli._completion_data_for_command("launch", self.workspace)
            cli._completion_data_for_command("build", self.workspace)
            self.assertEqual(capture.call_count, 4)

    @unittest.skipUnless(os.name == "posix", "slow setup fixture requires POSIX bash")
    def test_warm_runtime_completion_never_reexecutes_a_slow_setup_script(self) -> None:
        self.workspace.install.mkdir()
        calls = self.root / "setup-calls"
        setup = self.workspace.install / "local_setup.sh"
        setup.write_text(
            f"sleep 0.2\nprintf 'source\\n' >> {shlex.quote(str(calls))}\n",
            encoding="utf-8",
        )
        baseline = self.root / "baseline.json"
        baseline.write_text(json.dumps({"PATH": os.environ["PATH"]}), encoding="utf-8")
        data = CompletionData(executables={"demo": ("talker",)})
        with mock.patch.dict(os.environ, {"LAZYROS_BASELINE_FILE": str(baseline)}, clear=True), mock.patch.object(
            cli, "_xdg", return_value=self.paths
        ), mock.patch.object(cli.CompletionCollector, "collect", return_value=data), mock.patch.object(
            cli.CompletionCollector, "collect_launches", return_value=({"demo": ("bringup.launch.py",)}, {})
        ), mock.patch.object(cli.CompletionCollector, "collect_packages", return_value=("demo",)):
            for _ in range(3):
                for command in ("run", "launch", "build"):
                    cli._completion_data_for_command(command, self.workspace)
        self.assertEqual(calls.read_text().splitlines(), ["source", "source"])

    def test_live_topic_list_reuses_completion_snapshot(self) -> None:
        result = CommandResult(("ros2", "topic", "list"), 0, "/scan\n")
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(cli, "_xdg", return_value=self.paths), mock.patch.object(
            cli, "_runtime_environment", return_value={}
        ), mock.patch.object(cli, "run_command", return_value=result) as run, contextlib.redirect_stdout(io.StringIO()) as stdout:
            cli._collect_completion_objects("topic", self.workspace, time.monotonic() + 1.5)
            self.assertEqual(cli._watch_ros_list(self.workspace, {}, "topic"), 0)
        run.assert_called_once()
        self.assertIn("/scan", stdout.getvalue())

    def test_failed_package_creation_does_not_invalidate_completion(self) -> None:
        args = argparse.Namespace(pkg_action="create", name="demo", type="python", dependencies=())
        with mock.patch.object(cli, "_workspace", return_value=self.workspace), mock.patch.object(cli, "_runtime_environment", return_value={}), mock.patch.object(
            cli, "_run", return_value=7
        ), mock.patch.object(cli, "_mark_completion_dirty") as dirty:
            self.assertEqual(cli._pkg(args), 7)
        dirty.assert_not_called()

    def test_new_package_appears_on_next_build_completion_without_manual_refresh(self) -> None:
        args = argparse.Namespace(pkg_action="create", name="new_pkg", type="python", dependencies=())
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(cli, "_xdg", return_value=self.paths), mock.patch.object(
            cli, "_workspace", return_value=self.workspace
        ), mock.patch.object(cli, "_run", return_value=0), mock.patch.object(
            cli.CompletionCollector, "collect_packages", side_effect=[("old_pkg",), ("new_pkg", "old_pkg")]
        ) as collect:
            first = cli._completion_data_for_command("build", self.workspace)
            self.assertEqual(first.packages, ("old_pkg",))
            self.assertEqual(cli._pkg(args), 0)
            after_create = cli._completion_data_for_command("build", self.workspace)
        self.assertEqual(after_create.packages, ("new_pkg", "old_pkg"))
        self.assertEqual(collect.call_count, 2)

    def test_build_generation_changes_only_after_success(self) -> None:
        args = argparse.Namespace(command="build", packages=())
        marker = cli._overlay_generation_file(self.workspace, self.paths)
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(cli, "_xdg", return_value=self.paths), mock.patch.object(
            cli, "_workspace", return_value=self.workspace
        ), mock.patch.object(cli, "_run", side_effect=[7, 0]), mock.patch.object(cli, "_mark_completion_dirty"), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli._build(args, ()), 7)
            self.assertFalse(marker.exists())
            self.assertEqual(cli._build(args, ()), 0)
            self.assertIsInstance(json.loads(marker.read_text()), int)
        other_root = self.root / "other"
        (other_root / "src").mkdir(parents=True)
        self.assertNotEqual(marker, cli._overlay_generation_file(Workspace.open(other_root), self.paths))

    def test_build_notification_failure_warns_without_changing_build_success(self) -> None:
        args = argparse.Namespace(command="build", packages=())
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(cli, "_xdg", return_value=self.paths), mock.patch.object(cli, "_workspace", return_value=self.workspace), mock.patch.object(
            cli, "_run", return_value=0
        ), mock.patch.object(cli, "_write_private_json", side_effect=OSError("disk is full")), mock.patch.object(
            cli, "_mark_completion_dirty"
        ), contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(cli._build(args, ()), 0)
        self.assertIn("Reopen lazy", stderr.getvalue())
        self.assertIn("disk is full", stderr.getvalue())

    def test_task_baseline_survives_controller_cleanup_and_is_removed_with_task(self) -> None:
        registry = JobRegistry(self.paths.runtime)
        original = self.root / "control-baseline.json"
        original.write_text(json.dumps({"ROS_DISTRO": "jazzy"}), encoding="utf-8")
        adapter = mock.Mock(name="adapter")
        adapter.name = "gnome-terminal"
        adapter.command.return_value = ("terminal",)
        process = mock.Mock()
        process.wait.return_value = 0
        with mock.patch.dict(os.environ, {"LAZYROS_BASELINE_FILE": str(original)}, clear=True), mock.patch.object(
            cli, "_xdg", return_value=self.paths
        ), mock.patch.object(cli, "detect_terminal", return_value=adapter), mock.patch.object(
            cli.subprocess, "Popen", return_value=process
        ) as popen, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli._spawn_job(self.workspace, ("build",), "all"), 0)
        record = registry.list(prune=False)[0]
        task_baseline = Path(popen.call_args.kwargs["env"]["LAZYROS_BASELINE_FILE"])
        self.assertNotEqual(task_baseline, original)
        original.unlink()
        self.assertEqual(cli._load_baseline({"LAZYROS_BASELINE_FILE": str(task_baseline)}), {"ROS_DISTRO": "jazzy"})
        registry.remove(record.workspace, record.number)
        self.assertFalse(task_baseline.exists())

    def test_stale_task_pruning_removes_its_private_baseline(self) -> None:
        registry = JobRegistry(self.paths.runtime)
        job = registry.register(self.workspace.root, "build", "all", ("build",))
        baseline = registry.baseline_path(job.workspace, job.number)
        cli._write_private_json(baseline, {"ROS_DISTRO": "jazzy"})
        if os.name == "posix":
            self.assertEqual(baseline.stat().st_mode & 0o777, 0o600)
        with mock.patch("lazyros2.jobs._is_stale", return_value=True):
            self.assertEqual(registry.list(), ())
        self.assertFalse(baseline.exists())


if __name__ == "__main__":
    unittest.main()
