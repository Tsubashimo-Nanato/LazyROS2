# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Mapping, Sequence

from lazyros2.colcon import (
    ColconError,
    build_argv,
    find_missing_workspace_dependencies,
    test_argv,
    test_result_argv,
)
from lazyros2.completion import (
    CompletionCache,
    CompletionCollector,
    CompletionData,
    CompletionError,
    ambiguous_launch_paths,
)
from lazyros2.config import PRESET_COLORS, ColorScheme, ConfigError, ConfigStore
from lazyros2.environment import (
    OverlayEnvironmentError,
    capture_overlay_environment,
    find_self_overlay,
    validated_setup_script,
)
from lazyros2.jobs import JobRegistry, JobRegistryError, JobState
from lazyros2.paths import Workspace, WorkspaceError, XdgPaths
from lazyros2.process import normalize_returncode, run_command
from lazyros2.storage import ensure_private_directory
from lazyros2.terminal import TerminalKind, detect_terminal


PUBLIC_COMMANDS = (
    "build",
    "create",
    "pkg",
    "test",
    "test-result",
    "run",
    "launch",
    "rviz",
    "jobs",
    "status",
    "refresh",
    "config",
    "help",
    "about",
    "uninstall",
    "exit",
)
HELP_GROUPS = {"test": ("test", "test-result")}
CREATE_ENTITIES = ("package", "pkg")
CREATE_LANGUAGE_TYPES = {
    "python": "ament_python",
    "py": "ament_python",
    "cpp": "ament_cmake",
    "c++": "ament_cmake",
    "c": "ament_cmake",
}
COMPLETION_BUDGET_SECONDS = 1.5
WELCOME_ART = r"""
 _                    ____   ___  ____  ____
| |    __ _ _____   _|  _ \ / _ \/ ___||___ \
| |   / _` |_  / | | | |_) | | | \___ \  __) |
| |__| (_| |/ /| |_| |  _ <| |_| |___) |/ __/
|_____\__,_/___|\__, |_| \_\___/|____/|_____|
                 |___/
""".strip("\n")
ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
    "input": "\033[48;5;236m",
}


class LazyError(RuntimeError):
    """An actionable error at the LazyROS2 command boundary."""


class UsageError(LazyError):
    """Raised when user-supplied wrapper arguments violate the CLI contract."""


def _styled(text: object, *styles: str) -> str:
    if os.environ.get("NO_COLOR") is not None or not sys.stdout.isatty():
        return str(text)
    prefix = "".join(ANSI[style] for style in styles)
    return f"{prefix}{text}{ANSI['reset']}"


def _input(prompt: str, *, initial: str = "") -> str:
    if not initial or not sys.stdin.isatty():
        return input(_styled(f" {prompt} ", "input"))
    try:
        import readline
    except ImportError:
        return input(_styled(f" {prompt} ", "input"))
    readline.set_startup_hook(lambda: readline.insert_text(initial))
    try:
        return input(_styled(f" {prompt} ", "input"))
    finally:
        readline.set_startup_hook()


def _app_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _version() -> str:
    version_file = _app_root() / "VERSION"
    if not version_file.is_file():
        return "unknown"
    return version_file.read_text(encoding="utf-8").strip()


def _workspace_probe(root: Path, *, timeout: float = 2.0) -> Sequence[str]:
    result = run_command(
        ("colcon", "list", "--base-paths", str(root), "--names-only"),
        cwd=root,
        env=os.environ,
        timeout=timeout,
        capture_output=True,
    )
    if result.exit_code == 127:
        raise FileNotFoundError(2, "No such file or directory", "colcon")
    if result.exit_code != 0:
        return ()
    return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())


def _workspace(env: Mapping[str, str] | None = None) -> Workspace:
    source = os.environ if env is None else env
    root = source.get("LAZYROS_WORKSPACE", os.getcwd())
    return Workspace.open(root, package_probe=_workspace_probe)


def _ask_yes_no(prompt: str, *, default: bool = False) -> bool:
    if not sys.stdin.isatty():
        raise LazyError("interactive workspace setup requires a terminal")
    suffix = " [Y/n] " if default else " [y/N] "
    reply = _input(prompt + suffix.strip()).strip().lower()
    if not reply:
        return default
    return reply in {"y", "yes"}


def _workspace_fix(root: Path) -> str:
    if not root.exists():
        return "Choose an existing directory, or create it before launching LazyROS2."
    if not root.is_dir():
        return "Choose a directory instead of a file."
    src = root / "src"
    if src.exists() and not src.is_dir():
        return f"Move or rename {src}, then create a src directory."
    return f"A ROS 2 workspace normally has a src directory at {src}."


def _choose_workspace_action() -> str:
    if not sys.stdin.isatty():
        raise LazyError("interactive workspace setup requires a terminal")
    print()
    print(_styled("What would you like to do?", "bold"))
    print(f"  {_styled('[c]', 'cyan')} Create a new workspace in this directory")
    print(f"  {_styled('[d]', 'cyan')} Enter a different workspace directory")
    print(f"  {_styled('[q]', 'cyan')} Quit and launch lazy again from the correct directory")
    print()
    while True:
        reply = _input("Choice [c/d/q]:").strip().lower()
        aliases = {"c": "create", "create": "create", "d": "directory", "directory": "directory", "q": "quit", "quit": "quit"}
        if reply in aliases:
            return aliases[reply]
        print(_styled("Please enter c, d, or q.", "yellow"))


def _create_workspace(root: Path) -> Workspace | None:
    if root.exists() and not root.is_dir():
        print(_styled(f"Cannot create a workspace: {root} is not a directory.", "red"))
        print(f"{_styled('Possible fix:', 'yellow', 'bold')} {_workspace_fix(root)}")
        return None
    src = root / "src"
    if src.exists() and not src.is_dir():
        print(_styled(f"Cannot create a workspace: {src} already exists and is not a directory.", "red"))
        print(f"{_styled('Possible fix:', 'yellow', 'bold')} {_workspace_fix(root)}")
        return None
    print()
    print(_styled("Planned changes", "bold", "cyan"))
    if root.exists():
        print(f"  Create directory: {src}")
    else:
        print(f"  Create directory: {root}")
        print(f"  Create directory: {src}")
    print()
    print(f"{_styled('Data erased:', 'bold')} {_styled('none', 'green', 'bold')}")
    print(_styled("Existing files and directories will be preserved.", "dim"))
    print()
    if not _ask_yes_no("Apply these changes?"):
        print()
        print(_styled("Workspace creation cancelled; no files were changed.", "yellow"))
        return None
    try:
        src.mkdir(parents=True)
    except OSError as error:
        raise WorkspaceError(f"cannot create {src}: {error}") from error
    print()
    print(_styled(f"Created workspace: {root}", "green", "bold"))
    return Workspace.open(root, package_probe=_workspace_probe)


