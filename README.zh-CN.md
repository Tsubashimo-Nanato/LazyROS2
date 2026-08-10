<p align="right"><a href="./README.md">English</a> / <a href="./README.ja.md">日本語</a> / 简体中文</p>

# LazyROS2

> 面向日常 ROS 2 开发的 workspace-aware shell。

## 项目描述

LazyROS2 把常用的 `colcon`、`ros2 run`、`ros2 launch` 和 RViz 工作流整理成简短、可发现、支持上下文补全的命令。

```console
$ cd ~/robot_ws
$ lazy
[lazy:robot_ws | ros:jazzy] $ build navigation_bringup
[lazy:robot_ws | ros:jazzy] $ launch navigation_bringup navigation.launch.py
```

LazyROS2 不会替代 `ros2` 或 `colcon`。超出包装器范围的操作仍可使用原生命令。

## 主要能力

- 补全 workspace package、executable、launch 文件、RViz 配置、ROS graph 对象、bag 和任务。
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

LazyROS2 暂不支持在同一个实例中叠加多层 overlay。每个实例管理一个 workspace。

## 安装

```sh
git clone --branch v0.2.0 --depth 1 https://github.com/Tsubashimo-Nanato/LazyROS2.git
cd LazyROS2
sh install.sh
```

安装后重新打开终端。LazyROS2 不会安装或修改 ROS 2。校验、自定义 rc、升级、卸载和恢复说明均见[安装指南](docs/install-layout.md)；[Issue #18](https://github.com/Tsubashimo-Nanato/LazyROS2/issues/18) 用于跟踪后续首次使用体验改进。

## 日常使用

```console
$ cd ~/robot_ws
$ lazy
[lazy:robot_ws | ros:jazzy] $ build my_robot
[lazy:robot_ws | ros:jazzy] $ build up-to navigation_bringup
[lazy:robot_ws | ros:jazzy] $ test my_robot
[lazy:robot_ws | ros:jazzy] $ run my_robot controller
[lazy:robot_ws | ros:jazzy] $ launch my_robot bringup.launch.py
[lazy:robot_ws | ros:jazzy] $ rviz config/navigation.rviz
[lazy:robot_ws | ros:jazzy] $ jobs
[lazy:robot_ws | ros:jazzy] $ topic
```

使用 `help COMMAND` 查看准确语法。控制 shell 中的 build、test、run、launch、RViz、实时 graph 和 jobs 使用可保留的任务窗口。按一次 Tab 补前缀，再按一次即可用方向键选择。

脚本和 CI 可以直接使用非交互 CLI：

```sh
lazy build my_robot
lazy run my_robot controller
```

## 支持范围

| 平台 | ROS 2 | Python | 状态 |
| --- | --- | --- | --- |
| Ubuntu 22.04 | Humble | 3.10 | 支持 |
| Ubuntu 24.04 | Jazzy | 3.12 | 支持 |
| Ubuntu 26.04 | Lyrical | 3.14 | 支持 |
| Fedora 44 | Jazzy + micromamba | 发行版环境 | Experimental |

x86_64 上支持 Bash 和 zsh；arm64 为 best-effort，并已在 Ubuntu 22.04 + ROS 2 Humble 的 Jetson 上完成 smoke test。Windows、macOS、PowerShell 和多层 overlay 栈不在 v0.2 范围内。

## 文档

- [命令参考](docs/commands.md)
- [安装与卸载](docs/install-layout.md)
- [v0.1 历史验证记录](docs/validation.md)
- [Jetson Humble smoke 报告](docs/jetson-humble-smoke-2026-07-11.md)
- [参与开发](CONTRIBUTING.md)
- [安全策略](SECURITY.md)

## 许可

Copyright © 2026 Tsubashimo-Nanato.

LazyROS2 使用 [AGPL-3.0-or-later](LICENSE) 许可，不提供任何担保。
