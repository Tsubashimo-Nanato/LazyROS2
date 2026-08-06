# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lazyros2.environment import (  # noqa: E402
    OverlayEnvironmentError,
    capture_overlay_environment,
    find_self_overlay,
    validated_setup_script,
)
from lazyros2.paths import Workspace, WorkspaceError, XdgPaths  # noqa: E402


class XdgPathsTests(unittest.TestCase):
    def test_uses_xdg_directories_and_private_runtime_root(self) -> None:
        env = {
            "XDG_CONFIG_HOME": "/cfg",
            "XDG_STATE_HOME": "/state",
            "XDG_CACHE_HOME": "/cache",
            "XDG_RUNTIME_DIR": "/run/user/1000",
        }

        paths = XdgPaths.from_environment(env, home=Path("/home/tester"))

        self.assertEqual(paths.config, Path("/cfg/lazyros2"))
        self.assertEqual(paths.state, Path("/state/lazyros2"))
        self.assertEqual(paths.cache, Path("/cache/lazyros2"))
        self.assertEqual(paths.runtime, Path("/run/user/1000/lazyros2"))

    def test_relative_xdg_values_fall_back_to_home(self) -> None:
        paths = XdgPaths.from_environment(
            {
                "XDG_CONFIG_HOME": "relative",
                "XDG_STATE_HOME": "relative",
                "XDG_CACHE_HOME": "relative",
                "XDG_RUNTIME_DIR": "relative",
            },
            home=Path("/home/tester"),
            uid=1010,
        )

        self.assertEqual(paths.config, Path("/home/tester/.config/lazyros2"))
        self.assertEqual(paths.state, Path("/home/tester/.local/state/lazyros2"))
        self.assertEqual(paths.cache, Path("/home/tester/.cache/lazyros2"))
        self.assertEqual(
            paths.runtime,
            Path(tempfile.gettempdir()) / "lazyros2-1010",
        )


class WorkspaceTests(unittest.TestCase):
    def test_accepts_workspace_with_empty_src_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src").mkdir()

            workspace = Workspace.open(root)

            self.assertEqual(workspace.root, root.resolve())
            self.assertEqual(workspace.build, root.resolve() / "build")
            self.assertEqual(workspace.install, root.resolve() / "install")
            self.assertEqual(workspace.log, root.resolve() / "log")

    def test_accepts_package_discovered_outside_src(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            workspace = Workspace.open(root, package_probe=lambda _: ("robot_base",))

            self.assertEqual(workspace.root, root.resolve())

    def test_rejects_directory_without_src_or_discovered_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(WorkspaceError, "not a ROS 2 workspace"):
                Workspace.open(Path(temp_dir), package_probe=lambda _: ())


class OverlayEnvironmentTests(unittest.TestCase):
    def test_detects_workspace_prefix_in_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src").mkdir()
            workspace = Workspace.open(root)
            env = {
                "AMENT_PREFIX_PATH": os.pathsep.join(
                    ["/opt/ros/jazzy", str(workspace.install)]
                )
            }

            hits = find_self_overlay(workspace, env)

            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].variable, "AMENT_PREFIX_PATH")
            self.assertEqual(hits[0].path, workspace.install)

    def test_ignores_similarly_named_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src").mkdir()
            workspace = Workspace.open(root)
            env = {"AMENT_PREFIX_PATH": f"{workspace.install}-old"}

            self.assertEqual(find_self_overlay(workspace, env), ())

    @unittest.skipIf(os.name == "nt", "overlay setup scripts require bash semantics")
    def test_captures_environment_from_fixed_local_setup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src").mkdir()
            (root / "install").mkdir()
            setup = root / "install" / "local_setup.sh"
            setup.write_text("export LAZYROS2_TEST_VALUE='overlay ready'\n", encoding="utf-8")
            workspace = Workspace.open(root)

            overlay = capture_overlay_environment(workspace, {"PATH": os.environ["PATH"]})

            self.assertEqual(overlay["LAZYROS2_TEST_VALUE"], "overlay ready")

    def test_rejects_capture_when_baseline_contains_self_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src").mkdir()
            (root / "install").mkdir()
            (root / "install" / "local_setup.sh").write_text("", encoding="utf-8")
            workspace = Workspace.open(root)

            with self.assertRaisesRegex(OverlayEnvironmentError, "already contains"):
                capture_overlay_environment(
                    workspace,
                    {"AMENT_PREFIX_PATH": str(workspace.install)},
                )

    def test_rejects_setup_symlink_escaping_install_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src").mkdir()
            (root / "install").mkdir()
            outside = root / "outside.sh"
            outside.write_text("export BAD=1\n", encoding="utf-8")
            setup = root / "install" / "local_setup.sh"
            try:
                setup.symlink_to(outside)
            except OSError:
                self.skipTest("symlinks are unavailable")
            workspace = Workspace.open(root)

            with self.assertRaisesRegex(OverlayEnvironmentError, "escapes"):
                capture_overlay_environment(workspace, {"PATH": os.environ.get("PATH", "")})

    def test_rejects_shell_specific_setup_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src").mkdir()
            (root / "install").mkdir()
            outside = root / "outside.bash"
            outside.write_text("export BAD=1\n", encoding="utf-8")
            try:
                (root / "install" / "local_setup.bash").symlink_to(outside)
            except OSError:
                self.skipTest("symlinks are unavailable")
            workspace = Workspace.open(root)

            with self.assertRaisesRegex(OverlayEnvironmentError, "escapes"):
                validated_setup_script(workspace, "bash")

    def test_reports_setup_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src").mkdir()
            (root / "install").mkdir()
            (root / "install" / "local_setup.sh").write_text("", encoding="utf-8")
            workspace = Workspace.open(root)

            process = mock.Mock(pid=123, returncode=None)
            process.wait.side_effect = subprocess.TimeoutExpired(("bash",), 10.0)
            with mock.patch(
                "lazyros2.environment.subprocess.Popen",
                return_value=process,
            ), mock.patch("lazyros2.environment._stop_overlay_process"):
                with self.assertRaisesRegex(OverlayEnvironmentError, "timed out"):
                    capture_overlay_environment(workspace, {})
            process.wait.assert_called_once_with(timeout=10.0)


if __name__ == "__main__":
    unittest.main()
