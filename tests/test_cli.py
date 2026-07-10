# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import argparse
import contextlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

ROOT = Path(__file__).resolve().parents[1]

from lazyros2 import cli
from lazyros2.colcon import DependencyEvidence
from lazyros2.completion import (
    CachedCompletion,
    CompletionCache,
    CompletionData,
    CompletionError,
)
from lazyros2.jobs import JobRecord, JobRegistry, JobState
from lazyros2.paths import Workspace


class CliParserTests(unittest.TestCase):
    def test_splits_literal_passthrough_for_every_forwarding_command(self) -> None:
        cases = (
            (["build", "demo", "--", "--event-handlers", "console_direct+"], ["build", "demo"]),
            (["test", "demo", "--", "--executor", "sequential"], ["test", "demo"]),
            (["run", "demo", "talker", "--", "--ros-args", "-r", "a:=b"], ["run", "demo", "talker"]),
            (["launch", "demo", "bringup.launch.py", "--", "use_sim:=true"], ["launch", "demo", "bringup.launch.py"]),
            (["rviz", "view.rviz", "--", "--ros-args"], ["rviz", "view.rviz"]),
        )
        for values, expected_wrapper in cases:
            with self.subTest(values=values):
                wrapper, forwarded = cli._split_passthrough(values)
                self.assertEqual(wrapper, expected_wrapper)
                self.assertEqual(tuple(values[len(expected_wrapper) + 1 :]), forwarded)

    def test_ros_parser_rejects_extra_arguments_without_separator(self) -> None:
        parser, _ = cli._build_parser()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                parser.parse_args(["run", "demo", "talker", "--ros-args"])
        self.assertEqual(raised.exception.code, 2)

    def test_ros_argv_maps_forwarded_values_literally(self) -> None:
        run = argparse.Namespace(command="run", package="demo pkg", executable="$(touch nope)")
        high_level, target, process = cli._ros_command_argv(
            run,
            ("--ros-args", "value with space", "*", "'quoted'"),
        )
        self.assertEqual(
            process,
            (
                "ros2",
                "run",
                "demo pkg",
                "$(touch nope)",
                "--ros-args",
                "value with space",
                "*",
                "'quoted'",
            ),
        )
        self.assertEqual(high_level[4], "--")
        self.assertEqual(target, "demo pkg/$(touch nope)")

    def test_no_argument_cli_starts_detected_control_shell(self) -> None:
        with mock.patch.object(cli, "_current_shell", return_value="zsh"), mock.patch.object(
            cli, "_start_controller", return_value=17
        ) as start:
            self.assertEqual(cli.main([]), 17)
        start.assert_called_once_with("zsh")

    def test_usage_failures_return_two(self) -> None:
        stderr = io.StringIO()
        with mock.patch.object(cli, "_dispatch", side_effect=ValueError("bad argument")), contextlib.redirect_stderr(stderr):
            self.assertEqual(cli.main(["about"]), 2)
        self.assertIn("bad argument", stderr.getvalue())

    def test_missing_completion_command_maps_to_127(self) -> None:
        stderr = io.StringIO()
        with mock.patch.object(
            cli,
            "_dispatch",
            side_effect=CompletionError("colcon is unavailable", exit_code=127),
        ), contextlib.redirect_stderr(stderr):
            self.assertEqual(cli.main(["refresh"]), 127)
        self.assertIn("colcon is unavailable", stderr.getvalue())

    def test_help_hides_internal_protocol_commands_and_has_examples(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(cli.main(["help"]), 0)
        rendered = stdout.getvalue()
        self.assertIn("examples:", rendered)
        self.assertNotIn("__job-run", rendered)
        self.assertNotIn("__complete", rendered)


class CliCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        (self.root / "src").mkdir()
        self.workspace = Workspace.open(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_missing_underlying_command_is_visible_and_returns_127(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = cli._run(("definitely-not-a-real-lazyros2-command",), self.workspace, os.environ)
        self.assertEqual(code, 127)
        self.assertIn("command not found", stderr.getvalue())

    def test_launch_rejects_duplicate_installed_basename(self) -> None:
        args = argparse.Namespace(
            command="launch",
            location="here",
            package="demo",
            launch_file="bringup.launch.py",
        )
        with mock.patch.object(cli, "_workspace", return_value=self.workspace), mock.patch.object(
            cli, "_runtime_environment", return_value={}
        ), mock.patch.object(
            cli,
            "ambiguous_launch_paths",
            return_value=("first/bringup.launch.py", "second/bringup.launch.py"),
        ), self.assertRaisesRegex(cli.UsageError, "ambiguous"):
            cli._run_ros_command(args, ())

    def test_test_result_runs_even_when_test_command_fails(self) -> None:
        args = argparse.Namespace(packages=("demo",))
        with mock.patch.object(cli, "_workspace", return_value=self.workspace), mock.patch.object(
            cli, "_baseline_for_build", return_value={}
        ), mock.patch.object(cli, "_run", side_effect=(7, 9)) as run:
            self.assertEqual(cli._test(args, ()), 7)
        self.assertEqual(run.call_count, 2)

    def test_selected_build_offers_up_to_only_after_evidenced_failure(self) -> None:
        args = argparse.Namespace(packages=("lazy_app",), up_to=False)
        evidence = DependencyEvidence(
            up_to_packages=("lazy_dep", "lazy_app"),
            missing_dependencies=("lazy_dep",),
        )
        stdin = mock.Mock()
        stdin.isatty.return_value = True
        with mock.patch.object(cli, "_workspace", return_value=self.workspace), mock.patch.object(
            cli, "_baseline_for_build", return_value={}
        ), mock.patch.object(
            cli, "find_missing_workspace_dependencies", return_value=evidence
        ), mock.patch.object(
            cli, "_run", side_effect=(7, 0)
        ) as run, mock.patch.object(
            cli, "_mark_completion_dirty"
        ), mock.patch.object(
            cli.sys, "stdin", stdin
        ), mock.patch.object(
            cli.sys, "stderr", io.StringIO()
        ), mock.patch.dict(
            os.environ, {"LAZYROS_ACTIVE": "control"}, clear=False
        ), mock.patch(
            "builtins.input", return_value="yes"
        ):
            self.assertEqual(cli._build(args, ()), 0)

        self.assertEqual(run.call_count, 2)
        self.assertIn("--packages-select", run.call_args_list[0].args[0])
        self.assertIn("--packages-up-to", run.call_args_list[1].args[0])

    def test_noninteractive_selected_build_never_prompts_for_retry(self) -> None:
        args = argparse.Namespace(packages=("lazy_app",), up_to=False)
        evidence = DependencyEvidence(
            up_to_packages=("lazy_dep", "lazy_app"),
            missing_dependencies=("lazy_dep",),
        )
        with mock.patch.object(cli, "_workspace", return_value=self.workspace), mock.patch.object(
            cli, "_baseline_for_build", return_value={}
        ), mock.patch.object(
            cli, "find_missing_workspace_dependencies", return_value=evidence
        ), mock.patch.object(
            cli, "_run", return_value=7
        ) as run, mock.patch.dict(
            os.environ, {"LAZYROS_ACTIVE": ""}, clear=False
        ), mock.patch(
            "builtins.input", side_effect=AssertionError("must not prompt")
        ):
            self.assertEqual(cli._build(args, ()), 7)
        run.assert_called_once()

    def test_jobs_filter_uses_current_workspace_and_prints_pid(self) -> None:
        record = JobRecord(
            id="id",
            number=3,
            workspace=self.root,
            kind="run",
            target="demo/talker",
            command=("run", "--here", "demo", "talker"),
            color_slot=2,
            state=JobState.IDLE,
            pid=4321,
            exit_code=130,
            created_at=1.0,
            updated_at=2.0,
        )
        registry = mock.Mock()
        registry.list.return_value = (record,)
        stdout = io.StringIO()
        with mock.patch.object(cli, "_workspace", return_value=self.workspace), mock.patch.object(
            cli, "_job_registry", return_value=registry
        ), contextlib.redirect_stdout(stdout):
            self.assertEqual(cli._list_jobs("03"), 0)
        registry.list.assert_called_once_with(self.root)
        self.assertIn("4321", stdout.getvalue())
        self.assertIn("(130)", stdout.getvalue())

    def test_stale_private_session_is_removed_but_live_owner_is_kept(self) -> None:
        session_root = self.root / "runtime" / "sessions"
        dead = session_root / "dead"
        live = session_root / "live"
        dead.mkdir(parents=True)
        live.mkdir()
        (dead / "owner.json").write_text(
            '{"pid": 2147483647, "created_at": 1}\n', encoding="utf-8"
        )
        (live / "owner.json").write_text(
            f'{{"pid": {os.getpid()}, "created_at": {time.time()}}}\n',
            encoding="utf-8",
        )

        cli._prune_stale_sessions(session_root)

        self.assertFalse(dead.exists())
        self.assertTrue(live.is_dir())

    def test_window_title_uses_package_while_registry_keeps_full_target(self) -> None:
        self.assertEqual(
            cli._window_title_target("launch", "navigation/bringup.launch.py"),
            "navigation",
        )
        self.assertEqual(cli._window_title_target("rviz", "config/view.rviz"), "rviz2")


class CliCompletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name).resolve()
        (root / "src").mkdir()
        self.workspace = Workspace.open(root)
        self.data = CompletionData(
            packages=("demo", "messages"),
            executables={"demo": ("listener", "talker")},
            launches={"demo": ("bringup.launch.py",)},
            rviz_files=("config/view.rviz",),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def candidates(self, words: list[str], cursor: int | None = None) -> tuple[str, ...]:
        args = argparse.Namespace(
            words=["--", *words],
            cursor=len(words) - 1 if cursor is None else cursor,
            shell="bash",
        )
        return cli._completion_candidates(args, self.data, self.workspace)

    def test_top_level_and_run_context(self) -> None:
        self.assertIn("build", self.candidates(["lazy", ""]))
        self.assertEqual(
            self.candidates(["lazy", "run", "demo", "t"]),
            ("talker",),
        )
        self.assertEqual(
            self.candidates(["lazy", "run", "--window", "d"]),
            ("demo",),
        )

    def test_static_top_level_completion_needs_no_workspace_or_ros(self) -> None:
        args = argparse.Namespace(words=["--", "lazy", ""], cursor=1, shell="bash")
        stdout = io.StringIO()
        with mock.patch.object(cli, "_workspace", side_effect=AssertionError), contextlib.redirect_stdout(stdout):
            self.assertEqual(cli._complete(args), 0)
        self.assertIn("build", stdout.getvalue().splitlines())

    def test_build_completion_does_not_require_ros2_collection(self) -> None:
        collector = mock.Mock()
        collector.collect_packages.return_value = ("demo",)
        cache = CompletionCache(self.workspace.root / "cache")
        with mock.patch.object(
            cli,
            "_completion_cache_context",
            return_value=({}, cache, "a" * 64),
        ), mock.patch.object(
            cli, "CompletionCollector", return_value=collector
        ), mock.patch.object(cli, "_load_baseline", return_value={}), mock.patch.object(
            cli,
            "_runtime_environment",
            side_effect=AssertionError("build completion sourced the overlay"),
        ):
            first = cli._completion_data_for_command("build", self.workspace)
            second = cli._completion_data_for_command("build", self.workspace)
        self.assertEqual(first.packages, ("demo",))
        self.assertEqual(second.packages, ("demo",))
        collector.collect_packages.assert_called_once()
        collector.collect_executables.assert_not_called()

    def test_launch_dirty_cache_is_refreshed_once_without_losing_other_data(self) -> None:
        cache = CompletionCache(self.workspace.root / "launch-cache")
        key = "b" * 64
        cache.refresh(key, lambda: self.data)
        cache.mark_dirty(key)
        collector = mock.Mock()
        collector.collect_launches.return_value = (
            {"demo": ("new.launch.py",)},
            {},
        )
        with mock.patch.object(
            cli,
            "_completion_cache_context",
            return_value=({}, cache, key),
        ), mock.patch.object(cli, "CompletionCollector", return_value=collector):
            first = cli._completion_data_for_command("launch", self.workspace)
            second = cli._completion_data_for_command("launch", self.workspace)

        self.assertEqual(first.launches, {"demo": ("new.launch.py",)})
        self.assertEqual(second.launches, first.launches)
        self.assertEqual(first.executables, self.data.executables)
        collector.collect_launches.assert_called_once()

    def test_busy_cache_lock_uses_existing_dirty_snapshot_for_every_ros_context(self) -> None:
        for index, command in enumerate(("build", "run", "launch"), start=1):
            with self.subTest(command=command):
                cache = CompletionCache(self.workspace.root / f"busy-cache-{command}")
                key = f"{index:x}" * 64
                cache.refresh(key, lambda: self.data)
                cache.mark_dirty(key)
                lock_path = (cache.cache_dir / f"{key}.json").with_suffix(".lock")
                lock_path.write_text("held\n", encoding="ascii")
                collector = mock.Mock()
                collector.collect.side_effect = AssertionError("collector ran under lock")
                collector.collect_packages.side_effect = AssertionError(
                    "package collector ran under lock"
                )
                collector.collect_launches.side_effect = AssertionError(
                    "launch collector ran under lock"
                )
                with mock.patch.object(
                    cli,
                    "_completion_cache_context",
                    return_value=({}, cache, key),
                ), mock.patch.object(
                    cli,
                    "CompletionCollector",
                    return_value=collector,
                ), mock.patch.object(cli, "_load_baseline", return_value={}):
                    result = cli._completion_data_for_command(
                        command,
                        self.workspace,
                        deadline=time.monotonic() + 0.03,
                    )

                self.assertEqual(result.packages, self.data.packages)
                if command == "build":
                    self.assertEqual(result.executables, {})
                    self.assertEqual(result.launches, {})
                else:
                    self.assertEqual(result.executables, self.data.executables)
                    self.assertEqual(result.launches, self.data.launches)

    def test_expired_deadline_still_returns_an_existing_dirty_snapshot(self) -> None:
        cache = CompletionCache(self.workspace.root / "expired-cache")
        key = "e" * 64
        cache.refresh(key, lambda: self.data)
        cache.mark_dirty(key)
        with mock.patch.object(
            cli,
            "_completion_cache_context",
            return_value=({}, cache, key),
        ):
            result = cli._completion_data_for_command(
                "run",
                self.workspace,
                deadline=time.monotonic() - 1,
            )

        self.assertEqual(result, self.data)
        cached = cache.read(key)
        self.assertIsNotNone(cached)
        assert cached is not None
        self.assertTrue(cached.stale)

    def test_cache_domains_keep_build_and_launch_snapshots_out_of_run(self) -> None:
        for runtime_command in ("run", "launch"):
            with self.subTest(after_build=runtime_command):
                cache = CompletionCache(
                    self.workspace.root / f"domain-cache-{runtime_command}"
                )
                keys = {
                    "packages": "1" * 64,
                    "runtime": "2" * 64,
                    "launch": "3" * 64,
                }
                collector = mock.Mock()
                collector.collect_packages.return_value = self.data.packages
                collector.collect.return_value = self.data
                collector.collect_launches.return_value = (
                    self.data.launches,
                    {},
                )

                def context(
                    workspace: Workspace,
                    *,
                    domain: str,
                    env: object = None,
                    deadline: object = None,
                ) -> tuple[dict[str, str], CompletionCache, str]:
                    self.assertEqual(workspace, self.workspace)
                    return {}, cache, keys[domain]

                with mock.patch.object(
                    cli,
                    "_completion_cache_context",
                    side_effect=context,
                ), mock.patch.object(
                    cli,
                    "CompletionCollector",
                    return_value=collector,
                ), mock.patch.object(cli, "_load_baseline", return_value={}):
                    cli._completion_data_for_command("build", self.workspace)
                    result = cli._completion_data_for_command(
                        runtime_command,
                        self.workspace,
                    )

                collector.collect_packages.assert_called_once()
                if runtime_command == "run":
                    collector.collect.assert_called_once()
                    self.assertEqual(result.executables, self.data.executables)
                else:
                    collector.collect_launches.assert_called_once()
                    self.assertEqual(result.launches, self.data.launches)

        cache = CompletionCache(self.workspace.root / "launch-before-run-cache")
        keys = {"runtime": "4" * 64, "launch": "5" * 64}
        collector = mock.Mock()
        collector.collect_launches.return_value = (self.data.launches, {})
        collector.collect.return_value = self.data

        def launch_run_context(
            workspace: Workspace,
            *,
            domain: str,
            env: object = None,
            deadline: object = None,
        ) -> tuple[dict[str, str], CompletionCache, str]:
            return {}, cache, keys[domain]

        with mock.patch.object(
            cli,
            "_completion_cache_context",
            side_effect=launch_run_context,
        ), mock.patch.object(cli, "CompletionCollector", return_value=collector):
            cli._completion_data_for_command("launch", self.workspace)
            run_data = cli._completion_data_for_command("run", self.workspace)

        collector.collect_launches.assert_called_once()
        collector.collect.assert_called_once()
        self.assertEqual(run_data.executables, self.data.executables)

    def test_successful_empty_runtime_domains_are_cached_as_complete(self) -> None:
        empty_runtime = CompletionData(packages=("empty_pkg",))
        for command in ("run", "launch"):
            with self.subTest(command=command):
                cache = CompletionCache(self.workspace.root / f"empty-{command}")
                key = ("6" if command == "run" else "7") * 64
                collector = mock.Mock()
                collector.collect.return_value = empty_runtime
                collector.collect_launches.return_value = ({}, {})
                with mock.patch.object(
                    cli,
                    "_completion_cache_context",
                    return_value=({}, cache, key),
                ), mock.patch.object(
                    cli,
                    "CompletionCollector",
                    return_value=collector,
                ):
                    first = cli._completion_data_for_command(command, self.workspace)
                    second = cli._completion_data_for_command(command, self.workspace)

                self.assertEqual(first, second)
                if command == "run":
                    collector.collect.assert_called_once()
                else:
                    collector.collect_launches.assert_called_once()

    def test_refresh_seeds_packages_runtime_and_launch_domains(self) -> None:
        cache = CompletionCache(self.workspace.root / "refresh-domains")
        keys = {
            "packages": "8" * 64,
            "runtime": "9" * 64,
            "launch": "a" * 64,
        }
        collector = mock.Mock()
        collector.collect.return_value = self.data

        def context(
            workspace: Workspace,
            *,
            domain: str,
            env: object = None,
            deadline: object = None,
        ) -> tuple[dict[str, str], CompletionCache, str]:
            return {}, cache, keys[domain]

        with mock.patch.object(
            cli,
            "_completion_cache_context",
            side_effect=context,
        ), mock.patch.object(
            cli,
            "CompletionCollector",
            return_value=collector,
        ), mock.patch.object(cli, "_load_baseline", return_value={}):
            refreshed = cli._completion_snapshot_after_dirty(self.workspace)

        self.assertEqual(refreshed, self.data)
        packages = cache.read(keys["packages"])
        runtime = cache.read(keys["runtime"])
        launch = cache.read(keys["launch"])
        assert packages is not None and runtime is not None and launch is not None
        self.assertEqual(packages.data, CompletionData(packages=self.data.packages))
        self.assertEqual(runtime.data, self.data)
        self.assertEqual(launch.data, self.data)

    @unittest.skipIf(os.name == "nt", "overlay deadline requires POSIX process groups")
    def test_completion_setup_source_is_bounded_by_the_shared_deadline(self) -> None:
        install = self.workspace.install
        install.mkdir()
        (install / "local_setup.sh").write_text("sleep 3\n", encoding="utf-8")
        args = argparse.Namespace(
            words=["--", "lazy", "run", ""],
            cursor=2,
            shell="bash",
        )
        environment = dict(os.environ)
        environment.update(
            {
                "LAZYROS_WORKSPACE": str(self.workspace.root),
                "XDG_CACHE_HOME": str(self.workspace.root / "cache"),
            }
        )

        started = time.monotonic()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, environment, clear=True), contextlib.redirect_stdout(
            stdout
        ), contextlib.redirect_stderr(stderr):
            self.assertEqual(cli._complete(args), 0)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 2.0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    @unittest.skipUnless(
        os.name == "posix" and shutil.which("setsid"),
        "requires POSIX setsid",
    )
    def test_escaped_setup_descendant_cannot_hold_completion_capture_open(self) -> None:
        install = self.workspace.install
        install.mkdir()
        escape_pid_file = self.workspace.root / "escape-pid"
        (install / "local_setup.sh").write_text(
            "setsid sh -c 'sleep 4' &\n"
            "printf '%s\\n' \"$!\" > \"$LAZYROS_ESCAPE_PID_FILE\"\n"
            "sleep 4\n",
            encoding="utf-8",
        )
        args = argparse.Namespace(
            words=["--", "lazy", "run", ""],
            cursor=2,
            shell="bash",
        )
        environment = dict(os.environ)
        environment.update(
            {
                "LAZYROS_ESCAPE_PID_FILE": str(escape_pid_file),
                "LAZYROS_WORKSPACE": str(self.workspace.root),
                "XDG_CACHE_HOME": str(self.workspace.root / "escape-cache"),
            }
        )
        stdout = io.StringIO()
        stderr = io.StringIO()

        started = time.monotonic()
        try:
            with mock.patch.dict(
                os.environ,
                environment,
                clear=True,
            ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(cli._complete(args), 0)
            elapsed = time.monotonic() - started

            self.assertLess(elapsed, 2.1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "")
        finally:
            if escape_pid_file.is_file():
                escaped_pid = int(escape_pid_file.read_text().strip())
                try:
                    os.killpg(escaped_pid, 9)
                except ProcessLookupError:
                    pass

    @unittest.skipUnless(
        os.name == "posix" and shutil.which("setsid"),
        "requires POSIX setsid",
    )
    def test_launcher_keeps_escaped_setup_completion_under_two_seconds(self) -> None:
        install = self.workspace.install
        install.mkdir()
        escape_pid_file = self.workspace.root / "launcher-escape-pid"
        (install / "local_setup.sh").write_text(
            "setsid sh -c 'sleep 4' &\n"
            "printf '%s\\n' \"$!\" > \"$LAZYROS_ESCAPE_PID_FILE\"\n"
            "sleep 4\n",
            encoding="utf-8",
        )
        home = self.workspace.root / "launcher-home"
        home.mkdir()
        environment = dict(os.environ)
        environment.update(
            {
                "HOME": str(home),
                "LAZYROS_ESCAPE_PID_FILE": str(escape_pid_file),
                "LAZYROS_WORKSPACE": str(self.workspace.root),
                "XDG_CACHE_HOME": str(self.workspace.root / "launcher-cache"),
            }
        )

        started = time.monotonic()
        try:
            completed = subprocess.run(
                [
                    str(ROOT / "bin" / "lazy"),
                    "__complete",
                    "--shell",
                    "bash",
                    "--cursor",
                    "2",
                    "--",
                    "lazy",
                    "run",
                    "",
                ],
                env=environment,
                cwd=self.workspace.root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=3.0,
            )
            elapsed = time.monotonic() - started

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(completed.stderr, "")
            self.assertLess(elapsed, 2.0)
        finally:
            if escape_pid_file.is_file():
                escaped_pid = int(escape_pid_file.read_text().strip())
                try:
                    os.killpg(escaped_pid, 9)
                except ProcessLookupError:
                    pass

    def test_explicit_refresh_rejects_stale_fallback(self) -> None:
        cache = mock.Mock()
        cache.refresh.return_value = CachedCompletion(
            data=self.data,
            updated_at=1.0,
            stale=True,
            error="ros2 timed out",
            error_code=124,
        )
        with mock.patch.object(
            cli,
            "_completion_cache_context",
            return_value=({}, cache, "a" * 64),
        ), self.assertRaisesRegex(CompletionError, "timed out") as raised:
            cli._completion_snapshot_after_dirty(self.workspace)
        self.assertEqual(raised.exception.exit_code, 124)

    def test_config_context_includes_slots_and_adapters(self) -> None:
        self.assertIn("set", self.candidates(["lazy", "config", "colors", "s"]))
        self.assertIn(
            "navy",
            self.candidates(["lazy", "config", "colors", "set", "n"]),
        )
        self.assertIn(
            "gnome-terminal",
            self.candidates(["lazy", "config", "terminal", "set", "g"]),
        )


class CliJobFinishProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.workspace = root / "robot_ws"
        self.workspace.mkdir()
        self.registry = JobRegistry(root / "runtime")
        self.job = self.registry.register(
            self.workspace,
            "run",
            "demo/talker",
            ("run", "--here", "demo", "talker"),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _claim_as(self, pid: int) -> None:
        self.registry.update(
            self.workspace,
            self.job.number,
            JobState.STARTING,
            pid=pid,
        )

    def test_finish_marks_claimed_job_idle_with_exit_code(self) -> None:
        self._claim_as(4321)
        with mock.patch.object(cli, "_job_registry", return_value=self.registry), mock.patch.object(
            cli.os,
            "getppid",
            return_value=4321,
        ):
            self.assertEqual(cli._finish_job(self.job.id, 4321, 1), 0)

        finished = self.registry.get_by_id(self.job.id)
        self.assertEqual(finished.state, JobState.IDLE)
        self.assertEqual(finished.exit_code, 1)
        self.assertEqual(finished.pid, 4321)

    def test_finish_rejects_wrong_parent_or_unclaimed_owner(self) -> None:
        self._claim_as(4321)
        with mock.patch.object(cli, "_job_registry", return_value=self.registry), mock.patch.object(
            cli.os,
            "getppid",
            return_value=9999,
        ), self.assertRaisesRegex(cli.UsageError, "finishing process parent"):
            cli._finish_job(self.job.id, 4321, 1)

        with mock.patch.object(cli, "_job_registry", return_value=self.registry), mock.patch.object(
            cli.os,
            "getppid",
            return_value=4322,
        ), self.assertRaisesRegex(cli.UsageError, "does not own"):
            cli._finish_job(self.job.id, 4322, 1)


if __name__ == "__main__":
    unittest.main()
