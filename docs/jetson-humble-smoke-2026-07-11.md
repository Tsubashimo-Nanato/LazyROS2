# Jetson / ROS 2 Humble smoke report — 2026-07-11

## Result

**PASS**

- Target: Jetson Orin Nano / aarch64 test host
- OS: Ubuntu 22.04
- Architecture: aarch64 / NVIDIA Tegra kernel `5.15.148-tegra`
- ROS: ROS 2 Humble from `/opt/ros/humble`
- Python: 3.10.12
- Test window: 2026-07-11 20:17:42–20:18:36 UTC+08:00
- Dependency installation: none

## Safety boundary

Testing used a new directory under `/tmp` and two synthetic packages named
`lazy_dep` and `lazy_app`. No existing robot workspace, user rc file, hardware
startup script, motor driver, controller, actuator topic, service, or action was
opened or invoked. No motor command was issued.

## Checks completed

| Check | Result |
| --- | --- |
| Python unit, shell contract, PTY, installer and core tests | 159 passed, 18 skipped, 0 failed |
| `lazy build up-to lazy_app` | Built `lazy_dep` and `lazy_app` |
| Build all | Built both packages |
| `lazy build lazy_app` | Built only the selected package |
| `lazy test` and `test-result` | 0 errors and 0 failures |
| `lazy run lazy_app hello "value with space"` | Preserved argv and printed the expected value |
| `lazy launch lazy_app smoke.launch.py` | Launched and exited cleanly |
| Overlay refresh | Rebuild changed runtime output from v1 to v2 |
| Completion | Found package, executable and launch candidates |
| Explicit refresh | Found 2 packages, 282 executables and 89 launch files |
| Duplicate launch basename | Rejected as ambiguous with both paths shown |
| Bash syntax and completion PTY behavior | Passed |

The skipped tests require optional host facilities not present in this SSH
environment; they were not failures. Graphical task-window creation was not
attempted over the headless SSH session.

## Cleanup and power state

- Temporary LazyROS2 checkout: removed before shutdown.
- Temporary ROS workspace: removed by the smoke harness.
- Machine poweroff: completed; subsequent ICMP and SSH checks received no response.
