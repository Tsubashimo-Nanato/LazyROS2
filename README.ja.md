<p align="right"><a href="./README.md">English</a> / 日本語 / <a href="./README.zh-CN.md">简体中文</a></p>

# LazyROS2

> 日常の ROS 2 開発を支援する、ワークスペース対応シェル。

パッケージをビルドし、Tab で実行ファイルを探し、タスクを番号と色付きの独立ウィンドウで動かす。LazyROS2 は、よく使う `colcon` と `ros2` の操作を、短く見つけやすいコマンドにまとめます。

ROS 2 はオプションを覚えています。人間まで全部覚える必要はありません。

## インストール

Linux、Python 3.10 以上、Bash または zsh と、既存の ROS 2 環境が必要です。ビルドには `colcon`、タスクウィンドウにはデスクトップセッションと対応ターミナルを使用します。

```sh
git clone --branch v0.3.0 --depth 1 https://github.com/Tsubashimo-Nanato/LazyROS2.git
cd LazyROS2
sh install.sh
```

新しいターミナルを開き、`lazy version` で確認してください。インストールはユーザー単位で、sudo も実行時の PyPI 依存関係も不要です。`lazy uninstall` で削除でき、`--purge` を付けると設定と履歴も削除できます。

使用済みの v0.2 から更新する場合は、先に[旧バイトコードの移行に関する注意](docs/validation-2026-09-12.md#upgrading-an-existing-v020-installation)を確認してください。

ダウンロードからインストールまでの簡略化は [#18](https://github.com/Tsubashimo-Nanato/LazyROS2/issues/18) で進めています。オフライン利用、更新、復旧については[インストールガイド](docs/install-layout.md)を参照してください。

## 使い始める

普段どおりに ROS underlay を読み込みます。例えば Bash なら `source /opt/ros/jazzy/setup.bash` を実行し、その後：

```console
$ cd ~/robot_ws
$ lazy
[lazy:robot_ws | ros:jazzy] $ build my_robot
[lazy:robot_ws | ros:jazzy] $ run my_robot controller
[lazy:robot_ws | ros:jazzy] $ jobs
```

既存のワークスペースなら、そのまま開きます。`src/` 以下から起動すると上位のワークスペースを見つけます。それ以外の場所では、確認してから `src/` を作成するか、パス補完で別のディレクトリを選べます。

LazyROS2 は export 済みの ROS・ハードウェア環境を引き継ぎ、それらの setup スクリプトを自動実行しません。**このワークスペースの `install/` をまだ source していない**ターミナルから起動してください。ビルド用の基準環境を保持し、実行コマンドにはワークスペースの overlay を読み込みます。

## 短い入力、いつもの操作

コントロールシェル内では `lazy` を省略します。

| Lazy コマンド | 標準の操作 |
| --- | --- |
| `build my_robot` | `colcon build --packages-select my_robot` |
| `build up-to my_robot` | `colcon build --packages-up-to my_robot` |
| `test my_robot` | 選択したパッケージをテストし、`colcon test-result --verbose` |
| `run my_robot controller` | `ros2 run my_robot controller` |
| `launch my_robot bringup.launch.py` | `ros2 launch my_robot bringup.launch.py` |
| `rviz config/navigation.rviz` | `rviz2 -d config/navigation.rviz` |
| `topic echo /scan` | `ros2 topic echo /scan` |
| `pkg create sensors cpp rclcpp` | `src/` に `ament_cmake` パッケージを作成 |

Tab を 1 回押すと一意の候補または共通部分を補完し、もう一度押すと矢印キーで候補を選べます。パッケージ、実行ファイル、launch ファイル、パス、ROS グラフの対象を文脈に応じて補完します。グラフの結果は 3 秒間再利用し、未キャッシュ時の ROS クエリは 1.5 秒を上限に収集します。失敗時は以前の結果を保持します。

Build、test、run、launch、RViz は、終了後も残るタスクウィンドウを使用します。Ctrl+C でタスクのプロンプトに戻り、上矢印キーと Enter で再実行できます。再実行時には最新の overlay を読み込みます。ビルド成功後、コントロールシェルは次のプロンプト表示時に overlay を再読み込みします。入力待ちなら Enter を押してください。コントロールシェルを終了しても、タスクウィンドウは引き続き使えます。

SSH、スクリプト、CI では現在のターミナルで直接実行できます。

```sh
lazy build my_robot
lazy run my_robot controller
lazy topic echo /scan
```

標準の `ros2` と `colcon` も引き続き利用できます。構文と現在の制限は `help COMMAND` と[コマンドリファレンス](docs/commands.md)で確認できます。

## 複数ワークスペース

別々のターミナルを開き、`~/robot_a_ws` と `~/robot_b_ws` でそれぞれ `lazy` を実行します。各インスタンスは起動時のワークスペースに固定され、その後の `cd` では変わりません。履歴、overlay、キャッシュ、設定、タスク表示は分離されます。1 インスタンスが扱うのは 1 ワークスペースで、複数 overlay の積み重ねには対応していません。

## 対応環境と検証

CI の対象は Ubuntu 22.04/Humble、24.04/Jazzy、26.04/Lyrical と、Python 3.10、3.12、3.14 です。x86_64 の Bash と zsh をサポートし、GNOME Terminal を主なターミナルアダプターとしています。

Fedora/Jazzy の micromamba 環境と他のターミナルアダプターは実験的対応です。arm64 は best-effort で、[過去の Jetson テスト](docs/jetson-humble-smoke-2026-07-11.md)を参照できます。Windows、macOS、PowerShell は実行環境としてサポートしていません。

[今回の検証](docs/validation-2026-09-12.md) · [開発への参加](CONTRIBUTING.md) · [セキュリティ](SECURITY.md)

Copyright © 2026 Tsubashimo-Nanato. [AGPL-3.0-or-later](LICENSE) に基づき、保証なしで提供します。
