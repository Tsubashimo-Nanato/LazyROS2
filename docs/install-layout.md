# 安装、manifest 与卸载边界

LazyROS2 v0.1 只提供无 `sudo` 的单用户安装。安装器不调用 apt、dnf、pip，也不安装 ROS 2。

## 固定布局

```text
~/.local/bin/lazy
~/.local/lib/lazyros2/
├── 0.1.0/
│   ├── bin/lazy
│   ├── install.sh
│   ├── src/
│   ├── shell/
│   ├── VERSION
│   ├── LICENSE
│   └── pyproject.toml
└── current -> 0.1.0
~/.local/share/lazyros2/install-manifest.json
```

配置、历史、缓存和运行态与 payload 分开：

```text
${XDG_CONFIG_HOME:-~/.config}/lazyros2/
${XDG_STATE_HOME:-~/.local/state}/lazyros2/
${XDG_CACHE_HOME:-~/.cache}/lazyros2/
$XDG_RUNTIME_DIR/lazyros2/
```

launcher 是一个固定的小型 POSIX shell 文件，只转交给 `current/bin/lazy`。`current` 使用相对 symlink，因此版本目录和切换点位于同一文件系统。

## 安装事务

`sh install.sh` 按以下顺序工作：

1. 拒绝 UID 0，确认 Python ≥3.10，并检查 release 中的固定 payload 文件。
2. 拒绝 payload 内的 symlink，避免复制阶段越过 release 根目录。
3. 读取并验证已有 manifest；没有 manifest 时，任何已有 launcher、`current`、payload 或 rc marker 都视为 unmanaged collision。
4. 在 `~/.local/lib/lazyros2/` 内创建私有 preparing 目录并复制 staging payload。
5. 从 staging 直接执行 `bin/lazy --version`；退出码必须为 0，输出必须包含 `VERSION`。
6. 持久化旧 manifest、rc 快照和恢复 journal，再将 preparing 提升为 transaction。
7. 把新 payload 就位并原子替换 `current`；已有 launcher 全程保持可解析，首次安装时才创建它。
8. 原子更新受管 rc block，最后写入 manifest；任一步失败时按 journal 恢复，成功后清理 transaction。

同版本、同内容是 no-op。同版本内容不同必须显式使用 `--reinstall`。版本号只接受 `MAJOR.MINOR.PATCH`；降级必须显式使用 `--allow-downgrade`。

安装和卸载由 `$HOME/.lazyros2-install.lock/` 串行化。锁记录 PID 与 Linux 进程启动身份；正常退出会删除锁目录，异常退出后仅在确认原进程已经消失时回收。未知内容、权限/属主异常或无法证明已失效的锁都会 fail closed。

事务目录按状态区分：

- `.preparing-*` 尚未触碰 live 安装，启动时可以安全丢弃；
- `.transaction-*` 已持久化严格 journal、新旧 manifest 与 rc 快照，启动时按 journal 回滚未提交事务，或完成已提交事务的清理；
- `.cleanup-*` 表示 live 状态已经恢复或提交，只剩可安全删除的事务私有副本。

没有有效 journal、存在未知文件、快照 hash 不匹配或 live 路径在中断后被修改时，安装器保留现场并停止，不猜测恢复方式。新 payload 的文件与目录会先 `fsync`；payload 就位后使用原子 symlink 替换 `current`，已有 launcher 不会在升级时移走，manifest 最后原子替换；每个关键 rename/replace 后同步父目录。同版本重装使用 Linux `renameat2(RENAME_EXCHANGE)` 原子交换 payload；文件系统不支持时在修改 live 状态前拒绝重装。

## Manifest schema v1

manifest 是 UTF-8 JSON，权限为 `0600`。核心字段如下：

```json
{
  "schema_version": 1,
  "app": "LazyROS2",
  "app_version": "0.1.0",
  "license": "AGPL-3.0-or-later",
  "home": "/home/example",
  "installed_at": "2026-07-10T00:00:00+00:00",
  "source": {
    "ref": "v0.1.0",
    "commit": "<40-hex commit>"
  },
  "files": [],
  "created_directories": [],
  "rc_files": []
}
```

`files` 条目包含：

- `root`：v1 只接受 `local`，代表 `~/.local`；
- `path`：root 内的 POSIX 相对路径；
- `type`：`file` 或 `symlink`；
- `mode`：四位八进制 mode；
- `sha256`：文件内容 SHA-256；symlink 对 link target 文本计算 SHA-256。

`created_directories` 只记录安装器拥有且可在空时删除的目录。升级会继承旧 manifest 的 layout ownership，并以新版本 payload 目录替换旧版本条目。

