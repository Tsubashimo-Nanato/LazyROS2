# LazyROS2

LazyROS2 是一个面向 ROS 2 workspace 的开发 shell。它把常用的 `colcon`、`ros2 run`、`ros2 launch` 和 RViz 工作流整理成可补全、可诊断、可卸载的统一入口，同时保留原生命令供高级用法使用。

```console
$ cd ~/robot_ws
$ lazy
[lazy:robot_ws | ros:jazzy] $ build navigation_bringup
[lazy:robot_ws | ros:jazzy] $ launch navigation_bringup navigation.launch.py
```

v0.1.0 聚焦日常开发闭环：构建、测试、运行、launch、RViz、任务窗口、状态检查和上下文补全。它不是 `ros2` CLI 的完整替代品。

## 系统要求

- Linux、Python 3.10 或更新版本；运行时不依赖 PyPI 包。
- Bash 或 zsh。
- 对应功能所需的现有 `ros2`、`colcon`、`rviz2` 和图形终端。
- 单一 ROS 2 workspace；LazyROS2 不安装或修改 ROS 2。

正式验证目标如下：

| 系统 | ROS 2 | Python | 状态 |
| --- | --- | --- | --- |
| Ubuntu 22.04 | Humble | 3.10 | 支持 |
| Ubuntu 24.04 | Jazzy | 3.12 | 支持 |
| Ubuntu 26.04 | Lyrical | 3.14 | 支持 |
| Fedora 44 | Jazzy（micromamba） | 发行版环境 | Experimental |

x86_64 是正式验证架构；arm64 为 best-effort。Windows、macOS、PowerShell 和多层 overlay 栈不在 v0.1.0 支持范围内。

固定镜像 digest、真实 ROS workspace 覆盖项和 Fedora 实机版本记录见 [v0.1.0 验证记录](docs/validation.md)。

## 安装

从 [GitHub Releases](https://github.com/Tsubashimo-Nanato/LazyROS2/releases) 下载同一版本的归档和 `SHA256SUMS`，先验证再安装：

```sh
sha256sum --check SHA256SUMS
tar -xzf lazyros2-0.1.0.tar.gz
cd lazyros2-0.1.0
sh install.sh
```

安装器是无 `sudo` 的单用户安装器。它拒绝 root，先在目标文件系统内完成 staging 和 smoke test，再原子切换当前版本。默认会为当前 Bash 或 zsh 添加一个有明确 marker 的 rc 区块；不希望修改 rc 时使用：

```sh
sh install.sh --no-rc
```

Source ZIP 适合开发检出，但 ZIP 不保留 executable bit，因此仍应通过 `sh install.sh` 安装。项目不提供 `curl | sh` 路径，也不支持 `pip install`。

常见版本控制：

```sh
sh install.sh --reinstall          # 同版本内容发生变化时显式替换
sh install.sh --allow-downgrade    # 安装较旧版本
```

同版本且内容哈希一致时，安装器直接返回 no-op。布局、manifest schema 和失败恢复规则见 [安装与卸载设计](docs/install-layout.md)。

## 开始使用

进入 workspace 根目录再启动控制 shell。目录必须包含 `src/`，或能被 `colcon list` 识别出 package。

```sh
cd ~/robot_ws
lazy
```

常用命令：

```text
build [PKG...]              构建全部或指定 package
build --up-to PKG...        构建 package 及其 workspace 依赖
test [PKG...]               测试并显示详细 test-result
run PKG EXEC [-- ARGS...]   运行 executable
launch PKG FILE [-- ARGS...] 启动 launch 文件
rviz [CONFIG] [-- ARGS...]  启动 RViz
jobs                        查看 LazyROS2 任务窗口
status                      查看 workspace 和工具能力
refresh                     刷新补全快照
```

在控制 shell 中，`run`、`launch`、`rviz` 默认创建独立任务窗口；普通非交互调用（例如 `lazy run ...`）默认在当前前台执行。使用 `--here` 或 `--window` 可以显式选择。完整参数、透传规则和退出码见 [命令参考](docs/commands.md)。

传给 colcon、ROS 2 或 RViz 的底层参数必须放在显式 `--` 后；LazyROS2 移除这个分隔符，其余 argv（包括空格、引号字符、glob、`$()` 和 Unicode）不经 shell 求值地逐项传递。

## 环境边界

LazyROS2 在启动时固定 workspace 的真实路径，并把构建基线环境与运行 overlay 分开：

- `build` 和 `test` 不使用当前 workspace 的 overlay；
- `run`、`launch` 和 `rviz` 在执行前读取固定的 `install/local_setup.*`；
- 普通 `lazy build ...` 子进程不能修改调用者的父 shell；下一次 LazyROS2 命令会重新读取 overlay；
- 如果启动前已经 source 当前 workspace，LazyROS2 会拒绝启动并给出诊断。

ROS 2、硬件驱动和 micromamba 环境仍由用户现有 shell 配置负责。LazyROS2 的 rc 区块只加入自身 launcher 和 shell integration。

## 卸载

```sh
lazy uninstall
lazy uninstall --purge
```

普通卸载保留配置、补全缓存和历史。`--purge` 会先确认，再删除 LazyROS2 状态。若 CLI 无法启动，可直接运行当前 payload 内的安装器：

```sh
sh "$HOME/.local/lib/lazyros2/current/install.sh" --uninstall
```

卸载前会验证 manifest schema、路径边界、文件类型和 SHA-256。受管文件或 rc marker 被修改时默认停止；确认要移除这些内容时使用 `--force`。安装器不会删除未在 manifest 中声明的文件或非空父目录。

## 参与开发

开发流程、测试要求和 issue/PR 约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。发行步骤见 [RELEASING.md](RELEASING.md)。

## 许可

Copyright © 2026 Tsubashimo-Nanato.

LazyROS2 以 [GNU Affero General Public License v3.0 or later](LICENSE)（`AGPL-3.0-or-later`）发布，不提供任何担保。
