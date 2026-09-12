# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lazyros2.onboarding import (  # noqa: E402
    _directory_candidates,
    _read_directory,
    select_workspace,
)
from lazyros2 import cli  # noqa: E402
from lazyros2.paths import Workspace, WorkspaceError, XdgPaths  # noqa: E402
from lazyros2.process import CommandResult  # noqa: E402


class OnboardingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.cwd = self.root / "robot_ws"
        self.cwd.mkdir()
        self.probe = mock.Mock(return_value=())

    def choose(self, *answers: str, path: Path | None = None):
        with mock.patch("sys.stdin.isatty", return_value=True), mock.patch(
            "builtins.input", side_effect=answers
        ), contextlib.redirect_stderr(io.StringIO()):
            return select_workspace(path or self.cwd, self.probe)

    def test_existing_src_needs_no_prompt_or_package_probe(self) -> None:
        (self.cwd / "src").mkdir()
        with mock.patch("builtins.input", side_effect=AssertionError("unexpected prompt")):
            workspace = select_workspace(self.cwd, self.probe)
        self.assertEqual(workspace.root, self.cwd)
        self.probe.assert_not_called()

    def test_child_uses_nearest_src_workspace_before_package_probe(self) -> None:
        (self.root / "src").mkdir()
        child = self.cwd / "src" / "robot_base"
        child.mkdir(parents=True)
        self.probe.return_value = ("robot_base",)
        workspace = select_workspace(child, self.probe)
        self.assertEqual(workspace.root, self.cwd)
        self.probe.assert_not_called()

    def test_nonstandard_workspace_keeps_discovered_package_criteria(self) -> None:
        self.probe.return_value = ("robot_base",)
        workspace = select_workspace(self.cwd, self.probe)
        self.assertEqual(workspace.root, self.cwd)
        self.probe.assert_called_once_with(self.cwd)

    def test_cpp_package_source_directory_does_not_replace_enclosing_workspace(self) -> None:
        package = self.cwd / "src" / "navigation"
        descendant = package / "src" / "planner"
        descendant.mkdir(parents=True)
        (package / "package.xml").write_text("<package/>", encoding="utf-8")
        for directory in (package, descendant):
            with self.subTest(directory=directory):
                self.assertEqual(select_workspace(directory, self.probe).root, self.cwd)
        self.probe.assert_not_called()

    def test_standalone_package_keeps_explicit_nonstandard_fallback(self) -> None:
        (self.cwd / "src").mkdir()
        (self.cwd / "package.xml").write_text("<package/>", encoding="utf-8")
        self.assertEqual(select_workspace(self.cwd, self.probe).root, self.cwd)
        self.probe.assert_not_called()

    def test_native_workspace_probes_disable_logs_without_mutating_caller_environment(self) -> None:
        original_log_path = str(self.root / "user-logs")
        with mock.patch.dict(os.environ, {"COLCON_LOG_PATH": original_log_path}), mock.patch.object(
            cli, "run_command", return_value=CommandResult(("colcon",), 0, "robot_base\n")
        ) as command, mock.patch("lazyros2.process.run_command", command):
            self.assertEqual(cli._workspace_probe(self.cwd), ("robot_base",))
            self.assertEqual(Workspace.open(self.cwd).root, self.cwd)
            self.assertEqual(os.environ["COLCON_LOG_PATH"], original_log_path)
        self.assertEqual(command.call_count, 2)
        for call in command.call_args_list:
            self.assertEqual(call.kwargs["env"]["COLCON_LOG_PATH"], os.devnull)
            self.assertEqual(call.kwargs["cwd"], self.cwd)
        self.assertFalse((self.root / "user-logs").exists())

    def test_invalid_noninteractive_directory_has_actionable_error_without_writes(self) -> None:
        with mock.patch("sys.stdin.isatty", return_value=False), mock.patch(
            "builtins.input", side_effect=AssertionError("unexpected prompt")
        ):
            with self.assertRaisesRegex(WorkspaceError, "interactive terminal"):
                select_workspace(self.cwd, self.probe)
        self.probe.assert_called_once_with(self.cwd)
        self.assertEqual(list(self.cwd.iterdir()), [])

    def test_confirmed_creation_only_adds_src(self) -> None:
        sentinel = self.cwd / "notes.txt"
        sentinel.write_text("keep", encoding="utf-8")
        workspace = self.choose("c", "yes")
        self.assertEqual(workspace.root, self.cwd)
        self.assertTrue(workspace.src.is_dir())
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
        self.assertEqual({path.name for path in self.cwd.iterdir()}, {"notes.txt", "src"})

    def test_decline_quit_and_eof_make_no_changes(self) -> None:
        for answers in (("c", ""), ("c", "no"), ("q",), ("",)):
            with self.subTest(answers=answers):
                self.assertIsNone(self.choose(*answers))
                self.assertEqual(list(self.cwd.iterdir()), [])
        for error in (EOFError, KeyboardInterrupt):
            with self.subTest(error=error), mock.patch("sys.stdin.isatty", return_value=True), mock.patch(
                "builtins.input", side_effect=error
            ), contextlib.redirect_stderr(io.StringIO()):
                self.assertIsNone(select_workspace(self.cwd, self.probe))
                self.assertEqual(list(self.cwd.iterdir()), [])

    def test_selects_relative_directory_with_spaces_and_unicode(self) -> None:
        selected = self.cwd / "robot 日本語" / "src"
        selected.mkdir(parents=True)
        with mock.patch("lazyros2.onboarding._read_directory", return_value="robot 日本語"):
            workspace = self.choose("d")
        self.assertEqual(workspace.root, selected.parent)

    def test_empty_directory_selection_cancels(self) -> None:
        with mock.patch("lazyros2.onboarding._read_directory", return_value=""):
            self.assertIsNone(self.choose("d"))
        self.assertEqual(list(self.cwd.iterdir()), [])

    def test_existing_src_file_is_never_replaced(self) -> None:
        src = self.cwd / "src"
        src.write_text("keep", encoding="utf-8")
        self.assertIsNone(self.choose("c", "q"))
        self.assertEqual(src.read_text(encoding="utf-8"), "keep")

    def test_dangling_src_symlink_is_never_replaced(self) -> None:
        src = self.cwd / "src"
        try:
            src.symlink_to(self.cwd / "absent", target_is_directory=True)
        except OSError:
            self.skipTest("symlinks are unavailable")
        self.assertIsNone(self.choose("c", "q"))
        self.assertTrue(src.is_symlink())
        self.assertFalse((self.cwd / "absent").exists())

    def test_existing_src_directory_symlink_keeps_workspace_open_behavior(self) -> None:
        target = self.root / "packages"
        target.mkdir()
        try:
            (self.cwd / "src").symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks are unavailable")
        self.assertEqual(select_workspace(self.cwd, self.probe).root, self.cwd)
        self.probe.assert_not_called()

    def test_creation_race_preserves_newly_created_file(self) -> None:
        answers = iter(("c", "yes", "q"))

        def answer():
            value = next(answers)
            if value == "yes":
                (self.cwd / "src").write_text("raced", encoding="utf-8")
            return value

        with mock.patch("sys.stdin.isatty", return_value=True), mock.patch(
            "builtins.input", side_effect=answer
        ), contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertIsNone(select_workspace(self.cwd, self.probe))
        self.assertIn("cannot create workspace source directory", errors.getvalue())
        self.assertEqual((self.cwd / "src").read_text(encoding="utf-8"), "raced")

    def test_missing_directory_is_not_created(self) -> None:
        missing = self.cwd / "absent"
        self.assertIsNone(self.choose("c", "q", path=missing))
        self.assertFalse(missing.exists())

    def test_prompts_and_diagnostics_do_not_pollute_stdout(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertIsNone(self.choose("invalid", "q"))
        self.assertEqual(output.getvalue(), "")

    def test_directory_candidates_keep_spaces_and_only_offer_directories(self) -> None:
        (self.cwd / "robot one").mkdir()
        (self.cwd / "robot two").mkdir()
        (self.cwd / "robot file").write_text("", encoding="utf-8")
        self.assertEqual(
            _directory_candidates("robot ", self.cwd),
            ["robot one" + os.sep, "robot two" + os.sep],
        )
        self.assertEqual(_directory_candidates("missing/", self.cwd), [])

    def test_path_readline_state_is_restored_on_success_and_cancellation(self) -> None:
        for outcome in ("robot one", EOFError):
            with self.subTest(outcome=outcome):
                original_completer = object()
                readline = types.SimpleNamespace(
                    __doc__="GNU readline",
                    get_completer=mock.Mock(return_value=original_completer),
                    get_completer_delims=mock.Mock(return_value=" \t\n"),
                    set_completer=mock.Mock(),
                    set_completer_delims=mock.Mock(),
                    parse_and_bind=mock.Mock(),
                )
                with mock.patch.dict(sys.modules, {"readline": readline}), mock.patch(
                    "builtins.input", side_effect=[outcome]
                ), contextlib.redirect_stderr(io.StringIO()):
                    if outcome is EOFError:
                        with self.assertRaises(EOFError):
                            _read_directory(self.cwd)
                    else:
                        self.assertEqual(_read_directory(self.cwd), outcome)
                readline.set_completer.assert_called_with(original_completer)
                readline.set_completer_delims.assert_called_with(" \t\n")


class ControllerOnboardingTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.cwd = self.root / "robot_ws"
        self.cwd.mkdir()
        self.paths = XdgPaths(
            config=self.root / "config",
            state=self.root / "state",
            cache=self.root / "cache",
            runtime=self.root / "runtime",
        )
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.dict(os.environ, {"SHELL": "/bin/bash"}, clear=True))
        self.current_directory = self.stack.enter_context(
            mock.patch.object(cli.os, "getcwd", return_value=str(self.cwd))
        )
        self.stack.enter_context(mock.patch("sys.stdin.isatty", return_value=True))
        self.stack.enter_context(mock.patch.object(cli, "_workspace_probe", return_value=()))
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))

    def test_cancel_returns_success_before_runtime_or_baseline_writes(self) -> None:
        with mock.patch("builtins.input", return_value="q"), mock.patch.object(
            cli, "_xdg", side_effect=AssertionError("runtime setup reached after cancellation")
        ), mock.patch.object(cli, "_start_interactive_shell") as start:
            self.assertEqual(cli.main([]), 0)
        start.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [self.cwd])
        self.assertEqual(list(self.cwd.iterdir()), [])

    def test_child_directory_starts_controller_at_nearest_workspace(self) -> None:
        child = self.cwd / "src" / "robot_base"
        child.mkdir(parents=True)
        self.current_directory.return_value = str(child)
        with mock.patch("builtins.input", side_effect=AssertionError("unexpected prompt")), mock.patch.object(
            cli, "_xdg", return_value=self.paths
        ), mock.patch.object(cli, "_start_interactive_shell", return_value=17) as start:
            self.assertEqual(cli.main([]), 17)
        self.assertEqual(start.call_args.args[2]["LAZYROS_WORKSPACE"], str(self.cwd))
        self.assertEqual(list((self.paths.runtime / "sessions").iterdir()), [])

    def test_explicit_workspace_binding_bypasses_discovery(self) -> None:
        bound = self.root / "bound_ws"
        (bound / "src").mkdir(parents=True)
        (self.cwd / "src").mkdir()
        os.environ["LAZYROS_WORKSPACE"] = str(bound)
        with mock.patch.object(
            cli, "select_workspace", side_effect=AssertionError("explicit workspace was rediscovered")
        ), mock.patch.object(cli, "_xdg", return_value=self.paths), mock.patch.object(
            cli, "_start_interactive_shell", return_value=0
        ) as start:
            self.assertEqual(cli.main([]), 0)
        self.assertEqual(start.call_args.args[2]["LAZYROS_WORKSPACE"], str(bound))

    def test_controller_started_inside_cpp_package_keeps_the_enclosing_workspace(self) -> None:
        package = self.cwd / "src" / "navigation"
        descendant = package / "src" / "planner"
        descendant.mkdir(parents=True)
        (package / "package.xml").write_text("<package/>", encoding="utf-8")
        for directory in (package, descendant):
            with self.subTest(directory=directory):
                self.current_directory.return_value = str(directory)
                with mock.patch("builtins.input", side_effect=AssertionError("unexpected prompt")), mock.patch.object(
                    cli, "_xdg", return_value=self.paths
                ), mock.patch.object(cli, "_start_interactive_shell", return_value=0) as start:
                    self.assertEqual(cli.main([]), 0)
                self.assertEqual(start.call_args.args[2]["LAZYROS_WORKSPACE"], str(self.cwd))

    def test_explicit_cpp_package_binding_still_takes_precedence(self) -> None:
        package = self.cwd / "src" / "navigation"
        (package / "src").mkdir(parents=True)
        (package / "package.xml").write_text("<package/>", encoding="utf-8")
        os.environ["LAZYROS_WORKSPACE"] = str(package)
        with mock.patch.object(cli, "_xdg", return_value=self.paths), mock.patch.object(
            cli, "_start_interactive_shell", return_value=0
        ) as start:
            self.assertEqual(cli.main([]), 0)
        self.assertEqual(start.call_args.args[2]["LAZYROS_WORKSPACE"], str(package))

    def test_invalid_explicit_binding_does_not_switch_to_ancestor_or_prompt(self) -> None:
        child = self.cwd / "src" / "robot_base"
        child.mkdir(parents=True)
        os.environ["LAZYROS_WORKSPACE"] = str(child)
        with mock.patch.object(
            cli, "select_workspace", side_effect=AssertionError("explicit workspace was rediscovered")
        ), mock.patch("builtins.input", side_effect=AssertionError("unexpected prompt")), mock.patch.object(
            cli, "_xdg", side_effect=AssertionError("runtime setup reached for invalid binding")
        ):
            self.assertEqual(cli.main([]), 2)
        self.assertFalse(self.paths.runtime.exists())


if __name__ == "__main__":
    unittest.main()
