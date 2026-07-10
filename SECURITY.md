# Security Policy

## Supported versions

Until the first stable release, security fixes are provided for the latest published `0.1.x` release only. Development snapshots are not supported security releases.

## Reporting a vulnerability

Please use the repository's private **Security → Report a vulnerability** form. Do not open a public issue for command injection, unsafe path handling, installer/uninstaller deletion, environment disclosure, terminal execution, or other vulnerabilities before a fix is available.

Include the affected version and platform, a minimal reproduction, the expected security boundary, and whether the issue requires a modified workspace, release archive, manifest, shell rc file, or task registry. Remove passwords, tokens, SSH keys, ROS credentials, and hardware secrets from logs.

The maintainer will acknowledge the report, reproduce it in an isolated workspace, and coordinate disclosure after a tested release is available. Response timing depends on impact and maintainer availability; this document does not promise a fixed service-level agreement.

## Security boundaries

LazyROS2 executes existing ROS 2 development tools with the current user's permissions. It does not sandbox ROS nodes, launch files, workspace setup scripts, or compiler hooks. Treat workspaces and release archives as executable code.

The project does enforce narrower boundaries around its own behavior:

- external commands are passed as argv without `eval` or `shell=True`;
- the installer rejects root and release-payload symlinks;
- uninstall paths are allowlisted, home-relative, and checked against parent symlink escapes;
- managed files and rc marker blocks are verified before deletion;
- environment snapshots remain in a private runtime directory and must not be attached to reports without review.

`--force` is an explicit recovery mechanism for locally modified managed files. It does not bypass manifest schema or path-boundary checks.
