# LazyROS2 command reference

Inside the control shell, use `COMMAND`. Outside it, use `lazy COMMAND`. Examples below use the short form unless they specifically demonstrate standalone CLI behavior.

## Starting a workspace

```sh
cd ~/robot_ws
lazy
```

A directory with `src/` is accepted, including an empty workspace. Other layouts are accepted when `colcon list` discovers packages. Interactive startup also finds the nearest ancestor with a `src/` directory. It does not run a recursive colcon search over parent directories.

When no workspace is found, startup offers to create `src/` after confirmation or choose another existing directory. Directory input supports Tab; an empty answer, EOF, or Ctrl+C cancels. Existing files and dangling symlinks at `src` are not replaced. Noninteractive startup reports an actionable error without prompting.

The selected real path is fixed for that instance. Later `cd` commands do not change where Lazy builds or runs. Separate terminals can run separate workspaces concurrently. Build paths remain `<workspace>/build`, `install`, and `log`.

Load your ROS underlay before starting Lazy. Do not source this workspace's `install/` in the parent terminal. Lazy inherits the exported environment, captures the build baseline, then loads the workspace overlay for runtime commands. The installer only integrates Lazy itself; ROS and hardware setup remain under your control.

Runtime commands and graph completion follow later exports such as `ROS_DOMAIN_ID` in the control or task shell. Build/test keep the original baseline. Existing task windows inherit the environment from when they were opened; changes in a different terminal are not broadcast to them.

## Build and test

### `build [PKG...]`

With no package, run `colcon build`. Named packages use `--packages-select`.

```sh
build
build lidar_driver navigation_bringup
```

Package names complete from the current workspace. Build runs against the captured baseline, without this workspace's overlay.

### `build up-to PKG...`

Build the target and its recursive workspace dependencies using `colcon build --packages-up-to`.

```sh
build up-to navigation_bringup
```

Plain `build PKG` still tries only that package first. If it fails and Lazy found uninstalled workspace dependency candidates, an interactive task may offer an up-to retry. These candidates are evidence for a retry, not a diagnosis. Standalone CLI never prompts and preserves the original failure code.

Lazy does not expose colcon selection or layout passthrough. Use native `colcon` for CMake options or advanced build layouts.

### `test [PKG...]`

Run all tests, or select named packages, then run `colcon test-result --verbose`. A failed test command or result check returns nonzero. Tests use the same baseline and task-window rules as builds.

### `test-result`

Show the most recent workspace test results without running tests again.

## Run and launch

### `run PKG [EXEC] [ARGS...]`

Map to `ros2 run PKG EXEC ARGS...`. Tab first completes packages with executables, then the selected package's executables.

```sh
run my_robot controller
run my_robot controller --ros-args -p use_sim_time:=true
```

If a package has exactly one executable, `run PKG` selects it automatically. When several exist, choose one with Tab or the interactive selector. To pass arguments, explicitly name the executable.

### `launch PKG FILE [ARGS...]`

Map to `ros2 launch PKG FILE ARGS...`.

```sh
launch my_robot bringup.launch.py use_sim_time:=true
```

Tab completes installed packages with launch files, then their filenames. Discovery scans nested launch directories; ROS receives the basename. Duplicate basenames within one package are rejected as ambiguous.

### `rviz [CONFIG]` / `rviz2 [CONFIG]`

Both names run `rviz2`; a configuration maps to `rviz2 -d CONFIG`. Tab completes `.rviz` files. Quote paths containing spaces.

```sh
rviz "config/warehouse view.rviz"
```

Run and launch arguments pass directly after the fixed tokens as literal argv. No extra `--` separator is required.

## Task windows and headless use

In the control shell, build, test, run, launch, RViz, graph commands, bag operations, doctor, and jobs use persistent task windows. Windows have a workspace title, task number, and one of twelve color slots.

A task returns to its prompt after success, failure, or Ctrl+C. Up and Enter rerun it from in-memory history. The task shell skips user rc files and inherits the exported environment. Runtime commands read the current overlay on each restart.

Successful builds mark the overlay for reload in the control shell at its next prompt. If the control shell is already waiting, press Enter. Exiting the control shell leaves task windows running and restartable; a new controller for the same workspace can discover them.

Standalone CLI runs in the current terminal, making it suitable for SSH and CI:

