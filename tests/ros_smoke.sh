#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later

set -euo pipefail

repository=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
workspace=$(mktemp -d)
state_root=$(mktemp -d)
trap 'rm -rf -- "$workspace" "$state_root"' EXIT

export HOME="$state_root/home"
export XDG_CACHE_HOME="$state_root/cache"
export XDG_CONFIG_HOME="$state_root/config"
export XDG_RUNTIME_DIR="$state_root/runtime"
export XDG_STATE_HOME="$state_root/state"
mkdir -p "$HOME" "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

set +u
# shellcheck disable=SC1090
source "/opt/ros/${ROS_DISTRO}/setup.bash"
set -u
export PYTHONPATH="$repository/src${PYTHONPATH:+:$PYTHONPATH}"
lazy="$repository/bin/lazy"

mkdir -p \
    "$workspace/src/lazy_dep" \
    "$workspace/src/lazy_app/scripts" \
    "$workspace/src/lazy_app/launch"

cat >"$workspace/src/lazy_dep/package.xml" <<'EOF'
<?xml version="1.0"?>
<package format="3">
  <name>lazy_dep</name><version>0.0.1</version>
  <description>LazyROS2 integration dependency</description>
  <maintainer email="test@example.com">LazyROS2</maintainer>
  <license>Apache-2.0</license>
  <buildtool_depend>ament_cmake</buildtool_depend>
  <export><build_type>ament_cmake</build_type></export>
</package>
EOF

cat >"$workspace/src/lazy_dep/CMakeLists.txt" <<'EOF'
cmake_minimum_required(VERSION 3.8)
project(lazy_dep)
find_package(ament_cmake REQUIRED)
ament_package()
EOF

cat >"$workspace/src/lazy_app/package.xml" <<'EOF'
<?xml version="1.0"?>
<package format="3">
  <name>lazy_app</name><version>0.0.1</version>
  <description>LazyROS2 integration application</description>
  <maintainer email="test@example.com">LazyROS2</maintainer>
  <license>Apache-2.0</license>
  <buildtool_depend>ament_cmake</buildtool_depend>
  <depend>lazy_dep</depend>
  <exec_depend>ros2launch</exec_depend>
  <export><build_type>ament_cmake</build_type></export>
</package>
EOF

cat >"$workspace/src/lazy_app/CMakeLists.txt" <<'EOF'
cmake_minimum_required(VERSION 3.8)
project(lazy_app)
find_package(ament_cmake REQUIRED)
find_package(lazy_dep REQUIRED)
install(PROGRAMS scripts/hello DESTINATION lib/${PROJECT_NAME})
install(DIRECTORY launch DESTINATION share/${PROJECT_NAME})
ament_package()
EOF

cat >"$workspace/src/lazy_app/scripts/hello" <<'EOF'
#!/usr/bin/env python3
import sys
print("lazy-app-v1", *sys.argv[1:])
EOF
chmod 755 "$workspace/src/lazy_app/scripts/hello"

cat >"$workspace/src/lazy_app/launch/smoke.launch.py" <<'EOF'
from launch import LaunchDescription
from launch.actions import ExecuteProcess


def generate_launch_description():
    return LaunchDescription([
        ExecuteProcess(
            cmd=["ros2", "run", "lazy_app", "hello", "launched"],
            output="screen",
        ),
    ])
EOF

cd "$workspace"

set +e
"$lazy" build lazy_app >"$state_root/select.log" 2>&1
select_status=$?
set -e
if ((select_status == 0)); then
    printf '%s\n' 'selected build unexpectedly succeeded without lazy_dep' >&2
    exit 1
fi

"$lazy" build --up-to lazy_app
"$lazy" build
"$lazy" test lazy_app
"$lazy" test-result

"$lazy" run --here lazy_app hello -- "value with space" | grep -F 'lazy-app-v1 value with space'
set +e
launch_output=$("$lazy" launch --here lazy_app smoke.launch.py 2>&1)
launch_status=$?
set -e
printf '%s\n' "$launch_output"
((launch_status == 0))
grep -F 'lazy-app-v1 launched' <<<"$launch_output"

"$lazy" refresh
"$lazy" __complete --shell bash --cursor 2 -- lazy build "" | grep -Fx lazy_app
"$lazy" __complete --shell bash --cursor 2 -- lazy run "" | grep -Fx lazy_app
"$lazy" __complete --shell bash --cursor 3 -- lazy run lazy_app "" | grep -Fx hello
"$lazy" __complete --shell bash --cursor 3 -- lazy launch lazy_app "" | grep -Fx smoke.launch.py

sed -i 's/lazy-app-v1/lazy-app-v2/' "$workspace/src/lazy_app/scripts/hello"
"$lazy" build lazy_app
"$lazy" run --here lazy_app hello | grep -F 'lazy-app-v2'

mkdir -p "$workspace/src/lazy_app/launch/nested"
cp "$workspace/src/lazy_app/launch/smoke.launch.py" \
    "$workspace/src/lazy_app/launch/nested/smoke.launch.py"
"$lazy" build lazy_app
set +e
ambiguous_output=$("$lazy" launch --here lazy_app smoke.launch.py 2>&1)
ambiguous_status=$?
set -e
((ambiguous_status == 2))
grep -F 'launch basename is ambiguous' <<<"$ambiguous_output"

printf 'ROS smoke passed: %s\n' "$ROS_DISTRO"
