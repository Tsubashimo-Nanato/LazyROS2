# Changelog

All notable changes to LazyROS2 are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow Semantic Versioning.

## [Unreleased]

## [0.3.0] - 2026-09-12

### Added

- Interactive workspace startup with ancestor discovery, confirmed `src/` creation, and directory selection with path completion.
- A shared three-second ROS graph cache for completion and live lists, preserving stale snapshots when collection fails.
- A Japanese README and an English command reference alongside the Chinese quick start.

### Fixed

- Reload the control-shell overlay at its next prompt after a successful task-window build.
- Keep task-window baselines available for restarts after the controller exits.
- Quote shell-sensitive completion candidates consistently in Bash and zsh.
- Reuse warm run/launch completion without sourcing the workspace again, and invalidate package completion after package creation.
- Preserve failures from workspace setup scripts instead of continuing with a partial environment.
- Honor current runtime exports, including ROS domain and middleware changes, while keeping the build baseline unchanged.
- Prevent Lazy's bytecode files from interfering with managed reinstall and uninstall; exclude development bytecode from the payload.
- Replace deprecated GitHub Actions runtime revisions with verified Node.js 24 pins.

## [0.2.0] - 2026-07-11

### Added

- Context-aware ROS graph, bag, package, interface, doctor, `rviz2`, `version`, and workspace-list commands.
- Shared Bash/zsh second-Tab candidate picker with arrow-key navigation.
- Live task and ROS graph views in persistent task windows.
- Ubuntu 22.04 / ROS 2 Humble / aarch64 smoke validation on NVIDIA Jetson.

### Changed

- Replaced public `build --up-to` with `build up-to` and removed routine location/separator flags from the normal command path.
- Build and test now use persistent task windows from the control shell.
- `run PKG` infers a unique executable and asks the user to choose when several exist.
- Package creation uses `pkg create NAME TYPE [DEP...]` and writes to the workspace `src` directory.

## [0.1.0] - 2026-07-10

### Added

- Workspace-aware Bash and zsh control shell with interactive and non-interactive `lazy` entry points.
- Build, test, run, launch, RViz, jobs, status, refresh, config, help, about, and uninstall workflows.
- Context-aware package, executable, launch-file, RViz, job, color, and terminal completion caches.
- Independent task windows with numbered titles, twelve accessible color slots, in-memory restart history, and runtime job state.
- Separate build baseline and run overlay environments.
- Transactional single-user installer, manifest schema v1, verified uninstall, and optional state purge.
- Required CI and an explicitly dispatched annotated-tag release workflow for versioned tar archives, SHA-256 sums, and provenance attestation.

[Unreleased]: https://github.com/Tsubashimo-Nanato/LazyROS2/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/Tsubashimo-Nanato/LazyROS2/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Tsubashimo-Nanato/LazyROS2/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Tsubashimo-Nanato/LazyROS2/tree/v0.1.0