```sh
lazy build my_robot
lazy run my_robot controller
lazy topic echo /scan
```

GNOME Terminal is the primary graphical adapter. Other detected adapters are experimental. A control-shell window command on a headless machine reports an error; use standalone CLI from your normal shell.

### `jobs`

Show the workspace task panel: number, color slot, state, task-shell PID, command, and last exit code. It updates on a terminal and prints a snapshot when redirected. Use `builtin jobs` for native shell job control.

## ROS graph commands

`node`, `topic`, `service`, `action`, and `param` without arguments show a live list. With arguments, they pass the operation to the corresponding `ros2` command.

```sh
node info /controller
topic echo /scan
service type /reset
action info /navigate_to_pose
param get /controller use_sim_time
```

Tab offers domain actions and the relevant first object: node, topic, service, or action name. Available native operations depend on the ROS distribution. Parameter names after a node and typed message templates are not yet completed.

Graph lists and completion share snapshots valid for three seconds. ROS collection has a 1.5-second budget; failed or timed-out collection preserves an older snapshot when available. Empty successful lists are distinct from unavailable data. Live views label stale/unavailable data, while completion diagnostics stay off stdout.

## Bags

### `bag record TOPIC...`

Record selected topics into the workspace root. Tab completes live topic names.

### `bag play BAG [TOPIC...]`

Play a bag, optionally selecting recorded topics. Tab first completes bags in the workspace, then topics from the selected bag's metadata.

### `bag info BAG`

Show bag metadata. Record-all and output destination selection remain future work.

## Packages and interfaces

### `list`

List packages discovered by colcon in the current workspace.

### `pkg list`

List installed packages in the current ROS/overlay environment.

### `pkg create NAME TYPE [DEP...]`

Create a package under the workspace's `src/`. `TYPE` is `python` for `ament_python` or `cpp` for `ament_cmake`. Tab completes types and dependency package names.

```sh
pkg create sensors cpp rclcpp sensor_msgs
```

### `interface TYPE`

Show an interface definition, for example `interface sensor_msgs/msg/LaserScan`.

### `doctor` / `wtf`

Run ROS doctor. Both names are existing public entries.

## Status, completion, and settings

### `status`

Show the fixed workspace, ROS/colcon/RViz availability, baseline/overlay state, terminal adapter, and completion cache state.

### `refresh`

Refresh package, executable, and launch completion. Failure returns nonzero and keeps the previous snapshot. Successful builds invalidate runtime completion; the next relevant Tab refreshes it.

The first Tab inserts a unique candidate or common prefix. A second Tab with the same buffer opens the selector: Up/Down choose, Enter accepts, Esc or Ctrl+C cancels. Editing the command resets this sequence. Bash and zsh quote inserted shell-sensitive filenames.

### `config colors [list|show|set SLOT PRESET|custom SLOT BG FG ACCENT|reset|preview]`

Inspect or change workspace task colors. Custom colors must be `#RRGGBB`; foreground/background contrast must reach 4.5:1 and accent/background 3:1. Changes affect future windows. `NO_COLOR`, dumb/non-TTY output, tmux, and unknown terminals disable full-screen color control.

### `config terminal [show|set NAME]`

Inspect automatic terminal detection or select a built-in adapter. Arbitrary command templates are not accepted.

## Help and lifecycle

| Command | Behavior |
| --- | --- |
| `help [COMMAND]` | General or command-specific help |
| `version` | Print the version; `lazy --version` also remains available |
| `about` | Version, copyright, AGPL, warranty, and source information |
| `uninstall [--purge] [--force]` | Invoke the managed installer; purge confirms removal of settings/history |
| `exit` | Exit the controller, keeping task windows alive |

Uninstall normally refuses while task windows are active. It verifies managed file hashes and preserves unrelated files and rc content.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success; in window mode this means the window was created, not that its task succeeded |
| 2 | Invalid Lazy arguments |
| 127 | Required executable is missing |
| 128 + N | Underlying process terminated by signal N; Ctrl+C is normally 130 |

Other foreground command exit codes in the range 0–255 are preserved.

## Planned improvements

The [issue tracker](https://github.com/Tsubashimo-Nanato/LazyROS2/issues) covers the installation bootstrap (#18), bag destinations (#22), larger candidate selectors (#23), interactive configuration (#24), parameter completion (#26), and typed message templates (#28). These are not included in the commands above.
