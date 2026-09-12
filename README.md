<p align="right">English / <a href="./README.ja.md">日本語</a> / <a href="./README.zh-CN.md">简体中文</a></p>

# LazyROS2

> A workspace-aware shell for everyday ROS 2 development.

Build a package, find an executable with Tab, and keep each task in its own numbered, color-coded window. LazyROS2 gives common `colcon` and `ros2` workflows short, discoverable commands.

ROS 2 remembers every option. Humans should not have to.

## Install

You need Linux, Python 3.10+, Bash or zsh, and an existing ROS 2 environment. Builds need `colcon`; graphical task windows need a desktop session and a supported terminal.

```sh
git clone --branch v0.3.0 --depth 1 https://github.com/Tsubashimo-Nanato/LazyROS2.git
cd LazyROS2
sh install.sh
```

Open a new terminal, then run `lazy version`. Installation is per-user, without sudo or runtime PyPI dependencies. To remove it, run `lazy uninstall`; add `--purge` to also remove settings and history.

The complete download-and-install flow is still being simplified in [#18](https://github.com/Tsubashimo-Nanato/LazyROS2/issues/18). See the [install guide](docs/install-layout.md) for offline use, upgrades, and recovery.

## Start working

Load your ROS underlay as usual—for example, `source /opt/ros/jazzy/setup.bash` in Bash—then:

```console
$ cd ~/robot_ws
$ lazy
[lazy:robot_ws | ros:jazzy] $ build my_robot
[lazy:robot_ws | ros:jazzy] $ run my_robot controller
[lazy:robot_ws | ros:jazzy] $ jobs
```

An existing workspace opens immediately. Starting inside its `src/` tree finds the workspace above it. Outside a workspace, interactive startup can create `src/` after confirmation or let you choose another directory with path completion.

LazyROS2 inherits your exported ROS/hardware environment; it does not load those setup scripts for you. Start from a terminal that has **not** sourced this workspace's `install/`. Lazy keeps that clean build baseline and loads the workspace overlay for runtime commands.

## Fewer flags, familiar commands

Inside the control shell, omit `lazy`:

| Lazy command | Native operation |
| --- | --- |
| `build my_robot` | `colcon build --packages-select my_robot` |
| `build up-to my_robot` | `colcon build --packages-up-to my_robot` |
| `test my_robot` | Selected tests, then `colcon test-result --verbose` |
| `run my_robot controller` | `ros2 run my_robot controller` |
| `launch my_robot bringup.launch.py` | `ros2 launch my_robot bringup.launch.py` |
| `rviz config/navigation.rviz` | `rviz2 -d config/navigation.rviz` |
| `topic echo /scan` | `ros2 topic echo /scan` |
| `pkg create sensors cpp rclcpp` | Create an `ament_cmake` package under `src/` |

Press Tab once for a unique match or common prefix, then again for the arrow-key selector. Packages, executables, launch files, paths, and ROS graph objects complete in context. Graph snapshots are reused for three seconds; cold ROS queries have a 1.5-second budget and failures retain stale results.

Build, test, run, launch, and RViz open persistent task windows. Ctrl+C returns to the task prompt; Up and Enter rerun the command. Each restart reads the latest overlay. After a successful build, the control shell reloads it at the next prompt—press Enter if it is already waiting. Task windows remain usable after you exit the control shell.

For SSH, scripts, or CI, commands run in the current terminal:

```sh
lazy build my_robot
lazy run my_robot controller
lazy topic echo /scan
```

Native `ros2` and `colcon` remain available. See `help COMMAND` and the [command reference](docs/commands.md) for the full surface and current limits.

## Multiple workspaces

Open separate terminals and run `lazy` in `~/robot_a_ws` and `~/robot_b_ws`. Each instance stays bound to its workspace even after `cd`; histories, overlays, caches, settings, and task views remain separate. One instance manages one workspace, without a multi-overlay stack.

## Support and verification

The CI targets are Ubuntu 22.04/Humble, 24.04/Jazzy, and 26.04/Lyrical with Python 3.10, 3.12, and 3.14. Bash and zsh are supported on x86_64; GNOME Terminal is the primary graphical adapter.

Fedora/Jazzy with micromamba and other terminal adapters are experimental. arm64 is best-effort, with [historical Jetson testing](docs/jetson-humble-smoke-2026-07-11.md). Windows, macOS, and PowerShell are not supported runtimes.

[Current validation](docs/validation-2026-09-12.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

Copyright © 2026 Tsubashimo-Nanato. [AGPL-3.0-or-later](LICENSE), without warranty.
