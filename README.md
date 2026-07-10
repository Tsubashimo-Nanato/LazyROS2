<p align="right"><a href="./README.zh-CN.md">简体中文</a></p>

# LazyROS2

> A workspace-aware shell for everyday ROS 2 development.

## Description

LazyROS2 turns common `colcon`, `ros2 run`, `ros2 launch`, and RViz workflows into short, discoverable commands with context-aware completion.

ROS 2 remembers every option. Humans should not have to.

```console
$ cd ~/robot_ws
$ lazy
[lazy:robot_ws | ros:jazzy] $ build navigation_bringup
[lazy:robot_ws | ros:jazzy] $ launch navigation_bringup navigation.launch.py
```

LazyROS2 does not replace `ros2` or `colcon`. Native commands remain available whenever the wrapper should politely step aside.

## Highlights

- Complete workspace packages, executables, launch files, RViz configs, and jobs.
- Build one package without retyping long selection flags.
- Run ROS processes in numbered, color-coded task windows.
- Press Ctrl+C, then Up and Enter to start a task again.
- Keep build environments separate from runtime overlays.
- Install and uninstall without sudo or runtime PyPI dependencies.

## Multiple workspaces

Each LazyROS2 instance binds to the workspace where it starts. Open another terminal to work in another workspace:

```sh
cd ~/robot_a_ws && lazy
cd ~/robot_b_ws && lazy
```

Both instances may run at the same time. Their completion caches, histories, task lists, colors, and overlays stay separate.

LazyROS2 does not currently stack multiple overlays inside one instance. One instance, one workspace—fewer surprises, and considerably fewer haunted terminals.

## Install

```sh
git clone --branch v0.1.0 --depth 1 https://github.com/Tsubashimo-Nanato/LazyROS2.git
cd LazyROS2
sh install.sh
```

Open a new terminal after installation. LazyROS2 never installs or modifies ROS 2. The first-time installation flow is being simplified further in [Issue #18](https://github.com/Tsubashimo-Nanato/LazyROS2/issues/18); verification, custom rc handling, upgrade, and recovery details live in the [install guide](docs/install-layout.md).

## Daily workflow

```console
$ cd ~/robot_ws
$ lazy
[lazy:robot_ws | ros:jazzy] $ build my_robot
[lazy:robot_ws | ros:jazzy] $ test my_robot
[lazy:robot_ws | ros:jazzy] $ run my_robot controller
[lazy:robot_ws | ros:jazzy] $ launch my_robot bringup.launch.py
[lazy:robot_ws | ros:jazzy] $ rviz config/navigation.rviz
[lazy:robot_ws | ros:jazzy] $ jobs
```

Use `help COMMAND` for exact syntax. `run`, `launch`, and `rviz` open task windows from the control shell by default; pass `--here` to keep a command in the current terminal.

For scripts and CI, skip the control shell:

```sh
lazy build my_robot
lazy run --here my_robot controller
```

## Support

| Platform | ROS 2 | Python | Status |
| --- | --- | --- | --- |
| Ubuntu 22.04 | Humble | 3.10 | Supported |
| Ubuntu 24.04 | Jazzy | 3.12 | Supported |
| Ubuntu 26.04 | Lyrical | 3.14 | Supported |
| Fedora 44 | Jazzy with micromamba | Distribution environment | Experimental |

Bash and zsh are supported on x86_64. arm64 is best-effort. Windows, macOS, PowerShell, and multi-overlay stacks are outside the v0.1 scope.

## Documentation

- [Command reference](docs/commands.md)
- [Install and uninstall](docs/install-layout.md)
- [Validation matrix](docs/validation.md)
- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)

## License

Copyright © 2026 Tsubashimo-Nanato.

LazyROS2 is licensed under [AGPL-3.0-or-later](LICENSE), without warranty.
