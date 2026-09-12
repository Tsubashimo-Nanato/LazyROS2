# SPDX-License-Identifier: AGPL-3.0-or-later

"""PTY-level task-shell tests using only the Python standard library."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

try:
    import pty
except ImportError:  # pragma: no cover - exercised by non-POSIX CI hosts
    pty = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]
PROMPT = b"[lazy job #07 | ros:test]"
WINDOW_TITLE = "robot_ws · #07 · run · demo"
OSC_APPLY = (
    b"\x1b]11;#14213D\x07"
    b"\x1b]10;#E6EDF3\x07"
    b"\x1b]12;#7AA2F7\x07"
    + b"\x1b]0;"
    + WINDOW_TITLE.encode("utf-8")
    + b"\x07"
)
OSC_RESTORE = b"\x1b]111\x07\x1b]110\x07\x1b]112\x07\x1b]0;\x07"


class PtyShell:
    """Small expect-like wrapper around a real controlling terminal."""

    def __init__(self, argv: list[str], *, cwd: Path, env: dict[str, str]) -> None:
        if pty is None:
            raise unittest.SkipTest("the pty module is unavailable")

        pid, master_fd = pty.fork()
        if pid == 0:  # pragma: no branch - the child replaces itself immediately
            try:
                os.chdir(cwd)
                os.execve(argv[0], argv, env)
            except BaseException as error:  # pragma: no cover - diagnostic path
                os.write(2, f"PTY exec failed: {error}\n".encode("utf-8", "replace"))
                os._exit(127)

        self.pid = pid
        self.master_fd = master_fd
        self.output = bytearray()
        self._expect_offset = 0
        self._eof = False
        self._status: int | None = None

    def __enter__(self) -> "PtyShell":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def send(self, data: bytes) -> None:
        os.write(self.master_fd, data)

    def read_for(self, duration: float) -> bytes:
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            self._read_once(min(0.05, deadline - time.monotonic()))
            self._poll()
        return bytes(self.output)

    def expect(self, needle: bytes, timeout: float = 8.0) -> bytes:
        deadline = time.monotonic() + timeout
        while True:
            index = self.output.find(needle, self._expect_offset)
            if index >= 0:
                end = index + len(needle)
                matched = bytes(self.output[self._expect_offset : end])
                self._expect_offset = end
                return matched

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._fail_expectation(needle)
            self._read_once(min(remaining, 0.25))
            self._poll()
            if self._status is not None and self._eof:
                self._fail_expectation(needle)

    def wait(self, timeout: float = 8.0) -> int:
        deadline = time.monotonic() + timeout
        while self._status is None:
            self._read_once(min(max(deadline - time.monotonic(), 0.0), 0.1))
            self._poll()
            if time.monotonic() >= deadline and self._status is None:
                raise AssertionError(
                    f"PTY child did not exit; output={bytes(self.output)!r}"
                )

        for _ in range(5):
            if self._eof:
                break
            self._read_once(0.02)
        return os.waitstatus_to_exitcode(self._status)

    def close(self) -> None:
        try:
            self._poll()
            if self._status is None:
                try:
                    os.killpg(self.pid, signal.SIGHUP)
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + 1.0
                while self._status is None and time.monotonic() < deadline:
                    self._read_once(0.05)
                    self._poll()
            if self._status is None:
                try:
                    os.killpg(self.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                _, self._status = os.waitpid(self.pid, 0)
        finally:
            try:
                os.close(self.master_fd)
            except OSError:
                pass

    def _read_once(self, timeout: float) -> None:
        if self._eof:
            if timeout > 0:
                time.sleep(min(timeout, 0.01))
            return
        readable, _, _ = select.select([self.master_fd], [], [], max(timeout, 0.0))
        if not readable:
            return
        try:
            chunk = os.read(self.master_fd, 65536)
        except OSError as error:
            if error.errno == errno.EIO:
                self._eof = True
                return
            raise
        if chunk:
            self.output.extend(chunk)
        else:
            self._eof = True

    def _poll(self) -> None:
        if self._status is not None:
            return
        waited_pid, status = os.waitpid(self.pid, os.WNOHANG)
        if waited_pid:
            self._status = status

    def _fail_expectation(self, needle: bytes) -> None:
        raise AssertionError(
            f"did not observe {needle!r}; output={bytes(self.output)!r}"
        )


@unittest.skipUnless(os.name == "posix" and pty is not None, "requires POSIX PTYs")
class TaskShellPtyTests(unittest.TestCase):
    maxDiff = None

    def _shell(self, shell_name: str) -> str:
        executable = shutil.which(shell_name)
        if executable is None:
            self.skipTest(f"{shell_name} is not installed")
        return str(Path(executable).resolve())

    def _fixture(
        self,
        temp_path: Path,
        *,
        job_rc: int = 0,
        block: bool = False,
        colors: bool = False,
        overrides: dict[str, str | None] | None = None,
    ) -> tuple[Path, dict[str, str]]:
        workspace = temp_path / "robot_ws"
        workspace.mkdir()
        fake_bin = temp_path / "bin"
        fake_bin.mkdir()
        state_file = temp_path / "run-count"
        fake_lazy = fake_bin / "lazy"
        fake_lazy.write_text(
            """#!/bin/sh
