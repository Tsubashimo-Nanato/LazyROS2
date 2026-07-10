# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lazyros2.terminal import TerminalKind, adapter_for  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        runtime = root / "runtime"
        runtime.mkdir(mode=0o700)
        marker = root / "literal value $(not executed)"
        adapter = adapter_for(TerminalKind.GNOME)
        program = (
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('ok')",
            str(marker),
        )
        environment = dict(os.environ)
        environment["XDG_RUNTIME_DIR"] = str(runtime)
        completed = subprocess.run(
            adapter.command("robot_ws · #01 · run · demo", program),
            env=environment,
            check=False,
            timeout=15,
        )
        if completed.returncode != 0:
            raise SystemExit(f"gnome-terminal returned {completed.returncode}")
        deadline = time.monotonic() + 10
        while not marker.is_file() and time.monotonic() < deadline:
            time.sleep(0.05)
        if marker.read_text(encoding="utf-8") != "ok":
            raise SystemExit("GNOME Terminal did not execute the literal argv program")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
