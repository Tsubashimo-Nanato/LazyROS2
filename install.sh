#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later

set -eu

if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'install.sh: Python 3.10 or newer is required.' >&2
    exit 127
fi

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    python_version=$(python3 -c 'import sys; print(".".join(str(part) for part in sys.version_info[:3]))' 2>/dev/null || printf '%s' unknown)
    printf 'install.sh: Python 3.10 or newer is required; found %s.\n' "$python_version" >&2
    exit 1
fi

if [ "$(id -u)" = 0 ]; then
    printf '%s\n' 'install.sh: root installation is not supported. Run this as the target user.' >&2
    exit 1
fi

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

exec python3 - "$script_dir" "$@" <<'PY'
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import errno
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


APP_NAME = "lazyros2"
SCHEMA_VERSION = 1
MINIMUM_PYTHON = (3, 10)
BEGIN_MARKER = "# >>> LazyROS2 >>>"
END_MARKER = "# <<< LazyROS2 <<<"
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
VERSION_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
STARTING_GRACE_SECONDS = 30.0
LOCK_STARTING_GRACE_SECONDS = 30.0
TRANSACTION_SCHEMA_VERSION = 1
LOCK_SCHEMA_VERSION = 1
SOURCE_ITEMS = (
    "VERSION",
    "LICENSE",
    "pyproject.toml",
    "install.sh",
    "bin",
    "src",
    "shell",
)
OPTIONAL_SOURCE_ITEMS = ("SOURCE_REF", "SOURCE_COMMIT")


class InstallError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise InstallError(message)


def parse_args(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="install.sh",
        description="Install or remove LazyROS2 for the current user.",
    )
    parser.add_argument("--uninstall", action="store_true", help="remove the managed installation")
    parser.add_argument("--purge", action="store_true", help="also remove LazyROS2 user state")
    parser.add_argument("--force", action="store_true", help="remove modified managed files")
    parser.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-rc", action="store_true", help="do not edit a shell rc file")
    parser.add_argument("--reinstall", action="store_true", help="replace the same version")
    parser.add_argument(
        "--allow-downgrade",
        action="store_true",
        help="allow installing an older version",
    )
    options = parser.parse_args(arguments)

    install_only = options.no_rc or options.reinstall or options.allow_downgrade
    uninstall_only = options.purge or options.force or options.yes
    if options.uninstall and install_only:
        parser.error("installation flags cannot be used with --uninstall")
    if not options.uninstall and uninstall_only:
        parser.error("--purge, --force, and --yes require --uninstall")
    return options


def require_supported_python() -> None:
    if sys.version_info >= MINIMUM_PYTHON:
        return
    version = ".".join(str(part) for part in MINIMUM_PYTHON)
    fail(f"Python {version} or newer is required")


def require_non_root() -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        fail("root installation is not supported. Run this as the target user")


def require_home() -> Path:
    raw_home = os.environ.get("HOME")
    if not raw_home:
        fail("HOME is not set")
    home = Path(raw_home).expanduser().resolve()
    if not home.is_dir():
        fail(f"HOME is not a directory: {home}")
    return home


def read_version(path: Path) -> str:
    try:
        version = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        fail(f"cannot read {path}: {error}")
    if not VERSION_PATTERN.fullmatch(version):
        fail(f"VERSION must contain a stable semantic version, got {version!r}")
    return version