case ${1-} in
    __setup-path)
        printf '%s\\n' "${LAZYROS_TEST_SETUP_FILE:-}"
        exit 0
        ;;
    __job-claim)
        printf 'CLAIM:%s:%s\\n' "${2-}" "${3-}"
        printf '%s\\n' "${3-}" > "$LAZYROS_TEST_CLAIM_STATE"
        exit 0
        ;;
    __job-finish)
        claimed=
        if [ -r "$LAZYROS_TEST_CLAIM_STATE" ]; then
            IFS= read -r claimed < "$LAZYROS_TEST_CLAIM_STATE" || claimed=
        fi
        if [ "$#" -ne 4 ] || [ "${2-}" != "$LAZYROS_JOB_ID" ] || \\
           [ "${3-}" != "$claimed" ] || [ "${3-}" != "$PPID" ]; then
            printf 'BAD_FINISH:%s\\n' "$*" >&2
            exit 65
        fi
        printf 'idle:%s:%s\\n' "${4-}" "${3-}" > "$LAZYROS_TEST_FINISH_STATE"
        printf 'FINISH:%s:%s:%s\\n' "${2-}" "${3-}" "${4-}"
        exit 0
        ;;
    __job-run)
        count=0
        if [ -r "$LAZYROS_TEST_STATE" ]; then
            IFS= read -r count < "$LAZYROS_TEST_STATE" || count=0
        fi
        count=$((count + 1))
        printf '%s\\n' "$count" > "$LAZYROS_TEST_STATE"
        printf 'RUN:%s\\n' "$count"
        if [ "${LAZYROS_TEST_BLOCK:-0}" = 1 ] && [ "$count" -eq 1 ]; then
            exec "$LAZYROS_TEST_PYTHON" -c '
import os
import signal

def stop(_signum, _frame):
    os.write(1, b"JOB_INT\\n")
    os._exit(130)

signal.signal(signal.SIGINT, stop)
os.write(1, b"JOB_WAITING\\n")
signal.pause()
'
        fi
        exit "${LAZYROS_TEST_JOB_RC:-0}"
        ;;
    *)
        printf 'UNEXPECTED:%s\\n' "$*" >&2
        exit 64
        ;;
