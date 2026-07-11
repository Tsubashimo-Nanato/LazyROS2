# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def usable_bash() -> str:
    bash = shutil.which("bash")
    if bash is None:
        raise unittest.SkipTest("bash is not installed")

    version = subprocess.run(
        [bash, "--version"],
        capture_output=True,
        check=False,
        text=True,
        timeout=5,
    )
    if version.returncode != 0 or "GNU bash" not in version.stdout:
        raise unittest.SkipTest("the available bash launcher has no usable GNU bash")
    return bash


def write_fake_lazy(bin_dir: Path) -> Path:
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
    return launcher


def shell_environment(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    workspace = tmp_path / "robot ws"
    install = workspace / "install"
    bin_dir.mkdir()
    install.mkdir(parents=True)
    write_fake_lazy(bin_dir)

    log_file = tmp_path / "lazy.log"
    log_file.touch()
    history_file = tmp_path / "history" / "workspace.bash_history"
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


def run_bash(bash: str, script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [bash, "--noprofile", "--norc", "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )


class BashShellTests(unittest.TestCase):
    def test_init_uses_explicit_shell_mode_and_forwards_arguments(self) -> None:
        bash = usable_bash()
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            env = shell_environment(tmp_path)
            init = ROOT / "shell" / "lazy-init.bash"

            result = run_bash(
                bash,
                f'source "{init}"\nlazy\nlazy build "package one"',
                env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            lines = Path(env["LAZYROS_TEST_LOG"]).read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(lines, ["--shell bash", "build package one"])

    def test_control_build_does_not_reload_overlay_before_task_finishes(self) -> None:
        bash = usable_bash()
        for build_status in ("0", "19"):
            with self.subTest(build_status=build_status):
                with tempfile.TemporaryDirectory() as temp_dir:
                    tmp_path = Path(temp_dir)
                    env = shell_environment(tmp_path)
                    env["LAZYROS_TEST_BUILD_STATUS"] = build_status
                    source_count = tmp_path / "overlay-count"
                    env["LAZYROS_TEST_OVERLAY_COUNT"] = str(source_count)

                    setup = (
                        Path(env["LAZYROS_WORKSPACE"])
                        / "install"
                        / "local_setup.bash"
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
                    control = ROOT / "shell" / "lazy-control.bash"

                    result = run_bash(
                        bash,
                        f'source "{control}"\nbuild example_pkg',
                        env,
                    )

                    self.assertEqual(
                        int(source_count.read_text(encoding="utf-8")),
                        1,
                    )
                    self.assertEqual(result.returncode, int(build_status))

    def test_control_forwards_arguments_without_location_or_separator(self) -> None:
        bash = usable_bash()
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            env = shell_environment(tmp_path)
            control = ROOT / "shell" / "lazy-control.bash"

            result = run_bash(
                bash,
                f'source "{control}"\nrun demo talker "arg with space"',
                env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            lines = Path(env["LAZYROS_TEST_LOG"]).read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(
                lines,
                ["run demo talker arg with space"],
            )

    def test_task_does_not_execute_restart_history_text(self) -> None:
        bash = usable_bash()
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            env = shell_environment(tmp_path)
            env.update(
                {
                    "LAZYROS_JOB_ID": "job-7",
                    "LAZYROS_JOB_NUMBER": "7",
                    "LAZYROS_RESTART_LINE": "printf injected > should-not-exist",
                    "NO_COLOR": "1",
                }
            )
            task = ROOT / "shell" / "lazy-task.bash"
            marker = Path(env["LAZYROS_WORKSPACE"]) / "should-not-exist"

            result = run_bash(
                bash,
                f'source "{task}"\nrun demo talker',
                env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists())
            self.assertEqual(
                Path(env["LAZYROS_TEST_LOG"])
                .read_text(encoding="utf-8")
                .splitlines(),
                ["__job-run job-7", "run demo talker"],
            )


if __name__ == "__main__":
    unittest.main()
