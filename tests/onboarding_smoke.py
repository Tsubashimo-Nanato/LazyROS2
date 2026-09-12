# SPDX-License-Identifier: AGPL-3.0-or-later
"""Check real colcon discovery and cancelled startup without filesystem writes."""

from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import shutil
import sys
import tempfile
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lazyros2 import cli  # noqa: E402
from lazyros2.onboarding import select_workspace  # noqa: E402
from lazyros2.paths import Workspace, WorkspaceError  # noqa: E402


@contextlib.contextmanager
def working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def main() -> int:
    if shutil.which("colcon") is None:
        raise SystemExit("onboarding smoke requires an existing colcon installation")
    with tempfile.TemporaryDirectory(prefix="lazy-onboarding-") as temporary:
        root = Path(temporary).resolve()
        candidate = root / "empty"
        candidate.mkdir()
        environment = dict(os.environ)
        environment.pop("LAZYROS_WORKSPACE", None)
        environment["COLCON_LOG_PATH"] = str(root / "unexpected-logs")
        with mock.patch.dict(os.environ, environment, clear=True):
            if cli._workspace_probe(candidate):
                raise AssertionError("empty directory unexpectedly contains packages")
            try:
                Workspace.open(candidate)
            except WorkspaceError:
                pass
            else:
                raise AssertionError("empty directory unexpectedly accepted as workspace")
            with working_directory(candidate), mock.patch(
                "sys.stdin.isatty", return_value=True
            ), mock.patch("builtins.input", return_value="q") as prompt, mock.patch.object(
                cli, "_xdg", side_effect=AssertionError("cancelled startup reached runtime setup")
            ), contextlib.redirect_stderr(io.StringIO()):
                if cli.main([]) != 0:
                    raise AssertionError("cancelled startup did not return success")
                prompt.assert_called_once()
        if list(candidate.iterdir()) or set(root.iterdir()) != {candidate}:
            raise AssertionError("workspace discovery or cancellation wrote files")
        workspace = root / "robot_ws"
        package = workspace / "src" / "lazy_onboarding_cpp"
        descendant = package / "src" / "planner"
        descendant.mkdir(parents=True)
        (package / "package.xml").write_text(
            '<package format="3"><name>lazy_onboarding_cpp</name><version>0.0.1</version>'
            '<description>Workspace discovery fixture</description>'
            '<maintainer email="test@example.com">LazyROS2</maintainer>'
            '<license>Apache-2.0</license><buildtool_depend>ament_cmake</buildtool_depend>'
            '<export><build_type>ament_cmake</build_type></export></package>',
            encoding="utf-8",
        )
        (package / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.8)\nproject(lazy_onboarding_cpp)\n"
            "find_package(ament_cmake REQUIRED)\nament_package()\n",
            encoding="utf-8",
        )
        with mock.patch.dict(os.environ, environment, clear=True):
            if tuple(cli._workspace_probe(workspace)) != ("lazy_onboarding_cpp",):
                raise AssertionError("real colcon did not recognize the C++ package fixture")
            for directory in (package, descendant):
                with working_directory(directory):
                    if select_workspace(Path.cwd(), cli._workspace_probe).root != workspace:
                        raise AssertionError(f"package-local src was mistaken for a workspace: {directory}")
        if (root / "unexpected-logs").exists() or (workspace / "log").exists():
            raise AssertionError("C++ workspace discovery wrote log files")
    print("Real-colcon onboarding smoke passed; cancellation is clean and C++ package boundaries are correct.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
