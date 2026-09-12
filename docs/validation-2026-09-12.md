# Initial-usability validation — 2026-09-12

This milestone keeps the existing command surface and focuses on the ordinary development loop: install, choose a workspace, complete a command, build/test, run/launch, restart a task, and uninstall. Historical `v0.1.0` and `v0.2.0` tags are unchanged; the candidate version is `0.3.0`.

## Changes under test

- Workspace startup finds the nearest `src/` boundary. Outside a workspace, an interactive user can confirm creation or choose a directory with path completion; cancellation leaves the directory alone.
- Build/test retain the clean startup baseline. Runtime commands load the latest validated workspace setup, and setup failures remain visible.
- Successful builds notify the controller to reload at its next prompt. Task windows own their baseline, so controller exit does not break subsequent restarts.
- Bash and zsh completion quote spaces, quotes, glob characters, `$()` and Unicode as literal arguments. Cursor and cancellation regressions are covered by PTY tests.
- Warm run/launch completion avoids sourcing the overlay again. ROS graph completion and live views share a three-second cache, with bounded queries and stale fallback.
- Ordinary installation use does not write bytecode into the managed payload. Install/use/reinstall/uninstall/purge checks preserve unrelated files and shell settings.

## Reproducible checks

The [CI workflow](../.github/workflows/ci.yml) runs on Ubuntu 24.04 hosts with pinned actions. ROS integration uses digest-pinned official images rather than the host ROS installation.

| Check | Coverage |
| --- | --- |
| Python 3.10, 3.12, 3.14 | Standard-library tests, environment/cache/parser/installer regressions, available-shell PTY cases |
| Shell and metadata | Bash/zsh syntax, ShellCheck, shfmt, Bats, full test discovery with zsh installed |
| Humble / Jammy | Real dependency package, application package, and generated package |
| Jazzy / Noble | The same real ROS workflow |
| Lyrical / Resolute | The same real ROS workflow |
| GNOME Terminal | Literal argv and command startup under Xvfb + D-Bus |

The ROS fixture checks selected-build failure without its dependency, up-to/all builds, tests/results, run/launch, package/executable/launch completion, package creation and cache invalidation, updated installed executables, duplicate launch-basename rejection, and live graph discovery. Its synthetic graph node never publishes messages and is stopped in `finally`.

The initial stability run passed all three Python and ROS environments plus GNOME Terminal. Its ShellCheck warnings were corrected before the next run. Final check links and counts will be recorded before the candidate is tagged.

## Completion latency

The first ROS run measured repeated separate-process graph completion p95 at **104.1 ms (Humble), 117.9 ms (Jazzy), and 124.5 ms (Lyrical)**. These include launcher/Python startup and may include a TTL refresh; they are not an isolated cache-lookup benchmark. Tests separately verify that fresh hits do not spawn ROS discovery or source the overlay.

The 100 ms target is not yet demonstrated across supported environments. [Issue #25](https://github.com/Tsubashimo-Nanato/LazyROS2/issues/25) stays open for that remaining acceptance criterion. A cold graph query has a 1.5-second collection budget; failure retains an older snapshot when available.

## Boundaries and remaining work

- This run did not validate a physical robot or a real desktop session. The previously supplied LAN ROS machines were unreachable. No motors or other hardware were controlled.
- GNOME Terminal's automated adapter test is not a visual sign-off for all twelve colors, desktop focus behavior, or every experimental terminal.
- arm64 and Fedora/micromamba are not revalidated here. The [earlier Jetson report](jetson-humble-smoke-2026-07-11.md) is historical evidence, not proof for this candidate.
- Existing local Docker Desktop could not start; no Docker reset, replacement VM, or new local/remote dependency installation was performed. Linux validation therefore ran in GitHub CI.
- The primary installation path remains a tagged checkout followed by `sh install.sh`. End-to-end artifact selection, downloading and verification are still tracked in [#18](https://github.com/Tsubashimo-Nanato/LazyROS2/issues/18); a shorter README is not completion of that redesign.
- Large selectors, interactive configuration, parameter-name completion, contextual help and typed message templates remain in their existing issues. Native `ros2` and `colcon` remain available.
- This milestone creates a Git tag only. It does not publish a GitHub Release or downloadable release assets.
