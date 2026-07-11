# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def usable_zsh() -> str:
    zsh = shutil.which("zsh")
    if zsh is None:
        raise unittest.SkipTest("zsh is not installed")

    version = subprocess.run(
        [zsh, "--version"],
        capture_output=True,
        check=False,
        text=True,
        timeout=5,
    )
    if version.returncode != 0 or not version.stdout.startswith("zsh "):
        raise unittest.SkipTest("the available zsh launcher is not usable")
    return zsh


def write_fake_lazy(bin_dir: Path) -> None:
    launcher = bin_dir / "lazy"
    launcher.write_text(
        "#!/bin/sh\n"
        "if [ \"${1-}\" = __setup-path ]; then\n"
        "    printf '%s\\n' \"${LAZYROS_TEST_SETUP_FILE:-}\"\n"
        "    exit 0\n"
        "fi\n"
        "if [ \"${1-}\" = __job-claim ]; then\n"
        "    exit 0\n"
        "fi\n"
        "printf '%s\\n' \"$*\" >> \"$LAZYROS_TEST_LOG\"\n"
        "if [ \"${1-}\" = build ]; then\n"
        "    exit \"${LAZYROS_TEST_BUILD_STATUS:-0}\"\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)


def shell_environment(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    workspace = tmp_path / "robot ws"
    install = workspace / "install"
    bin_dir.mkdir()
    install.mkdir(parents=True)
    write_fake_lazy(bin_dir)

    log_file = tmp_path / "lazy.log"
    log_file.touch()
    history_file = tmp_path / "history" / "workspace.zsh_history"
    history_file.parent.mkdir()
    history_file.touch()

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
            "LAZYROS_HISTORY_FILE": str(history_file),
            "LAZYROS_TEST_LOG": str(log_file),
            "LAZYROS_WORKSPACE": str(workspace),
            "TERM": "dumb",
        }
    )
    return env


def run_zsh(zsh: str, script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [zsh, "-d", "-f", "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )


class ZshShellTests(unittest.TestCase):
    def test_init_uses_explicit_zsh_mode_and_forwards_arguments(self) -> None:
        zsh = usable_zsh()
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            env = shell_environment(tmp_path)
            init = ROOT / "shell" / "lazy-init.zsh"

            result = run_zsh(
                zsh,
                f'source "{init}"\nlazy\nlazy build "package one"',
                env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            lines = Path(env["LAZYROS_TEST_LOG"]).read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(lines, ["--shell zsh", "build package one"])

    def test_control_forwards_without_location_and_does_not_reload_after_build(self) -> None:
        zsh = usable_zsh()
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            env = shell_environment(tmp_path)
            source_count = tmp_path / "overlay-count"
            env["LAZYROS_TEST_OVERLAY_COUNT"] = str(source_count)
            setup = (
                Path(env["LAZYROS_WORKSPACE"])
                / "install"
                / "local_setup.zsh"
            )
            setup.write_text(
                "count=0\n"
                "if [[ -f $LAZYROS_TEST_OVERLAY_COUNT ]]; then\n"
                "    read -r count < \"$LAZYROS_TEST_OVERLAY_COUNT\"\n"
                "fi\n"
                "printf '%s\\n' \"$((count + 1))\" > \"$LAZYROS_TEST_OVERLAY_COUNT\"\n",
                encoding="utf-8",
            )
            env["LAZYROS_TEST_SETUP_FILE"] = str(setup)
            control = ROOT / "shell" / "lazy-control.zsh"

            result = run_zsh(
                zsh,
                f'source "{control}"\nrun demo talker "arg with space"\n'
                "build demo",
                env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(source_count.read_text(encoding="utf-8").strip(), "1")
            self.assertEqual(
                Path(env["LAZYROS_TEST_LOG"])
                .read_text(encoding="utf-8")
                .splitlines(),
                ["run demo talker arg with space", "build demo"],
            )

    def test_task_keeps_restart_text_in_memory_and_forwards_directly(self) -> None:
        zsh = usable_zsh()
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            env = shell_environment(tmp_path)
            env.update(
                {
                    "LAZYROS_JOB_ID": "job-8",
                    "LAZYROS_JOB_NUMBER": "8",
                    "LAZYROS_RESTART_LINE": "touch should-not-exist",
                    "NO_COLOR": "1",
                }
            )
            task = ROOT / "shell" / "lazy-task.zsh"
            marker = Path(env["LAZYROS_WORKSPACE"]) / "should-not-exist"

            result = run_zsh(
                zsh,
                f'source "{task}"\nrun demo talker',
                env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists())
            self.assertEqual(
                Path(env["LAZYROS_TEST_LOG"])
                .read_text(encoding="utf-8")
                .splitlines(),
                ["__job-run job-8", "run demo talker"],
            )


if __name__ == "__main__":
    unittest.main()
