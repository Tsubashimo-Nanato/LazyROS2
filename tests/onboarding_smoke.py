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
from lazyros2.paths import Workspace, WorkspaceError  # noqa: E402


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
            with mock.patch.object(cli.os, "getcwd", return_value=str(candidate)), mock.patch(
                "sys.stdin.isatty", return_value=True
            ), mock.patch("builtins.input", return_value="q"), contextlib.redirect_stderr(io.StringIO()):
                if cli.main([]) != 0:
                    raise AssertionError("cancelled startup did not return success")
        if list(candidate.iterdir()) or set(root.iterdir()) != {candidate}:
            raise AssertionError("workspace discovery or cancellation wrote files")
    print("Real-colcon onboarding smoke passed; cancellation left no files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