esac
""",
            encoding="utf-8",
        )
        fake_lazy.chmod(0o755)

        env = dict(os.environ)
        env.update(
            {
                "HOME": str(temp_path / "home"),
                "PATH": str(fake_bin) + os.pathsep + env.get("PATH", ""),
                "TERM": "xterm-256color",
                "ROS_DISTRO": "test",
                "LAZYROS_WORKSPACE": str(workspace),
                "LAZYROS_JOB_ID": "job-7",
                "LAZYROS_JOB_NUMBER": "7",
                "LAZYROS_RESTART_LINE": "lazy __job-run job-7",
                "LAZYROS_TEST_STATE": str(state_file),
                "LAZYROS_TEST_CLAIM_STATE": str(temp_path / "claim-state"),
                "LAZYROS_TEST_FINISH_STATE": str(temp_path / "finish-state"),
                "LAZYROS_TEST_JOB_RC": str(job_rc),
                "LAZYROS_TEST_BLOCK": "1" if block else "0",
                "LAZYROS_TEST_PYTHON": sys.executable,
                "LAZYROS_WINDOW_TITLE": WINDOW_TITLE,
            }
        )
        for name in ("NO_COLOR", "TMUX"):
            env.pop(name, None)
        if colors:
            env.update(
                {
                    "LAZYROS_COLOR_BACKGROUND": "#14213D",
                    "LAZYROS_COLOR_FOREGROUND": "#E6EDF3",
                    "LAZYROS_COLOR_ACCENT": "#7AA2F7",
                }
            )
        else:
            env["NO_COLOR"] = "1"
        for name, value in (overrides or {}).items():
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
        return workspace, env

    def _argv(
        self, shell_name: str, shell: str, temp_path: Path, env: dict[str, str]
    ) -> list[str]:
        task = ROOT / "shell" / f"lazy-task.{shell_name}"
        if shell_name == "bash":
            return [shell, "--noprofile", "--rcfile", str(task), "-i"]

        zdotdir = temp_path / "zdotdir"
        zdotdir.mkdir(mode=0o700)
        (zdotdir / ".zshrc").write_text(
            f"source {shlex.quote(str(task))}\n", encoding="utf-8"
        )
        env["ZDOTDIR"] = str(zdotdir)
        return [shell, "-d", "-i"]

    def _spawn(
        self,
        shell_name: str,
        temp_path: Path,
        *,
        job_rc: int = 0,
        block: bool = False,
        colors: bool = False,
        overrides: dict[str, str | None] | None = None,
    ) -> PtyShell:
        shell = self._shell(shell_name)
        workspace, env = self._fixture(
            temp_path,
            job_rc=job_rc,
            block=block,
            colors=colors,
            overrides=overrides,
        )
        return PtyShell(
            self._argv(shell_name, shell, temp_path, env), cwd=workspace, env=env
        )

    def _assert_first_job_leaves_shell_open(self, shell_name: str) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self._spawn(shell_name, Path(temp_dir), job_rc=23) as child:
                child.expect(b"RUN:1")
                child.expect(PROMPT)
                child.send(b"printf 'ALIVE:%s\\n' \"$?\"\r")
                child.expect(b"ALIVE:23")
                child.send(b"exit\r")
                self.assertEqual(child.wait(), 0, bytes(child.output))

    def test_bash_first_job_failure_leaves_task_shell_open(self) -> None:
        self._assert_first_job_leaves_shell_open("bash")

    def test_zsh_first_job_failure_leaves_task_shell_open(self) -> None:
        self._assert_first_job_leaves_shell_open("zsh")

    def _assert_overlay_failure_finishes_idle(self, shell_name: str) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            setup = temp_path / "failing-setup"
            setup.write_text("return 1\n", encoding="utf-8")
            with self._spawn(
                shell_name,
                temp_path,
                overrides={"LAZYROS_TEST_SETUP_FILE": str(setup)},
            ) as child:
                child.expect(b"failed to load workspace overlay")
                child.expect(b"FINISH:job-7:")
                child.expect(PROMPT)
                child.send(b"printf 'STATUS:%s\\n' \"$?\"\r")
                child.expect(b"STATUS:1")
                child.send(b"exit\r")
                self.assertEqual(child.wait(), 0, bytes(child.output))

            claimed = (temp_path / "claim-state").read_text().strip()
            finished = (temp_path / "finish-state").read_text().strip()
            self.assertEqual(finished, f"idle:1:{claimed}")

    def test_bash_overlay_failure_marks_job_idle(self) -> None:
        self._assert_overlay_failure_finishes_idle("bash")

    def test_zsh_overlay_failure_marks_job_idle(self) -> None:
        self._assert_overlay_failure_finishes_idle("zsh")

    def _assert_ctrl_c_returns_130_and_prompt(self, shell_name: str) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self._spawn(shell_name, Path(temp_dir), block=True) as child:
                child.expect(b"JOB_WAITING")
                child.send(b"\x03")
                child.expect(b"JOB_INT")
                child.expect(PROMPT)
                child.send(b"printf 'STATUS:%s\\n' \"$?\"\r")
                child.expect(b"STATUS:130")
                child.send(b"exit\r")
                self.assertEqual(child.wait(), 0, bytes(child.output))

    def test_bash_ctrl_c_returns_130_and_keeps_prompt(self) -> None:
        self._assert_ctrl_c_returns_130_and_prompt("bash")

    def test_zsh_ctrl_c_returns_130_and_keeps_prompt(self) -> None:
        self._assert_ctrl_c_returns_130_and_prompt("zsh")

    def _assert_up_arrow_restarts_from_memory_history(self, shell_name: str) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with self._spawn(shell_name, temp_path) as child:
                child.expect(b"RUN:1")
                child.expect(PROMPT)
                child.send(b"\x1b[A\r")
                child.expect(b"RUN:2")
                child.expect(PROMPT)
                child.send(b"exit\r")
                self.assertEqual(child.wait(), 0, bytes(child.output))
            self.assertEqual((temp_path / "run-count").read_text().strip(), "2")
            self.assertFalse((temp_path / "home" / ".bash_history").exists())
            self.assertFalse((temp_path / "home" / ".zsh_history").exists())

    def test_bash_up_arrow_restarts_job_from_memory_history(self) -> None:
        self._assert_up_arrow_restarts_from_memory_history("bash")

    def test_zsh_up_arrow_restarts_job_from_memory_history(self) -> None:
        self._assert_up_arrow_restarts_from_memory_history("zsh")

    def _assert_osc_is_tty_only_and_restored(self, shell_name: str) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self._spawn(shell_name, Path(temp_dir), colors=True) as child:
                child.expect(OSC_APPLY)
                child.expect(PROMPT)
                child.send(b"exit\r")
                child.expect(OSC_RESTORE)
                self.assertEqual(child.wait(), 0, bytes(child.output))

    def test_bash_tty_applies_and_restores_osc_colors(self) -> None:
        self._assert_osc_is_tty_only_and_restored("bash")

    def test_zsh_tty_applies_and_restores_osc_colors(self) -> None:
        self._assert_osc_is_tty_only_and_restored("zsh")

    def _assert_osc_guards(self, shell_name: str) -> None:
        cases = {
            "no_color": {"NO_COLOR": "", "TMUX": None, "TERM": "xterm-256color"},
            "tmux": {"NO_COLOR": None, "TMUX": "/tmp/tmux,1,0", "TERM": "xterm-256color"},
            "dumb": {"NO_COLOR": None, "TMUX": None, "TERM": "dumb"},
            "unknown": {"NO_COLOR": None, "TMUX": None, "TERM": "vt100"},
        }
        for case_name, overrides in cases.items():
            with self.subTest(shell=shell_name, guard=case_name):
                with tempfile.TemporaryDirectory() as temp_dir:
                    with self._spawn(
                        shell_name,
                        Path(temp_dir),
                        colors=True,
                        overrides=overrides,
                    ) as child:
                        child.expect(b"RUN:1")
                        child.expect(PROMPT)
                        child.send(b"exit\r")
                        self.assertEqual(child.wait(), 0, bytes(child.output))
                        self.assertNotIn(b"\x1b]", child.output)

    def test_bash_disables_osc_for_no_color_tmux_dumb_and_unknown_term(self) -> None:
        self._assert_osc_guards("bash")

    def test_zsh_disables_osc_for_no_color_tmux_dumb_and_unknown_term(self) -> None:
        self._assert_osc_guards("zsh")

    def _assert_non_tty_has_no_osc(self, shell_name: str) -> None:
        shell = self._shell(shell_name)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            workspace, env = self._fixture(temp_path, colors=True)
            task = ROOT / "shell" / f"lazy-task.{shell_name}"
            source = f"source {shlex.quote(str(task))}"
            if shell_name == "bash":
                argv = [shell, "--noprofile", "--norc", "-c", source]
            else:
                argv = [shell, "-d", "-f", "-c", source]
            result = subprocess.run(
                argv,
                cwd=workspace,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=8,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(b"RUN:1", result.stdout)
            self.assertNotIn(b"\x1b]", result.stdout + result.stderr)

    def test_bash_non_tty_never_emits_osc(self) -> None:
        self._assert_non_tty_has_no_osc("bash")

    def test_zsh_non_tty_never_emits_osc(self) -> None:
        self._assert_non_tty_has_no_osc("zsh")


@unittest.skipUnless(os.name == "posix" and pty is not None, "requires POSIX PTYs")
class ZshCompletionPtyTests(unittest.TestCase):
    PROMPT = b"ZCOMP> "

    def _shell(self) -> str:
        executable = shutil.which("zsh")
        if executable is None:
            self.skipTest("zsh is not installed")
        return str(Path(executable).resolve())

    def _spawn(self, temp_path: Path, integration: str) -> tuple[PtyShell, Path]:
        workspace = temp_path / "robot_ws"
        workspace.mkdir()
        (workspace / "src").mkdir()
        home = temp_path / "home"
        home.mkdir()
        history = temp_path / "history"
        history.touch()
        mode_file = temp_path / "completion-mode"
        mode_file.write_text("zero\n", encoding="utf-8")
        fake_bin = temp_path / "bin"
        fake_bin.mkdir()
        fake_lazy = fake_bin / "lazy"
        fake_lazy.write_text(
            """#!/bin/sh