def _standalone_workspace() -> Workspace | None:
    root = Path(os.environ.get("LAZYROS_WORKSPACE", os.getcwd())).expanduser().resolve(strict=False)
    print(_styled(WELCOME_ART, "cyan", "bold"))
    print()
    print(_styled("Hello from LazyROS2!", "bold"))
    print()
    while True:
        try:
            workspace = Workspace.open(root, package_probe=_workspace_probe)
        except WorkspaceError as error:
            print(_styled("Workspace not detected", "yellow", "bold"))
            print(f"  {_styled('Directory:', 'bold')} {root}")
            print(f"  {_styled('Reason:', 'red', 'bold')} {error}")
            print()
            print(f"{_styled('Possible fix:', 'yellow', 'bold')} {_workspace_fix(root)}")
            action = _choose_workspace_action()
            if action == "quit":
                print()
                print(_styled("No files were changed.", "green"))
                print("Change to the correct directory and run lazy again.")
                return None
            if action == "create":
                return _create_workspace(root)
            entered = _input("Workspace directory:").strip()
            if not entered:
                print(_styled("No directory entered; no files were changed.", "yellow"))
                continue
            root = Path(entered).expanduser().resolve(strict=False)
            continue

        print(_styled("Workspace detected", "green", "bold"))
        print(f"  {_styled('Directory:', 'bold')} {workspace.root}")
        print()
        if not _ask_yes_no("Use this workspace?", default=True):
            print()
            print(_styled("Launch cancelled; no files were changed.", "yellow"))
            return None
        return workspace


def _completion_workspace(deadline: float) -> Workspace:
    root = os.environ.get("LAZYROS_WORKSPACE", os.getcwd())
    return Workspace.open(
        root,
        package_probe=lambda candidate: _workspace_probe(
            candidate,
            timeout=_completion_remaining(deadline),
        ),
    )


def _xdg(env: Mapping[str, str] | None = None) -> XdgPaths:
    return XdgPaths.from_environment(os.environ if env is None else env)


def _config_store(paths: XdgPaths) -> ConfigStore:
    return ConfigStore(paths.config / "config.json")


def _job_registry(paths: XdgPaths) -> JobRegistry:
    return JobRegistry(paths.runtime)


def _load_baseline(env: Mapping[str, str]) -> dict[str, str]:
    baseline_file = env.get("LAZYROS_BASELINE_FILE")
    if not baseline_file:
        return dict(env)

    path = Path(baseline_file)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LazyError(f"baseline environment is unreadable: {path}: {exc}") from exc

    if not isinstance(value, dict):
        raise LazyError(f"baseline environment is not an object: {path}")
    if not all(isinstance(key, str) and isinstance(item, str) for key, item in value.items()):
        raise LazyError(f"baseline environment contains a non-string value: {path}")
    return dict(value)


def _write_private_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _baseline_for_build(workspace: Workspace, env: Mapping[str, str]) -> dict[str, str]:
    baseline = _load_baseline(env)
    hits = find_self_overlay(workspace, baseline)
    if hits:
        details = ", ".join(f"{hit.variable}={hit.path}" for hit in hits)
        raise LazyError(
            "current workspace is already sourced in the build environment "
            f"({details}). Open a fresh terminal before building it."
        )
    return baseline


def _runtime_environment(
    workspace: Workspace,
    env: Mapping[str, str],
    *,
    overlay_timeout: float = 10.0,
) -> dict[str, str]:
    if find_self_overlay(workspace, env):
        return dict(env)

    baseline = _load_baseline(env)
    setup = workspace.install / "local_setup.sh"
    if not setup.is_file():
        return baseline
    return capture_overlay_environment(
        workspace,
        baseline,
        timeout=overlay_timeout,
    )


def _mark_completion_dirty(workspace: Workspace, env: Mapping[str, str]) -> None:
    paths = _xdg(env)
    cache = CompletionCache(paths.cache / "completion")
    environments: list[tuple[str, dict[str, str]]] = [
        ("runtime", dict(env)),
        ("launch", dict(env)),
    ]
    try:
        baseline = _load_baseline(env)
        environments.append(("packages", baseline))
        setup = validated_setup_script(workspace, "sh")
        runtime = (
            baseline
            if setup is None
            else capture_overlay_environment(workspace, baseline)
        )
        environments.extend((("runtime", runtime), ("launch", runtime)))
    except (LazyError, OverlayEnvironmentError, OSError):
        # A successful build must not be turned into a failure by optional cache work.
        pass

    marked: set[str] = set()
    for domain, candidate in environments:
        key = _completion_cache_key(
            cache,
            workspace,
            candidate,
            domain,
        )
        if key in marked:
            continue
        try:
            cache.mark_dirty(key)
        except TimeoutError:
            continue
        marked.add(key)


def _run(argv: Sequence[str], workspace: Workspace, env: Mapping[str, str]) -> int:
    result = run_command(argv, cwd=workspace.root, env=env)
    if result.returncode in {124, 126, 127} and result.stderr:
        print(f"lazy: {result.stderr.strip()}", file=sys.stderr)
    return result.exit_code


def _build(args: argparse.Namespace, passthrough: Sequence[str]) -> int:
    workspace = _workspace()
    baseline = _baseline_for_build(workspace, os.environ)
    evidence = None
    if args.packages and not args.up_to:
        try:
            evidence = find_missing_workspace_dependencies(
                workspace,
                args.packages,
                baseline,
            )
        except ColconError:
            evidence = None

    command = build_argv(
        workspace,
        args.packages,
        up_to=args.up_to,
        passthrough=passthrough,
    )
    exit_code = _run(command, workspace, baseline)
    if exit_code == 0:
        _mark_completion_dirty(workspace, os.environ)
        if not os.environ.get("LAZYROS_ACTIVE"):
            print(
                "Build finished. This CLI process cannot update its parent shell; "
                "the next lazy run/launch command will load install/local_setup.sh.",
                file=sys.stderr,
            )
        return 0

    if not _should_offer_dependency_retry(evidence):
        return exit_code

    missing = ", ".join(evidence.missing_dependencies)
    print(
        f"Workspace dependencies without install markers: {missing}",
        file=sys.stderr,
    )
    reply = input("Retry with recursive dependencies? [y/N] ").strip().lower()
    if reply not in {"y", "yes"}:
        return exit_code

    retry = build_argv(workspace, args.packages, up_to=True, passthrough=passthrough)
    retry_code = _run(retry, workspace, baseline)
    if retry_code == 0:
        _mark_completion_dirty(workspace, os.environ)
    return retry_code


def _should_offer_dependency_retry(evidence: object) -> bool:
    if evidence is None:
        return False
    if not getattr(evidence, "missing_dependencies", ()):
        return False
    if os.environ.get("LAZYROS_ACTIVE") != "control":
        return False
    return sys.stdin.isatty()


def _test(args: argparse.Namespace, passthrough: Sequence[str]) -> int:
    workspace = _workspace()
    baseline = _baseline_for_build(workspace, os.environ)
    test_code = _run(
        test_argv(workspace, args.packages, passthrough=passthrough),
        workspace,
        baseline,
    )
    result_code = _run(test_result_argv(workspace), workspace, baseline)
    return test_code if test_code != 0 else result_code


def _test_result() -> int:
    workspace = _workspace()
    baseline = _load_baseline(os.environ)
    return _run(test_result_argv(workspace), workspace, baseline)


def _prompt_create_value(prompt: str, choices: Mapping[str, str] | None = None) -> str | None:
    if not sys.stdin.isatty():
        raise UsageError(
            "package creation is missing essential values; use "
            "lazy create pkg LANGUAGE NAME [DEPENDENCY ...]"
        )
    previous = ""
    if choices is not None:
        print(_styled(f"Do you mean: {', '.join(choices)}", "cyan"))
    while True:
        value = _input(prompt, initial=previous).strip()
        if not value:
            print(_styled("Package creation cancelled; no files were changed.", "yellow"))
            return None
        normalized = value.lower()
        if choices is None or normalized in choices:
            return normalized if choices is not None else value
        suggestions = difflib.get_close_matches(
            normalized,
            tuple(choices),
            n=3,
            cutoff=0.35,
        )
        offered = suggestions or list(choices)
        print(
            _styled(
                f"Do you mean: {', '.join(offered)}? Enter cancels.",
                "yellow",
            )
        )
        previous = value


