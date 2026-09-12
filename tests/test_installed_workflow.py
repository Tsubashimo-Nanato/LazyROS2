# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exercise the shipped installer and real payload together in a disposable home."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "posix", "installed shell workflow requires Linux")
class InstalledWorkflowTests(unittest.TestCase):
    def test_real_payload_installs_runs_and_uninstalls_with_user_files_intact(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("the real installer deliberately rejects root")
        with tempfile.TemporaryDirectory(prefix="lazyros2-installed-") as temporary:
            root = Path(temporary)
            home = root / "home"
            workspace = root / "robot ws"
            home.mkdir()
            (workspace / "src").mkdir(parents=True)
            sentinel = home / "keep.txt"
            sentinel.write_text("user file\n", encoding="utf-8")
            rc = home / ".bashrc"
            rc.write_text("# user shell settings\n", encoding="utf-8")
            env = {
                name: value for name, value in os.environ.items()
                if not name.startswith("LAZYROS_")
            }
            env.update({
                "HOME": str(home),
                "SHELL": "/bin/bash",
                "XDG_CONFIG_HOME": str(root / "config"),
                "XDG_STATE_HOME": str(root / "state"),
                "XDG_CACHE_HOME": str(root / "cache"),
                "XDG_RUNTIME_DIR": str(root / "runtime"),
            })
            env.pop("PYTHONDONTWRITEBYTECODE", None)

            def run(*argv: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    argv, cwd=workspace, env=env, text=True,
                    capture_output=True, timeout=30, check=False,
                )

            installed = run("sh", str(ROOT / "install.sh"))
            self.assertEqual(installed.returncode, 0, installed.stderr)
            launcher = str(home / ".local/bin/lazy")
            version = run(launcher, "version")
            self.assertEqual(version.returncode, 0, version.stderr)
            expected = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
            self.assertEqual(version.stdout, f"lazy {expected}\n")

            # Load the actual installed rc fragment and resolve the launcher via PATH.
            shell = run("bash", "--noprofile", "--norc", "-c", '. "$HOME/.bashrc"; lazy version')
            self.assertEqual(shell.returncode, 0, shell.stderr)
            self.assertEqual(shell.stdout, version.stdout)
            help_result = run(launcher, "help", "build")
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertIn("build", help_result.stdout)
            status = run(launcher, "status")
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertIn(str(workspace), status.stdout)
            payload = home / ".local/lib/lazyros2" / expected
            self.assertEqual(list(payload.rglob("__pycache__")), [])

            # Ordinary use must not create unmanaged bytecode that blocks the
            # installer's next manifest check.
            repeated = run("sh", str(ROOT / "install.sh"))
            self.assertEqual(repeated.returncode, 0, repeated.stderr)

            removed = run(launcher, "uninstall")
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertFalse(Path(launcher).exists())
            self.assertFalse(payload.exists())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "user file\n")
            self.assertEqual(rc.read_text(encoding="utf-8"), "# user shell settings\n")
            purged = run("sh", str(ROOT / "install.sh"), "--uninstall", "--purge", "--yes")
            self.assertEqual(purged.returncode, 0, purged.stderr)
            for name in ("config", "state", "cache", "runtime"):
                self.assertFalse((root / name / "lazyros2").exists())


if __name__ == "__main__":
    unittest.main()