`rc_files` 条目包含 `.bashrc` 或 `.zshrc` 相对路径、shell、marker block SHA-256，以及该 rc 文件是否由安装器创建。用户可自由修改 marker 外的内容。

未知 schema、未知 root、绝对路径、`..`、重复路径、越过 `~/.local` 的父 symlink、未知文件类型或不属于当前 `$HOME` 的 manifest 都会 fail closed。每个 manifest 路径、固定的 `share/lazyros2/install-manifest.json` 路径和受管目录都会从 `~/.local` 起逐级 `lstat` 父组件；即使 symlink 的最终目标仍在 `~/.local` 内，也不会沿它访问或删除。安装和升级也会拒绝受管父目录 symlink，以及旧 payload 中不在 manifest 内的额外路径，避免把内容写到边界外或在升级时静默删除用户文件。`--force` 只能跳过受管内容的 hash/type/mode 差异，不能跳过 schema 和路径边界校验。

## RC 区块

默认只修改 `$SHELL` 对应的 Bash 或 zsh rc。无法判断 shell 时，安装器只考虑已经存在的 `.bashrc`、`.zshrc`；都不存在时跳过并给出提示。

```sh
# >>> LazyROS2 >>>
case ":${PATH}:" in
    *":${HOME}/.local/bin:"*) ;;
    *) export PATH="${HOME}/.local/bin:${PATH}" ;;
esac
if [ -r "${HOME}/.local/lib/lazyros2/current/shell/lazy-init.bash" ]; then
    . "${HOME}/.local/lib/lazyros2/current/shell/lazy-init.bash"
fi
# <<< LazyROS2 <<<
```

zsh 区块使用 `lazy-init.zsh`。marker 缺失、重复、次序错误或区块内容被修改时，默认停止，不猜测用户意图。

## 卸载

公共入口是：

```sh
lazy uninstall [--purge] [--force]
```

CLI 将参数转交给：

```sh
sh "$HOME/.local/lib/lazyros2/current/install.sh" --uninstall [--purge] [--force]
```

卸载器先完整验证 manifest、所有受管路径与 rc block，再开始删除。它只 unlink manifest 中的文件和 symlink；目录只在为空且列于 `created_directories` 时删除。受管文件缺失或 hash/type/mode 不符时默认停止。没有 manifest 的重复普通卸载是 no-op，不会接管或删除同名 unmanaged 文件。

实际修改 rc 前，卸载器先原子写入并同步 `$HOME/.lazyros2-uninstall-preparing.json` provenance，再创建私有 `.lazyros2-uninstall-preparing/`。原 manifest、完整 hash、每个 rc 文件的修改前/目标结果和 journal 全部同步后，preparing 目录才原子改名为 `$HOME/.lazyros2-uninstall-transaction/`。因此即使进程紧接 `mkdir` 后被终止，下一次普通卸载也能证明该 preparing 目录归本安装器所有且尚未触碰 live 状态，并安全清理后重试。

rc 更新、每个受管文件 unlink、manifest unlink 都是可重放的持久化检查点。进程被 SIGKILL 或系统中断后，再次执行 `sh install.sh --uninstall` 会在同一安装锁内继续事务；它只接受路径仍处于原状态或已经到达记录的目标状态，第三种内容一律 fail closed。所有删除完成后 journal 标记 committed，再经 `$HOME/.lazyros2-uninstall-cleanup/` 原子切换并清理；正常完成不留下这些临时路径。未完成卸载存在时，新的安装会拒绝启动并要求先完成卸载。

`--purge` 还删除 LazyROS2 的 XDG 配置、状态、缓存和运行态，需要交互确认。空值或相对 XDG base 不会按当前目录解释，而是回退到 `$HOME` 下的标准绝对目录。自动化环境中，已经完成同等确认的上层 CLI 可以附加内部 `--yes`。活动任务 registry 位于 `$XDG_RUNTIME_DIR/lazyros2/jobs.json`（未设置或为相对值时使用临时目录中的等价路径）；存活 PID 以及启动宽限期内的 `STARTING/pid=0` 任务都会使卸载 fail closed。

## Release 归档

正式 release 提供：

```text
lazyros2-0.1.0.tar.gz
SHA256SUMS
```

归档顶层目录、`VERSION`、tag 和 release 名必须一致。release workflow 从已验证的 annotated tag 构建可复现顺序的 tar，写入 `SOURCE_REF` 与 `SOURCE_COMMIT`，生成 SHA-256 并提交 artifact attestation。既有同名 release 或资产不会被覆盖。
