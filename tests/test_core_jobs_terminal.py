# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lazyros2.jobs import JobRegistry, JobRegistryError, JobState  # noqa: E402
from lazyros2.terminal import (  # noqa: E402
    TerminalKind,
    TerminalSupport,
    adapter_for,
    detect_terminal,
)


class JobRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.workspace = root / "robot_ws"
        self.workspace.mkdir()
        self.registry = JobRegistry(root / "runtime")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_registers_monotonic_numbers_per_workspace(self) -> None:
        first = self.registry.register(
            self.workspace,
            "run",
            "demo_nodes_cpp/talker",
            ("ros2", "run", "demo_nodes_cpp", "talker"),
            pid=101,
        )
        second = self.registry.register(
            self.workspace,
            "launch",
            "nav/bringup.launch.py",
            ("ros2", "launch", "nav", "bringup.launch.py"),
            pid=102,
        )

        self.assertEqual((first.number, second.number), (1, 2))
        self.assertEqual((first.color_slot, second.color_slot), (0, 1))
        self.assertEqual(self.registry.get_by_id(first.id), first)
        self.assertEqual([job.number for job in self.registry.list(prune=False)], [1, 2])

    def test_updates_state_and_exit_code(self) -> None:
        job = self.registry.register(
            self.workspace,
            "run",
            "demo/talker",
            ("ros2", "run", "demo", "talker"),
        )

        updated = self.registry.update(
            self.workspace,
            job.number,
            JobState.IDLE,
            exit_code=130,
            pid=101,
        )

        self.assertEqual(updated.state, JobState.IDLE)
        self.assertEqual(updated.exit_code, 130)
        self.assertEqual(updated.pid, 101)
        self.assertEqual(updated.command, job.command)

    def test_rejects_terminal_control_characters_in_display_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "control characters"):
            self.registry.register(
                self.workspace,
                "run",
                "pkg/evil\x1b]0;title\a",
                ("run", "--here", "pkg", "node"),
            )

    def test_color_slots_cycle_after_twelve_tasks(self) -> None:
        records = [
            self.registry.register(
                self.workspace,
                "run",
                f"pkg/node-{index}",
                ("run", "--here", "pkg", f"node-{index}"),
            )
            for index in range(13)
        ]
        self.assertEqual([record.color_slot for record in records], [*range(12), 0])

    def test_prunes_job_when_task_shell_is_gone(self) -> None:
        job = self.registry.register(
            self.workspace,
            "run",
            "demo/talker",
            ("ros2", "run", "demo", "talker"),
        )
        self.registry.update(
            self.workspace,
            job.number,
            JobState.RUNNING,
            pid=999_999_999,
        )

        self.assertEqual(self.registry.list(), ())

    def test_starting_job_without_pid_is_kept_during_launch_grace(self) -> None:
        job = self.registry.register(
            self.workspace,
            "run",
            "demo/talker",
            ("ros2", "run", "demo", "talker"),
        )

        self.assertEqual(job.state, JobState.STARTING)
        self.assertEqual(job.pid, 0)
        self.assertEqual(self.registry.list(), (job,))

    def test_remove_does_not_renumber_future_jobs(self) -> None:
        first = self.registry.register(
            self.workspace,
            "run",
            "demo/talker",
            ("ros2", "run", "demo", "talker"),
            pid=101,
        )
        self.registry.remove(self.workspace, first.number)

        second = self.registry.register(
            self.workspace,
            "run",
            "demo/listener",
            ("ros2", "run", "demo", "listener"),
            pid=102,
        )

        self.assertEqual(second.number, 2)

    def test_rejects_corrupt_registry_record(self) -> None:
        self.registry.path.write_text(
            '{"schema_version":1,"next_numbers":{},"jobs":'
            '[{"id":"bad","number":1,"workspace":"relative",'
            '"kind":"run","target":"demo/talker","command":["ros2"],'
            '"color_slot":0,"state":"running","pid":1,"exit_code":null,'
            '"created_at":1,"updated_at":1}]}',
            encoding="utf-8",
        )

        with self.assertRaises(JobRegistryError):
            self.registry.list(prune=False)


class TerminalAdapterTests(unittest.TestCase):
    def test_gnome_terminal_uses_literal_argv(self) -> None:
        adapter = adapter_for(TerminalKind.GNOME, "/usr/bin/gnome-terminal")
        program = ("lazy", "__task", "value with spaces", "$(echo no)")

        argv = adapter.command("robot_ws · #03 · run · demo", program)

        self.assertEqual(argv[0], "/usr/bin/gnome-terminal")
        self.assertIn("--", argv)
        self.assertEqual(argv[-4:], program)
        self.assertEqual(adapter.support, TerminalSupport.SUPPORTED)

    def test_experimental_adapters_never_join_program_into_shell_text(self) -> None:
        for kind in (
            TerminalKind.XDG,
            TerminalKind.KONSOLE,
            TerminalKind.KITTY,
            TerminalKind.GHOSTTY,
            TerminalKind.ALACRITTY,
        ):
            with self.subTest(kind=kind):
                adapter = adapter_for(kind, f"/usr/bin/{kind.value}")
                argv = adapter.command("task title", ("program", "a b", "$HOME"))
                self.assertIn("a b", argv)
                self.assertIn("$HOME", argv)
                self.assertEqual(adapter.support, TerminalSupport.EXPERIMENTAL)

    def test_xdg_terminal_uses_monolithic_title_option(self) -> None:
        adapter = adapter_for(TerminalKind.XDG, "/usr/bin/xdg-terminal-exec")

        argv = adapter.command("task title", ("program",))

        self.assertEqual(argv[1], "--title=task title")
        self.assertEqual(argv[2:], ("--", "program"))

    def test_detection_requires_graphical_session(self) -> None:
        found = detect_terminal(
            {},
            which=lambda name: f"/usr/bin/{name}",
        )

        self.assertIsNone(found)

    def test_detection_honors_supported_preference(self) -> None:
        found = detect_terminal(
            {"DISPLAY": ":1"},
            preferred="kitty",
            which=lambda name: f"/usr/bin/{name}" if name == "kitty" else None,
        )

        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.kind, TerminalKind.KITTY)


if __name__ == "__main__":
    unittest.main()