def _creation_values(
    args: argparse.Namespace,
) -> tuple[str | None, str | None, str | None, tuple[str, ...]]:
    if args.command == "pkg":
        entity = "pkg" if args.pkg_action == "create" else None
        return entity, args.language, args.package_name, tuple(args.dependencies)
    return args.create_entity, args.language, args.package_name, tuple(args.dependencies)


def _known_dependency_packages(
    workspace: Workspace,
    env: Mapping[str, str],
) -> tuple[set[str], str | None]:
    known: set[str] = set()
    failures: list[str] = []
    try:
        known.update(_workspace_probe(workspace.root))
    except OSError as error:
        failures.append(f"workspace packages: {error}")
    result = run_command(
        ("ros2", "pkg", "list"),
        cwd=workspace.root,
        env=env,
        timeout=2.0,
        capture_output=True,
    )
    if result.exit_code == 0:
        known.update(line.strip() for line in result.stdout.splitlines() if line.strip())
    else:
        failures.append(f"runtime packages: exit {result.exit_code}")
    return known, "; ".join(failures) or None


def _create_package(args: argparse.Namespace) -> int:
    workspace = _workspace()
    entity, language, package_name, dependencies = _creation_values(args)
    prompted = False
    if entity is None:
        entity = _prompt_create_value(
            "Create what? [package/pkg]:",
            {name: name for name in CREATE_ENTITIES},
        )
        prompted = True
    if entity is None:
        return 0
    if language is None:
        language = _prompt_create_value("Language [python/py/cpp/c++/c]:", CREATE_LANGUAGE_TYPES)
        prompted = True
    if language is None:
        return 0
    if package_name is None:
        package_name = _prompt_create_value("Package name:")
        prompted = True
    if package_name is None:
        return 0
    if prompted:
        entered = _input("Dependencies (space-separated; Enter for none):").strip()
        dependencies = tuple(entered.split()) if entered else ()

    destination = workspace.src / package_name
    if destination.exists():
        raise UsageError(f"package destination already exists: {destination}")

    runtime = _runtime_environment(workspace, os.environ)
    known, validation_failure = _known_dependency_packages(workspace, runtime)
    if not dependencies:
        print(_styled("No dependencies specified; creating the package anyway.", "yellow"))
    else:
        unknown = tuple(dependency for dependency in dependencies if dependency not in known)
        if unknown:
            print(
                _styled(
                    "Dependencies not currently discoverable: " + ", ".join(unknown),
                    "yellow",
                )
            )
            print("They will still be recorded and can be corrected later.")
    if validation_failure:
        print(_styled(f"Dependency validation incomplete: {validation_failure}", "yellow"))

    command = [
        "ros2", "pkg", "create", "--build-type", CREATE_LANGUAGE_TYPES[language]
    ]
    if dependencies:
        command.extend(("--dependencies", *dependencies))
    command.append(package_name)
    result = run_command(command, cwd=workspace.src, env=runtime)
    if result.returncode in {124, 126, 127} and result.stderr:
        print(f"lazy: {result.stderr.strip()}", file=sys.stderr)
    if result.exit_code == 0:
        _mark_completion_dirty(workspace, os.environ)
    return result.exit_code


def _command_location(args: argparse.Namespace) -> str:
    if args.location:
        return args.location
    if os.environ.get("LAZYROS_ACTIVE") == "control":
        return "window"
    return "here"


def _run_ros_command(
    args: argparse.Namespace,
    passthrough: Sequence[str],
) -> int:
    workspace = _workspace()
    runtime = _runtime_environment(workspace, os.environ)
    if args.command == "launch":
        if Path(args.launch_file).name != args.launch_file:
            raise UsageError("launch FILE must be a basename, not a path")
        duplicate_paths = ambiguous_launch_paths(
            workspace,
            runtime,
            args.package,
            args.launch_file,
        )
        if duplicate_paths:
            paths = ", ".join(duplicate_paths)
            raise UsageError(
                f"launch basename is ambiguous for {args.package}: "
                f"{args.launch_file} matches {paths}"
            )

    high_level, target, process_argv = _ros_command_argv(args, passthrough)
    if _command_location(args) == "window":
        return _spawn_job(workspace, high_level, target)
    return _run(process_argv, workspace, runtime)


def _ros_command_argv(
    args: argparse.Namespace,
    passthrough: Sequence[str] = (),
) -> tuple[tuple[str, ...], str, tuple[str, ...]]:
    forwarded = tuple(passthrough)
    wrapper_separator = ("--", *forwarded) if forwarded else ()
    if args.command == "run":
        high_level = (
            "run",
            "--here",
            args.package,
            args.executable,
            *wrapper_separator,
        )
        process = ("ros2", "run", args.package, args.executable, *forwarded)
        return high_level, f"{args.package}/{args.executable}", process
    if args.command == "launch":
        high_level = (
            "launch",
            "--here",
            args.package,
            args.launch_file,
            *wrapper_separator,
        )
        process = ("ros2", "launch", args.package, args.launch_file, *forwarded)
        return high_level, f"{args.package}/{args.launch_file}", process

    rviz_arguments = forwarded
    if args.config_file:
        rviz_arguments = ("-d", args.config_file, *rviz_arguments)
    high_level = (
        "rviz",
        "--here",
        *((args.config_file,) if args.config_file else ()),
        *wrapper_separator,
    )
    process = ("rviz2", *rviz_arguments)
    return high_level, args.config_file or "rviz2", process


def _spawn_job(
    workspace: Workspace,
    command: Sequence[str],
    target: str,
) -> int:
    paths = _xdg()
    store = _config_store(paths)
    preferred = store.get_workspace(workspace.root).terminal
    adapter = detect_terminal(os.environ, preferred=preferred)
    if adapter is None:
        raise LazyError(
            "no supported graphical terminal is available; retry with --here"
        )

    registry = _job_registry(paths)
    job = registry.register(
        workspace.root,
        command[0],
        target,
        command,
    )
    shell_name = _current_shell()
    program = (
        *_launcher_argv(),
        "__job-shell",
        job.id,
        "--shell",
        shell_name,
    )
    title_target = _window_title_target(job.kind, job.target)
    title = (
        f"{_safe_display(workspace.root.name)} · #{job.number:02d} · "
        f"{job.kind} · {_safe_display(title_target)}"
    )
    terminal_argv = adapter.command(title, program)
    job_env = dict(os.environ)
    job_env["LAZYROS_WORKSPACE"] = str(workspace.root)
    try:
        terminal_process = subprocess.Popen(
            terminal_argv,
            cwd=workspace.root,
            env=job_env,
            start_new_session=True,
        )
    except OSError:
        registry.remove(workspace.root, job.number)
        raise

    try:
        terminal_code = terminal_process.wait(timeout=0.75)
    except subprocess.TimeoutExpired:
        terminal_code = None
    if terminal_code not in {None, 0}:
        registry.remove(workspace.root, job.number)
        raise LazyError(
            f"{adapter.name} failed to create the task window "
            f"(exit {normalize_returncode(terminal_code)}); retry with --here"
        )

    print(f"Started #{job.number:02d} in {adapter.name}: {job.kind} {job.target}")
    return 0


