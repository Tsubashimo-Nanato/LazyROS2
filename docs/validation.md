# v0.1.0 validation record

Validation recorded on 2026-07-10 used the same source tree and `tests/ros_smoke.sh` in every ROS image. No ROS package manager or Python runtime dependency was installed by LazyROS2.

## Supported ROS matrix

The x86_64 image manifests are pinned in `CI / required`:

| ROS 2 / Ubuntu | Python target | Image manifest |
| --- | --- | --- |
| Humble / Jammy | 3.10 | `docker.io/library/ros@sha256:5c793b92e0b12d6babb438cb20eed7766495fde6419a21e3d2e918464f09dc17` |
| Jazzy / Noble | 3.12 | `docker.io/library/ros@sha256:567b81bc54f44479e16ef1b75e4984d132f154b6511ea4fc851ee6bde76c30f8` |
| Lyrical / Resolute | 3.14 | `docker.io/library/ros@sha256:8734a98f31c79c28e103cc6c254cb300d412db72932eca137cb66ea5376a5228` |

Each environment creates two disposable `ament_cmake` packages with a workspace dependency and verifies selected-build failure, `--packages-up-to`, build all, test/test-result, literal run arguments, launch, package/executable/launch completion, rebuild overlay visibility, and duplicate launch basename rejection.

## Fedora experimental acceptance

The experimental host was Fedora 44 x86_64 with zsh 5.9, a ROS 2 Jazzy micromamba environment, ros2cli 0.32.9, colcon-core 0.21.0, and Python 3.14.6. The checkout path was `/home/nanato/LazyRos`; every installer check used a temporary HOME and every ROS check used a temporary workspace.

Validated host behavior included:

- the complete standard-library suite, including Bash/zsh task and completion PTY cases;
- ShellCheck 0.11.0, shfmt 3.7.0, and Bats 1.13.0;
- a real GNOME Terminal task window under the active Wayland/Xwayland session;
- task-shell PID claim, idle/exit status, numbered title, the first accessible color slot, and automatic registry cleanup;
- a temporary release-layout install, version smoke, verified uninstall, rollback and sentinel preservation.

The twelve preset colors and their wraparound assignment are also covered by deterministic configuration and registry tests. Visual preference is intentionally not treated as an automated pass/fail criterion.