case ${1-} in
    __setup-path)
        exit 0
        ;;
    __complete)
        mode=$(sed -n '1p' "$LAZYROS_TEST_COMPLETION_MODE")
        case $mode in
            one) printf 'build\\n' ;;
            many) printf 'alpha\\nbeta\\n' ;;
            common) printf 'alpha\\nalpine\\n' ;;
            zero) ;;
            *) exit 64 ;;
        esac
        exit 0
        ;;
    __select)
        PYTHONPATH="$LAZYROS_TEST_SOURCE" \
            exec "$LAZYROS_TEST_PYTHON" -m lazyros2 "$@"
        ;;
    *)
        printf 'EXEC:%s\\n' "$*"
        exit 0
        ;;
esac
""",
            encoding="utf-8",
        )
        fake_lazy.chmod(0o755)
        zdotdir = temp_path / "zdotdir"
        zdotdir.mkdir()
        script = ROOT / "shell" / f"lazy-{integration}.zsh"
        (zdotdir / ".zshrc").write_text(
            f"source {shlex.quote(str(script))}\n"
            "setopt complete_in_word\n"
            "PROMPT='ZCOMP> '\n"
            "RPROMPT=\n",
            encoding="utf-8",
        )
        env = dict(os.environ)
        env.update(
            {
                "HOME": str(home),
                "PATH": str(fake_bin) + os.pathsep + env.get("PATH", ""),
                "TERM": "xterm-256color",
                "NO_COLOR": "1",
                "ZDOTDIR": str(zdotdir),
                "LAZYROS_HISTORY_FILE": str(history),
                "LAZYROS_TEST_COMPLETION_MODE": str(mode_file),
                "LAZYROS_TEST_PYTHON": sys.executable,
                "LAZYROS_TEST_SOURCE": str(ROOT / "src"),
                "LAZYROS_WORKSPACE": str(workspace),
            }
        )
        child = PtyShell(
            [self._shell(), "-d", "-i"],
            cwd=workspace,
            env=env,
        )
        child.expect(self.PROMPT)
        return child, mode_file

    @staticmethod
    def _mode(path: Path, value: str) -> None:
        path.write_text(value + "\n", encoding="utf-8")

    def test_zero_one_and_many_candidates_follow_double_tab_contract(self) -> None:
        for integration in ("init", "control"):
            with self.subTest(integration=integration):
                with tempfile.TemporaryDirectory() as temp_dir:
                    child, mode = self._spawn(Path(temp_dir), integration)
                    with child:
                        self._mode(mode, "zero")
                        start = len(child.output)
                        child.send(b"lazy \t")
                        child.read_for(0.2)
                        self.assertNotIn(b"alpha", child.output[start:])
                        child.send(b"\x15")

                        self._mode(mode, "one")
                        child.send(b"lazy \t\r")
                        child.expect(b"EXEC:build")
                        child.expect(self.PROMPT)

                        self._mode(mode, "many")
                        start = len(child.output)
                        child.send(b"lazy \t")
                        child.read_for(0.2)
                        self.assertNotIn(b"alpha", child.output[start:])
                        self.assertNotIn(b"beta", child.output[start:])
                        child.send(b"\t")
                        child.expect(b"[1/2] alpha")
                        child.send(b"\x1b[B\r\r")
                        child.expect(b"EXEC:beta")
                        child.expect(self.PROMPT)
                        child.send(b"exit\r")
                        self.assertEqual(child.wait(), 0, bytes(child.output))

    def test_common_prefix_and_zero_candidate_edit_reset_listing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            child, mode = self._spawn(Path(temp_dir), "control")
            with child:
                self._mode(mode, "common")
                start = len(child.output)
                child.send(b"lazy a\t")
                child.read_for(0.2)
                segment = child.output[start:]
                self.assertNotIn(b"alpha", segment)
                self.assertNotIn(b"alpine", segment)
                child.send(b"\t")
                child.expect(b"[1/2] alpha")
                child.send(b"\r\r")
                child.expect(b"EXEC:alpha")
                child.expect(self.PROMPT)

                self._mode(mode, "many")
                child.send(b"lazy \t")
                child.read_for(0.1)
                child.send(b"x")
                self._mode(mode, "zero")
                child.send(b"\t\x7f")
                self._mode(mode, "many")
                start = len(child.output)
                child.send(b"\t")
                child.read_for(0.2)
                self.assertNotIn(b"alpha", child.output[start:])
                self.assertNotIn(b"beta", child.output[start:])
                child.send(b"\t")
                child.expect(b"[1/2] alpha")
                child.send(b"\x03\x15exit\r")
                self.assertEqual(child.wait(), 0, bytes(child.output))

    def test_cursor_movement_resets_the_lazy_completion_repeat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            child, mode = self._spawn(Path(temp_dir), "control")
            with child:
                self._mode(mode, "common")
                child.send(b"lazy a\t")
                child.read_for(0.1)
                child.send(b"\x1b[D\t\x1b[C")
                start = len(child.output)
                child.send(b"\t")
                child.read_for(0.2)
                self.assertNotIn(b"alpha", child.output[start:])
                self.assertNotIn(b"alpine", child.output[start:])
                child.send(b"\t")
                child.expect(b"[1/2] alpha")
                child.send(b"\x03\x15exit\r")
                self.assertEqual(child.wait(), 0, bytes(child.output))

    def test_unique_completion_advancing_argument_resets_double_tab_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            child, mode = self._spawn(Path(temp_dir), "control")
            with child:
                self._mode(mode, "one")
                child.send(b"lazy bu\t")
                child.read_for(0.2)

                self._mode(mode, "common")
                start = len(child.output)
                child.send(b"\t")
                child.read_for(0.2)
                self.assertNotIn(b"alpha", child.output[start:])
                self.assertNotIn(b"alpine", child.output[start:])

                child.send(b"\t")
                child.expect(b"[1/2] alpha")
                child.send(b"\r\r")
                child.expect(b"EXEC:build alpha")
                child.expect(self.PROMPT)
                child.send(b"exit\r")
                self.assertEqual(child.wait(), 0, bytes(child.output))


@unittest.skipUnless(os.name == "posix" and pty is not None, "requires POSIX PTYs")
class BashCompletionPtyTests(unittest.TestCase):
    PROMPT = b"BCOMP> "

    def _spawn(self, temp_path: Path, integration: str) -> PtyShell:
        shell = shutil.which("bash")
        if shell is None:
            self.skipTest("bash is not installed")
        workspace = temp_path / "robot_ws"
        (workspace / "src").mkdir(parents=True)
        home = temp_path / "home"
        home.mkdir()
        history = temp_path / "history"
        history.touch()
        fake_bin = temp_path / "bin"
        fake_bin.mkdir()
        fake_lazy = fake_bin / "lazy"
        fake_lazy.write_text(
            """#!/bin/sh