def _current_shell() -> str:
    selected = os.environ.get("LAZYROS_SHELL")
    if selected in {"bash", "zsh"}:
        return selected
    login_shell = Path(os.environ.get("SHELL", "bash")).name
    return login_shell if login_shell in {"bash", "zsh"} else "bash"


def _launcher_argv() -> tuple[str, ...]:
    explicit = os.environ.get("LAZYROS_LAUNCHER")
    if explicit:
        return (explicit,)
    installed = shutil.which("lazy")
    if installed:
        return (installed,)
    source_launcher = _app_root() / "bin" / "lazy"
    if source_launcher.is_file():
        return (str(source_launcher),)
    return (sys.executable, "-m", "lazyros2")


def _start_controller(shell_name: str, workspace: Workspace | None = None) -> int:
    workspace = _workspace() if workspace is None else workspace
    hits = find_self_overlay(workspace, os.environ)
    if hits:
        details = ", ".join(f"{hit.variable}={hit.path}" for hit in hits)
        raise LazyError(
            "this workspace is already sourced in the current terminal "
            f"({details}). Open a fresh terminal, then run lazy again."
        )

    paths = _xdg()
    session_root = paths.runtime / "sessions"
    session_dir = _new_session_directory(session_root, "control-")
    baseline_file = session_dir / "baseline.json"
    _write_private_json(baseline_file, dict(os.environ))

    history_key = hashlib.sha256(str(workspace.root).encode()).hexdigest()[:16]
    history_file = paths.state / "history" / f"{history_key}.{shell_name}"
    ensure_private_directory(history_file.parent)
    history_file.touch(exist_ok=True)
    os.chmod(history_file, 0o600)

    child_env = dict(os.environ)
    child_env.update(
        {
            "LAZYROS_ACTIVE": "control",
            "LAZYROS_APP_ROOT": str(_app_root()),
            "LAZYROS_BASELINE_FILE": str(baseline_file),
            "LAZYROS_HISTORY_FILE": str(history_file),
            "LAZYROS_LAUNCHER": _launcher_argv()[0],
            "LAZYROS_SHELL": shell_name,
            "LAZYROS_WORKSPACE": str(workspace.root),
        }
    )
    try:
        return _start_interactive_shell(shell_name, "control", child_env, session_dir)
    finally:
        _safe_remove_session(session_dir, session_root)


def _start_job_shell(job_id: str, shell_name: str) -> int:
    paths = _xdg()
    registry = _job_registry(paths)
    job = registry.get_by_id(job_id)
    if job is None:
        raise LazyError(f"job does not exist: {job_id}")

    colors = tuple(_config_store(paths).effective_colors(job.workspace).values())
    color = colors[job.color_slot % len(colors)]
    session_root = paths.runtime / "task-shells"
    session_dir = _new_session_directory(session_root, f"{job.id}-")
    child_env = dict(os.environ)
    child_env.update(
        {
            "LAZYROS_ACTIVE": "job",
            "LAZYROS_APP_ROOT": str(_app_root()),
            "LAZYROS_COLOR_ACCENT": color.accent,
            "LAZYROS_COLOR_BACKGROUND": color.background,
            "LAZYROS_COLOR_FOREGROUND": color.foreground,
            "LAZYROS_JOB_ID": job.id,
            "LAZYROS_JOB_NUMBER": str(job.number),
            "LAZYROS_RESTART_LINE": f"lazy __job-run {shlex.quote(job.id)}",
            "LAZYROS_SHELL": shell_name,
            "LAZYROS_WINDOW_TITLE": (
                f"{_safe_display(Path(job.workspace).name)} · #{job.number:02d} · "
                f"{job.kind} · {_safe_display(_window_title_target(job.kind, job.target))}"
            ),
            "LAZYROS_WORKSPACE": str(job.workspace),
        }
    )
    try:
        return _start_interactive_shell(
            shell_name,
            "task",
            child_env,
            session_dir,
        )
    finally:
        try:
            registry.remove(job.workspace, job.number)
        except JobRegistryError:
            pass
        _safe_remove_session(session_dir, session_root)


def _start_interactive_shell(
    shell_name: str,
    mode: str,
    env: Mapping[str, str],
    session_dir: Path,
) -> int:
    shell = shutil.which(shell_name)
    if shell is None:
        raise LazyError(f"{shell_name} is not installed")
    script = _app_root() / "shell" / f"lazy-{mode}.{shell_name}"
    if not script.is_file():
        raise LazyError(f"shell integration is missing: {script}")

    child_env = dict(env)
    if shell_name == "bash":
        command = (shell, "--noprofile", "--rcfile", str(script), "-i")
    else:
        zdotdir = session_dir / "zdotdir"
        zdotdir.mkdir(mode=0o700)
        zshrc = zdotdir / ".zshrc"
        zshrc.write_text(f"source {shlex.quote(str(script))}\n", encoding="utf-8")
        os.chmod(zshrc, 0o600)
        child_env["ZDOTDIR"] = str(zdotdir)
        command = (shell, "-d", "-i")

    process = subprocess.Popen(command, env=child_env, cwd=child_env["LAZYROS_WORKSPACE"])
    return normalize_returncode(process.wait())


def _safe_remove_session(session_dir: Path, session_root: Path) -> None:
    resolved_dir = session_dir.resolve()
    resolved_root = session_root.resolve()
    if resolved_dir.parent != resolved_root:
        raise LazyError(f"refusing to remove unexpected session path: {resolved_dir}")
    shutil.rmtree(resolved_dir, ignore_errors=True)


def _new_session_directory(session_root: Path, prefix: str) -> Path:
    ensure_private_directory(session_root)
    _prune_stale_sessions(session_root)
    session_dir = Path(tempfile.mkdtemp(prefix=prefix, dir=session_root))
    os.chmod(session_dir, 0o700)
    _write_private_json(
        session_dir / "owner.json",
        {"pid": os.getpid(), "created_at": time.time()},
    )
    return session_dir


def _prune_stale_sessions(session_root: Path, *, ttl: float = 86400.0) -> None:
    now = time.time()
    for candidate in session_root.iterdir():
        try:
            if candidate.is_symlink():
                candidate.unlink()
                continue
            if not candidate.is_dir():
                continue
            age = max(0.0, now - candidate.stat().st_mtime)
            owner = json.loads((candidate / "owner.json").read_text(encoding="utf-8"))
            pid = owner.get("pid") if isinstance(owner, dict) else None
            if isinstance(pid, int) and pid > 0 and _pid_is_alive(pid):
                continue
            if isinstance(pid, int) and pid > 0:
                _safe_remove_session(candidate, session_root)
                continue
            if age > ttl:
                _safe_remove_session(candidate, session_root)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            try:
                if now - candidate.stat().st_mtime > ttl:
                    _safe_remove_session(candidate, session_root)
            except OSError:
                continue


def _pid_is_alive(pid: int) -> bool:
    if os.name == "nt":
        return _windows_pid_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_pid_is_alive(pid: int) -> bool:
    # os.kill(pid, 0) can terminate a process on Windows rather than probe it.
    import ctypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def _safe_display(value: str) -> str:
    return "".join(
        "?" if ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F else character
        for character in value
    )


def _window_title_target(kind: str, target: str) -> str:
    if kind in {"run", "launch"}:
        return target.split("/", 1)[0]
    return "rviz2"


