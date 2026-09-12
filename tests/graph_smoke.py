# SPDX-License-Identifier: AGPL-3.0-or-later
"""Real ROS discovery and warm completion, run only inside the ROS smoke fixture."""

from __future__ import annotations

import subprocess
import sys
import time


NODE = """
import rclpy
from std_msgs.msg import String

rclpy.init()
node = rclpy.create_node('lazy_smoke_probe')
publisher = node.create_publisher(String, '/lazy_smoke_status', 10)
try:
    rclpy.spin(node)
finally:
    node.destroy_node()
    rclpy.shutdown()
"""


def complete(launcher: str, domain: str, action: str) -> list[str]:
    result = subprocess.run(
        [launcher, "__complete", "--shell", "bash", "--cursor", "3", "--",
         "lazy", domain, action, ""],
        capture_output=True, text=True, timeout=3, check=False,
    )
    if result.returncode or result.stderr:
        raise RuntimeError(f"{domain} completion failed: {result.stderr or result.returncode}")
    return result.stdout.splitlines()


def main() -> None:
    launcher = sys.argv[1]
    # This fixture creates a named ROS node and a publisher, but never publishes
    # data. No hardware interface or user workspace is involved.
    node = subprocess.Popen([sys.executable, "-c", NODE], stdout=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 15
        while "/lazy_smoke_status" not in complete(launcher, "topic", "echo"):
            if node.poll() is not None:
                raise RuntimeError(f"ROS probe exited: {node.returncode}")
            if time.monotonic() >= deadline:
                raise RuntimeError("ROS topic did not appear in Lazy completion")
            time.sleep(0.25)
        if "/lazy_smoke_probe" not in complete(launcher, "node", "info"):
            raise RuntimeError("ROS node did not appear in Lazy completion")
        samples = []
        for _ in range(10):
            started = time.monotonic()
            if "/lazy_smoke_status" not in complete(launcher, "topic", "echo"):
                raise RuntimeError("warm cache lost the ROS topic")
            samples.append((time.monotonic() - started) * 1000)
        print(f"ROS graph smoke passed; repeated completion p95: {sorted(samples)[-1]:.1f} ms")
    finally:
        node.terminate()
        try:
            node.wait(timeout=5)
        except subprocess.TimeoutExpired:
            node.kill()
            node.wait(timeout=5)


if __name__ == "__main__":
    main()
