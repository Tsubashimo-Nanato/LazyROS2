# Contributing to LazyROS2

LazyROS2 keeps the runtime dependency-free and the public command surface small. Changes should make a common ROS 2 workflow shorter or safer without hiding native `ros2` and `colcon` behavior.

## Before changing code

1. Search existing issues and open one for non-trivial work.
2. Branch from current `main` using a focused name such as `feat/task-windows` or `fix/manifest-path-check`.
3. State the user workflow, failure boundary, supported shells/ROS distributions, and test approach in the issue.
4. Avoid automatic package installation in application code and tests.

Use Python 3.10 syntax for runtime code. Runtime dependencies must remain in the Python standard library unless a separate design discussion establishes a compelling need. Shell integration supports Bash and zsh; keep user-provided values out of evaluated shell strings.

## Local checks

Run the checks available on your system without rewriting files unexpectedly:

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
bash -n install.sh bin/lazy shell/*.bash tests/ros_smoke.sh
zsh -n shell/*.zsh
shellcheck install.sh bin/lazy shell/*.bash tests/ros_smoke.sh
shfmt -d -i 4 -ci -fn install.sh bin/lazy shell/*.bash tests/ros_smoke.sh
bats tests/shell_contract.bats
```

ROS integration changes should also run `bash tests/ros_smoke.sh` inside the digest-pinned Humble, Jazzy, and Lyrical images used by CI. Do not point installer tests at the real `$HOME`; tests must use a temporary home and leave a sentinel file outside managed paths.

If a listed tool is unavailable, do not silently install it. Record the skipped check in the pull request.

## Pull requests

Keep each pull request tied to one issue and use `Closes #<issue>` when appropriate. The description must include:

- the concrete workflow changed;
- security or compatibility risks;
- tests and environments used;
- any staged cleanup that remains, with an owner and removal condition.

The required check is `CI / required`. Conversations must be resolved before merge. The repository uses squash merge and linear history; branches are deleted after merge.

Do not add generated-by or AI co-author trailers. Commits, pull requests, tags, and releases must use the contributor's authenticated identity.

## License

Contributions are accepted under `AGPL-3.0-or-later`, the repository's existing license. Preserve third-party license notices and do not add vendored dependencies without documenting their source and license.