def _run_job(job_id: str) -> int:
    paths = _xdg()
    registry = _job_registry(paths)
    job = registry.get_by_id(job_id)
    if job is None:
        raise LazyError(f"job does not exist: {job_id}")

    registry.update(job.workspace, job.number, JobState.RUNNING)
    try:
        exit_code = main(job.command)
    except KeyboardInterrupt:
        exit_code = 130
    registry.update(
        job.workspace,
        job.number,
        JobState.IDLE,
        exit_code=exit_code,
    )
    return exit_code


def _claim_job(job_id: str, pid: int) -> int:
    if pid <= 0:
        raise UsageError("task shell PID must be positive")
    getppid = getattr(os, "getppid", None)
    if getppid is not None and pid != getppid():
        raise UsageError("task shell PID does not match the claiming process parent")
    registry = _job_registry(_xdg())
    job = registry.get_by_id(job_id)
    registry.update(
        job.workspace,
        job.number,
        JobState.STARTING,
        pid=pid,
    )
    return 0


def _finish_job(job_id: str, pid: int, exit_code: int) -> int:
    if pid <= 0:
        raise UsageError("task shell PID must be positive")
    if not 0 <= exit_code <= 255:
        raise UsageError("task exit code must be between 0 and 255")
    getppid = getattr(os, "getppid", None)
    if getppid is not None and pid != getppid():
        raise UsageError("task shell PID does not match the finishing process parent")

    registry = _job_registry(_xdg())
    job = registry.get_by_id(job_id)
    if job.pid != pid:
        raise UsageError("task shell PID does not own the registry record")
    registry.update(
        job.workspace,
        job.number,
        JobState.IDLE,
        exit_code=exit_code,
    )
    return 0


def _list_jobs(number: str | None = None) -> int:
    workspace = _workspace()
    jobs = _job_registry(_xdg()).list(workspace.root)
    if number is not None:
        normalized = number.removeprefix("#")
        if not normalized.isdecimal() or int(normalized) <= 0:
            raise UsageError("jobs NUMBER must be a positive task number")
        selected = int(normalized)
        jobs = tuple(job for job in jobs if job.number == selected)
    if not jobs:
        suffix = f" matching #{int(number.removeprefix('#')):02d}" if number else ""
        print(f"No LazyROS2 task windows{suffix} for this workspace.")
        return 0
    print("ID      Color  State     PID      Command")
    for job in jobs:
        exit_text = "" if job.exit_code is None else f" ({job.exit_code})"
        print(
            f"#{job.number:02d}     {job.color_slot + 1:02d}     "
            f"{job.state.value:<9} {job.pid:<8} "
            f"{job.kind} {job.target}{exit_text}"
        )
    return 0


def _status() -> int:
    workspace = _workspace()
    env = os.environ
    paths = _xdg()
    config = _config_store(paths).get_workspace(workspace.root)
    terminal = detect_terminal(env, preferred=config.terminal)
    overlay = "active" if find_self_overlay(workspace, env) else "not active"
    baseline = _load_baseline(env)
    baseline_state = "contains workspace overlay" if find_self_overlay(
        workspace, baseline
    ) else "clean"
    print(f"Workspace: {workspace.root}")
    print(f"ROS distro: {env.get('ROS_DISTRO') or 'not exported'}")
    print(f"Build baseline: {baseline_state}")
    print(f"Overlay: {overlay}")
    print(f"ros2: {shutil.which('ros2') or 'not found'}")
    print(f"colcon: {shutil.which('colcon') or 'not found'}")
    print(f"rviz2: {shutil.which('rviz2') or 'not found'}")
    print(f"Terminal: {terminal.name if terminal else 'not available'}")
    print(f"Completion cache: {_completion_cache_state(workspace, env)}")
    print(f"Active tasks: {len(_job_registry(paths).list(workspace.root))}")
    return 0


def _completion_cache_state(
    workspace: Workspace,
    env: Mapping[str, str],
) -> str:
    paths = _xdg(env)
    try:
        runtime = _runtime_environment(workspace, env)
        cache = CompletionCache(paths.cache / "completion")
        key = cache.key(
            workspace,
            runtime,
            shutil.which("ros2", path=runtime.get("PATH")) or "ros2",
            shutil.which("colcon", path=runtime.get("PATH")) or "colcon",
            domain="runtime",
        )
        snapshot = cache.read(key)
    except (CompletionError, OverlayEnvironmentError, OSError) as exc:
        return f"unavailable ({exc})"
    if snapshot is None:
        return "missing"
    flags = [name for name in ("dirty", "stale") if getattr(snapshot, name)]
    return ", ".join(flags) if flags else "fresh"


def _completion_snapshot(
    workspace: Workspace,
    *,
    deadline: float | None = None,
) -> CompletionData:
    env, cache, key = _completion_cache_context(
        workspace,
        deadline=deadline,
        domain="runtime",
    )
    cached = cache.read(key)
    if cached is None or cached.dirty:
        try:
            cached = cache.refresh(
                key,
                lambda: CompletionCollector().collect(
                    workspace,
                    env,
                    deadline=deadline,
                ),
                lock_timeout=(
                    0.2 if deadline is None else _completion_lock_timeout(deadline)
                ),
            )
        except TimeoutError:
            if cached is None:
                raise
    return cached.data


def _completion_cache_context(
    workspace: Workspace,
    *,
    domain: str,
    env: Mapping[str, str] | None = None,
    deadline: float | None = None,
) -> tuple[dict[str, str], CompletionCache, str]:
    paths = _xdg()
    if env is None:
        overlay_timeout = (
            10.0
            if deadline is None
            else max(0.001, deadline - time.monotonic())
        )
        selected_env = _runtime_environment(
            workspace,
            os.environ,
            overlay_timeout=overlay_timeout,
        )
    else:
        selected_env = dict(env)
    cache = CompletionCache(paths.cache / "completion")
    key = _completion_cache_key(
        cache,
        workspace,
        selected_env,
        domain,
    )
    return selected_env, cache, key


def _completion_cache_key(
    cache: CompletionCache,
    workspace: Workspace,
    env: Mapping[str, str],
    domain: str,
) -> str:
    return cache.key(
        workspace,
        env,
        shutil.which("ros2", path=env.get("PATH")) or "ros2",
        shutil.which("colcon", path=env.get("PATH")) or "colcon",
        domain=domain,
    )


def _refresh() -> int:
    workspace = _workspace()
    data = _completion_snapshot_after_dirty(workspace)
    executable_count = sum(len(values) for values in data.executables.values())
    launch_count = sum(len(values) for values in data.launches.values())
    print(
        f"Completion cache: {len(data.packages)} packages, "
        f"{executable_count} executables, {launch_count} launch files."
    )
    return 0


def _completion_snapshot_after_dirty(workspace: Workspace) -> CompletionData:
    env, cache, runtime_key = _completion_cache_context(
        workspace,
        domain="runtime",
    )
    baseline = _load_baseline(os.environ)
    _, _, packages_key = _completion_cache_context(
        workspace,
        domain="packages",
        env=baseline,
    )
    _, _, launch_key = _completion_cache_context(
        workspace,
        domain="launch",
        env=env,
    )
    for key in (runtime_key, packages_key, launch_key):
        cache.mark_dirty(key)
    cached = cache.refresh(
        runtime_key,
        lambda: CompletionCollector().collect(workspace, env),
    )
    if cached.stale:
        raise CompletionError(
            cached.error or "completion refresh failed; the previous snapshot is stale",
            exit_code=cached.error_code,
        )
    cache.refresh(
        packages_key,
        lambda: CompletionData(packages=cached.data.packages),
    )
    cache.refresh(launch_key, lambda: cached.data)
    return cached.data