def version_tuple(version: str) -> tuple[int, int, int]:
    match = VERSION_PATTERN.fullmatch(version)
    if not match:
        fail(f"manifest app_version is invalid: {version!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalized_mode(path: Path) -> str:
    return f"{stat.S_IMODE(path.lstat().st_mode):04o}"


def path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    if path.is_dir():
        shutil.rmtree(path)


def ensure_directory(path: Path, relative: str, created: list[str]) -> None:
    if path.is_symlink():
        fail(f"installation directory must not be a symlink: {path}")
    if path_exists(path) and not path.is_dir():
        fail(f"installation directory is not a directory: {path}")
    if path.is_dir():
        return
    try:
        path.mkdir()
    except FileExistsError:
        if path.is_symlink() or not path.is_dir():
            fail(f"installation directory is not a directory: {path}")
        return
    created.append(relative)


def fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_tree(root: Path) -> None:
    directories: list[Path] = []
    for directory, names, filenames in os.walk(root, followlinks=False):
        current = Path(directory)
        directories.append(current)
        for name in filenames:
            path = current / name
            if path.is_symlink() or not path.is_file():
                fail(f"cannot persist unsupported staged entry: {path}")
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        for name in names:
            if (current / name).is_symlink():
                fail(f"cannot persist staged symlink: {current / name}")
    for directory in reversed(directories):
        fsync_directory(directory)


def atomic_write(path: Path, content: bytes, mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(content)
            destination.flush()
            os.fchmod(destination.fileno(), mode)
            os.fsync(destination.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_symlink(path: Path, target: str) -> None:
    temporary = path.parent / f".{path.name}.{os.getpid()}.{time.monotonic_ns()}"
    try:
        temporary.symlink_to(target)
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def unlink_durable(path: Path) -> None:
    path.unlink(missing_ok=True)
    fsync_directory(path.parent)


def exchange_paths(first: Path, second: Path) -> None:
    renameat2 = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
    if renameat2 is None:
        fail("same-version reinstall requires renameat2(RENAME_EXCHANGE) on this Linux host")
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    at_fdcwd = -100
    rename_exchange = 2
    result = renameat2(
        at_fdcwd,
        os.fsencode(first),
        at_fdcwd,
        os.fsencode(second),
        rename_exchange,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        if error_number in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}:
            fail("the filesystem cannot atomically exchange same-version payload directories")
        raise OSError(error_number, os.strerror(error_number), str(first), str(second))
    fsync_directory(first.parent)
    if second.parent != first.parent:
        fsync_directory(second.parent)


def validate_source_tree(source_root: Path) -> None:
    for relative in SOURCE_ITEMS:
        path = source_root / relative
        if not path_exists(path):
            fail(f"release is incomplete; missing {relative}")

    required_files = (
        source_root / "bin" / "lazy",
        source_root / "src" / "lazyros2" / "__main__.py",
        source_root / "shell" / "lazy-init.bash",
        source_root / "shell" / "lazy-init.zsh",
    )
    for path in required_files:
        if not path.is_file() or path.is_symlink():
            fail(f"release is incomplete; expected a regular file at {path}")

    selected = [source_root / relative for relative in SOURCE_ITEMS]
    selected.extend(
        source_root / relative
        for relative in OPTIONAL_SOURCE_ITEMS
        if path_exists(source_root / relative)
    )
    for root in selected:
        if root.is_symlink():
            fail(f"release payload must not contain symlinks: {root}")
        if root.is_file():
            continue
        for directory, names, filenames in os.walk(root, followlinks=False):
            for name in [*names, *filenames]:
                candidate = Path(directory) / name
                if candidate.is_symlink():
                    fail(f"release payload must not contain symlinks: {candidate}")


def copy_payload(source_root: Path, destination: Path) -> None:
    destination.mkdir()
    for relative in (*SOURCE_ITEMS, *OPTIONAL_SOURCE_ITEMS):
        source = source_root / relative
        if not path_exists(source):
            continue
        target = destination / relative
        if source.is_dir():
            shutil.copytree(source, target, copy_function=shutil.copy2)
        else:
            shutil.copy2(source, target)

    os.chmod(destination / "bin" / "lazy", 0o755)
    os.chmod(destination / "install.sh", 0o755)
    fsync_tree(destination)
    fsync_directory(destination.parent)


def smoke_test(payload: Path, version: str) -> None:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        completed = subprocess.run(
            [str(payload / "bin" / "lazy"), "--version"],
            cwd=payload,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        fail(f"staged launcher smoke test could not run: {error}")
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        suffix = f": {detail}" if detail else ""
        fail(f"staged launcher failed --version with exit code {completed.returncode}{suffix}")
    output = completed.stdout.decode("utf-8", errors="replace")
    if version not in output:
        fail(f"staged launcher --version output does not contain {version}")


def tree_snapshot(root: Path) -> dict[str, tuple[str, str, str]]:
    snapshot: dict[str, tuple[str, str, str]] = {}
    if not root.is_dir() or root.is_symlink():
        fail(f"managed payload is not a directory: {root}")
    for directory, names, filenames in os.walk(root, followlinks=False):
        for name in sorted(names):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                fail(f"managed payload contains an unexpected symlink: {path}")
            snapshot[f"{relative}/"] = ("directory", normalized_mode(path), "")
        for name in sorted(filenames):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or not path.is_file():
                fail(f"managed payload contains an unsupported file type: {path}")
            snapshot[relative] = ("file", normalized_mode(path), sha256_file(path))
    return snapshot


def payloads_match(first: Path, second: Path) -> bool:
    return tree_snapshot(first) == tree_snapshot(second)


def source_metadata(source_root: Path) -> tuple[str, str]:
    ref_path = source_root / "SOURCE_REF"
    commit_path = source_root / "SOURCE_COMMIT"
    if ref_path.is_file() and commit_path.is_file():
        return (
            ref_path.read_text(encoding="utf-8").strip(),
            commit_path.read_text(encoding="utf-8").strip(),
        )

    if shutil.which("git") and (source_root / ".git").exists():
        ref = subprocess.run(
            ["git", "-C", str(source_root), "describe", "--tags", "--always"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        ).stdout.strip()
        commit = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        ).stdout.strip()
        return ref or "development-tree", commit
    return "source-archive", ""


def rc_block(shell_name: str) -> str:
    init_name = "lazy-init.bash" if shell_name == "bash" else "lazy-init.zsh"
    return "\n".join(
        (
            BEGIN_MARKER,
            'case ":${PATH}:" in',
            '    *":${HOME}/.local/bin:"*) ;;',
            '    *) export PATH="${HOME}/.local/bin:${PATH}" ;;',
            "esac",
            f'if [ -r "${{HOME}}/.local/lib/lazyros2/current/shell/{init_name}" ]; then',
            f'    . "${{HOME}}/.local/lib/lazyros2/current/shell/{init_name}"',
            "fi",
            END_MARKER,
        )
    )


def marker_span(content: str, rc_path: Path) -> tuple[int, int] | None:
    lines = content.splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == BEGIN_MARKER]
    ends = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == END_MARKER]
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or starts[0] > ends[0]:
        fail(f"LazyROS2 marker block is malformed in {rc_path}")
    return starts[0], ends[0] + 1


def extract_marker(content: str, rc_path: Path) -> str | None:
    span = marker_span(content, rc_path)
    if span is None:
        return None
    lines = content.splitlines(keepends=True)
    block = "".join(lines[span[0] : span[1]])
    return block.rstrip("\r\n")


def replace_marker(content: str, block: str, rc_path: Path) -> str:
    span = marker_span(content, rc_path)
    if span is None:
        separator = "" if not content or content.endswith(("\n", "\r")) else "\n"
        return f"{content}{separator}{block}\n"
    lines = content.splitlines(keepends=True)
    return "".join(lines[: span[0]]) + block + "\n" + "".join(lines[span[1] :])


def remove_marker(content: str, rc_path: Path) -> str:
    span = marker_span(content, rc_path)
    if span is None:
        return content
    lines = content.splitlines(keepends=True)
    before = "".join(lines[: span[0]])
    after = "".join(lines[span[1] :])
    if before.endswith("\n") and after.startswith("\n"):
        after = after[1:]
    return before + after


def selected_rc_files(home: Path) -> list[tuple[Path, str]]:
    shell_name = Path(os.environ.get("SHELL", "")).name
    if shell_name in {"bash", "zsh"}:
        return [(home / f".{shell_name}rc", shell_name)]

    detected: list[tuple[Path, str]] = []
    for candidate in ("bash", "zsh"):
        path = home / f".{candidate}rc"
        if path.is_file() and not path.is_symlink():
            detected.append((path, candidate))
    return detected


def validate_home_relative(relative: str, allowed: set[str]) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        fail(f"manifest contains an unsafe home-relative path: {relative!r}")
    if relative not in allowed:
        fail(f"manifest contains an unmanaged rc path: {relative!r}")
    return Path(*pure.parts)


class Layout:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.local = home / ".local"
        self.bin = self.local / "bin"
        self.lib = self.local / "lib"
        self.app = self.lib / APP_NAME
        self.share = self.local / "share"
        self.metadata = self.share / APP_NAME
        self.manifest = self.metadata / "install-manifest.json"
        self.launcher = self.bin / "lazy"
        self.current = self.app / "current"
        self.uninstall_transaction = home / ".lazyros2-uninstall-transaction"
        self.uninstall_preparing = home / ".lazyros2-uninstall-preparing"
        self.uninstall_provenance = home / ".lazyros2-uninstall-preparing.json"
        self.uninstall_cleanup = home / ".lazyros2-uninstall-cleanup"

    def create(self) -> list[str]:
        managed_directories = (
            self.local,
            self.bin,
            self.lib,
            self.app,
            self.share,
            self.metadata,
        )
        for path in managed_directories:
            if path.is_symlink():
                fail(f"installation directory must not be a symlink: {path}")

        created: list[str] = []
        ensure_directory(self.local, ".local", created)
        ensure_directory(self.bin, ".local/bin", created)
        ensure_directory(self.lib, ".local/lib", created)
        ensure_directory(self.app, ".local/lib/lazyros2", created)
        ensure_directory(self.share, ".local/share", created)
        ensure_directory(self.metadata, ".local/share/lazyros2", created)
        return created


def process_identity(pid: int) -> str | None:
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        closing = stat_text.rfind(")")
        fields = stat_text[closing + 2 :].split()
        start_time = fields[19]
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except (OSError, IndexError, UnicodeError):
        return None
    return f"{boot_id}:{start_time}"


def process_matches(pid: int, identity: str | None) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    if identity is None:
        return True
    observed = process_identity(pid)
    return observed is None or observed == identity


class InstallLock:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.root = home / ".lazyros2-install.lock"
        self.owner_path = self.root / "owner.json"
        self.identity = process_identity(os.getpid())
        self.owner = {
            "schema_version": LOCK_SCHEMA_VERSION,
            "app": "LazyROS2",
            "home": str(home),
            "pid": os.getpid(),
            "identity": self.identity,
        }
        self.acquired = False

    def __enter__(self) -> "InstallLock":
        for _attempt in range(2):
            try:
                self.root.mkdir(mode=0o700)
            except FileExistsError:
                if not self._remove_stale():
                    fail("another LazyROS2 install or uninstall is already running")
                continue
            try:
                atomic_write(
                    self.owner_path,
                    (json.dumps(self.owner, sort_keys=True) + "\n").encode("utf-8"),
                    0o600,
                )
                fsync_directory(self.home)
            except BaseException:
                self.owner_path.unlink(missing_ok=True)
                try:
                    self.root.rmdir()
                except OSError:
                    pass
                raise
            self.acquired = True
            return self
        fail("could not acquire the LazyROS2 installer lock")

    def _remove_stale(self) -> bool:
        if self.root.is_symlink() or not self.root.is_dir():
            fail(f"installer lock path is not a private directory: {self.root}")
        details = self.root.stat()
        if details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) != 0o700:
            fail(f"installer lock directory has unsafe ownership or mode: {self.root}")
        entries = list(self.root.iterdir())
        if not entries:
            if time.time() - details.st_mtime <= LOCK_STARTING_GRACE_SECONDS:
                return False
            self.root.rmdir()
            fsync_directory(self.home)
            return True
        if entries != [self.owner_path] and set(entries) != {self.owner_path}:
            fail(f"installer lock directory contains unexpected entries: {self.root}")
        if self.owner_path.is_symlink() or not self.owner_path.is_file():
            fail(f"installer lock owner record is not a regular file: {self.owner_path}")
        try:
            owner = json.loads(self.owner_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            fail(f"cannot read installer lock owner record: {error}")
        expected_keys = {"schema_version", "app", "home", "pid", "identity"}
        if not isinstance(owner, dict) or set(owner) != expected_keys:
            fail("installer lock owner record is malformed")
        if (
            owner.get("schema_version") != LOCK_SCHEMA_VERSION
            or owner.get("app") != "LazyROS2"
            or owner.get("home") != str(self.home)
        ):
            fail("installer lock owner record does not belong to this installation")
        pid = owner.get("pid")
        identity = owner.get("identity")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            fail("installer lock owner pid is invalid")
        if identity is not None and not isinstance(identity, str):
            fail("installer lock owner identity is invalid")
        if process_matches(pid, identity):
            return False
        self.owner_path.unlink()
        self.root.rmdir()
        fsync_directory(self.home)
        return True

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        if not self.acquired:
            return
        try:
            if self.owner_path.is_file() and not self.owner_path.is_symlink():
                current = json.loads(self.owner_path.read_text(encoding="utf-8"))
                if current == self.owner:
                    self.owner_path.unlink()
            self.root.rmdir()
            fsync_directory(self.home)
        except (OSError, UnicodeError, json.JSONDecodeError):
            if exc_type is None:
                fail(f"could not safely release installer lock: {self.root}")


def safe_fixed_local_relative(layout: Layout, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        fail(f"unsafe ~/.local-relative path: {relative!r}")

    current = layout.local
    if current.is_symlink():
        fail(f"manifest path crosses a symlinked ~/.local root: {relative!r}")
    if path_exists(current) and not current.is_dir():
        fail(f"manifest path crosses a non-directory ~/.local root: {relative!r}")
    for part in pure.parts[:-1]:
        current = current / part
        if current.is_symlink():
            fail(f"manifest path crosses a symlinked parent component: {relative!r}")
        if path_exists(current) and not current.is_dir():
            fail(f"manifest path crosses a non-directory parent component: {relative!r}")

    candidate = layout.local.joinpath(*pure.parts)
    local_real = layout.local.resolve()
    parent_real = candidate.parent.resolve(strict=False)
    try:
        parent_real.relative_to(local_real)
    except ValueError:
        fail(f"manifest path escapes ~/.local through a symlink: {relative!r}")
    return candidate


def safe_manifest_path(layout: Layout) -> Path:
    return safe_fixed_local_relative(layout, "share/lazyros2/install-manifest.json")


def safe_local_path(layout: Layout, relative: str, version: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        fail(f"manifest contains an unsafe managed path: {relative!r}")

    allowed_exact = {"bin/lazy", "lib/lazyros2/current"}
    payload_root = f"lib/lazyros2/{version}"
    payload_prefix = f"{payload_root}/"
    if (
        relative not in allowed_exact
        and relative != payload_root
        and not relative.startswith(payload_prefix)
    ):
        fail(f"manifest path is outside the LazyROS2 installation: {relative!r}")
    return safe_fixed_local_relative(layout, relative)


def validate_manifest(layout: Layout, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        fail("install manifest root must be an object")
    if raw.get("schema_version") != SCHEMA_VERSION:
        fail(f"unsupported install manifest schema: {raw.get('schema_version')!r}")
    if raw.get("app") != "LazyROS2":
        fail("install manifest does not belong to LazyROS2")
    if raw.get("home") != str(layout.home):
        fail("install manifest belongs to a different home directory")

    version = raw.get("app_version")
    if not isinstance(version, str):
        fail("install manifest app_version must be a string")
    version_tuple(version)

    entries = raw.get("files")
    if not isinstance(entries, list) or not entries:
        fail("install manifest files must be a non-empty array")
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            fail(f"manifest files[{index}] must be an object")
        if entry.get("root") != "local":
            fail(f"manifest files[{index}] has an unknown root")
        relative = entry.get("path")
        if not isinstance(relative, str):
            fail(f"manifest files[{index}].path must be a string")
        safe_local_path(layout, relative, version)
        if relative in seen:
            fail(f"manifest contains duplicate path: {relative}")
        seen.add(relative)
        if entry.get("type") not in {"file", "symlink"}:
            fail(f"manifest files[{index}] has an unsupported type")
        digest = entry.get("sha256")
        mode = entry.get("mode")
        if not isinstance(digest, str) or not HASH_PATTERN.fullmatch(digest):
            fail(f"manifest files[{index}] has an invalid sha256")
        if not isinstance(mode, str) or not re.fullmatch(r"[0-7]{4}", mode):
            fail(f"manifest files[{index}] has an invalid mode")

    for required in ("bin/lazy", "lib/lazyros2/current", f"lib/lazyros2/{version}/VERSION"):
        if required not in seen:
            fail(f"manifest is missing required managed path: {required}")

    directories = raw.get("created_directories")
    if not isinstance(directories, list):
        fail("manifest created_directories must be an array")
    for index, entry in enumerate(directories):
        if not isinstance(entry, dict) or entry.get("root") != "local":
            fail(f"manifest created_directories[{index}] is invalid")
        relative = entry.get("path")
        if not isinstance(relative, str):
            fail(f"manifest created_directories[{index}].path must be a string")
        if relative in {"bin", "lib", "share", "lib/lazyros2", "share/lazyros2"}:
            safe_fixed_local_relative(layout, relative)
            continue
        safe_local_path(layout, relative, version)
        if relative == f"lib/lazyros2/{version}" or relative.startswith(
            f"lib/lazyros2/{version}/"
        ):
            continue
        fail(f"manifest contains an unmanaged directory: {relative!r}")

    rc_entries = raw.get("rc_files")
    if not isinstance(rc_entries, list):
        fail("manifest rc_files must be an array")
    rc_seen: set[str] = set()
    for index, entry in enumerate(rc_entries):
        if not isinstance(entry, dict):
            fail(f"manifest rc_files[{index}] must be an object")
        relative = entry.get("path")
        if not isinstance(relative, str):
            fail(f"manifest rc_files[{index}].path must be a string")
        validate_home_relative(relative, {".bashrc", ".zshrc"})
        if relative in rc_seen:
            fail(f"manifest contains duplicate rc path: {relative}")
        rc_seen.add(relative)
        digest = entry.get("block_sha256")
        if not isinstance(digest, str) or not HASH_PATTERN.fullmatch(digest):
            fail(f"manifest rc_files[{index}] has an invalid block_sha256")
        if not isinstance(entry.get("created"), bool):
            fail(f"manifest rc_files[{index}].created must be boolean")
    return raw


def read_manifest(layout: Layout) -> dict[str, Any] | None:
    manifest_path = safe_manifest_path(layout)
    if not manifest_path.exists():
        if manifest_path.is_symlink():
            fail(f"install manifest must not be a symlink: {manifest_path}")
        return None
    if manifest_path.is_symlink() or not manifest_path.is_file():
        fail(f"install manifest must be a regular file: {manifest_path}")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        fail(f"cannot read install manifest: {error}")
    return validate_manifest(layout, raw)


def verify_rc_entries(layout: Layout, manifest: dict[str, Any]) -> None:
    for entry in manifest["rc_files"]:
        rc_path = layout.home / validate_home_relative(entry["path"], {".bashrc", ".zshrc"})
        if rc_path.is_symlink() or not rc_path.is_file():
            fail(f"managed rc file is missing or not a regular file: {rc_path}")
        content = rc_path.read_text(encoding="utf-8")
        block = extract_marker(content, rc_path)
        if block is None or sha256_bytes(block.encode("utf-8")) != entry["block_sha256"]:
            fail(f"managed LazyROS2 marker block was modified in {rc_path}")


def verify_manifest_files(layout: Layout, manifest: dict[str, Any], force: bool = False) -> None:
    version = manifest["app_version"]
    errors: list[str] = []
    for entry in manifest["files"]:
        path = safe_local_path(layout, entry["path"], version)
        actual_type = "symlink" if path.is_symlink() else "file" if path.is_file() else "missing"
        if actual_type != entry["type"]:
            errors.append(f"{entry['path']}: expected {entry['type']}, found {actual_type}")
            continue
        digest = (
            sha256_bytes(os.readlink(path).encode("utf-8"))
            if actual_type == "symlink"
            else sha256_file(path)
        )
        if digest != entry["sha256"]:
            errors.append(f"{entry['path']}: sha256 mismatch")
        if normalized_mode(path) != entry["mode"]:
            errors.append(f"{entry['path']}: mode mismatch")
    if errors and not force:
        detail = "\n  ".join(errors)
        fail(f"managed installation was modified:\n  {detail}\nUse --force only if removal is intended.")


def verify_no_unmanaged_payload_entries(layout: Layout, manifest: dict[str, Any]) -> None:
    version = manifest["app_version"]
    payload_root = f"lib/lazyros2/{version}"
    payload_prefix = f"{payload_root}/"
    expected: set[str] = set()

    for entry in manifest["files"]:
        relative = entry["path"]
        if relative.startswith(payload_prefix):
            expected.add(relative.removeprefix(payload_prefix))

    for entry in manifest["created_directories"]:
        relative = entry["path"]
        if relative.startswith(payload_prefix):
            expected.add(f"{relative.removeprefix(payload_prefix)}/")

    actual = set(tree_snapshot(layout.app / version))
    unexpected = sorted(actual - expected)
    missing = sorted(expected - actual)
    if not unexpected and not missing:
        return

    details: list[str] = []
    if unexpected:
        details.append("untracked: " + ", ".join(unexpected))
    if missing:
        details.append("missing: " + ", ".join(missing))
    fail(
        "managed payload tree differs from its manifest; refusing to replace it:\n  "
        + "\n  ".join(details)
    )


def ensure_no_unmanaged_collisions(layout: Layout, manifest: dict[str, Any] | None) -> None:
    if manifest is None:
        collisions = [path for path in (layout.launcher, layout.current) if path_exists(path)]
        if layout.app.is_dir():
            collisions.extend(layout.app.iterdir())
        marker_paths = []
        for name in (".bashrc", ".zshrc"):
            path = layout.home / name
            if path.is_file() and not path.is_symlink():
                if extract_marker(path.read_text(encoding="utf-8"), path) is not None:
                    marker_paths.append(path)
        collisions.extend(marker_paths)
        if collisions:
            listed = ", ".join(str(path) for path in collisions)
            fail(f"unmanaged LazyROS2 paths already exist: {listed}")
        return

    version = manifest["app_version"]
    allowed = {version, "current"}
    unexpected = [path for path in layout.app.iterdir() if path.name not in allowed]
    if unexpected:
        listed = ", ".join(str(path) for path in unexpected)
        fail(f"unmanaged entries exist in the LazyROS2 payload directory: {listed}")


def launcher_content() -> bytes:
    return (
        "#!/bin/sh\n"
        "# SPDX-License-Identifier: AGPL-3.0-or-later\n\n"
        "set -eu\n\n"
        'exec "${HOME}/.local/lib/lazyros2/current/bin/lazy" "$@"\n'
    ).encode("utf-8")


def payload_records(payload: Path, version: str) -> tuple[list[dict[str, str]], list[str]]:
    files: list[dict[str, str]] = []
    directories = [f"lib/lazyros2/{version}"]
    for directory, names, filenames in os.walk(payload, followlinks=False):
        root = Path(directory)
        for name in sorted(names):
            path = root / name
            if path.is_symlink():
                fail(f"staged payload contains an unexpected symlink: {path}")
            relative = path.relative_to(payload).as_posix()
            directories.append(f"lib/lazyros2/{version}/{relative}")
        for name in sorted(filenames):
            path = root / name
            if path.is_symlink() or not path.is_file():
                fail(f"staged payload contains an unsupported file: {path}")
            relative = path.relative_to(payload).as_posix()
            files.append(
                {
                    "root": "local",
                    "path": f"lib/lazyros2/{version}/{relative}",
                    "type": "file",
                    "mode": normalized_mode(path),
                    "sha256": sha256_file(path),
                }
            )
    return files, directories


def build_manifest(
    layout: Layout,
    payload: Path,
    version: str,
    source_root: Path,
    created_layout: Iterable[str],
    rc_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    files, payload_directories = payload_records(payload, version)
    launcher = launcher_content()
    files.extend(
        (
            {
                "root": "local",
                "path": "bin/lazy",
                "type": "file",
                "mode": "0755",
                "sha256": sha256_bytes(launcher),
            },
            {
                "root": "local",
                "path": "lib/lazyros2/current",
                "type": "symlink",
                "mode": "0777",
                "sha256": sha256_bytes(version.encode("utf-8")),
            },
        )
    )
    source_ref, source_commit = source_metadata(source_root)
    layout_directories = [
        relative.removeprefix(".local/")
        for relative in created_layout
        if relative.startswith(".local/")
    ]
    created = sorted(set([*layout_directories, *payload_directories]), key=lambda item: (item.count("/"), item))
    return {
        "schema_version": SCHEMA_VERSION,
        "app": "LazyROS2",
        "app_version": version,
        "license": "AGPL-3.0-or-later",
        "home": str(layout.home),
        "installed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": {"ref": source_ref, "commit": source_commit},
        "files": sorted(files, key=lambda entry: entry["path"]),
        "created_directories": [
            {"root": "local", "path": relative} for relative in created
        ],
        "rc_files": sorted(rc_entries, key=lambda entry: entry["path"]),
    }


def payload_matches_manifest(payload: Path, manifest: dict[str, Any], version: str) -> bool:
    if payload.is_symlink() or not payload.is_dir():
        return False
    prefix = f"lib/lazyros2/{version}/"
    expected_files: dict[str, dict[str, Any]] = {}
    expected_directories: set[str] = set()
    for entry in manifest["files"]:
        relative = entry["path"]
        if relative.startswith(prefix):
            expected_files[relative.removeprefix(prefix)] = entry
    payload_root = f"lib/lazyros2/{version}"
    for entry in manifest["created_directories"]:
        relative = entry["path"]
        if relative.startswith(prefix):
            expected_directories.add(relative.removeprefix(prefix))
        elif relative == payload_root:
            continue
    try:
        actual = tree_snapshot(payload)
    except InstallError:
        return False
    actual_files = {key for key in actual if not key.endswith("/")}
    actual_directories = {key[:-1] for key in actual if key.endswith("/")}
    if actual_files != set(expected_files) or actual_directories != expected_directories:
        return False
    for relative, entry in expected_files.items():
        actual_type, actual_mode, actual_digest = actual[relative]
        if (
            actual_type != "file"
            or actual_mode != entry["mode"]
            or actual_digest != entry["sha256"]
        ):
            return False
    return True


def payload_is_owned_subset(payload: Path, manifest: dict[str, Any], version: str) -> bool:
    if not path_exists(payload):
        return True
    if payload.is_symlink() or not payload.is_dir():
        return False
    prefix = f"lib/lazyros2/{version}/"
    expected_files = {
        entry["path"].removeprefix(prefix): entry
        for entry in manifest["files"]
        if entry["path"].startswith(prefix)
    }
    expected_directories = {
        entry["path"].removeprefix(prefix)
        for entry in manifest["created_directories"]
        if entry["path"].startswith(prefix)
    }
    try:
        actual = tree_snapshot(payload)
    except InstallError:
        return False
    for relative, (actual_type, actual_mode, actual_digest) in actual.items():
        if relative.endswith("/"):
            if relative[:-1] not in expected_directories:
                return False
            continue
        entry = expected_files.get(relative)
        if entry is None:
            return False
        if (
            actual_type != "file"
            or actual_mode != entry["mode"]
            or actual_digest != entry["sha256"]
        ):
            return False
    return True


def remove_path_durable(path: Path) -> None:
    if not path_exists(path):
        return
    parent = path.parent
    remove_path(path)
    fsync_directory(parent)


def private_transaction_directory(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        fail(f"installer transaction path is not a directory: {path}")
    details = path.stat()
    if details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) != 0o700:
        fail(f"installer transaction has unsafe ownership or mode: {path}")


def write_scratch_marker(layout: Layout, root: Path, nonce: str) -> None:
    marker = {
        "schema_version": TRANSACTION_SCHEMA_VERSION,
        "app": "LazyROS2",
        "home": str(layout.home),
        "nonce": nonce,
    }
    atomic_write(
        root / "preparing.json",
        (json.dumps(marker, sort_keys=True) + "\n").encode("utf-8"),
        0o600,
    )


def validate_scratch_marker(layout: Layout, root: Path) -> None:
    marker_path = root / "preparing.json"
    if not marker_path.exists() and not any(root.iterdir()):
        return
    if marker_path.is_symlink() or not marker_path.is_file():
        fail(f"installer scratch directory cannot be recovered safely without its ownership marker: {root}")
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        fail(f"cannot read installer scratch marker {marker_path}: {error}")
    expected_keys = {"schema_version", "app", "home", "nonce"}
    if not isinstance(marker, dict) or set(marker) != expected_keys:
        fail(f"installer scratch marker is malformed: {marker_path}")
    nonce = marker.get("nonce")
    if (
        marker.get("schema_version") != TRANSACTION_SCHEMA_VERSION
        or marker.get("app") != "LazyROS2"
        or marker.get("home") != str(layout.home)
        or not isinstance(nonce, str)
        or not nonce
        or root.name not in {f".preparing-{nonce}", f".transaction-{nonce}", f".cleanup-{nonce}"}
    ):
        fail(f"installer scratch marker does not belong to this installation: {root}")


def discard_scratch_directory(layout: Layout, root: Path) -> None:
    private_transaction_directory(root)
    validate_scratch_marker(layout, root)
    marker = root / "preparing.json"
    for entry in list(root.iterdir()):
        if entry == marker:
            continue
        remove_path(entry)
    fsync_directory(root)
    marker.unlink(missing_ok=True)
    fsync_directory(root)
    root.rmdir()
    fsync_directory(root.parent)


def parse_transaction_manifest(layout: Layout, path: Path, digest: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        fail(f"installer transaction manifest is missing or unsafe: {path}")
    content = path.read_bytes()
    if sha256_bytes(content) != digest:
        fail(f"installer transaction manifest hash does not match: {path}")
    try:
        raw = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        fail(f"cannot read installer transaction manifest {path}: {error}")
    return validate_manifest(layout, raw)


def read_transaction(layout: Layout, root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    private_transaction_directory(root)
    validate_scratch_marker(layout, root)
    journal_path = root / "journal.json"
    if journal_path.is_symlink() or not journal_path.is_file():
        fail(
            f"unrecognized installer transaction cannot be recovered safely: {root}; "
            "leave it in place for manual inspection"
        )
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        fail(f"cannot read installer transaction journal {journal_path}: {error}")
    required_keys = {
        "schema_version",
        "app",
        "home",
        "transaction",
        "state",
        "version",
        "old_version",
        "new_manifest_sha256",
        "old_manifest_sha256",
        "rc_snapshots",
    }
    if not isinstance(journal, dict) or set(journal) != required_keys:
        fail(f"installer transaction journal is malformed: {journal_path}")
    if (
        journal.get("schema_version") != TRANSACTION_SCHEMA_VERSION
        or journal.get("app") != "LazyROS2"
        or journal.get("home") != str(layout.home)
        or journal.get("transaction") != root.name
    ):
        fail(f"installer transaction does not belong to this installation: {root}")
    if journal.get("state") not in {"prepared", "committed"}:
        fail(f"installer transaction has an unknown state: {root}")
    version = journal.get("version")
    old_version = journal.get("old_version")
    if not isinstance(version, str):
        fail(f"installer transaction version is invalid: {root}")
    version_tuple(version)
    if old_version is not None:
        if not isinstance(old_version, str):
            fail(f"installer transaction old_version is invalid: {root}")
        version_tuple(old_version)
    new_digest = journal.get("new_manifest_sha256")
    old_digest = journal.get("old_manifest_sha256")
    if not isinstance(new_digest, str) or not HASH_PATTERN.fullmatch(new_digest):
        fail(f"installer transaction new manifest hash is invalid: {root}")
    if old_digest is not None and (
        not isinstance(old_digest, str) or not HASH_PATTERN.fullmatch(old_digest)
    ):
        fail(f"installer transaction old manifest hash is invalid: {root}")
    if (old_version is None) != (old_digest is None):
        fail(f"installer transaction old installation metadata is inconsistent: {root}")
    snapshots = journal.get("rc_snapshots")
    if not isinstance(snapshots, list):
        fail(f"installer transaction rc snapshots are invalid: {root}")
    backup_names: set[str] = set()
    for index, snapshot in enumerate(snapshots):
        keys = {"path", "backup", "existed", "mode", "old_sha256", "new_sha256"}
        if not isinstance(snapshot, dict) or set(snapshot) != keys:
            fail(f"installer transaction rc snapshot {index} is malformed: {root}")
        relative = snapshot.get("path")
        if not isinstance(relative, str):
            fail(f"installer transaction rc path is invalid: {root}")
        validate_home_relative(relative, {".bashrc", ".zshrc"})
        backup = snapshot.get("backup")
        existed = snapshot.get("existed")
        mode = snapshot.get("mode")
        old_sha = snapshot.get("old_sha256")
        new_sha = snapshot.get("new_sha256")
        if not isinstance(existed, bool) or not isinstance(mode, str) or not re.fullmatch(r"[0-7]{4}", mode):
            fail(f"installer transaction rc snapshot metadata is invalid: {root}")
        if not isinstance(new_sha, str) or not HASH_PATTERN.fullmatch(new_sha):
            fail(f"installer transaction rc new hash is invalid: {root}")
        if existed:
            if not isinstance(backup, str) or not re.fullmatch(r"rc-[0-9]+", backup):
                fail(f"installer transaction rc backup name is invalid: {root}")
            if not isinstance(old_sha, str) or not HASH_PATTERN.fullmatch(old_sha):
                fail(f"installer transaction rc old hash is invalid: {root}")
            if backup in backup_names:
                fail(f"installer transaction contains duplicate rc backup: {root}")
            backup_names.add(backup)
        elif backup is not None or old_sha is not None:
            fail(f"installer transaction has a backup for a new rc file: {root}")

    allowed_root = {"preparing.json", "journal.json", "new-manifest.json", "backups", "payload"}
    if old_version is not None:
        allowed_root.add("old-manifest.json")
    unexpected_root = {entry.name for entry in root.iterdir()} - allowed_root
    if unexpected_root:
        fail(f"installer transaction contains unexpected entries: {root}")
    backups = root / "backups"
    if backups.is_symlink() or not backups.is_dir():
        fail(f"installer transaction backup directory is missing or unsafe: {backups}")
    unexpected_backups = {entry.name for entry in backups.iterdir()} - backup_names
    if unexpected_backups:
        fail(f"installer transaction contains unexpected backups: {root}")

    new_manifest = parse_transaction_manifest(layout, root / "new-manifest.json", new_digest)
    if new_manifest["app_version"] != version:
        fail(f"installer transaction new manifest version does not match: {root}")
    old_manifest = None
    if old_version is not None and old_digest is not None:
        old_manifest = parse_transaction_manifest(layout, root / "old-manifest.json", old_digest)
        if old_manifest["app_version"] != old_version:
            fail(f"installer transaction old manifest version does not match: {root}")
    return journal, new_manifest, old_manifest


def write_transaction_journal(root: Path, journal: dict[str, Any]) -> None:
    atomic_write(
        root / "journal.json",
        (json.dumps(journal, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        0o600,
    )


def prepare_transaction(
    layout: Layout,
    preparing_root: Path,
    final_name: str,
    manifest_bytes: bytes,
    existing: dict[str, Any] | None,
    rc_updates: dict[Path, str],
) -> Path:
    backups = preparing_root / "backups"
    backups.mkdir(mode=0o700)
    atomic_write(preparing_root / "new-manifest.json", manifest_bytes, 0o600)
    old_manifest_bytes: bytes | None = None
    if existing is not None:
        old_manifest_bytes = safe_manifest_path(layout).read_bytes()
        atomic_write(preparing_root / "old-manifest.json", old_manifest_bytes, 0o600)

    snapshots: list[dict[str, Any]] = []
    for index, (rc_path, new_content) in enumerate(sorted(rc_updates.items(), key=lambda item: str(item[0]))):
        relative = rc_path.relative_to(layout.home).as_posix()
        new_bytes = new_content.encode("utf-8")
        if path_exists(rc_path):
            if rc_path.is_symlink() or not rc_path.is_file():
                fail(f"shell rc path must be a regular file: {rc_path}")
            old_bytes = rc_path.read_bytes()
            backup_name = f"rc-{index}"
            atomic_write(backups / backup_name, old_bytes, 0o600)
            snapshots.append(
                {
                    "path": relative,
                    "backup": backup_name,
                    "existed": True,
                    "mode": normalized_mode(rc_path),
                    "old_sha256": sha256_bytes(old_bytes),
                    "new_sha256": sha256_bytes(new_bytes),
                }
            )
        else:
            snapshots.append(
                {
                    "path": relative,
                    "backup": None,
                    "existed": False,
                    "mode": "0600",
                    "old_sha256": None,
                    "new_sha256": sha256_bytes(new_bytes),
                }
            )
    journal = {
        "schema_version": TRANSACTION_SCHEMA_VERSION,
        "app": "LazyROS2",
        "home": str(layout.home),
        "transaction": final_name,
        "state": "prepared",
        "version": json.loads(manifest_bytes.decode("utf-8"))["app_version"],
        "old_version": existing["app_version"] if existing else None,
        "new_manifest_sha256": sha256_bytes(manifest_bytes),
        "old_manifest_sha256": sha256_bytes(old_manifest_bytes) if old_manifest_bytes else None,
        "rc_snapshots": snapshots,
    }
    write_transaction_journal(preparing_root, journal)
    fsync_tree(preparing_root)
    final_root = layout.app / final_name
    os.replace(preparing_root, final_root)
    fsync_directory(layout.app)
    return final_root


def restore_rc_snapshots(layout: Layout, root: Path, journal: dict[str, Any]) -> None:
    backups = root / "backups"
    for snapshot in journal["rc_snapshots"]:
        rc_path = layout.home / validate_home_relative(snapshot["path"], {".bashrc", ".zshrc"})
        current_digest = None
        if path_exists(rc_path):
            if rc_path.is_symlink() or not rc_path.is_file():
                fail(f"cannot recover modified shell rc path safely: {rc_path}")
            current_digest = sha256_file(rc_path)
        allowed = {snapshot["new_sha256"]}
        if snapshot["old_sha256"] is not None:
            allowed.add(snapshot["old_sha256"])
        elif current_digest is None:
            allowed.add(None)
        if current_digest not in allowed:
            fail(f"shell rc file changed after the interrupted transaction: {rc_path}")
        if snapshot["existed"]:
            backup = backups / snapshot["backup"]
            if backup.is_symlink() or not backup.is_file():
                fail(f"installer transaction rc backup is missing: {backup}")
            old_bytes = backup.read_bytes()
            if sha256_bytes(old_bytes) != snapshot["old_sha256"]:
                fail(f"installer transaction rc backup hash does not match: {backup}")
            if current_digest != snapshot["old_sha256"] or normalized_mode(rc_path) != snapshot["mode"]:
                atomic_write(rc_path, old_bytes, int(snapshot["mode"], 8))
        elif current_digest == snapshot["new_sha256"]:
            unlink_durable(rc_path)


def restore_manifest(layout: Layout, root: Path, journal: dict[str, Any]) -> None:
    manifest_path = safe_manifest_path(layout)
    current_digest = sha256_file(manifest_path) if manifest_path.is_file() and not manifest_path.is_symlink() else None
    if path_exists(manifest_path) and current_digest is None:
        fail(f"cannot recover unsafe install manifest path: {manifest_path}")
    allowed = {journal["new_manifest_sha256"], journal["old_manifest_sha256"], None}
    if current_digest not in allowed:
        fail("install manifest changed after the interrupted transaction")
    if journal["old_manifest_sha256"] is None:
        if current_digest == journal["new_manifest_sha256"]:
            unlink_durable(manifest_path)
        return
    backup = root / "old-manifest.json"
    old_bytes = backup.read_bytes()
    if sha256_bytes(old_bytes) != journal["old_manifest_sha256"]:
        fail(f"installer transaction old manifest backup hash does not match: {backup}")
    if current_digest != journal["old_manifest_sha256"]:
        atomic_write(manifest_path, old_bytes, 0o600)


def restore_current(layout: Layout, old_version: str | None, new_version: str) -> None:
    if path_exists(layout.current) and not layout.current.is_symlink():
        fail(f"cannot recover unsafe current path: {layout.current}")
    observed = os.readlink(layout.current) if layout.current.is_symlink() else None
    if observed not in {old_version, new_version, None}:
        fail("current symlink changed after the interrupted transaction")
    if old_version is None:
        if observed == new_version:
            unlink_durable(layout.current)
    elif observed != old_version:
        atomic_symlink(layout.current, old_version)


def restore_launcher(layout: Layout, old_manifest: dict[str, Any] | None) -> None:
    if old_manifest is not None:
        launcher_entry = next(entry for entry in old_manifest["files"] if entry["path"] == "bin/lazy")
        if layout.launcher.is_symlink() or not layout.launcher.is_file():
            fail("managed launcher disappeared during the interrupted transaction")
        if sha256_file(layout.launcher) != launcher_entry["sha256"]:
            fail("managed launcher changed during the interrupted transaction")
        return
    if not path_exists(layout.launcher):
        return
    if layout.launcher.is_symlink() or not layout.launcher.is_file():
        fail("cannot recover unsafe launcher path")
    if sha256_file(layout.launcher) != sha256_bytes(launcher_content()):
        fail("launcher changed after the interrupted transaction")
    unlink_durable(layout.launcher)


def cleanup_transaction_root(root: Path) -> None:
    private_transaction_directory(root)
    cleanup = root.parent / root.name.replace(".transaction-", ".cleanup-", 1)
    if path_exists(cleanup):
        fail(f"installer cleanup path already exists: {cleanup}")
    os.replace(root, cleanup)
    fsync_directory(cleanup.parent)
    marker = cleanup / "preparing.json"
    for entry in list(cleanup.iterdir()):
        if entry == marker:
            continue
        remove_path(entry)
    fsync_directory(cleanup)
    marker.unlink(missing_ok=True)
    fsync_directory(cleanup)
    cleanup.rmdir()
    fsync_directory(cleanup.parent)


def rollback_transaction(
    layout: Layout,
    root: Path,
    journal: dict[str, Any],
    new_manifest: dict[str, Any],
    old_manifest: dict[str, Any] | None,
) -> None:
    version = journal["version"]
    old_version = journal["old_version"]
    target = layout.app / version
    staged = root / "payload"
    if old_version == version:
        target_is_new = payload_matches_manifest(target, new_manifest, version)
        target_is_old = old_manifest is not None and payload_matches_manifest(target, old_manifest, version)
        staged_is_new = payload_matches_manifest(staged, new_manifest, version)
        staged_is_old = old_manifest is not None and payload_matches_manifest(staged, old_manifest, version)
        if target_is_new and staged_is_old:
            exchange_paths(target, staged)
        elif not (target_is_old and staged_is_new):
            fail("same-version payload state is ambiguous; refusing automatic recovery")
        restore_current(layout, old_version, version)
    else:
        restore_current(layout, old_version, version)
        if path_exists(target):
            if not payload_matches_manifest(target, new_manifest, version):
                fail("new payload changed after the interrupted transaction")
            remove_path_durable(target)
        if path_exists(staged) and not payload_matches_manifest(staged, new_manifest, version):
            fail("staged payload changed after the interrupted transaction")
        if old_version is not None:
            old_payload = layout.app / old_version
            if old_manifest is None or not payload_matches_manifest(old_payload, old_manifest, old_version):
                fail("old payload cannot be verified during transaction recovery")
    restore_launcher(layout, old_manifest)
    restore_rc_snapshots(layout, root, journal)
    restore_manifest(layout, root, journal)
    cleanup_transaction_root(root)


def finish_committed_transaction(
    layout: Layout,
    root: Path,
    journal: dict[str, Any],
    new_manifest: dict[str, Any],
    old_manifest: dict[str, Any] | None,
) -> None:
    manifest_path = safe_manifest_path(layout)
    if manifest_path.is_symlink() or not manifest_path.is_file():
        fail("committed transaction is missing its install manifest")
    if sha256_file(manifest_path) != journal["new_manifest_sha256"]:
        fail("committed transaction manifest changed; refusing automatic cleanup")
    verify_manifest_files(layout, new_manifest)
    verify_rc_entries(layout, new_manifest)
    old_version = journal["old_version"]
    version = journal["version"]
    if old_manifest is not None and old_version is not None:
        obsolete = root / "payload" if old_version == version else layout.app / old_version
        if path_exists(obsolete):
            if not payload_is_owned_subset(obsolete, old_manifest, old_version):
                fail("obsolete payload contains unknown content; refusing automatic cleanup")
            remove_path_durable(obsolete)
    cleanup_transaction_root(root)


def recover_transactions(layout: Layout) -> None:
    if not path_exists(layout.app):
        return
    if layout.app.is_symlink() or not layout.app.is_dir():
        fail(f"installation directory must not be a symlink: {layout.app}")
    discardable = sorted(
        path
        for path in layout.app.iterdir()
        if path.name.startswith(".preparing-") or path.name.startswith(".cleanup-")
    )
    for path in discardable:
        discard_scratch_directory(layout, path)
        print(f"install.sh: removed incomplete transaction scratch directory {path.name}", file=sys.stderr)
    transactions = sorted(path for path in layout.app.iterdir() if path.name.startswith(".transaction-"))
    if len(transactions) > 1:
        fail("multiple installer transactions exist; refusing ambiguous automatic recovery")
    if not transactions:
        return
    root = transactions[0]
    journal, new_manifest, old_manifest = read_transaction(layout, root)
    if journal["state"] == "committed":
        finish_committed_transaction(layout, root, journal, new_manifest, old_manifest)
        print(f"install.sh: completed cleanup for interrupted transaction {root.name}", file=sys.stderr)
    else:
        rollback_transaction(layout, root, journal, new_manifest, old_manifest)
        print(f"install.sh: rolled back interrupted transaction {root.name}", file=sys.stderr)


def planned_rc_entries(
    layout: Layout,
    existing: dict[str, Any] | None,
    no_rc: bool,
) -> tuple[list[dict[str, Any]], dict[Path, str]]:
    entries_by_path: dict[str, dict[str, Any]] = {}
    updates: dict[Path, str] = {}
    if existing:
        verify_rc_entries(layout, existing)
        entries_by_path = {entry["path"]: dict(entry) for entry in existing["rc_files"]}

    if no_rc:
        return list(entries_by_path.values()), updates

    selected = selected_rc_files(layout.home)
    if not selected:
        print("install.sh: no Bash or zsh rc file was detected; skipping rc integration", file=sys.stderr)
        return list(entries_by_path.values()), updates

    for rc_path, shell_name in selected:
        relative = rc_path.relative_to(layout.home).as_posix()
        if path_exists(rc_path) and (rc_path.is_symlink() or not rc_path.is_file()):
            fail(f"shell rc path must be a regular file: {rc_path}")
        content = rc_path.read_text(encoding="utf-8") if rc_path.exists() else ""
        current_block = extract_marker(content, rc_path)
        if current_block is not None and relative not in entries_by_path:
            fail(f"unmanaged LazyROS2 marker block already exists in {rc_path}")
        block = rc_block(shell_name)
        updates[rc_path] = replace_marker(content, block, rc_path)
        created = entries_by_path.get(relative, {}).get("created", not rc_path.exists())
        entries_by_path[relative] = {
            "path": relative,
            "shell": shell_name,
            "created": bool(created),
            "block_sha256": sha256_bytes(block.encode("utf-8")),
        }
    return list(entries_by_path.values()), updates


def carry_created_layout(existing: dict[str, Any] | None, newly_created: list[str]) -> list[str]:
    carried = set(newly_created)
    if existing:
        old_version = existing["app_version"]
        old_prefix = f"lib/lazyros2/{old_version}"
        for entry in existing["created_directories"]:
            relative = entry["path"]
            if relative != old_prefix and not relative.startswith(f"{old_prefix}/"):
                carried.add(f".local/{relative}")
    return sorted(carried)


def install(source_root: Path, options: argparse.Namespace) -> None:
    validate_source_tree(source_root)
    version = read_version(source_root / "VERSION")
    home = require_home()
    with InstallLock(home):
        layout = Layout(home)
        created_layout: list[str] = []
        scratch_root: Path | None = None
        transaction_root: Path | None = None
        transaction_committed = False
        try:
            recover_uninstall_transaction(layout, allow_finish=False)
            created_layout = layout.create()
            recover_transactions(layout)
            existing = read_manifest(layout)
            ensure_no_unmanaged_collisions(layout, existing)
            if existing:
                verify_manifest_files(layout, existing, force=options.reinstall)
                verify_no_unmanaged_payload_entries(layout, existing)
                verify_rc_entries(layout, existing)
                installed_version = existing["app_version"]
                if version_tuple(version) < version_tuple(installed_version) and not options.allow_downgrade:
                    fail(
                        f"refusing to downgrade {installed_version} to {version}; "
                        "use --allow-downgrade"
                    )

            scratch_root = Path(tempfile.mkdtemp(prefix=".preparing-", dir=layout.app))
            scratch_nonce = scratch_root.name.removeprefix(".preparing-")
            write_scratch_marker(layout, scratch_root, scratch_nonce)
            payload_stage = scratch_root / "payload"
            copy_payload(source_root, payload_stage)
            smoke_test(payload_stage, version)

            if existing and existing["app_version"] == version and not options.reinstall:
                installed_payload = layout.app / version
                if payloads_match(payload_stage, installed_payload):
                    remove_path_durable(scratch_root)
                    scratch_root = None
                    print(f"LazyROS2 {version} is already installed; no changes made.")
                    return
                fail(f"LazyROS2 {version} differs from the installed copy; use --reinstall")

            rc_entries, rc_updates = planned_rc_entries(layout, existing, options.no_rc)
            carried_layout = carry_created_layout(existing, created_layout)
            manifest = build_manifest(
                layout,
                payload_stage,
                version,
                source_root,
                carried_layout,
                rc_entries,
            )
            manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
            final_name = f".transaction-{scratch_nonce}"
            transaction_root = prepare_transaction(
                layout,
                scratch_root,
                final_name,
                manifest_bytes,
                existing,
                rc_updates,
            )
            scratch_root = None
            journal, new_manifest, old_manifest = read_transaction(layout, transaction_root)
            old_version = journal["old_version"]
            target_payload = layout.app / version
            payload_stage = transaction_root / "payload"

            try:
                if old_version == version:
                    exchange_paths(target_payload, payload_stage)
                else:
                    if path_exists(target_payload):
                        fail(f"unmanaged target payload already exists: {target_payload}")
                    os.replace(payload_stage, target_payload)
                    fsync_directory(layout.app)

                atomic_symlink(layout.current, version)
                if existing is None:
                    atomic_write(layout.launcher, launcher_content(), 0o755)

                for rc_path, content in rc_updates.items():
                    original_mode = (
                        stat.S_IMODE(rc_path.stat().st_mode) if rc_path.exists() else 0o600
                    )
                    atomic_write(rc_path, content.encode("utf-8"), original_mode)

                atomic_write(safe_manifest_path(layout), manifest_bytes, 0o600)
                journal["state"] = "committed"
                write_transaction_journal(transaction_root, journal)
                transaction_committed = True
            except BaseException:
                rollback_transaction(layout, transaction_root, journal, new_manifest, old_manifest)
                transaction_root = None
                raise

            finish_committed_transaction(
                layout,
                transaction_root,
                journal,
                new_manifest,
                old_manifest,
            )
            transaction_root = None
            print(f"Installed LazyROS2 {version} in {target_payload}")
            if rc_updates:
                print("Restart the shell, or source its rc file, before using lazy.")
            else:
                print("Ensure ~/.local/bin is on PATH before using lazy.")
        except BaseException:
            if scratch_root is not None and scratch_root.exists():
                shutil.rmtree(scratch_root, ignore_errors=True)
            if transaction_root is not None and transaction_root.exists() and not transaction_committed:
                # A failed rollback is intentionally preserved for fail-closed recovery.
                pass
            for relative in sorted(created_layout, key=lambda item: item.count("/"), reverse=True):
                path = layout.home / relative
                try:
                    path.rmdir()
                except OSError:
                    pass
            raise


def runtime_state_path() -> Path:
    runtime_base = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_base:
        candidate = Path(runtime_base).expanduser()
        if candidate.is_absolute():
            return candidate / APP_NAME
    return Path(tempfile.gettempdir()) / f"lazyros2-{os.getuid()}"


def active_tasks() -> list[str]:
    runtime_root = runtime_state_path()
    if not runtime_root.exists():
        return []
    if runtime_root.is_symlink() or not runtime_root.is_dir():
        fail(f"LazyROS2 runtime path is not a directory: {runtime_root}")

    active: set[str] = set()
    registry = runtime_root / "jobs.json"
    if not registry.exists():
        return []
    if registry.is_symlink() or not registry.is_file():
        fail(f"LazyROS2 task registry is not a regular file: {registry}")
    try:
        raw = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        fail(f"cannot inspect task registry {registry}: {error}")
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        fail(f"unsupported task registry schema: {registry}")
    records = raw.get("jobs")
    if not isinstance(records, list):
        fail(f"task registry jobs must be an array: {registry}")
    for record in records:
        if not isinstance(record, dict):
            fail(f"task registry record must be an object: {registry}")
        pid = record.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int):
            continue
        if pid == 0 and record.get("state") == "starting":
            created_at = record.get("created_at")
            if (
                not isinstance(created_at, bool)
                and isinstance(created_at, (int, float))
                and time.time() - float(created_at) <= STARTING_GRACE_SECONDS
            ):
                identifier = record.get("id")
                label = identifier if isinstance(identifier, str) and identifier else "unclaimed"
                active.add(f"{label} (starting)")
            continue
        if pid <= 0:
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            active.add(f"pid {pid}")
        else:
            active.add(f"pid {pid}")
    return sorted(active)


def confirm_purge(assume_yes: bool) -> None:
    if assume_yes:
        return
    if not sys.stdin.isatty():
        fail("--purge requires confirmation; rerun interactively")
    response = input("Remove all LazyROS2 configuration, cache, history, and runtime state? [y/N] ")
    if response.strip().lower() not in {"y", "yes"}:
        fail("purge cancelled")


def xdg_root(variable: str, fallback: Path) -> Path:
    raw_value = os.environ.get(variable, "")
    if raw_value:
        candidate = Path(raw_value).expanduser()
        if candidate.is_absolute():
            return candidate
    return fallback


def purge_paths(home: Path) -> list[Path]:
    config_home = xdg_root("XDG_CONFIG_HOME", home / ".config")
    cache_home = xdg_root("XDG_CACHE_HOME", home / ".cache")
    state_home = xdg_root("XDG_STATE_HOME", home / ".local" / "state")
    return [
        config_home / APP_NAME,
        cache_home / APP_NAME,
        state_home / APP_NAME,
        runtime_state_path(),
    ]


def apply_purge(home: Path) -> None:
    for path in purge_paths(home):
        if not path.is_absolute():
            fail(f"refusing to purge a non-absolute path: {path}")
        if path.name != APP_NAME and not path.name.startswith("lazyros2-"):
            fail(f"refusing to purge unexpected path: {path}")
        remove_path(path)


def remove_rc_entries(layout: Layout, manifest: dict[str, Any], force: bool) -> None:
    for entry in manifest["rc_files"]:
        relative = validate_home_relative(entry["path"], {".bashrc", ".zshrc"})
        rc_path = layout.home / relative
        if rc_path.is_symlink() or not rc_path.is_file():
            if force:
                continue
            fail(f"managed rc file is missing or not a regular file: {rc_path}")
        content = rc_path.read_text(encoding="utf-8")
        block = extract_marker(content, rc_path)
        digest = sha256_bytes(block.encode("utf-8")) if block is not None else None
        if digest != entry["block_sha256"] and not force:
            fail(f"managed LazyROS2 marker block was modified in {rc_path}")
        if block is None:
            continue
        updated = remove_marker(content, rc_path)
        if entry["created"] and not updated.strip():
            rc_path.unlink()
        else:
            atomic_write(rc_path, updated.encode("utf-8"), stat.S_IMODE(rc_path.stat().st_mode))


def remove_managed_files(layout: Layout, manifest: dict[str, Any]) -> None:
    version = manifest["app_version"]
    paths = [safe_local_path(layout, entry["path"], version) for entry in manifest["files"]]
    for path in sorted(paths, key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
    safe_manifest_path(layout).unlink(missing_ok=True)

    directories = []
    for entry in manifest["created_directories"]:
        relative = entry["path"]
        if relative in {"bin", "lib", "share", "lib/lazyros2", "share/lazyros2"}:
            directories.append(safe_fixed_local_relative(layout, relative))
        else:
            directories.append(safe_local_path(layout, relative, version))
    for directory in sorted(set(directories), key=lambda item: len(item.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass


def plan_uninstall_rc(
    layout: Layout,
    manifest: dict[str, Any],
    force: bool,
) -> list[dict[str, Any]]:
    planned: list[dict[str, Any]] = []
    for entry in manifest["rc_files"]:
        relative = validate_home_relative(entry["path"], {".bashrc", ".zshrc"})
        rc_path = layout.home / relative
        if not path_exists(rc_path):
            if not force:
                fail(f"managed rc file is missing or not a regular file: {rc_path}")
            planned.append(
                {
                    "path": entry["path"],
                    "before_sha256": None,
                    "after_sha256": None,
                    "after_exists": False,
                    "mode": "0600",
                }
            )
            continue
        if rc_path.is_symlink() or not rc_path.is_file():
            fail(f"shell rc path must be a regular file: {rc_path}")
        content = rc_path.read_text(encoding="utf-8")
        block = extract_marker(content, rc_path)
        block_digest = sha256_bytes(block.encode("utf-8")) if block is not None else None
        if block_digest != entry["block_sha256"] and not force:
            fail(f"managed LazyROS2 marker block was modified in {rc_path}")
        updated = remove_marker(content, rc_path) if block is not None else content
        after_exists = not (entry["created"] and not updated.strip())
        planned.append(
            {
                "path": entry["path"],
                "before_sha256": sha256_bytes(content.encode("utf-8")),
                "after_sha256": sha256_bytes(updated.encode("utf-8")) if after_exists else None,
                "after_exists": after_exists,
                "mode": normalized_mode(rc_path),
            }
        )
    return planned


def read_uninstall_provenance(layout: Layout) -> dict[str, Any]:
    path = layout.uninstall_provenance
    if path.is_symlink() or not path.is_file():
        fail(f"uninstall preparing provenance is missing or unsafe: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        fail(f"cannot read uninstall preparing provenance: {error}")
    required = {"schema_version", "app", "home", "preparing", "transaction", "nonce"}
    nonce = raw.get("nonce") if isinstance(raw, dict) else None
    if (
        not isinstance(raw, dict)
        or set(raw) != required
        or raw.get("schema_version") != TRANSACTION_SCHEMA_VERSION
        or raw.get("app") != "LazyROS2"
        or raw.get("home") != str(layout.home)
        or raw.get("preparing") != layout.uninstall_preparing.name
        or raw.get("transaction") != layout.uninstall_transaction.name
        or not isinstance(nonce, str)
        or not re.fullmatch(r"[0-9a-f]{32}", nonce)
    ):
        fail("uninstall preparing provenance does not belong to this installation")
    return raw


def recover_uninstall_preparing(layout: Layout) -> None:
    preparing_exists = path_exists(layout.uninstall_preparing)
    provenance_exists = path_exists(layout.uninstall_provenance)
    if preparing_exists and not provenance_exists:
        fail("uninstall preparing directory has no provenance; refusing automatic cleanup")
    if not provenance_exists:
        return
    provenance = read_uninstall_provenance(layout)
    if preparing_exists and path_exists(layout.uninstall_transaction):
        fail("both uninstall preparing and transaction directories exist")
    if preparing_exists:
        root = layout.uninstall_preparing
        private_transaction_directory(root)
        names = {entry.name for entry in root.iterdir()}
        if not names.issubset({"manifest.json", "journal.json"}):
            fail("uninstall preparing directory contains unexpected files")
        remove_path_durable(root)
        unlink_durable(layout.uninstall_provenance)
        print("install.sh: removed incomplete uninstall preparing directory", file=sys.stderr)
        return
    if path_exists(layout.uninstall_transaction):
        root = layout.uninstall_transaction
        private_transaction_directory(root)
        journal_path = root / "journal.json"
        if journal_path.is_symlink() or not journal_path.is_file():
            fail("promoted uninstall transaction has no journal")
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            fail(f"cannot verify promoted uninstall transaction: {error}")
        if not isinstance(journal, dict) or journal.get("provenance_nonce") != provenance["nonce"]:
            fail("promoted uninstall transaction does not match its provenance")
    unlink_durable(layout.uninstall_provenance)


def prepare_uninstall_transaction(
    layout: Layout,
    manifest: dict[str, Any],
    force: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    root = layout.uninstall_preparing
    if any(
        path_exists(path)
        for path in (
            layout.uninstall_preparing,
            layout.uninstall_provenance,
            layout.uninstall_transaction,
        )
    ):
        fail("an uninstall preparing or transaction path already exists")
    manifest_bytes = safe_manifest_path(layout).read_bytes()
    rc_results = plan_uninstall_rc(layout, manifest, force)
    nonce = secrets.token_hex(16)
    provenance = {
        "schema_version": TRANSACTION_SCHEMA_VERSION,
        "app": "LazyROS2",
        "home": str(layout.home),
        "preparing": layout.uninstall_preparing.name,
        "transaction": layout.uninstall_transaction.name,
        "nonce": nonce,
    }
    atomic_write(
        layout.uninstall_provenance,
        (json.dumps(provenance, sort_keys=True) + "\n").encode("utf-8"),
        0o600,
    )
    root.mkdir(mode=0o700)
    # The durable provenance predates this mkdir, so an immediate crash is recoverable.
    fsync_directory(layout.home)
    atomic_write(root / "manifest.json", manifest_bytes, 0o600)
    journal = {
        "schema_version": TRANSACTION_SCHEMA_VERSION,
        "app": "LazyROS2",
        "home": str(layout.home),
        "state": "prepared",
        "force": force,
        "provenance_nonce": nonce,
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "rc_results": rc_results,
    }
    atomic_write(
        root / "journal.json",
        (json.dumps(journal, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        0o600,
    )
    fsync_directory(root)
    os.replace(root, layout.uninstall_transaction)
    fsync_directory(layout.home)
    unlink_durable(layout.uninstall_provenance)
    return journal, manifest


def read_uninstall_transaction(layout: Layout) -> tuple[dict[str, Any], dict[str, Any]]:
    root = layout.uninstall_transaction
    private_transaction_directory(root)
    journal_path = root / "journal.json"
    manifest_path = root / "manifest.json"
    if journal_path.is_symlink() or not journal_path.is_file():
        fail(f"interrupted uninstall has no valid journal: {root}")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        fail(f"interrupted uninstall has no valid manifest snapshot: {root}")
    if {path.name for path in root.iterdir()} != {"journal.json", "manifest.json"}:
        fail(f"interrupted uninstall contains unexpected files: {root}")
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        fail(f"cannot read interrupted uninstall journal: {error}")
    required = {
        "schema_version",
        "app",
        "home",
        "state",
        "force",
        "provenance_nonce",
        "manifest_sha256",
        "rc_results",
    }
    if not isinstance(journal, dict) or set(journal) != required:
        fail("interrupted uninstall journal is malformed")
    if (
        journal.get("schema_version") != TRANSACTION_SCHEMA_VERSION
        or journal.get("app") != "LazyROS2"
        or journal.get("home") != str(layout.home)
        or journal.get("state") not in {"prepared", "committed"}
        or not isinstance(journal.get("force"), bool)
        or not isinstance(journal.get("provenance_nonce"), str)
        or not re.fullmatch(r"[0-9a-f]{32}", journal["provenance_nonce"])
    ):
        fail("interrupted uninstall journal does not belong to this installation")
    digest = journal.get("manifest_sha256")
    if not isinstance(digest, str) or not HASH_PATTERN.fullmatch(digest):
        fail("interrupted uninstall manifest hash is invalid")
    manifest_bytes = manifest_path.read_bytes()
    if sha256_bytes(manifest_bytes) != digest:
        fail("interrupted uninstall manifest snapshot hash does not match")
    try:
        manifest_raw = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        fail(f"cannot read interrupted uninstall manifest: {error}")
    manifest = validate_manifest(layout, manifest_raw)

    results = journal.get("rc_results")
    if not isinstance(results, list):
        fail("interrupted uninstall rc results must be an array")
    seen: set[str] = set()
    for result in results:
        keys = {"path", "before_sha256", "after_sha256", "after_exists", "mode"}
        if not isinstance(result, dict) or set(result) != keys:
            fail("interrupted uninstall rc result is malformed")
        relative = result.get("path")
        if not isinstance(relative, str):
            fail("interrupted uninstall rc path is invalid")
        validate_home_relative(relative, {".bashrc", ".zshrc"})
        if relative in seen:
            fail("interrupted uninstall has duplicate rc results")
        seen.add(relative)
        if not isinstance(result.get("after_exists"), bool):
            fail("interrupted uninstall rc result has invalid existence state")
        if not isinstance(result.get("mode"), str) or not re.fullmatch(r"[0-7]{4}", result["mode"]):
            fail("interrupted uninstall rc result has invalid mode")
        for key in ("before_sha256", "after_sha256"):
            value = result.get(key)
            if value is not None and (not isinstance(value, str) or not HASH_PATTERN.fullmatch(value)):
                fail("interrupted uninstall rc result has invalid hash")
        if result["after_exists"] != (result["after_sha256"] is not None):
            fail("interrupted uninstall rc result is inconsistent")
    expected_rc = {entry["path"] for entry in manifest["rc_files"]}
    if seen != expected_rc:
        fail("interrupted uninstall rc results do not match the manifest")
    return journal, manifest


def apply_uninstall_rc(layout: Layout, journal: dict[str, Any]) -> None:
    for result in journal["rc_results"]:
        rc_path = layout.home / validate_home_relative(result["path"], {".bashrc", ".zshrc"})
        if not path_exists(rc_path):
            if result["after_exists"]:
                fail(f"shell rc file disappeared during interrupted uninstall: {rc_path}")
            continue
        if rc_path.is_symlink() or not rc_path.is_file():
            fail(f"shell rc path must be a regular file: {rc_path}")
        content = rc_path.read_text(encoding="utf-8")
        current_digest = sha256_bytes(content.encode("utf-8"))
        current_mode = normalized_mode(rc_path)
        if current_digest == result["after_sha256"] and current_mode == result["mode"]:
            continue
        if current_digest != result["before_sha256"] or current_mode != result["mode"]:
            fail(f"shell rc file changed after uninstall began: {rc_path}")
        block = extract_marker(content, rc_path)
        updated = remove_marker(content, rc_path) if block is not None else content
        updated_bytes = updated.encode("utf-8")
        if result["after_exists"]:
            if sha256_bytes(updated_bytes) != result["after_sha256"]:
                fail(f"shell rc result cannot be reproduced safely: {rc_path}")
            atomic_write(rc_path, updated_bytes, int(result["mode"], 8))
        else:
            if updated.strip():
                fail(f"shell rc result cannot be removed safely: {rc_path}")
            unlink_durable(rc_path)


def remove_uninstall_files(
    layout: Layout,
    manifest: dict[str, Any],
    force: bool,
) -> None:
    version = manifest["app_version"]
    entries = sorted(
        manifest["files"],
        key=lambda entry: len(PurePosixPath(entry["path"]).parts),
        reverse=True,
    )
    for entry in entries:
        path = safe_local_path(layout, entry["path"], version)
        if not path_exists(path):
            continue
        actual_type = "symlink" if path.is_symlink() else "file" if path.is_file() else "other"
        matches = actual_type == entry["type"]
        if matches:
            digest = (
                sha256_bytes(os.readlink(path).encode("utf-8"))
                if actual_type == "symlink"
                else sha256_file(path)
            )
            matches = digest == entry["sha256"] and normalized_mode(path) == entry["mode"]
        if not matches and not force:
            fail(f"managed file changed after uninstall began: {entry['path']}")
        if actual_type not in {"file", "symlink"}:
            fail(f"managed path has an unsafe type during uninstall: {entry['path']}")
        path.unlink()
        fsync_directory(path.parent)
        # The journal makes every durable unlink an idempotent recovery checkpoint.

    manifest_path = safe_manifest_path(layout)
    if path_exists(manifest_path):
        if manifest_path.is_symlink() or not manifest_path.is_file():
            fail(f"install manifest became unsafe during uninstall: {manifest_path}")
        current_digest = sha256_file(manifest_path)
        if current_digest != sha256_file(layout.uninstall_transaction / "manifest.json"):
            fail("install manifest changed after uninstall began")
        unlink_durable(manifest_path)

    directories: list[Path] = []
    for entry in manifest["created_directories"]:
        relative = entry["path"]
        if relative in {"bin", "lib", "share", "lib/lazyros2", "share/lazyros2"}:
            directories.append(safe_fixed_local_relative(layout, relative))
        else:
            directories.append(safe_local_path(layout, relative, version))
    for directory in sorted(set(directories), key=lambda item: len(item.parts), reverse=True):
        try:
            directory.rmdir()
            fsync_directory(directory.parent)
        except OSError:
            pass


def discard_uninstall_cleanup(layout: Layout) -> None:
    root = layout.uninstall_cleanup
    if not path_exists(root):
        return
    private_transaction_directory(root)
    entries = {path.name for path in root.iterdir()}
    if not entries:
        root.rmdir()
        fsync_directory(layout.home)
        return
    if not entries.issubset({"journal.json", "manifest.json"}) or "journal.json" not in entries:
        fail(f"uninstall cleanup directory contains unexpected files: {root}")
    try:
        journal = json.loads((root / "journal.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        fail(f"cannot validate uninstall cleanup journal: {error}")
    required = {
        "schema_version",
        "app",
        "home",
        "state",
        "force",
        "provenance_nonce",
        "manifest_sha256",
        "rc_results",
    }
    if (
        not isinstance(journal, dict)
        or set(journal) != required
        or journal.get("schema_version") != TRANSACTION_SCHEMA_VERSION
        or journal.get("app") != "LazyROS2"
        or journal.get("home") != str(layout.home)
        or journal.get("state") != "committed"
        or not isinstance(journal.get("force"), bool)
        or not isinstance(journal.get("provenance_nonce"), str)
        or not re.fullmatch(r"[0-9a-f]{32}", journal["provenance_nonce"])
        or not isinstance(journal.get("rc_results"), list)
        or not isinstance(journal.get("manifest_sha256"), str)
        or not HASH_PATTERN.fullmatch(journal["manifest_sha256"])
    ):
        fail("uninstall cleanup journal is not a committed LazyROS2 transaction")
    manifest_snapshot = root / "manifest.json"
    if manifest_snapshot.exists() and (
        manifest_snapshot.is_symlink()
        or not manifest_snapshot.is_file()
        or sha256_file(manifest_snapshot) != journal["manifest_sha256"]
    ):
        fail("uninstall cleanup manifest snapshot is unsafe or changed")
    for name in ("manifest.json", "journal.json"):
        (root / name).unlink(missing_ok=True)
    fsync_directory(root)
    root.rmdir()
    fsync_directory(layout.home)


def cleanup_uninstall_transaction(layout: Layout) -> None:
    if path_exists(layout.uninstall_cleanup):
        fail(f"uninstall cleanup path already exists: {layout.uninstall_cleanup}")
    os.replace(layout.uninstall_transaction, layout.uninstall_cleanup)
    fsync_directory(layout.home)
    discard_uninstall_cleanup(layout)


def finish_uninstall_transaction(
    layout: Layout,
    journal: dict[str, Any],
    manifest: dict[str, Any],
) -> None:
    apply_uninstall_rc(layout, journal)
    remove_uninstall_files(layout, manifest, journal["force"])
    journal["state"] = "committed"
    atomic_write(
        layout.uninstall_transaction / "journal.json",
        (json.dumps(journal, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        0o600,
    )
    cleanup_uninstall_transaction(layout)


def recover_uninstall_transaction(
    layout: Layout,
    allow_finish: bool,
) -> dict[str, Any] | None:
    recover_uninstall_preparing(layout)
    discard_uninstall_cleanup(layout)
    if not path_exists(layout.uninstall_transaction):
        return None
    if not allow_finish:
        fail("an interrupted uninstall exists; rerun install.sh --uninstall to finish it")
    journal, manifest = read_uninstall_transaction(layout)
    finish_uninstall_transaction(layout, journal, manifest)
    print("install.sh: completed interrupted uninstall transaction", file=sys.stderr)
    return manifest


def uninstall(options: argparse.Namespace) -> None:
    home = require_home()
    with InstallLock(home):
        layout = Layout(home)
        recover_transactions(layout)
        if options.purge:
            confirm_purge(options.yes)

        tasks = active_tasks()
        if tasks and not options.force:
            listed = ", ".join(str(pid) for pid in tasks)
            fail(f"active LazyROS2 task windows are still running: {listed}")

        recovered = recover_uninstall_transaction(layout, allow_finish=True)
        if recovered is not None:
            if options.purge:
                apply_purge(layout.home)
            print(f"Removed LazyROS2 {recovered['app_version']}.")
            return

        manifest = read_manifest(layout)

        if manifest is None:
            if options.purge:
                apply_purge(layout.home)
                print("LazyROS2 state removed; no managed installation was present.")
            else:
                print("LazyROS2 is not installed; no changes made.")
            return

        verify_manifest_files(layout, manifest, force=options.force)
        if not options.force:
            verify_rc_entries(layout, manifest)
        journal, manifest = prepare_uninstall_transaction(layout, manifest, options.force)
        finish_uninstall_transaction(layout, journal, manifest)
        if options.purge:
            apply_purge(layout.home)
        print(f"Removed LazyROS2 {manifest['app_version']}.")


def main() -> int:
    require_supported_python()
    require_non_root()
    source_root = Path(sys.argv[1]).resolve()
    options = parse_args(sys.argv[2:])
    if options.uninstall:
        uninstall(options)
    else:
        install(source_root, options)
    return 0


try:
    raise SystemExit(main())
except InstallError as error:
    print(f"install.sh: {error}", file=sys.stderr)
    raise SystemExit(1)
except OSError as error:
    print(f"install.sh: filesystem operation failed: {error}", file=sys.stderr)
    raise SystemExit(1)
except KeyboardInterrupt:
    print("install.sh: interrupted", file=sys.stderr)
    raise SystemExit(130)
PY
