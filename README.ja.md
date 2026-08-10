<p align="right"><a href="./README.md">English</a> / 日本語 / <a href="./README.zh-CN.md">简体中文</a></p>

# LazyROS2

> 日常の ROS 2 開発を支援する、ワークスペース対応シェル。

## 概要

LazyROS2 は、よく使う `colcon`、`ros2 run`、`ros2 launch`、RViz のワークフローを、短く見つけやすいコマンドと状況に応じた補完にまとめます。

```console
$ cd ~/robot_ws
$ lazy
[lazy:robot_ws | ros:jazzy] $ build navigation_bringup
[lazy:robot_ws | ros:jazzy] $ launch navigation_bringup navigation.launch.py
```

LazyROS2 は `ros2` や `colcon` を置き換えるものではありません。ラッパーの対象外となる操作では、引き続き標準コマンドを使用できます。

## 主な機能

- ワークスペース内のパッケージ、実行ファイル、launch ファイル、RViz 設定、ROS グラフのオブジェクト、bag、ジョブを補完。
- 長い選択オプションを繰り返し入力せず、単一パッケージをビルド。
- ROS プロセスを、番号と色で識別できるタスクウィンドウで実行。
- Ctrl+C の後に上矢印キーと Enter を押すだけで、タスクを再実行。
- ビルド環境と実行時 overlay を分離。
- sudo を使わずにインストール・アンインストールでき、実行時の PyPI 依存関係も不要。

## 複数ワークスペース

各 LazyROS2 インスタンスは、起動したワークスペースに結び付けられます。別のワークスペースで作業する場合は、別のターミナルを開きます。

```sh
cd ~/robot_a_ws && lazy
cd ~/robot_b_ws && lazy
```

両方のインスタンスを同時に実行できます。補完キャッシュ、履歴、タスクリスト、配色、overlay はインスタンスごとに分離されます。

現時点では、1 つのインスタンス内で複数の overlay を重ねることはできません。各インスタンスが管理するワークスペースは 1 つです。

## インストール

```sh
git clone --branch v0.2.0 --depth 1 https://github.com/Tsubashimo-Nanato/LazyROS2.git
cd LazyROS2
sh install.sh
```

インストール後は新しいターミナルを開いてください。LazyROS2 が ROS 2 をインストールまたは変更することはありません。動作確認、カスタム rc の扱い、アップグレード、アンインストール、復旧については[インストールガイド（英語）](docs/install-layout.md)を参照してください。初回利用時の UX 改善予定は [Issue #18](https://github.com/Tsubashimo-Nanato/LazyROS2/issues/18) で管理しています。

## 基本的な使い方

```console
$ cd ~/robot_ws
$ lazy
[lazy:robot_ws | ros:jazzy] $ build my_robot
[lazy:robot_ws | ros:jazzy] $ build up-to navigation_bringup
[lazy:robot_ws | ros:jazzy] $ test my_robot
[lazy:robot_ws | ros:jazzy] $ run my_robot controller
[lazy:robot_ws | ros:jazzy] $ launch my_robot bringup.launch.py
[lazy:robot_ws | ros:jazzy] $ rviz config/navigation.rviz
[lazy:robot_ws | ros:jazzy] $ jobs
[lazy:robot_ws | ros:jazzy] $ topic
```

正確な構文は `help COMMAND` で確認できます。コントロールシェルから実行する build、test、run、launch、RViz、リアルタイムのグラフ表示、jobs には、状態を保持するタスクウィンドウが使われます。Tab を 1 回押すと前方一致で補完し、もう一度押すと矢印キーで候補を選択できます。

スクリプトや CI では、コントロールシェルを介さずに実行できます。

```sh
lazy build my_robot
lazy run my_robot controller
```

## 対応環境と検証範囲

| プラットフォーム | ROS 2 | Python | 状態 |
| --- | --- | --- | --- |
| Ubuntu 22.04 | Humble | 3.10 | サポート |
| Ubuntu 24.04 | Jazzy | 3.12 | サポート |
| Ubuntu 26.04 | Lyrical | 3.14 | サポート |
| Fedora 44 | Jazzy（micromamba） | ディストリビューション環境 | 実験的 |

x86_64 では Bash と zsh をサポートします。arm64 は best-effort で、Ubuntu 22.04、ROS 2 Humble の Jetson におけるスモークテストのみ実施しています。Windows、macOS、PowerShell、複数 overlay のスタックは v0.2 の対象外です。

## ドキュメント

- [コマンドリファレンス（英語）](docs/commands.md)
- [インストールとアンインストール（英語）](docs/install-layout.md)
- [v0.1 の過去の検証記録（英語）](docs/validation.md)
- [Jetson Humble スモークテスト報告（英語）](docs/jetson-humble-smoke-2026-07-11.md)

## コントリビューションとセキュリティ

変更の提案や開発への参加方法は [CONTRIBUTING.md（英語）](CONTRIBUTING.md)を参照してください。脆弱性の報告方法は [SECURITY.md（英語）](SECURITY.md)に記載しています。

## ライセンス

Copyright © 2026 Tsubashimo-Nanato.

LazyROS2 は [AGPL-3.0-or-later](LICENSE) の下で提供され、保証はありません。
