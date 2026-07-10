# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(os.name == "nt", "install.sh requires a POSIX host")
class InstallScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("sh") is None or shutil.which("python3") is None:
            self.skipTest("sh and python3 are required")

        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.release = self.root / "release"
        self.home = self.root / "home"
        self.fake_bin = self.root / "fake-bin"
        self.runtime = self.root / "runtime"
        self.temp = self.root / "tmp"
        self.release.mkdir()
        self.home.mkdir()
        self.fake_bin.mkdir()
        self.runtime.mkdir()
        self.temp.mkdir()
        self._write_fake_id(1000)
        self._write_release("0.1.0")

        self.environment = os.environ.copy()
        self.environment.update(
            {
                "HOME": str(self.home),
                "SHELL": "/bin/bash",
                "XDG_CONFIG_HOME": str(self.root / "config"),
                "XDG_CACHE_HOME": str(self.root / "cache"),
                "XDG_STATE_HOME": str(self.root / "state"),
                "XDG_RUNTIME_DIR": str(self.runtime),
                "TMPDIR": str(self.temp),
                "PATH": f"{self.fake_bin}{os.pathsep}{os.environ['PATH']}",
            }
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @property
    def manifest_path(self) -> Path:
        return self.home / ".local" / "share" / "lazyros2" / "install-manifest.json"

    @property
    def current_installer(self) -> Path:
        return self.home / ".local" / "lib" / "lazyros2" / "current" / "install.sh"

    def _write_fake_id(self, user_id: int) -> None:
        path = self.fake_bin / "id"
        path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{user_id}'\n", encoding="utf-8")
        path.chmod(0o755)

    def _write_release(self, version: str) -> None:
        shutil.copy2(REPOSITORY_ROOT / "install.sh", self.release / "install.sh")
        shutil.copy2(REPOSITORY_ROOT / "LICENSE", self.release / "LICENSE")
        shutil.copy2(REPOSITORY_ROOT / "pyproject.toml", self.release / "pyproject.toml")
        (self.release / "VERSION").write_text(f"{version}\n", encoding="utf-8")

        launcher = self.release / "bin" / "lazy"
        launcher.parent.mkdir(exist_ok=True)
        launcher.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            'script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
            'app_root=$(CDPATH= cd -- "$script_dir/.." && pwd)\n'
            'IFS= read -r version < "$app_root/VERSION"\n'
            "printf 'lazy %s\\n' \"$version\"\n",
            encoding="utf-8",
        )
        launcher.chmod(0o755)
        (self.release / "install.sh").chmod(0o755)

        package = self.release / "src" / "lazyros2"
        package.mkdir(parents=True, exist_ok=True)
        (package / "__main__.py").write_text("raise SystemExit(0)\n", encoding="utf-8")

        shell_dir = self.release / "shell"
        shell_dir.mkdir()
        (shell_dir / "lazy-init.bash").write_text("# bash integration\n", encoding="utf-8")
        (shell_dir / "lazy-init.zsh").write_text("# zsh integration\n", encoding="utf-8")

    def _set_release_version(self, version: str) -> None:
        (self.release / "VERSION").write_text(f"{version}\n", encoding="utf-8")

    def _run(
        self,
        *arguments: str,
        script: Path | None = None,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        target = script or self.release / "install.sh"
        return subprocess.run(
            ["sh", str(target), *arguments],
            cwd=self.release,
            env=self.environment,
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=20,
        )

    def _install(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        result = self._run(*arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def _inject_installer_statement(self, statement: str) -> None:
        installer = self.release / "install.sh"
        content = installer.read_text(encoding="utf-8")
        anchor = "                atomic_symlink(layout.current, version)\n"
        self.assertEqual(content.count(anchor), 1)
        installer.write_text(
            content.replace(anchor, f"{anchor}                {statement}\n"),
            encoding="utf-8",
        )

    def _inject_after(self, anchor: str, statement: str, indentation: str) -> None:
        installer = self.release / "install.sh"
        content = installer.read_text(encoding="utf-8")
        self.assertEqual(content.count(anchor), 1)
        installer.write_text(
            content.replace(anchor, f"{anchor}{indentation}{statement}\n"),
            encoding="utf-8",
        )

    def _restore_release_installer(self) -> None:
        shutil.copy2(REPOSITORY_ROOT / "install.sh", self.release / "install.sh")
        (self.release / "install.sh").chmod(0o755)

    def test_installs_fixed_layout_and_manifest_v1(self) -> None:
        self._install("--no-rc")

        local = self.home / ".local"
        launcher = local / "bin" / "lazy"
        current = local / "lib" / "lazyros2" / "current"
        self.assertTrue(launcher.is_file())
        self.assertEqual(stat.S_IMODE(launcher.stat().st_mode), 0o755)
        self.assertTrue(current.is_symlink())
        self.assertEqual(os.readlink(current), "0.1.0")
        self.assertTrue((current / "install.sh").is_file())

        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["app_version"], "0.1.0")
        self.assertEqual(manifest["license"], "AGPL-3.0-or-later")
        self.assertEqual(manifest["home"], str(self.home.resolve()))
        self.assertEqual(manifest["rc_files"], [])
        self.assertTrue(all(entry["root"] == "local" for entry in manifest["files"]))
        self.assertFalse(any(".." in Path(entry["path"]).parts for entry in manifest["files"]))

        completed = subprocess.run(
            [str(launcher), "--version"],
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("0.1.0", completed.stdout)

    def test_rejects_root_before_writing_layout(self) -> None:
        self._write_fake_id(0)

        result = self._run("--no-rc")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("root installation is not supported", result.stderr)
        self.assertFalse((self.home / ".local").exists())

    def test_python_version_is_rejected_before_embedded_code_is_parsed(self) -> None:
        python = self.fake_bin / "python3"
        python.write_text(
            "#!/bin/sh\n"
            "case \"$2\" in\n"
            "  *'sys.version_info[:3]'*) printf '%s\\n' '3.9.19'; exit 0 ;;\n"
            "  *) exit 1 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        python.chmod(0o755)

        result = self._run("--no-rc")

        self.assertEqual(result.returncode, 1)
        self.assertIn("Python 3.10 or newer is required; found 3.9.19", result.stderr)
        self.assertNotIn("SyntaxError", result.stderr)
        self.assertFalse((self.home / ".local").exists())

    def test_missing_python_keeps_command_not_found_exit_code(self) -> None:
        environment = dict(self.environment)
        environment["PATH"] = str(self.fake_bin)

        result = subprocess.run(
            ["/bin/sh", str(self.release / "install.sh"), "--no-rc"],
            cwd=self.release,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=20,
        )

        self.assertEqual(result.returncode, 127)
        self.assertIn("Python 3.10 or newer is required", result.stderr)
        self.assertFalse((self.home / ".local").exists())

    def test_concurrent_installer_is_rejected_by_private_home_lock(self) -> None:
        launcher = self.release / "bin" / "lazy"
        launcher.write_text(
            launcher.read_text(encoding="utf-8").replace("set -eu\n", "set -eu\nsleep 2\n"),
            encoding="utf-8",
        )
        first = subprocess.Popen(
            ["sh", str(self.release / "install.sh"), "--no-rc"],
            cwd=self.release,
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        owner = self.home / ".lazyros2-install.lock" / "owner.json"
        deadline = time.monotonic() + 5
        while not owner.exists() and first.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(owner.is_file(), "first installer did not acquire its lock")

        second = self._run("--no-rc")
        first_stdout, first_stderr = first.communicate(timeout=10)

        self.assertNotEqual(second.returncode, 0)
        self.assertIn("already running", second.stderr)
        self.assertEqual(first.returncode, 0, first_stderr)
        self.assertIn("Installed LazyROS2", first_stdout)
        self.assertFalse((self.home / ".lazyros2-install.lock").exists())

    def test_launcher_and_current_remain_resolvable_during_upgrade(self) -> None:
        self._install("--no-rc")
        launcher = self.home / ".local" / "bin" / "lazy"
        self._set_release_version("0.2.0")
        self._inject_installer_statement("time.sleep(1)")
        upgrade = subprocess.Popen(
            ["sh", str(self.release / "install.sh"), "--no-rc"],
            cwd=self.release,
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        observations: list[str] = []
        while upgrade.poll() is None:
            completed = subprocess.run(
                [str(launcher), "--version"],
                env=self.environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=5,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            observations.append(completed.stdout.strip())
        stdout, stderr = upgrade.communicate(timeout=10)

        self.assertEqual(upgrade.returncode, 0, stderr)
        self.assertIn("Installed LazyROS2 0.2.0", stdout)
        self.assertTrue(observations)
        self.assertTrue(all(value in {"lazy 0.1.0", "lazy 0.2.0"} for value in observations))

    def test_rejects_unmanaged_launcher_collision(self) -> None:
        launcher = self.home / ".local" / "bin" / "lazy"
        launcher.parent.mkdir(parents=True)
        launcher.write_text("unmanaged\n", encoding="utf-8")

        result = self._run("--no-rc")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unmanaged LazyROS2 paths", result.stderr)
        self.assertEqual(launcher.read_text(encoding="utf-8"), "unmanaged\n")
        self.assertFalse(self.manifest_path.exists())

    def test_rejects_managed_directory_symlink_before_writing_outside(self) -> None:
        outside = self.root / "outside-managed-root"
        outside.mkdir()
        app = self.home / ".local" / "lib" / "lazyros2"
        app.parent.mkdir(parents=True)
        app.symlink_to(outside, target_is_directory=True)

        result = self._run("--no-rc")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("installation directory must not be a symlink", result.stderr)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((self.home / ".local" / "bin" / "lazy").exists())
        self.assertFalse(self.manifest_path.exists())

    def test_same_version_is_noop_and_changed_copy_requires_reinstall(self) -> None:
        self._install("--no-rc")
        original_manifest = self.manifest_path.read_bytes()

        noop = self._run("--no-rc")

        self.assertEqual(noop.returncode, 0, noop.stderr)
        self.assertIn("already installed", noop.stdout)
        self.assertEqual(self.manifest_path.read_bytes(), original_manifest)

        module = self.release / "src" / "lazyros2" / "__main__.py"
        module.write_text("print('changed release')\n", encoding="utf-8")
        rejected = self._run("--no-rc")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("use --reinstall", rejected.stderr)

        replaced = self._run("--no-rc", "--reinstall")
        self.assertEqual(replaced.returncode, 0, replaced.stderr)
        installed = self.home / ".local" / "lib" / "lazyros2" / "0.1.0" / "src" / "lazyros2" / "__main__.py"
        self.assertEqual(installed.read_text(encoding="utf-8"), "print('changed release')\n")

    def test_downgrade_requires_explicit_permission(self) -> None:
        self._set_release_version("0.2.0")
        self._install("--no-rc")
        self._set_release_version("0.1.0")

        rejected = self._run("--no-rc")

        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("refusing to downgrade", rejected.stderr)
        allowed = self._run("--no-rc", "--allow-downgrade")
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertEqual(os.readlink(self.home / ".local" / "lib" / "lazyros2" / "current"), "0.1.0")
        self.assertFalse((self.home / ".local" / "lib" / "lazyros2" / "0.2.0").exists())

    def test_upgrade_rejects_untracked_old_payload_entries(self) -> None:
        self._install("--no-rc")
        app = self.home / ".local" / "lib" / "lazyros2"
        sentinel = app / "0.1.0" / "user-sentinel"
        sentinel.write_text("do not delete\n", encoding="utf-8")
        self._set_release_version("0.2.0")

        result = self._run("--no-rc")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("untracked: user-sentinel", result.stderr)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "do not delete\n")
        self.assertEqual(os.readlink(app / "current"), "0.1.0")
        self.assertFalse((app / "0.2.0").exists())

    def test_interrupted_transaction_and_stale_lock_are_rolled_back_on_startup(self) -> None:
        self._install("--no-rc")
        old_manifest = self.manifest_path.read_bytes()
        self._set_release_version("0.2.0")
        self._inject_installer_statement("os._exit(88)")

        interrupted = self._run("--no-rc")

        self.assertEqual(interrupted.returncode, 88, interrupted.stderr)
        app = self.home / ".local" / "lib" / "lazyros2"
        transactions = list(app.glob(".transaction-*"))
        self.assertEqual(len(transactions), 1)
        self.assertTrue((self.home / ".lazyros2-install.lock").is_dir())
        self.assertEqual(os.readlink(app / "current"), "0.2.0")

        runtime_root = self.runtime / "lazyros2"
        runtime_root.mkdir()
        (runtime_root / "jobs.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "next_numbers": {},
                    "jobs": [{"pid": os.getpid(), "state": "idle"}],
                }
            ),
            encoding="utf-8",
        )
        recovered = self._run("--uninstall")

        self.assertNotEqual(recovered.returncode, 0)
        self.assertIn("rolled back interrupted transaction", recovered.stderr)
        self.assertIn("active LazyROS2 task windows", recovered.stderr)
        self.assertEqual(self.manifest_path.read_bytes(), old_manifest)
        self.assertEqual(os.readlink(app / "current"), "0.1.0")
        self.assertTrue((app / "0.1.0").is_dir())
        self.assertFalse((app / "0.2.0").exists())
        self.assertEqual(list(app.glob(".transaction-*")), [])
        self.assertFalse((self.home / ".lazyros2-install.lock").exists())

    def test_unrecognized_residual_transaction_fails_closed(self) -> None:
        self._install("--no-rc")
        app = self.home / ".local" / "lib" / "lazyros2"
        residual = app / ".transaction-legacy"
        residual.mkdir(mode=0o700)
        (residual / "unknown").write_text("not a journal\n", encoding="utf-8")

        result = self._run("--no-rc")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot be recovered safely", result.stderr)
        self.assertTrue(residual.is_dir())
        self.assertEqual(os.readlink(app / "current"), "0.1.0")
        self.assertTrue(self.manifest_path.is_file())

    def test_commit_failure_restores_old_launcher_current_manifest_and_payload(self) -> None:
        self._install("--no-rc")
        old_manifest = self.manifest_path.read_bytes()
        launcher = self.home / ".local" / "bin" / "lazy"
        old_launcher = launcher.read_bytes()
        self._set_release_version("0.2.0")
        self._inject_installer_statement('fail("injected transaction failure")')

        failed = self._run("--no-rc")

        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("injected transaction failure", failed.stderr)
        app = self.home / ".local" / "lib" / "lazyros2"
        self.assertEqual(self.manifest_path.read_bytes(), old_manifest)
        self.assertEqual(launcher.read_bytes(), old_launcher)
        self.assertEqual(os.readlink(app / "current"), "0.1.0")
        self.assertTrue((app / "0.1.0").is_dir())
        self.assertFalse((app / "0.2.0").exists())
        self.assertEqual(list(app.glob(".*transaction-*")), [])

    def test_rc_marker_is_exactly_removed_without_touching_user_content(self) -> None:
        rc_path = self.home / ".bashrc"
        rc_path.write_text("export ROBOT_NAME=field_unit\n", encoding="utf-8")
        self._install()
        installed_content = rc_path.read_text(encoding="utf-8")
        self.assertEqual(installed_content.count("# >>> LazyROS2 >>>"), 1)
        self.assertIn("lazy-init.bash", installed_content)

        removed = self._run("--uninstall", script=self.current_installer)

        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertEqual(rc_path.read_text(encoding="utf-8"), "export ROBOT_NAME=field_unit\n")
        self.assertFalse(self.manifest_path.exists())
        repeated = self._run("--uninstall")
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertIn("not installed", repeated.stdout)

    def test_uninstall_requires_hash_match_unless_forced(self) -> None:
        sentinel = self.home / "keep-me"
        sentinel.write_text("outside managed roots\n", encoding="utf-8")
        self._install("--no-rc")
        modified = self.home / ".local" / "lib" / "lazyros2" / "0.1.0" / "src" / "lazyros2" / "__main__.py"
        modified.write_text("locally modified\n", encoding="utf-8")

        rejected = self._run("--uninstall", script=self.current_installer)

        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("sha256 mismatch", rejected.stderr)
        self.assertTrue(self.manifest_path.exists())
        forced = self._run("--uninstall", "--force", script=self.current_installer)
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertFalse(self.manifest_path.exists())
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "outside managed roots\n")

    def test_uninstall_rejects_manifest_path_traversal_even_with_force(self) -> None:
        sentinel = self.home / "sentinel"
        sentinel.write_text("safe\n", encoding="utf-8")
        self._install("--no-rc")
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["files"][0]["path"] = "../../sentinel"
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        result = self._run("--uninstall", "--force", script=self.current_installer)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe managed path", result.stderr)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "safe\n")
        self.assertTrue(self.current_installer.exists())

    def test_force_uninstall_rejects_symlinked_payload_parent_inside_local(self) -> None:
        self._install("--no-rc")
        local = self.home / ".local"
        neighbour = local / "lib" / "neighbour"
        redirected = neighbour / "lazyros2"
        redirected.mkdir(parents=True)
        sentinel = redirected / "__main__.py"
        sentinel.write_text("neighbour sentinel\n", encoding="utf-8")
        payload_src = local / "lib" / "lazyros2" / "0.1.0" / "src"
        shutil.rmtree(payload_src)
        payload_src.symlink_to(neighbour, target_is_directory=True)

        result = self._run("--uninstall", "--force")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlinked parent component", result.stderr)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "neighbour sentinel\n")
        self.assertTrue(self.manifest_path.is_file())

    def test_force_uninstall_rejects_symlinked_manifest_parent(self) -> None:
        self._install("--no-rc")
        share = self.home / ".local" / "share"
        outside_share = self.home / "outside-share"
        share.rename(outside_share)
        share.symlink_to(outside_share, target_is_directory=True)
        outside_manifest = outside_share / "lazyros2" / "install-manifest.json"
        original_manifest = outside_manifest.read_bytes()
        sentinel = outside_share / "neighbour-sentinel"
        sentinel.write_text("outside share must survive\n", encoding="utf-8")

        result = self._run("--uninstall", "--force")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlinked parent component", result.stderr)
        self.assertEqual(outside_manifest.read_bytes(), original_manifest)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "outside share must survive\n")
        self.assertTrue(share.is_symlink())

    def test_uninstall_retries_after_hard_exit_immediately_after_preparing_mkdir(self) -> None:
        self._install("--no-rc")
        self._inject_after(
            "    # The durable provenance predates this mkdir, so an immediate crash is recoverable.\n",
            "os._exit(93)",
            "    ",
        )

        interrupted = self._run("--uninstall")

        self.assertEqual(interrupted.returncode, 93, interrupted.stderr)
        self.assertTrue((self.home / ".lazyros2-uninstall-preparing").is_dir())
        self.assertTrue((self.home / ".lazyros2-uninstall-preparing.json").is_file())
        self.assertTrue(self.manifest_path.is_file())
        self.assertTrue((self.home / ".local" / "bin" / "lazy").is_file())
        self._restore_release_installer()
        retried = self._run("--uninstall")

        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertIn("removed incomplete uninstall preparing directory", retried.stderr)
        self.assertFalse(self.manifest_path.exists())
        self.assertFalse((self.home / ".lazyros2-uninstall-preparing").exists())
        self.assertFalse((self.home / ".lazyros2-uninstall-preparing.json").exists())
        self.assertFalse((self.home / ".lazyros2-uninstall-transaction").exists())

    def test_uninstall_retries_after_hard_exit_following_rc_update(self) -> None:
        rc_path = self.home / ".bashrc"
        original = "export ROBOT_NAME=recovery_test\n"
        rc_path.write_text(original, encoding="utf-8")
        self._install()
        self._inject_after(
            "    apply_uninstall_rc(layout, journal)\n",
            "os._exit(91)",
            "    ",
        )

        interrupted = self._run("--uninstall")

        self.assertEqual(interrupted.returncode, 91, interrupted.stderr)
        self.assertEqual(rc_path.read_text(encoding="utf-8"), original)
        self.assertTrue((self.home / ".lazyros2-uninstall-transaction").is_dir())
        self._restore_release_installer()
        retried = self._run("--uninstall")

        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertIn("completed interrupted uninstall transaction", retried.stderr)
        self.assertEqual(rc_path.read_text(encoding="utf-8"), original)
        self.assertFalse(self.manifest_path.exists())
        self.assertFalse((self.home / ".local" / "bin" / "lazy").exists())
        self.assertFalse((self.home / ".lazyros2-uninstall-transaction").exists())
        self.assertFalse((self.home / ".lazyros2-uninstall-cleanup").exists())

    def test_uninstall_retries_after_hard_exit_during_file_removal(self) -> None:
        self._install("--no-rc")
        self._inject_after(
            "        # The journal makes every durable unlink an idempotent recovery checkpoint.\n",
            "os._exit(92)",
            "        ",
        )

        interrupted = self._run("--uninstall")

        self.assertEqual(interrupted.returncode, 92, interrupted.stderr)
        self.assertTrue((self.home / ".lazyros2-uninstall-transaction").is_dir())
        self.assertTrue(self.manifest_path.is_file())
        self._restore_release_installer()
        retried = self._run("--uninstall")

        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertIn("completed interrupted uninstall transaction", retried.stderr)
        self.assertFalse(self.manifest_path.exists())
        self.assertFalse((self.home / ".local" / "bin" / "lazy").exists())
        self.assertFalse((self.home / ".local" / "lib" / "lazyros2").exists())
        self.assertFalse((self.home / ".lazyros2-uninstall-transaction").exists())

    def test_purge_requires_confirmation_and_removes_only_lazyros2_state(self) -> None:
        self._install("--no-rc")
        state_paths = [
            self.root / "config" / "lazyros2",
            self.root / "cache" / "lazyros2",
            self.root / "state" / "lazyros2",
        ]
        for path in state_paths:
            path.mkdir(parents=True)
            (path / "state.json").write_text("{}\n", encoding="utf-8")
        neighbour = self.root / "config" / "other-tool"
        neighbour.mkdir(parents=True)

        rejected = self._run("--uninstall", "--purge", script=self.current_installer)

        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("requires confirmation", rejected.stderr)
        self.assertTrue(self.manifest_path.exists())
        purged = self._run("--uninstall", "--purge", "--yes", script=self.current_installer)
        self.assertEqual(purged.returncode, 0, purged.stderr)
        self.assertTrue(all(not path.exists() for path in state_paths))
        self.assertTrue(neighbour.is_dir())

    def test_purge_ignores_empty_and_relative_xdg_roots(self) -> None:
        for value in ("", "relative-root"):
            with self.subTest(value=value):
                unrelated = self.release / "lazyros2"
                unrelated.mkdir(exist_ok=True)
                sentinel = unrelated / "sentinel"
                sentinel.write_text("unrelated\n", encoding="utf-8")
                self.environment.update(
                    {
                        "XDG_CONFIG_HOME": value,
                        "XDG_CACHE_HOME": value,
                        "XDG_STATE_HOME": value,
                        "XDG_RUNTIME_DIR": value,
                    }
                )
                fallback_paths = (
                    self.home / ".config" / "lazyros2",
                    self.home / ".cache" / "lazyros2",
                    self.home / ".local" / "state" / "lazyros2",
                )
                for path in fallback_paths:
                    path.mkdir(parents=True, exist_ok=True)

                result = self._run("--uninstall", "--purge", "--yes")

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "unrelated\n")
                self.assertTrue(all(not path.exists() for path in fallback_paths))

    def test_active_task_pid_blocks_uninstall(self) -> None:
        self._install("--no-rc")
        runtime_root = self.runtime / "lazyros2"
        runtime_root.mkdir()
        registry = {
            "schema_version": 1,
            "next_numbers": {},
            "jobs": [{"pid": os.getpid(), "state": "idle"}],
        }
        (runtime_root / "jobs.json").write_text(json.dumps(registry), encoding="utf-8")

        rejected = self._run("--uninstall", script=self.current_installer)

        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("active LazyROS2 task windows", rejected.stderr)
        forced = self._run("--uninstall", "--force", script=self.current_installer)
        self.assertEqual(forced.returncode, 0, forced.stderr)

    def test_fresh_starting_task_without_pid_blocks_uninstall(self) -> None:
        self._install("--no-rc")
        runtime_root = self.runtime / "lazyros2"
        runtime_root.mkdir()
        registry_path = runtime_root / "jobs.json"
        registry = {
            "schema_version": 1,
            "next_numbers": {},
            "jobs": [
                {
                    "id": "starting-task",
                    "pid": 0,
                    "state": "starting",
                    "created_at": time.time(),
                }
            ],
        }
        registry_path.write_text(json.dumps(registry), encoding="utf-8")

        rejected = self._run("--uninstall", script=self.current_installer)

        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("active LazyROS2 task windows", rejected.stderr)
        self.assertIn("starting-task (starting)", rejected.stderr)
        registry["jobs"][0]["created_at"] = time.time() - 31.0
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        removed = self._run("--uninstall", script=self.current_installer)
        self.assertEqual(removed.returncode, 0, removed.stderr)

    @unittest.skipIf(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        "permission rollback check needs an unprivileged test process",
    )
    def test_failed_rc_commit_restores_previous_version(self) -> None:
        self._install()
        old_manifest = self.manifest_path.read_bytes()
        old_rc = (self.home / ".bashrc").read_bytes()
        self._set_release_version("0.2.0")
        original_mode = stat.S_IMODE(self.home.stat().st_mode)
        self.home.chmod(0o500)
        try:
            failed = self._run()
        finally:
            self.home.chmod(original_mode)

        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(self.manifest_path.read_bytes(), old_manifest)
        self.assertEqual((self.home / ".bashrc").read_bytes(), old_rc)
        app = self.home / ".local" / "lib" / "lazyros2"
        self.assertEqual(os.readlink(app / "current"), "0.1.0")
        self.assertTrue((app / "0.1.0").is_dir())
        self.assertFalse((app / "0.2.0").exists())


if __name__ == "__main__":
    unittest.main()