case ${1-} in
    __setup-path)
        exit 0
        ;;
    __complete)
        printf 'alpha\nbeta\n'
        exit 0
        ;;
    __select)
        PYTHONPATH="$LAZYROS_TEST_SOURCE" \
            exec "$LAZYROS_TEST_PYTHON" -m lazyros2 "$@"
        ;;
    *)
        printf 'EXEC:%s\n' "$*"
        exit 0
        ;;
esac
""",
            encoding="utf-8",
        )
        fake_lazy.chmod(0o755)
        integration_script = ROOT / "shell" / f"lazy-{integration}.bash"
        rcfile = temp_path / "bashrc"
        rcfile.write_text(
            f"source {shlex.quote(str(integration_script))}\n"
            "PS1='BCOMP> '\n",
            encoding="utf-8",
        )
        env = dict(os.environ)
        env.update(
            {
                "HOME": str(home),
                "PATH": str(fake_bin) + os.pathsep + env.get("PATH", ""),
                "TERM": "xterm-256color",
                "NO_COLOR": "1",
                "LAZYROS_HISTORY_FILE": str(history),
                "LAZYROS_TEST_PYTHON": sys.executable,
                "LAZYROS_TEST_SOURCE": str(ROOT / "src"),
                "LAZYROS_WORKSPACE": str(workspace),
            }
        )
        child = PtyShell(
            [str(Path(shell).resolve()), "--noprofile", "--rcfile", str(rcfile), "-i"],
            cwd=workspace,
            env=env,
        )
        child.expect(self.PROMPT)
        return child

    def test_second_tab_uses_arrow_candidate_picker(self) -> None:
        for integration in ("init", "control"):
            with self.subTest(integration=integration):
                with tempfile.TemporaryDirectory() as temp_dir:
                    child = self._spawn(Path(temp_dir), integration)
                    with child:
                        start = len(child.output)
                        child.send(b"lazy \t")
                        child.read_for(0.2)
                        self.assertNotIn(b"alpha", child.output[start:])
                        self.assertNotIn(b"beta", child.output[start:])
                        child.send(b"\t")
                        child.expect(b"[1/2] alpha")
                        child.send(b"\x1b[B\r\r")
                        child.expect(b"EXEC:beta")
                        child.expect(self.PROMPT)
                        child.send(b"exit\r")
                        self.assertEqual(child.wait(), 0, bytes(child.output))

    def test_typing_resets_repeat_and_reuses_current_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            child = self._spawn(Path(temp_dir), "control")
            with child:
                child.send(b"lazy \t")
                child.read_for(0.2)
                child.send(b"a\t\r")
                child.expect(b"EXEC:alpha")
                child.expect(self.PROMPT)
                child.send(b"exit\r")
                self.assertEqual(child.wait(), 0, bytes(child.output))


class PromptOverlayTests(unittest.TestCase):
    def _check_generation_reload(self, shell_name: str) -> None:
        shell = shutil.which(shell_name)
        if shell is None or os.name != "posix":
            self.skipTest(f"requires POSIX {shell_name}")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "robot_ws"
            (workspace / "src").mkdir(parents=True)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            lazy = fake_bin / "lazy"
            lazy.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$LAZYROS_TEST_SETUP"\n',
                encoding="utf-8",
            )
            lazy.chmod(0o755)
            generation = root / "generation"
            generation.write_text("1\n", encoding="utf-8")
            setup = root / "setup"
            setup.write_text(
                'printf "loaded\\n" >> "$LAZYROS_TEST_LOADS"\n'
                'export LAZYROS_TEST_VERSION=$(command cat "$LAZYROS_OVERLAY_GENERATION_FILE")\n'
                '[[ $LAZYROS_TEST_VERSION != 3 || -f $LAZYROS_TEST_ALLOW_THREE ]]\n',
                encoding="utf-8",
            )
            env = dict(os.environ)
            env.update(
                HOME=str(root),
                PATH=str(fake_bin) + os.pathsep + env.get("PATH", ""),
                TERM="dumb",
                LAZYROS_WORKSPACE=str(workspace),
                LAZYROS_HISTORY_FILE=str(root / "history"),
                LAZYROS_OVERLAY_GENERATION_FILE=str(generation),
                LAZYROS_TEST_SETUP=str(setup),
                LAZYROS_TEST_LOADS=str(root / "loads"),
                LAZYROS_TEST_ALLOW_THREE=str(root / "allow-three"),
            )
            control = ROOT / "shell" / f"lazy-control.{shell_name}"
            script = f"source {shlex.quote(str(control))}\n" + """
