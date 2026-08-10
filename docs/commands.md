# LazyROS2 v0.2 命令参考

## 调用方式

进入 workspace 根目录后，不带参数运行 `lazy` 会在当前 Bash 或 zsh 中打开控制 shell：

```sh
cd ~/robot_ws
lazy
```

带参数时是非交互 CLI，可用于脚本和 CI：

```sh
lazy build lidar_driver
```

启动时的 workspace 会经过 `realpath` 固定。控制 shell 内之后执行 `cd` 不会改变 LazyROS2 命令的 workspace。构建、安装和日志目录固定为 `<workspace>/build`、`<workspace>/install`、`<workspace>/log`。

## 构建与测试

### `build [PKG...]`

- 无 package：`colcon build`。
- 指定 package：`colcon build --packages-select PKG...`。
- `build up-to PKG...`：`colcon build --packages-up-to PKG...`。

`build PKG` 始终先只构建目标 package。失败后，只有 LazyROS2 找到尚未安装的 workspace 依赖候选时，任务窗口才询问是否改用 `--packages-up-to` 重试。候选只是重试依据，不会被描述成已确认的失败根因。非交互 CLI 从不询问，保留原失败码。

Lazy 不公开 colcon 参数透传。需要 CMake 参数、自定义 selection 或自定义布局时直接调用原生 `colcon`。

### `test [PKG...]`

无 package 时测试全部；指定 package 时使用 `--packages-select`。测试命令结束后自动运行：

```sh
colcon test-result --verbose
```

任一测试失败时返回非零。

### `test-result`

显示最近一次 workspace 测试的详细结果，不重新运行测试。

## 运行工具

### `run PKG [EXEC] [ARGS...]`

映射为 `ros2 run PKG EXEC ARGS...`。只有一个 executable 时，提交 `run PKG` 会自动选择；多个时必须用 Tab 选择。若需要传 ARGS，必须明确写出 EXEC。

### `launch PKG FILE [ARGS...]`

映射为 `ros2 launch PKG FILE ARGS...`，窗口默认值与 `run` 相同。LazyROS2 递归发现已安装 package 的 launch 文件，但传给 `ros2 launch` 的是 basename。同一 package 出现重复 basename 时会报告歧义，不会静默选择。

### `rviz [CONFIG]` / `rviz2 [CONFIG]`

两个入口同义。无配置时运行 `rviz2`；有配置时运行 `rviz2 -d CONFIG`。

固定 token 之后的运行参数按 argv 原样传递，不需要额外分隔符。例如：

```sh
lazy run demo talker --ros-args -r 'chatter:=robot chatter'
lazy launch demo bringup.launch.py use_sim_time:=true
```

任务窗口在进程成功、失败或收到 Ctrl+C 后保持打开，并回到任务 prompt。首次命令只放在该窗口的内存历史中；上箭头、回车即可重新运行。每次重启前重新读取 workspace 的 `install/local_setup.*`。

控制 shell 中的 build/test/run/launch/RViz 自动创建任务窗口；普通 CLI 在当前前台执行。没有图形会话或终端适配器时会明确报错，不会静默改变运行位置。GNOME Terminal 是 v0.2 的正式窗口适配器；其他适配器为 experimental。

## ROS graph、bag 与 package

- `node`、`topic`、`service`、`action`、`param`：零参数打开实时列表窗口；空格后 Tab 补动作，再按上下文补 ROS graph 对象。
- `bag record TOPIC...`：在 workspace 根目录录制，topic 可补全。
- `bag play BAG [TOPIC...]`：先补 workspace 中的 bag，再补 metadata 中的 topic。
- `bag info BAG`：显示 bag 信息。
- `list`：列出当前 workspace package。
- `pkg list`：列出当前 ROS/overlay 已安装的 package。
- `pkg create NAME TYPE [DEP...]`：在 `<workspace>/src` 创建 package；TYPE 为 `python` 或 `cpp`。
- `interface TYPE`：显示 interface 定义。
- `doctor` / `wtf`：在任务窗口运行 ROS doctor。

## 会话与状态

### `jobs`

打开实时任务面板，显示编号、颜色槽、命令类型、目标、状态、退出码和任务 shell PID。它不会替代原生 shell job control；在 Bash 或 zsh 中使用 `builtin jobs` 查看 shell job。

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
- `version`：输出包含 `VERSION` 中版本号的单行版本文本；`lazy --version` 暂时保留兼容。

## 补全数据

- `build`、`test`：workspace package。
- `run`：具有 executable 的 package，以及所选 package 的 executable。
- `launch`：具有 launch 文件的已安装 package，以及所选 package 的文件 basename。
- `rviz` / `rviz2`：`.rviz` 文件。
- graph 命令：动作和实时 ROS graph 对象。
- `bag`：topic、bag 路径和 bag metadata 中的 topic。
- `pkg create`：`python/cpp` 与 dependency package。
- `config`：颜色槽和终端适配器。

首次 Tab 只接受唯一候选或公共前缀；连续第二次 Tab 打开候选选择器，可用上下方向键选择并用 Enter 确认。缓存命中目标不超过 100 ms；冷采集硬超时为 2 秒，超时时使用 stale 快照。

## 退出码

| 退出码 | 含义 |
| --- | --- |
| 0 | LazyROS2 操作成功；窗口模式表示窗口已创建，不代表窗口内进程成功 |
| 2 | LazyROS2 参数错误 |
| 127 | 所需底层命令不存在 |
| 128 + N | 底层进程被 signal N 终止；Ctrl+C 通常为 130 |

其他情况下，当前前台执行的底层命令退出码 0–255 原样返回。