def _complete(args: argparse.Namespace) -> int:
    deadline = time.monotonic() + COMPLETION_BUDGET_SECONDS
    try:
        command = _completion_command(args)
        workspace = (
            _completion_workspace(deadline)
            if command in {"build", "test", "run", "launch", "rviz", "jobs", "create", "pkg"}
            else None
        )
        data = _completion_data_for_command(
            command,
            workspace,
            deadline=deadline,
        )
        candidates = _completion_candidates(args, data, workspace)
    except (
        LazyError,
        WorkspaceError,
        ColconError,
        CompletionError,
        OverlayEnvironmentError,
        OSError,
        TimeoutError,
    ):
        return 0
    for candidate in candidates:
        print(candidate)
    return 0


def _completion_command(args: argparse.Namespace) -> str | None:
    words = list(args.words)
    if words and words[0] == "--":
        words.pop(0)
    cursor = args.cursor if args.shell == "bash" else args.cursor - 1
    if cursor <= 1 or len(words) <= 1:
        return None
    return words[1]


def _completion_data_for_command(
    command: str | None,
    workspace: Workspace | None,
    *,
    deadline: float | None = None,
) -> CompletionData:
    if workspace is None or command in {None, "jobs"}:
        return CompletionData()

    if deadline is None:
        deadline = time.monotonic() + COMPLETION_BUDGET_SECONDS
    if command in {"build", "test"}:
        baseline = _load_baseline(os.environ)
        env, cache, key = _completion_cache_context(
            workspace,
            domain="packages",
            env=baseline,
            deadline=deadline,
        )
        cached = cache.read(key)
        if cached is None or cached.dirty:
            collector = CompletionCollector()
            try:
                cached = cache.refresh(
                    key,
                    lambda: CompletionData(
                        packages=collector.collect_packages(
                            workspace,
                            env,
                            deadline=deadline,
                        )
                    ),
                    lock_timeout=_completion_lock_timeout(deadline),
                )
            except TimeoutError:
                if cached is None:
                    raise
        return CompletionData(packages=cached.data.packages)
    if command == "run":
        return _completion_snapshot(workspace, deadline=deadline)
    if command == "launch":
        env, cache, key = _completion_cache_context(
            workspace,
            domain="launch",
            deadline=deadline,
        )
        cached = cache.read(key)
        if cached is not None and not cached.dirty:
            return cached.data
        collector = CompletionCollector()
        previous = CompletionData() if cached is None else cached.data

        def collect_launches() -> CompletionData:
            launches, ambiguities = collector.collect_launches(
                workspace,
                env,
                deadline=deadline,
            )
            return CompletionData(
                packages=previous.packages,
                executables=previous.executables,
                launches=launches,
                ambiguous_launches=ambiguities,
                rviz_files=previous.rviz_files,
            )

        try:
            refreshed = cache.refresh(
                key,
                collect_launches,
                lock_timeout=_completion_lock_timeout(deadline),
            )
        except TimeoutError:
            if cached is None:
                raise
            return cached.data
        return refreshed.data
    if command == "rviz":
        collector = CompletionCollector()
        return CompletionData(
            rviz_files=collector.collect_rviz_files(
                workspace,
                deadline=deadline,
            )
        )
    if command in {"create", "pkg"}:
        runtime = _runtime_environment(
            workspace,
            os.environ,
            overlay_timeout=_completion_remaining(deadline),
        )
        result = run_command(
            ("ros2", "pkg", "list"),
            cwd=workspace.root,
            env=runtime,
            timeout=_completion_remaining(deadline),
            capture_output=True,
        )
        packages = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        packages.update(
            _workspace_probe(workspace.root, timeout=_completion_remaining(deadline))
        )
        return CompletionData(packages=tuple(sorted(packages)))
    return CompletionData()


def _completion_remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CompletionError(
            "completion collection exceeded its deadline",
            exit_code=124,
        )
    return remaining


def _completion_lock_timeout(deadline: float) -> float:
    return max(0.0, min(0.2, deadline - time.monotonic()))


def _completion_candidates(
    args: argparse.Namespace,
    data: CompletionData,
    workspace: Workspace | None = None,
) -> tuple[str, ...]:
    words = list(args.words)
    if words and words[0] == "--":
        words.pop(0)
    cursor = args.cursor if args.shell == "bash" else args.cursor - 1
    current = words[cursor] if 0 <= cursor < len(words) else ""
    if cursor <= 1:
        return _prefix(PUBLIC_COMMANDS, current)
    command = words[1] if len(words) > 1 else ""
    before = words[2:cursor]
    if "--" in before:
        return ()

    if command in {"build", "test"}:
        options = ("--up-to",) if command == "build" else ()
        return _prefix((*options, *data.packages), current)
    if command == "create":
        if not before:
            return _prefix(CREATE_ENTITIES, current)
        if before[0] not in CREATE_ENTITIES:
            return ()
        if len(before) == 1:
            return _prefix(tuple(CREATE_LANGUAGE_TYPES), current)
        if before[1] not in CREATE_LANGUAGE_TYPES or len(before) == 2:
            return ()
        selected = set(before[3:])
        return _prefix(
            tuple(package for package in data.packages if package not in selected),
            current,
        )
    if command == "pkg":
        if not before:
            return _prefix(("create",), current)
        if before[0] != "create":
            return ()
        if len(before) == 1:
            return ()
        if len(before) == 2:
            return _prefix(tuple(CREATE_LANGUAGE_TYPES), current)
        selected = set(before[3:])
        return _prefix(
            tuple(package for package in data.packages if package not in selected),
            current,
        )
    if command == "run":
        return _complete_target_command(
            before,
            current,
            tuple(sorted(data.executables)),
            data.executables,
        )
    if command == "launch":
        packages = tuple(sorted(set(data.launches) | set(data.ambiguous_launches)))
        return _complete_target_command(before, current, packages, data.launches)
    if command == "rviz":
        positionals = tuple(item for item in before if item not in {"--here", "--window"})
        if positionals:
            return ()
        return _prefix(("--here", "--window", *data.rviz_files), current)
    if command == "jobs":
        root = workspace.root if workspace is not None else _workspace().root
        job_ids = tuple(
            f"{job.number:02d}" for job in _job_registry(_xdg()).list(root)
        )
        return _prefix(job_ids, current)
    if command == "config":
        return _complete_config(before, current)
    if command == "help":
        if not before:
            return _prefix(PUBLIC_COMMANDS, current)
        if before == ["create"]:
            return _prefix(CREATE_ENTITIES, current)
        if before == ["pkg"]:
            return _prefix(("create",), current)
        return ()
    return ()


def _complete_target_command(
    before: Sequence[str],
    current: str,
    packages: Sequence[str],
    targets: Mapping[str, Sequence[str]],
) -> tuple[str, ...]:
    positionals = tuple(item for item in before if item not in {"--here", "--window"})
    if not positionals:
        return _prefix(("--here", "--window", *packages), current)
    if len(positionals) == 1:
        return _prefix(targets.get(positionals[0], ()), current)
    return ()


def _complete_config(before: Sequence[str], current: str) -> tuple[str, ...]:
    if not before:
        return _prefix(("colors", "terminal"), current)
    group = before[0]
    if group == "colors":
        if len(before) == 1:
            return _prefix(("list", "show", "set", "custom", "reset", "preview"), current)
        action = before[1]
        if action in {"set", "custom"} and len(before) == 2:
            return _prefix(tuple(PRESET_COLORS), current)
        if action == "set" and len(before) == 3:
            return _prefix(tuple(PRESET_COLORS), current)
        return ()
    if group == "terminal":
        if len(before) == 1:
            return _prefix(("show", "set"), current)
        if before[1] == "set" and len(before) == 2:
            return _prefix(("auto", *(kind.value for kind in TerminalKind)), current)
    return ()


