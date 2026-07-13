# LazyROS2 v0.1 命令参考

## 调用方式

进入 workspace 根目录后，不带参数运行 `lazy` 会在当前 Bash 或 zsh 中打开控制 shell：

```sh
cd ~/robot_ws
lazy
```

不带参数启动时，LazyROS2 会显示欢迎图案并确认探测到的 workspace。无法识别时会说明
原因和可能修复，并允许在当前目录创建 workspace、输入另一个目录，或退出后从正确目录
重新运行。创建前会列出新增目录和数据删除情况，并再次确认。此启动形式要求终端。

带参数时是非交互 CLI，可用于脚本和 CI：

```sh
lazy build lidar_driver
```

启动时的 workspace 会经过 `realpath` 固定。控制 shell 内之后执行 `cd` 不会改变 LazyROS2 命令的 workspace。构建、安装和日志目录固定为 `<workspace>/build`、`<workspace>/install`、`<workspace>/log`。

## 构建与测试

### `create pkg [LANGUAGE] [NAME] [DEPENDENCY...]`

在 workspace 的 `src` 目录中调用 `ros2 pkg create`。`pkg` 和 `package` 等价；Python
可写作 `python` 或 `py`，C++ 可写作 `cpp`、`c++` 或 `c`。缺少语言或名称时会交互询问；
直接回车会取消且不创建文件。依赖项可省略，LazyROS2 会提醒但仍继续创建。例如：

```sh
lazy create pkg python lidar_driver rclpy sensor_msgs
lazy create package py camera_driver
```

兼容写法 `lazy pkg create NAME LANGUAGE [DEPENDENCY...]` 也可用。Tab 会依次补全资源类型、
语言别名和依赖 package。无法发现的依赖会被警告，但仍会写入生成的 package；之后可编辑
`package.xml` 和构建文件修正。

### `build [PKG...] [-- ARGS...]`

- 无 package：`colcon build`。
- 指定 package：`colcon build --packages-select PKG...`。
- `build --up-to PKG...`：`colcon build --packages-up-to PKG...`。
- `--` 后的参数按 argv 原样传给 colcon。

`build PKG` 始终先只构建目标 package。失败后，只有 LazyROS2 找到尚未安装的 workspace 依赖候选时，交互控制 shell 才询问是否改用 `--packages-up-to` 重试。候选只是重试依据，不会被描述成已确认的失败根因。非交互 CLI 从不询问，保留原失败码。

以下参数不能出现在透传区：package selection 参数、`--base-paths`、`--build-base`、`--install-base`、`--log-base` 和 `--merge-install`。需要自定义 colcon 布局时应直接调用原生命令。

### `test [PKG...] [-- ARGS...]`

无 package 时测试全部；指定 package 时使用 `--packages-select`。测试命令结束后自动运行：

```sh
colcon test-result --verbose
```

任一测试失败时返回非零。

### `test-result`

显示最近一次 workspace 测试的详细结果，不重新运行测试。

## 运行工具

### `run [--window|--here] PKG EXEC [-- ARGS...]`

映射为 `ros2 run PKG EXEC ARGS...`。控制 shell 默认 `--window`，普通 CLI 默认 `--here`。

### `launch [--window|--here] PKG FILE [-- ARGS...]`

映射为 `ros2 launch PKG FILE ARGS...`，窗口默认值与 `run` 相同。LazyROS2 递归发现已安装 package 的 launch 文件，但传给 `ros2 launch` 的是 basename。同一 package 出现重复 basename 时会报告歧义，不会静默选择。

### `rviz [--window|--here] [CONFIG] [-- ARGS...]`

无配置时运行 `rviz2`；有配置时运行 `rviz2 -d CONFIG`。控制 shell 默认创建任务窗口。

三条命令的额外底层参数都必须位于显式 `--` 后。LazyROS2 只移除分隔符，后续 argv 不做拼接、展开或求值。例如：

```sh
lazy run --here demo talker -- --ros-args -r 'chatter:=robot chatter'
lazy launch demo bringup.launch.py -- use_sim_time:=true
```

任务窗口在进程成功、失败或收到 Ctrl+C 后保持打开，并回到任务 prompt。首次命令只放在该窗口的内存历史中；上箭头、回车即可重新运行。每次重启前重新读取 workspace 的 `install/local_setup.*`。

若没有图形会话或支持的终端，`--window` 返回错误并建议 `--here`，不会静默改变运行位置。GNOME Terminal 是 v0.1 的正式窗口适配器；`xdg-terminal-exec`、Konsole、Kitty、Ghostty 和 Alacritty 为 experimental。

## 会话与状态

### `jobs [NUMBER]`

列出 LazyROS2 创建的任务窗口，包括编号、颜色槽、命令类型、目标、状态、退出码和任务 shell PID。可选编号只显示该任务。它不会替代原生 shell job control；在 Bash 或 zsh 中使用 `builtin jobs` 查看 shell job。

### `status`

显示：

- 固定 workspace 路径；
- `ROS_DISTRO` 及 ROS 2、colcon、RViz 可用性；
- 构建基线和 overlay 状态；
- 终端适配器；
- 补全缓存是否 fresh、dirty 或 stale。

### `refresh`

重新采集 package、executable 和 launch 补全数据，并原子替换成功的缓存快照。采集失败不会覆盖旧快照；显式 `refresh` 会报告失败并返回非零，而 Tab 补全可静默使用 stale 快照。

## 配置

### `config colors [list|show|set SLOT PRESET|custom SLOT BG FG ACCENT|reset|preview]`

查看、预览、设置或重置当前 workspace 的任务窗口配色。自定义值必须是 `#RRGGBB`；前景/背景对比度至少为 4.5:1，强调色/背景至少为 3:1。修改只影响之后创建的窗口。

### `config terminal [show|set NAME]`

查看自动探测结果，或为当前 workspace 选择一个内置终端适配器。不接受任意 shell command template。

## 帮助与生命周期

- `help [COMMAND]`：显示命令帮助和示例。
- `about`：显示版本、版权、`AGPL-3.0-or-later`、无担保声明和源码地址。
- `uninstall [--purge] [--force]`：调用当前 payload 中的受管卸载器。
- `exit`：退出控制 shell；已创建的任务窗口继续运行。
- `lazy --version`：输出包含 `VERSION` 中版本号的版本文本。

## 补全数据

- `build`、`test`：workspace package。
- `run`：具有 executable 的 package，以及所选 package 的 executable。
- `launch`：具有 launch 文件的已安装 package，以及所选 package 的文件 basename。
- `rviz`：`.rviz` 文件。
- `jobs`：任务编号。
- `config`：颜色槽和终端适配器。

首次 Tab 只接受唯一候选或公共前缀；缓冲区未改变时再次 Tab 才列出全部。缓存命中目标不超过 100 ms；冷采集硬超时为 2 秒，超时时使用 stale 快照。

## 退出码

| 退出码 | 含义 |
| --- | --- |
| 0 | LazyROS2 操作成功；窗口模式表示窗口已创建，不代表窗口内进程成功 |
| 2 | LazyROS2 参数错误 |
| 127 | 所需底层命令不存在 |
| 128 + N | 底层进程被 signal N 终止；Ctrl+C 通常为 130 |

其他情况下，当前前台执行的底层命令退出码 0–255 原样返回。