_lazyros_check_overlay
printf '2\n' > "$LAZYROS_OVERLAY_GENERATION_FILE"
false
_lazyros_check_overlay
printf 'STATUS:%s VERSION:%s\n' "$?" "$LAZYROS_TEST_VERSION"
_lazyros_check_overlay
printf '3\n' > "$LAZYROS_OVERLAY_GENERATION_FILE"
_lazyros_check_overlay
touch "$LAZYROS_TEST_ALLOW_THREE"
_lazyros_check_overlay
_lazyros_check_overlay
printf 'FINAL:%s\n' "$LAZYROS_TEST_VERSION"
"""
            args = [shell, "--noprofile", "--norc"] if shell_name == "bash" else [shell, "-d", "-f"]
            completed = subprocess.run(
                [*args, "-c", script],
                env=env,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("STATUS:1 VERSION:2", completed.stdout)
            self.assertIn("FINAL:3", completed.stdout)
            self.assertIn("failed to load workspace overlay", completed.stderr)
            self.assertEqual((root / "loads").read_text().splitlines(), ["loaded"] * 4)

    def test_bash_reload_tracks_successful_build_generations(self) -> None:
        self._check_generation_reload("bash")

    def test_zsh_reload_tracks_successful_build_generations(self) -> None:
        self._check_generation_reload("zsh")


@unittest.skipUnless(os.name == "posix" and pty is not None, "requires POSIX PTYs")
class LiteralCompletionPtyTests(unittest.TestCase):
    PROMPT = b"LITERAL> "

    def _spawn(
        self,
        root: Path,
        shell_name: str,
        integration: str,
        candidates: tuple[str, ...],
    ) -> PtyShell:
        shell = shutil.which(shell_name)
        if shell is None:
            self.skipTest(f"{shell_name} is not installed")
        workspace = root / "robot_ws"
        (workspace / "src").mkdir(parents=True)
        home = root / "home"
        home.mkdir()
        fake_bin = root / "bin"
        fake_bin.mkdir()
        candidate_file = root / "candidates"
        candidate_file.write_text("\n".join(candidates) + "\n", encoding="utf-8")
        lazy = fake_bin / "lazy"
        lazy.write_text(
            """#!/bin/sh
