<p align="right"><a href="./README.md">English</a> / <a href="./README.ja.md">日本語</a> / 简体中文</p>

# LazyROS2

> 面向日常 ROS 2 开发的 workspace-aware shell。

构建单个 package，用 Tab 找 executable，再把任务放进带编号和配色的独立窗口。LazyROS2 将常用的 `colcon` 和 `ros2` 工作流整理成简短、可发现的命令。

ROS 2 很擅长记住参数，人类不必也这么擅长。

## 安装

需要 Linux、Python 3.10+、Bash 或 zsh，以及已有的 ROS 2 环境。构建需要 `colcon`；图形任务窗口需要桌面会话和受支持的终端。

```sh
git clone --branch v0.3.0 --depth 1 https://github.com/Tsubashimo-Nanato/LazyROS2.git
cd LazyROS2
sh install.sh
```

重新打开终端，运行 `lazy version` 验证。安装仅面向当前用户，无需 sudo，也没有运行时 PyPI 依赖。卸载使用 `lazy uninstall`；加上 `--purge` 可同时清除配置和历史。

完整的下载与安装流程仍在 [#18](https://github.com/Tsubashimo-Nanato/LazyROS2/issues/18) 中继续简化。离线安装、升级和恢复说明见[安装指南](docs/install-layout.md)。

## 开始工作

先按平时的方式加载 ROS underlay，例如在 Bash 中运行 `source /opt/ros/jazzy/setup.bash`，然后：

```console
$ cd ~/robot_ws
$ lazy
[lazy:robot_ws | ros:jazzy] $ build my_robot
[lazy:robot_ws | ros:jazzy] $ run my_robot controller
[lazy:robot_ws | ros:jazzy] $ jobs
```

已有 workspace 直接进入。从它的 `src/` 子目录启动时，会找到上层 workspace。在其他目录启动时，交互引导可以经确认创建 `src/`，或让你用路径补全选择另一个目录。

LazyROS2 继承已导出的 ROS/硬件环境，不会替你加载这些 setup 脚本。请使用**尚未 source 当前 workspace 的 `install/`** 的终端。Lazy 保存这份构建基线，并为运行命令加载 workspace overlay。

## 少打参数，保留熟悉的命令

控制 shell 内省略 `lazy`：

| Lazy 命令 | 原生操作 |
| --- | --- |
| `build my_robot` | `colcon build --packages-select my_robot` |
| `build up-to my_robot` | `colcon build --packages-up-to my_robot` |
| `test my_robot` | 测试选中 package，再运行 `colcon test-result --verbose` |
| `run my_robot controller` | `ros2 run my_robot controller` |
| `launch my_robot bringup.launch.py` | `ros2 launch my_robot bringup.launch.py` |
| `rviz config/navigation.rviz` | `rviz2 -d config/navigation.rviz` |
| `topic echo /scan` | `ros2 topic echo /scan` |
| `pkg create sensors cpp rclcpp` | 在 `src/` 创建 `ament_cmake` package |

首次 Tab 补唯一候选或公共前缀，再按一次即可用方向键选择。Package、executable、launch 文件、路径和 ROS graph 对象均按上下文补全。Graph 快照复用三秒；冷查询的 ROS 采集预算为 1.5 秒，失败时保留旧结果。

Build、test、run、launch 和 RViz 使用保留的任务窗口。Ctrl+C 返回任务 prompt，上箭头和回车即可重跑，每次重启都会读取最新 overlay。构建成功后，控制 shell 在下次显示 prompt 时重新加载 overlay；已经停在 prompt 时按 Enter 即可。退出控制 shell 后，任务窗口仍可继续使用。

SSH、脚本和 CI 可以直接在当前终端执行：

```sh
lazy build my_robot
lazy run my_robot controller
lazy topic echo /scan
```

原生 `ros2` 和 `colcon` 始终可用。完整语法和当前限制见 `help COMMAND` 与[命令参考](docs/commands.md)。

## 多 workspace

分别打开终端，在 `~/robot_a_ws` 和 `~/robot_b_ws` 运行 `lazy`。每个实例始终绑定自己的 workspace，之后 `cd` 也不会改变它；历史、overlay、缓存、配置和任务视图彼此隔离。一个实例管理一个 workspace，暂不支持多层 overlay 栈。

## 支持与验证

CI 目标为 Ubuntu 22.04/Humble、24.04/Jazzy 和 26.04/Lyrical，对应 Python 3.10、3.12 和 3.14。x86_64 支持 Bash 和 zsh，GNOME Terminal 是主要图形终端适配器。

Fedora/Jazzy + micromamba 以及其他终端适配器为 experimental。arm64 为 best-effort，可查看[历史 Jetson 测试](docs/jetson-humble-smoke-2026-07-11.md)。Windows、macOS 和 PowerShell 不是受支持的运行环境。

[本次验证](docs/validation-2026-09-12.md) · [参与开发](CONTRIBUTING.md) · [安全策略](SECURITY.md)

Copyright © 2026 Tsubashimo-Nanato。[AGPL-3.0-or-later](LICENSE)，不提供担保。
