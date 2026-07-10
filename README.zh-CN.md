<p align="right"><a href="./README.md">English</a></p>

# LazyROS2

> 面向日常 ROS 2 开发的 workspace-aware shell。

## 项目描述

LazyROS2 把常用的 `colcon`、`ros2 run`、`ros2 launch` 和 RViz 工作流整理成简短、可发现、支持上下文补全的命令。

ROS 2 很擅长记住参数，人类不必也这么擅长。

```console
$ cd ~/robot_ws
$ lazy
[lazy:robot_ws | ros:jazzy] $ build navigation_bringup
[lazy:robot_ws | ros:jazzy] $ launch navigation_bringup navigation.launch.py
```

LazyROS2 不会替代 `ros2` 或 `colcon`。需要高级用法时，原生命令始终可以接手。

## 主要能力

- 补全 workspace package、executable、launch 文件、RViz 配置和任务编号。
- 构建单个 package，不再重复输入冗长的选择参数。
- 在带编号和配色的独立窗口中运行 ROS 进程。
- Ctrl+C 后按上箭头和回车即可重新运行任务。
- 分离构建环境与运行 overlay。
- 无 sudo 安装和卸载，运行时不依赖 PyPI 包。

## 多 workspace

每个 LazyROS2 实例固定绑定启动时所在的 workspace。需要处理另一个 workspace 时，再开一个终端：

```sh
cd ~/robot_a_ws && lazy
cd ~/robot_b_ws && lazy
```

两个实例可以同时运行。它们的补全缓存、历史、任务列表、配色和 overlay 彼此隔离。

LazyROS2 暂不支持在同一个实例中叠加多层 overlay。一个实例，一个 workspace——少一点意外，也少一点终端闹鬼。

## 安装

```sh
git clone --branch v0.1.0 --depth 1 https://github.com/Tsubashimo-Nanato/LazyROS2.git
cd LazyROS2
sh install.sh
```

安装后重新打开终端。LazyROS2 不会安装或修改 ROS 2。首次安装流程会在 [Issue #18](https://github.com/Tsubashimo-Nanato/LazyROS2/issues/18) 中继续简化；校验、自定义 rc、升级和恢复说明见[安装指南](docs/install-layout.md)。

## 日常使用

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

使用 `help COMMAND` 查看准确语法。在控制 shell 中，`run`、`launch` 和 `rviz` 默认打开任务窗口；传入 `--here` 可留在当前终端。

脚本和 CI 可以直接使用非交互 CLI：

```sh
lazy build my_robot
lazy run --here my_robot controller
```

## 支持范围

| 平台 | ROS 2 | Python | 状态 |
| --- | --- | --- | --- |
| Ubuntu 22.04 | Humble | 3.10 | 支持 |
| Ubuntu 24.04 | Jazzy | 3.12 | 支持 |
| Ubuntu 26.04 | Lyrical | 3.14 | 支持 |
| Fedora 44 | Jazzy + micromamba | 发行版环境 | Experimental |

x86_64 上支持 Bash 和 zsh；arm64 为 best-effort。Windows、macOS、PowerShell 和多层 overlay 栈不在 v0.1 范围内。

## 文档

- [命令参考](docs/commands.md)
- [安装与卸载](docs/install-layout.md)
- [验证矩阵](docs/validation.md)
- [参与开发](CONTRIBUTING.md)
- [安全策略](SECURITY.md)

## 许可

Copyright © 2026 Tsubashimo-Nanato.

LazyROS2 使用 [AGPL-3.0-or-later](LICENSE) 许可，不提供任何担保。