def _prefix(candidates: Sequence[str], prefix: str) -> tuple[str, ...]:
    return tuple(sorted({item for item in candidates if item.startswith(prefix)}))


def _config(args: argparse.Namespace) -> int:
    workspace = _workspace()
    store = _config_store(_xdg())
    if args.config_group == "colors":
        return _config_colors(store, workspace, args)
    if args.config_group == "terminal":
        return _config_terminal(store, workspace, args)
    raise LazyError("config requires colors or terminal")


def _config_colors(
    store: ConfigStore,
    workspace: Workspace,
    args: argparse.Namespace,
) -> int:
    if args.color_action in {None, "list", "show"}:
        colors = store.effective_colors(workspace.root)
        for index, (name, color) in enumerate(colors.items(), start=1):
            print(
                f"{index:02d} {name:<10} "
                f"{color.background}/{color.foreground}/{color.accent}"
            )
        return 0
    if args.color_action == "set":
        if args.preset not in PRESET_COLORS:
            raise LazyError(f"unknown color preset: {args.preset}")
        store.set_color(workspace.root, args.slot, PRESET_COLORS[args.preset])
        return 0
    if args.color_action == "custom":
        store.set_color(
            workspace.root,
            args.slot,
            ColorScheme(args.background, args.foreground, args.accent),
        )
        return 0
    if args.color_action == "reset":
        store.reset_colors(workspace.root)
        return 0
    if args.color_action == "preview":
        return _preview_colors(store.effective_colors(workspace.root))
    raise LazyError(f"unknown colors action: {args.color_action}")


def _preview_colors(colors: Mapping[str, ColorScheme]) -> int:
    tty = None
    if sys.stdout.isatty() and "NO_COLOR" not in os.environ:
        try:
            tty = open("/dev/tty", "w", encoding="utf-8", buffering=1)
        except OSError:
            tty = None
    try:
        for name, color in colors.items():
            if tty is None:
                print(f"{name}: {color.background}/{color.foreground}/{color.accent}")
                continue
            background = _rgb(color.background)
            foreground = _rgb(color.foreground)
            print(
                f"\033[48;2;{background}m\033[38;2;{foreground}m"
                f"  {name:<10}  \033[0m",
                file=tty,
            )
    finally:
        if tty is not None:
            tty.close()
    return 0


def _rgb(value: str) -> str:
    return ";".join(str(int(value[index : index + 2], 16)) for index in (1, 3, 5))


def _config_terminal(
    store: ConfigStore,
    workspace: Workspace,
    args: argparse.Namespace,
) -> int:
    if args.terminal_action in {None, "show"}:
        selected = store.get_workspace(workspace.root).terminal or "auto"
        print(selected)
        return 0
    selected = None if args.name == "auto" else args.name
    known = {kind.value for kind in TerminalKind}
    if selected is not None and selected not in known:
        raise LazyError(f"unknown terminal adapter: {selected}")
    store.set_terminal(workspace.root, selected)
    return 0


def _uninstall(args: argparse.Namespace) -> int:
    installer = _app_root() / "install.sh"
    if not installer.is_file():
        raise LazyError(f"installer is missing from the payload: {installer}")
    command = ["sh", str(installer), "--uninstall"]
    if args.purge:
        command.append("--purge")
    if args.force:
        command.append("--force")
    return run_command(command).exit_code


def _about() -> int:
    print(f"LazyROS2 {_version()}")
    print("Copyright © 2026 Tsubashimo-Nanato")
    print("License: GNU AGPL v3 or later (AGPL-3.0-or-later)")
    print("This program comes with no warranty.")
    print("Source: https://github.com/Tsubashimo-Nanato/LazyROS2")
    return 0


def _setup_path(shell_name: str) -> int:
    setup = validated_setup_script(_workspace(), shell_name)
    if setup is not None:
        print(setup)
    return 0


def _split_passthrough(argv: Sequence[str]) -> tuple[list[str], tuple[str, ...]]:
    values = list(argv)
    command = None
    skip_next = False
    for value in values:
        if skip_next:
            skip_next = False
            continue
        if value == "--shell":
            skip_next = True
            continue
        if not value.startswith("-"):
            command = value
            break
    passthrough_commands = {"build", "test", "run", "launch", "rviz"}
    if command not in passthrough_commands or "--" not in values:
        return values, ()
    index = values.index("--")
    return values[:index], tuple(values[index + 1 :])


def _normalize_help_argv(argv: Sequence[str]) -> list[str]:
    values = list(argv)
    if values == ["?"]:
        return ["help"]
    if len(values) >= 2 and values[-1] == "?" and not values[0].startswith("-"):
        return ["help", *values[:-1]]
    return values


def _print_help_topic(
    topics: Sequence[str],
    parser: argparse.ArgumentParser,
    command_parsers: Mapping[str, argparse.ArgumentParser],
) -> int:
    if not topics:
        parser.print_help()
        return 0
    topic = " ".join(topics)
    names = HELP_GROUPS.get(topic, (topic,))
    selected = [command_parsers[name] for name in names if name in command_parsers]
    if len(selected) != len(names):
        print(f"lazy: unknown help topic: {topic}", file=sys.stderr)
        return 2
    for index, (name, topic_parser) in enumerate(zip(names, selected)):
        if len(names) > 1:
            if index:
                print()
            print(_styled(f"{name} command", "cyan", "bold"))
        topic_parser.print_help()
    return 0