case ${1-} in
    __setup-path) exit 0 ;;
    __complete) cat "$LAZYROS_TEST_CANDIDATES" ;;
    __select)
        PYTHONPATH="$LAZYROS_TEST_SOURCE" \
            exec "$LAZYROS_TEST_PYTHON" -m lazyros2 "$@"
        ;;
    *)
        exec "$LAZYROS_TEST_PYTHON" -c \
            'import json,sys; print("ARGV:" + json.dumps(sys.argv[1:], ensure_ascii=False))' "$@"
        ;;
esac
""",
            encoding="utf-8",
        )
        lazy.chmod(0o755)
        env = dict(os.environ)
        env.update(
            HOME=str(home),
            PATH=str(fake_bin) + os.pathsep + env.get("PATH", ""),
            TERM="xterm-256color",
            NO_COLOR="1",
            LAZYROS_HISTORY_FILE=str(root / "history"),
            LAZYROS_WORKSPACE=str(workspace),
            LAZYROS_TEST_CANDIDATES=str(candidate_file),
            LAZYROS_TEST_SOURCE=str(ROOT / "src"),
            LAZYROS_TEST_PYTHON=sys.executable,
        )
        script = ROOT / "shell" / f"lazy-{integration}.{shell_name}"
        rcfile = root / (".zshrc" if shell_name == "zsh" else "bashrc")
        rcfile.write_text(
            f"source {shlex.quote(str(script))}\n"
            "PS1='LITERAL> '\n"
            + ("RPROMPT=\n" if shell_name == "zsh" else ""),
            encoding="utf-8",
        )
        if shell_name == "zsh":
            env["ZDOTDIR"] = str(root)
            argv = [str(Path(shell).resolve()), "-d", "-i"]
        else:
            argv = [str(Path(shell).resolve()), "--noprofile", "--rcfile", str(rcfile), "-i"]
        child = PtyShell(argv, cwd=workspace, env=env)
        child.expect(self.PROMPT)
        return child

    def _expect_argv(self, child: PtyShell, *arguments: str) -> None:
        expected = "ARGV:" + json.dumps(arguments, ensure_ascii=False)
        child.expect(expected.encode("utf-8"))
        child.expect(self.PROMPT)

    def test_unique_candidate_is_one_literal_argument(self) -> None:
        candidate = 'map "quote" \'single\' $(touch marker) * ; 日本.rviz'
        for shell_name in ("bash", "zsh"):
            for integration in ("init", "control"):
                with self.subTest(shell=shell_name, integration=integration):
                    with tempfile.TemporaryDirectory() as temp:
                        root = Path(temp)
                        with self._spawn(root, shell_name, integration, (candidate,)) as child:
                            child.send(b"lazy rviz map\t\r")
                            self._expect_argv(child, "rviz", candidate)
                            self.assertFalse((root / "robot_ws" / "marker").exists())

    def test_common_prefix_and_picker_keep_metacharacters_literal(self) -> None:
        prefix = "map $(touch marker) 'quoted' * 日本 "
        candidates = (prefix + "one.rviz", prefix + "two.rviz")
        for shell_name in ("bash", "zsh"):
            for integration in ("init", "control"):
                with self.subTest(shell=shell_name, integration=integration):
                    with tempfile.TemporaryDirectory() as temp:
                        root = Path(temp)
                        with self._spawn(root, shell_name, integration, candidates) as child:
                            child.send(b"lazy rviz map\t")
                            child.read_for(0.2)
                            child.send(b"\t")
                            child.expect(b"[1/2]")
                            child.send(b"\x1b[B\r extra\r")
                            self._expect_argv(child, "rviz", candidates[1], "extra")
                            self.assertFalse((root / "robot_ws" / "marker").exists())

    def test_picker_replaces_an_open_quoted_token(self) -> None:
        candidates = ("map one.rviz", "map two.rviz")
        for shell_name in ("bash", "zsh"):
            for quote in (b"'", b'"'):
                with self.subTest(shell=shell_name, quote=quote):
                    with tempfile.TemporaryDirectory() as temp:
                        with self._spawn(Path(temp), shell_name, "control", candidates) as child:
                            child.send(b"lazy rviz " + quote + b"map \t")
                            child.read_for(0.2)
                            child.send(b"\t")
                            child.expect(b"[1/2]")
                            child.send(b"\r\r")
                            self._expect_argv(child, "rviz", candidates[0])

    def test_aborted_line_resets_first_tab_before_new_completion(self) -> None:
        candidates = ("alpha", "beta")
        for shell_name in ("bash", "zsh"):
            with self.subTest(shell=shell_name):
                with tempfile.TemporaryDirectory() as temp:
                    with self._spawn(Path(temp), shell_name, "control", candidates) as child:
                        child.send(b"lazy \t")
                        child.read_for(0.2)
                        child.send(b"\x03")
                        child.expect(self.PROMPT)
                        start = len(child.output)
                        child.send(b"lazy \t")
                        child.read_for(0.2)
                        self.assertNotIn(b"[1/2]", child.output[start:])
                        child.send(b"\t")
                        child.expect(b"[1/2]")
                        child.send(b"\r\r")
                        self._expect_argv(child, candidates[0])

    def test_cursor_completion_preserves_suffix_and_following_argument(self) -> None:
        for shell_name in ("bash", "zsh"):
            for multiple in (False, True):
                with self.subTest(shell=shell_name, multiple=multiple):
                    candidates = ("map one.rviz", "map two.rviz") if multiple else ("map one.rviz",)
                    with tempfile.TemporaryDirectory() as temp:
                        with self._spawn(Path(temp), shell_name, "control", candidates) as child:
                            child.send(b"lazy rviz map.rviz trailing\x01" + b"\x1b[C" * 13 + b"\t")
                            child.read_for(0.2)
                            if multiple:
                                child.send(b"\t")
                                child.expect(b"[1/2]")
                                child.send(b"\r")
                            child.send(b"\r")
                            self._expect_argv(child, "rviz", candidates[0], "trailing")


if __name__ == "__main__":
    unittest.main()
