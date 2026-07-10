# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lazyros2.colcon import (  # noqa: E402
    ColconError,
    build_argv,
    discover_packages,
    find_missing_workspace_dependencies,
    test_argv as make_test_argv,
    test_result_argv as make_test_result_argv,
)
from lazyros2.paths import Workspace  # noqa: E402
from lazyros2.process import CommandResult, normalize_returncode, run_command  # noqa: E402


class ProcessTests(unittest.TestCase):
    def test_runs_literal_argv_without_shell_expansion(self) -> None:
        payload = "$(echo injected) * ' quoted"

        result = run_command(
            [sys.executable, "-c", "import sys; print(sys.argv[1])", payload],
            capture_output=True,
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout.strip(), payload)

    def test_normalizes_signal_return_code(self) -> None:
        self.assertEqual(normalize_returncode(-2), 130)
        self.assertEqual(normalize_returncode(7), 7)
        self.assertEqual(normalize_returncode(511), 255)

    def test_rejects_nul_in_argv(self) -> None:
        with self.assertRaisesRegex(ValueError, "NUL"):
            run_command(["echo", "bad\0argument"])


class ColconTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        (root / "src").mkdir()
        self.workspace = Workspace.open(root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_build_argv_pins_workspace_layout_and_selects_packages(self) -> None:
        argv = build_argv(self.workspace, ["navigation", "drivers"])

        self.assertEqual(
            argv,
            (
                "colcon",
                "--log-base",
                str(self.workspace.log),
                "build",
                "--base-paths",
                str(self.workspace.root),
                "--build-base",
                str(self.workspace.build),
                "--install-base",
                str(self.workspace.install),
                "--packages-select",
                "navigation",
                "drivers",
            ),
        )

    def test_build_up_to_uses_explicit_package_mode(self) -> None:
        argv = build_argv(self.workspace, ["navigation"], up_to=True)

        self.assertIn("--packages-up-to", argv)
        self.assertNotIn("--packages-select", argv)

    def test_rejects_layout_or_selection_passthrough(self) -> None:
        for forbidden in ("--build-base", "--base-paths=/tmp/src", "--packages-skip"):
            with self.subTest(forbidden=forbidden):
                with self.assertRaisesRegex(ValueError, "reserved"):
                    build_argv(self.workspace, (), passthrough=(forbidden, "value"))

    def test_rejects_invalid_package_name_before_colcon(self) -> None:
        with self.assertRaisesRegex(ValueError, "package name"):
            build_argv(self.workspace, ["not a package"])

    def test_test_commands_pin_layout_and_result_base(self) -> None:
        test_command = make_test_argv(self.workspace, ["drivers"])
        result_command = make_test_result_argv(self.workspace)

        self.assertIn("--packages-select", test_command)
        self.assertEqual(
            result_command,
            (
                "colcon",
                "test-result",
                "--test-result-base",
                str(self.workspace.build),
                "--verbose",
            ),
        )

    def test_discovers_sorted_unique_packages(self) -> None:
        def runner(*args: object, **kwargs: object) -> CommandResult:
            return CommandResult(("colcon",), 0, "zeta\nalpha\nalpha\n", "")

        packages = discover_packages(self.workspace, {}, runner=runner)

        self.assertEqual(packages, ("alpha", "zeta"))

    def test_discovery_failure_is_not_an_empty_workspace(self) -> None:
        def runner(*args: object, **kwargs: object) -> CommandResult:
            return CommandResult(("colcon",), 127, "", "not found")

        with self.assertRaisesRegex(ColconError, "colcon list failed"):
            discover_packages(self.workspace, {}, runner=runner)

    def test_reports_only_uninstalled_up_to_dependencies_as_evidence(self) -> None:
        marker = (
            self.workspace.install
            / "share"
            / "ament_index"
            / "resource_index"
            / "packages"
        )
        marker.mkdir(parents=True)
        (marker / "already_built").touch()

        def runner(*args: object, **kwargs: object) -> CommandResult:
            return CommandResult(
                ("colcon",),
                0,
                "already_built\nmissing_dep\ntarget_pkg\n",
                "",
            )

        evidence = find_missing_workspace_dependencies(
            self.workspace,
            ["target_pkg"],
            {},
            runner=runner,
        )

        self.assertEqual(evidence.up_to_packages, ("already_built", "missing_dep", "target_pkg"))
        self.assertEqual(evidence.missing_dependencies, ("missing_dep",))


if __name__ == "__main__":
    unittest.main()
