# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import re
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SHELL_DIR = ROOT / "shell"


def read_shell(name: str) -> str:
    return (SHELL_DIR / name).read_text(encoding="utf-8")


def test_managed_init_preserves_external_launcher() -> None:
    for name, shell_name in (("lazy-init.bash", "bash"), ("lazy-init.zsh", "zsh")):
        script = read_shell(name)
        assert f"command lazy --shell {shell_name}" in script
        assert 'command lazy "$@"' in script
        assert "exec lazy" not in script


def test_launcher_uses_one_python_process_and_does_not_read_code_from_stdin() -> None:
    launcher = (ROOT / "bin" / "lazy").read_text(encoding="utf-8")
    assert launcher.count("exec python3 -c") == 1
    assert "python3 -m lazyros2" not in launcher
    assert "runpy.run_module" in launcher


def test_completion_uses_cli_protocol_without_eval() -> None:
    for path in SHELL_DIR.iterdir():
        if path.suffix not in {".bash", ".zsh"}:
            continue

        script = path.read_text(encoding="utf-8")
        assert not re.search(r"(^|\s)eval(\s|$)", script, flags=re.MULTILINE)

    for name in (
        "lazy-init.bash",
        "lazy-init.zsh",
        "lazy-control.bash",
        "lazy-control.zsh",
    ):
        script = read_shell(name)
        assert "command lazy __complete" in script
        assert "--cursor" in script
        assert '2>/dev/null' in script


def test_zsh_double_tab_is_scoped_to_lazy_completion_state() -> None:
    for name in ("lazy-init.zsh", "lazy-control.zsh"):
        script = read_shell(name)
        assert "compstate[list]" in script
        assert "compstate[insert]" in script
        assert "unambiguous" in script
        assert "LASTWIDGET" in script
        assert "WIDGET" in script
        assert "bindkey" not in script
        assert "zstyle" not in script


def test_controller_and_task_do_not_inject_location_flags() -> None:
    for name in ("lazy-control.bash", "lazy-control.zsh"):
        script = read_shell(name)
        assert 'command lazy run "$@"' in script
        assert 'command lazy launch "$@"' in script
        assert 'command lazy rviz "$@"' in script
        assert "_lazyros_command_at" not in script

    for name in ("lazy-task.bash", "lazy-task.zsh"):
        script = read_shell(name)
        assert "--here" not in script
        assert "--window" not in script


def test_task_history_is_memory_only_and_restart_line_is_not_executed() -> None:
    bash_script = read_shell("lazy-task.bash")
    zsh_script = read_shell("lazy-task.zsh")

    assert "unset HISTFILE" in bash_script
    assert 'builtin history -s "$LAZYROS_RESTART_LINE"' in bash_script
    assert "SAVEHIST=0" in zsh_script
    assert 'print -sr -- "$LAZYROS_RESTART_LINE"' in zsh_script

    for script in (bash_script, zsh_script):
        assert 'command lazy __job-run "$LAZYROS_JOB_ID"' in script
        assert 'command lazy __job-finish "$LAZYROS_JOB_ID" "$$" 1' in script
        assert "LAZYROS_RESTART_LINE" in script
        assert not re.search(
            r"(?:source|\.|command|sh|bash|zsh)\s+[\"']?\$\{?LAZYROS_RESTART_LINE",
            script,
        )


def test_terminal_osc_is_tty_only_and_restored() -> None:
    for name in ("lazy-task.bash", "lazy-task.zsh"):
        script = read_shell(name)
        assert "-t 1" in script
        assert "-w /dev/tty" in script
        assert "NO_COLOR" in script
        assert "TMUX" in script
        assert re.search(r">\s*/dev/tty", script)
        assert "\\033]111" in script
        assert "_lazyros_restore_terminal" in script


def test_prompt_names_workspace_or_job_and_ros_distro() -> None:
    for name in ("lazy-control.bash", "lazy-control.zsh"):
        script = read_shell(name)
        assert "LAZYROS_WORKSPACE" in script
        assert "ROS_DISTRO" in script
        assert "[lazy:" in script

    for name in ("lazy-task.bash", "lazy-task.zsh"):
        script = read_shell(name)
        assert "LAZYROS_JOB_ID" in script
        assert "LAZYROS_JOB_NUMBER" in script
        assert "LAZYROS_WINDOW_TITLE" in script
        assert "ROS_DISTRO" in script
        assert "[lazy job #" in script


class ShellContractTests(unittest.TestCase):
    def test_managed_init_preserves_external_launcher(self) -> None:
        test_managed_init_preserves_external_launcher()

    def test_launcher_uses_one_python_process_and_does_not_read_code_from_stdin(self) -> None:
        test_launcher_uses_one_python_process_and_does_not_read_code_from_stdin()

    def test_completion_uses_cli_protocol_without_eval(self) -> None:
        test_completion_uses_cli_protocol_without_eval()

    def test_zsh_double_tab_is_scoped_to_lazy_completion_state(self) -> None:
        test_zsh_double_tab_is_scoped_to_lazy_completion_state()

    def test_controller_and_task_do_not_inject_location_flags(self) -> None:
        test_controller_and_task_do_not_inject_location_flags()

    def test_task_history_is_memory_only_and_restart_line_is_not_executed(self) -> None:
        test_task_history_is_memory_only_and_restart_line_is_not_executed()

    def test_terminal_osc_is_tty_only_and_restored(self) -> None:
        test_terminal_osc_is_tty_only_and_restored()

    def test_prompt_names_workspace_or_job_and_ros_distro(self) -> None:
        test_prompt_names_workspace_or_job_and_ros_distro()