def _build_parser() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    parser = argparse.ArgumentParser(
        prog="lazy",
        description="A focused ROS 2 developer shell.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  cd ~/robot_ws && lazy\n"
            "  lazy build my_package\n"
            "  lazy run --here my_package my_node -- --ros-args\n"
            "  lazy launch my_package bringup.launch.py -- use_sim:=true"
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    parser.add_argument("--shell", choices=("bash", "zsh"), help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    command_parsers: dict[str, argparse.ArgumentParser] = {}

    build = commands.add_parser("build", help="Build all or selected workspace packages.")
    build.add_argument("--up-to", action="store_true")
    build.add_argument("packages", nargs="*")
    command_parsers["build"] = build

    create = commands.add_parser("create", help="Create a ROS 2 resource interactively.")
    create_entities = create.add_subparsers(dest="create_entity", metavar="ENTITY")
    create_package = create_entities.add_parser(
        "package", aliases=["pkg"], help="Create a package in the workspace src directory."
    )
    create_package.add_argument(
        "language", nargs="?", choices=tuple(CREATE_LANGUAGE_TYPES), metavar="LANGUAGE"
    )
    create_package.add_argument("package_name", nargs="?", metavar="NAME")
    create_package.add_argument("dependencies", nargs="*", metavar="DEPENDENCY")
    command_parsers["create"] = create
    command_parsers["create package"] = create_package
    command_parsers["create pkg"] = create_package

    pkg = commands.add_parser("pkg", help="Package commands (compatibility form).")
    pkg_actions = pkg.add_subparsers(dest="pkg_action", metavar="ACTION")
    pkg_create = pkg_actions.add_parser("create", help="Create a package.")
    pkg_create.add_argument("package_name", nargs="?", metavar="NAME")
    pkg_create.add_argument(
        "language", nargs="?", choices=tuple(CREATE_LANGUAGE_TYPES), metavar="LANGUAGE"
    )
    pkg_create.add_argument("dependencies", nargs="*", metavar="DEPENDENCY")
    command_parsers["pkg"] = pkg
    command_parsers["pkg create"] = pkg_create

    test = commands.add_parser("test", help="Test all or selected workspace packages.")
    test.add_argument("packages", nargs="*")
    command_parsers["test"] = test
    command_parsers["test-result"] = commands.add_parser(
        "test-result", help="Show the latest verbose colcon test results."
    )

    for name in ("run", "launch"):
        child = commands.add_parser(
            name,
            help=(
                "Run a package executable."
                if name == "run"
                else "Launch an installed package launch file."
            ),
        )
        location = child.add_mutually_exclusive_group()
        location.add_argument("--window", dest="location", action="store_const", const="window")
        location.add_argument("--here", dest="location", action="store_const", const="here")
        child.add_argument("package")
        child.add_argument("executable" if name == "run" else "launch_file")
        command_parsers[name] = child

    rviz = commands.add_parser("rviz", help="Open RViz, optionally with a config file.")
    location = rviz.add_mutually_exclusive_group()
    location.add_argument("--window", dest="location", action="store_const", const="window")
    location.add_argument("--here", dest="location", action="store_const", const="here")
    rviz.add_argument("config_file", nargs="?")
    command_parsers["rviz"] = rviz

    jobs = commands.add_parser("jobs", help="List LazyROS2 task windows.")
    jobs.add_argument("number", nargs="?")
    command_parsers["jobs"] = jobs
    simple_help = {
        "status": "Show workspace, ROS, terminal, task, and cache status.",
        "refresh": "Refresh ROS-aware completion data.",
        "about": "Show version, copyright, license, and source.",
        "exit": "Exit a LazyROS2 control shell.",
    }
    for name, description in simple_help.items():
        command_parsers[name] = commands.add_parser(name, help=description)

    config = commands.add_parser("config", help="Manage workspace colors and terminal choice.")
    config_groups = config.add_subparsers(dest="config_group")
    colors = config_groups.add_parser("colors")
    color_actions = colors.add_subparsers(dest="color_action")
    color_actions.add_parser("list")
    color_actions.add_parser("show")
    color_set = color_actions.add_parser("set")
    color_set.add_argument("slot")
    color_set.add_argument("preset")
    custom = color_actions.add_parser("custom")
    custom.add_argument("slot")
    custom.add_argument("background")
    custom.add_argument("foreground")
    custom.add_argument("accent")
    color_actions.add_parser("reset")
    color_actions.add_parser("preview")
    terminal = config_groups.add_parser("terminal")
    terminal_actions = terminal.add_subparsers(dest="terminal_action")
    terminal_actions.add_parser("show")
    terminal_set = terminal_actions.add_parser("set")
    terminal_set.add_argument("name")
    command_parsers["config"] = config

    help_parser = commands.add_parser("help", help="Show general or command-specific help.")
    help_parser.add_argument("topics", nargs="*")
    command_parsers["help"] = help_parser
    uninstall = commands.add_parser("uninstall", help="Remove the managed user installation.")
    uninstall.add_argument("--purge", action="store_true")
    uninstall.add_argument("--force", action="store_true")
    command_parsers["uninstall"] = uninstall

    complete = commands.add_parser("__complete")
    complete.add_argument("--shell", required=True, choices=("bash", "zsh"))
    complete.add_argument("--cursor", required=True, type=int)
    complete.add_argument("words", nargs=argparse.REMAINDER)
    job_shell = commands.add_parser("__job-shell")
    job_shell.add_argument("job_id")
    job_shell.add_argument("--shell", required=True, choices=("bash", "zsh"))
    job_run = commands.add_parser("__job-run")
    job_run.add_argument("job_id")
    job_claim = commands.add_parser("__job-claim")
    job_claim.add_argument("job_id")
    job_claim.add_argument("pid", type=int)
    job_finish = commands.add_parser("__job-finish")
    job_finish.add_argument("job_id")
    job_finish.add_argument("pid", type=int)
    job_finish.add_argument("exit_code", type=int)
    setup_path = commands.add_parser("__setup-path")
    setup_path.add_argument("--shell", required=True, choices=("bash", "zsh"))
    return parser, command_parsers


def _dispatch(
    args: argparse.Namespace,
    passthrough: Sequence[str],
    parser: argparse.ArgumentParser,
    command_parsers: Mapping[str, argparse.ArgumentParser],
) -> int:
    if args.command is None:
        workspace = _standalone_workspace()
        if workspace is None:
            return 0
        return _start_controller(args.shell or _current_shell(), workspace)
    if passthrough and args.command not in {"build", "test", "run", "launch", "rviz"}:
        raise UsageError(f"{args.command} does not accept arguments after --")
    if args.command == "build":
        return _build(args, passthrough)
    if args.command == "create":
        return _create_package(args)
    if args.command == "pkg":
        if args.pkg_action != "create":
            raise UsageError("pkg requires create")
        return _create_package(args)
    if args.command == "test":
        return _test(args, passthrough)
    if args.command == "test-result":
        return _test_result()
    if args.command in {"run", "launch", "rviz"}:
        return _run_ros_command(args, passthrough)
    if args.command == "jobs":
        return _list_jobs(args.number)
    if args.command == "status":
        return _status()
    if args.command == "refresh":
        return _refresh()
    if args.command == "config":
        return _config(args)
    if args.command == "about":
        return _about()
    if args.command == "help":
        return _print_help_topic(args.topics, parser, command_parsers)
    if args.command == "uninstall":
        return _uninstall(args)
    if args.command == "exit":
        raise UsageError("exit is available only inside the LazyROS2 control shell")
    if args.command == "__complete":
        return _complete(args)
    if args.command == "__job-shell":
        return _start_job_shell(args.job_id, args.shell)
    if args.command == "__job-run":
        return _run_job(args.job_id)
    if args.command == "__job-claim":
        return _claim_job(args.job_id, args.pid)
    if args.command == "__job-finish":
        return _finish_job(args.job_id, args.pid, args.exit_code)
    if args.command == "__setup-path":
        return _setup_path(args.shell)
    raise LazyError(f"unknown command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    values = _normalize_help_argv(sys.argv[1:] if argv is None else argv)
    parser, command_parsers = _build_parser()
    parse_values, passthrough = _split_passthrough(values)
    try:
        args = parser.parse_args(parse_values)
        return _dispatch(args, passthrough, parser, command_parsers)
    except KeyboardInterrupt:
        return 130
    except FileNotFoundError as exc:
        command = exc.filename or "required command"
        print(f"lazy: {command} was not found", file=sys.stderr)
        return 127
    except CompletionError as exc:
        print(f"lazy: {exc}", file=sys.stderr)
        return exc.exit_code if exc.exit_code is not None else 3
    except (UsageError, ValueError, TypeError) as exc:
        print(f"lazy: {exc}", file=sys.stderr)
        return 2
    except (
        LazyError,
        WorkspaceError,
        OverlayEnvironmentError,
        ColconError,
        ConfigError,
        JobRegistryError,
        OSError,
    ) as exc:
        print(f"lazy: {exc}", file=sys.stderr)
        return 3
    finally:
        command = values[0] if values else ""
        if not command.startswith("__") and not os.environ.get("LAZYROS_ACTIVE"):
            print()
