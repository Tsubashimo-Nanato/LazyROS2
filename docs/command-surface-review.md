# LazyROS2 命令与 Tab 补全评审

> 这是供维护者直接修改的设计草案，不代表最终公共 API。
>
> 实机基线：Fedora 44、ROS 2 Jazzy、ros2cli 0.32.9、colcon-core 0.21.0、Bash/zsh。

## 如何填写

每个命令都独占一个小节，并固定包含六部分：

1. **现在**：v0.1 当前真实行为；
2. **我的建议**：更少参数、更可预测 Tab 的建议；
3. **你的要求**：预留给维护者修改。
4. **确认后的规则**：把讨论结果写成可实现的公共行为；
5. **补全行为**：逐 token 说明 Tab 候选；
6. **歧义状态**：标记为已确认、待确认或严重歧义。

“你的要求”保留维护者原文；讨论后的可实现解释只写进后续三部分，不反向改写原话。

- **已确认**：可以据此设计 parser、补全和任务窗口；
- **待确认**：记录方向，但不据此提前创建 mapping；
- **严重歧义**：必须先向维护者报告准确命令、冲突解释和实现影响。

## 快速索引

### 已经映射到原生工具

- `build`
- `test`
- `test-result`
- `run`
- `launch`
- `rviz`
- `rviz2`

### LazyROS2 自有命令，没有直接原生映射

- `lazy`
- `jobs`
- `status`
- `refresh`
- `config colors`
- `config terminal`
- `help`
- `about`
- `exit`
- `uninstall`
- `version`（当前实现仍是 `lazy --version`）

### ROS 2 已存在，但 LazyROS2 尚未映射

- `node`
- `topic`
- `service`
- `action`
- `param`
- `bag`
- `lifecycle`
- `component`
- `interface`
- `pkg`
- `doctor` / `wtf`
- `daemon`
- `multicast`
- `security`
- `plugin`

### colcon 已存在，但 LazyROS2 尚未公开映射

- `list`
- `info`
- `graph`
- `metadata`
- `extension-points` / `extensions`
- `version-check`

## 当前已确认的公共语法

shell 外在下列命令前加 `lazy`；控制 shell 内直接输入。每行只表达一条正常路径：

```text
lazy
build [PKG...]
build up-to PKG...
test [PKG...]
test-result
run PKG [EXEC] [ARGS...]
launch PKG FILE [ARGS...]
rviz [CONFIG]
rviz2 [CONFIG]
jobs
status
refresh
about
exit
uninstall
version
node
topic
service
action
param
bag record TOPIC...
bag play BAG [TOPIC...]
list
pkg list
pkg create NAME TYPE [DEP...]
interface TYPE
doctor
wtf
```

这里没有列出的深层动作仍是待确认项，不应从示例反推一个尚未批准的公共 API。

---

# 第一部分：全局设计规则

这些规则影响所有命令。应该先确定它们，再讨论具体 parser。

## G1 — 第一次和第二次 Tab

### 现在

- 顶层候选包含 14 个公共命令。
- zsh 明确实现：第一次 Tab 补唯一值或公共前缀，第二次 Tab 列出全部候选。
- Bash 返回同一候选集合，但候选展示仍受 readline 配置影响。

### 我的建议

- Bash 与 zsh 必须有同一套可测试行为。
- 0 个候选：保持输入不变，不打印错误。
- 1 个候选：第一次 Tab 直接补全。
- 多个候选：第一次只补公共前缀；输入未改变时，第二次才列出。
- 输入、删除或移动光标后，第二次 Tab 状态必须重置。

### 你的要求

> ✍️ 没看懂

### 确认后的规则

- 第一次 Tab：唯一候选直接补全；多个候选只补公共前缀。
- 缓冲区没有变化时连续按第二次 Tab，打开候选选择器。
- 候选选择器用上下方向键移动、Enter 确认；输入、删除或移动光标后重置连续 Tab 状态。
- Bash 与 zsh 对用户表现一致，不把 Bash 默认的历史上下键行为冒充候选选择。

### 补全行为

- 当前 token 为空时显示当前层级的候选；已有前缀时只保留匹配项。
- 0 个候选保持输入不变；1 个候选立即完成；多个候选按上述两阶段行为处理。

### 歧义状态

**已确认。** “双击 Tab”统一解释为连续按两次 Tab，不是鼠标双击。

---

## G2 — Tab 延迟与缓存

### 现在

- 缓存命中目标为 100 ms。
- 冷采集内部预算为 1.5 秒，外部硬边界约 2 秒。
- 采集失败时保留 stale cache。
- 冷采集仍可能让当前 Tab 等待。

### 我的建议

- 第一次 Tab 不等待 ROS graph 或长时间子进程。
- 第一击只读缓存，并在 100 ms 内返回。
- 无缓存时在后台刷新；这次没有候选也比卡住终端更好。
- `refresh` 才执行用户明确要求的同步刷新。
- workspace、overlay 和 ROS graph 使用不同失效规则，不能共用一个笼统的 dirty 标志。

### 你的要求

> ✍️ 我说的tab是命令补齐，比如说bui按tab会自动填入build,如果有ambiguity那么用tab和箭头选择。parameters 同样tab补齐 比如bu（tab）（space）（tab）—>出packag list。双击tab也显示candidate

### 确认后的规则

- 命令、子命令、package、executable、topic、配置和路径都属于补全对象。
- 缓存命中必须快速返回；过期数据可以先作为 stale 候选使用，再在后台刷新。
- 首次没有缓存时不长时间卡住终端；`refresh` 提供显式同步刷新。

### 补全行为

- `bui<Tab>` 补成 `build`。
- `build <Tab>` 显示 build 子动作和 workspace package；进入确定的参数位置后只显示该位置的对象。
- 候选采集失败不打印进命令行，也不覆盖最后一次成功快照。

### 歧义状态

**已确认。** 缓存与延迟是实现细节，不改变两次 Tab 和方向键选择的公共行为。

---

## G3 — Flags 与业务候选

### 现在

- `build` 会把 `--up-to` 和 package 混在同一候选列表。
- `run`、`launch`、`rviz` 会把 `--here`、`--window` 和 package/文件混在一起。

### 我的建议

- 默认 Tab 只显示当前领域对象，例如 package、executable、topic 或文件。
- 只有当前 token 已经以 `-` 开头时，才显示 flags。
- 不为了展示“功能完整”而让两个 flags 挤在几十个 package 之间。

### 你的要求

> ✍️ 我认为上面回答了你的问题，避免用--作为parameters，双击tab显示candidate（注意避免让用户重新输入整条命令，而且up down arrow key应该可以navigate。比如build是colcon build，build双击tab显示candidate比如（up to）等，再tab 补齐或者double tab显示pkg list

### 确认后的规则

- 日常 Lazy 语法不公开 `--window`、`--here` 或额外的 `--` 分隔符。
- 候选按当前位置显示动作或业务对象，不把底层 flags 混进 package/topic 列表。
- 候选选择直接修改当前命令行；取消选择也保留原输入，不要求用户重输整条命令。

### 补全行为

- `build <Tab><Tab>` 可以同时展示 `up-to` 和 workspace package，并标明类型。
- 选中 `up-to` 后，下一个 token 只补 workspace package。
- 已进入 package、executable、topic 或路径位置后，只补相应对象。

### 歧义状态

**已确认。** `up-to` 是无前导横线的 Lazy 子动作，不是 colcon flag。

---

## G4 — 底层参数与额外的 `--`

### 现在

- `build`、`test`、`run`、`launch`、`rviz` 要求底层参数放在额外的 `--` 后。
- LazyROS2 删除分隔符，再逐项透传后续 argv。
- `--` 后停止 Lazy 补全。

### 我的建议

- 如果固定位置已经能确定 Lazy 参数结束，就直接透传剩余 argv。
- `run PKG EXEC` 后面的内容天然属于 ROS 进程，不应再要求一个 `--`。
- 只有真实存在语法歧义时才保留 escape separator。
- 进入透传区后，应尽可能交回原生 ROS/shell 补全，而不是让 Tab 彻底失效。

### 你的要求

> ✍️ 没看懂，我为什么不能直接在shell里面 run (pkg) (param list) (param) (paramlist) (param)

### 确认后的规则

- 可以直接写 `run PKG EXEC [ARGS...]`，不需要额外 `--`。
- `run` 和 `launch` 的固定 Lazy token 结束后，其余 argv 原样交给 ROS 进程。
- `build/test` 只接受 Lazy 明确定义的动作和 package；复杂 colcon flags 继续使用原生 `colcon`，避免多 package 语法无法判断边界。

### 补全行为

- Lazy 负责补全固定 token；进入任意应用参数后，不伪造语义候选，可回退到普通文件补全。

### 歧义状态

**已确认。** “param”在这里指底层进程参数，不是 ROS 2 的 `param` 命令。

---

## G5 — 参数数量预算

### 现在

- 不同命令各自增加 flags 和位置参数。
- 没有统一的“Lazy 命令最多应该记多少东西”规则。

### 我的建议

- 高频路径最多是：**一个动作词 + 两个必填领域 token**。
- package、type、路径、窗口模式能推断就不要求用户输入。
- 低频复杂操作直接使用原生 `ros2` 或 `colcon`。
- 不为了覆盖率制造 wrapper 的 wrapper。

### 你的要求

> ✍️ 没看懂，build也应该打开新窗口执行colcon build。等用户c掉或者关闭子终端

### 确认后的规则

- `build`、`test`、`run`、`launch`、`rviz` 和 `rviz2` 都打开独立任务窗口。
- 命令结束后窗口进入轻量 idle prompt，显示退出码；上箭头可选中原命令并重新执行。
- Ctrl+C 只停止该窗口当前任务，不关闭窗口，也不终止其他 Lazy 任务。

### 补全行为

- 是否开窗口由命令类型决定，不占用参数位置，也不出现在候选中。

### 歧义状态

**已确认。** “等用户 C 掉”解释为任务运行期间可 Ctrl+C；任务自然结束后同样进入 idle，而不是永久等待 Ctrl+C。

---

## G6 — Tab 候选应该做什么

### 现在

- Tab 只输出纯文本候选。
- 候选来源可能是 workspace、overlay、ROS CLI、任务注册表或静态配置。
- 重复 launch basename 在执行时才会被拒绝。

### 我的建议

- Tab 只补 token，不执行动作，不静默修改配置。
- 第二次 Tab 可以显示候选类型和歧义，例如 package、executable、launch。
- Tab 不自动插入大段 YAML、service request 或 action goal。
- 模板生成必须是显式动作。

### 你的要求

> ✍️ 我认为我已经回答了你的问题，如果没有的话继续问。

### 确认后的规则

- Tab 只补 token，不执行 ROS 动作、不修改配置，也不自动插入大段 YAML。
- 第二次 Tab 的候选选择器可以显示候选类型、来源和歧义提示。
- 模板、请求体或 goal 的生成必须由显式命令触发。

### 补全行为

- 已知对象使用 Lazy 数据源；没有可靠数据源时保持输入不变，不猜测不存在的候选。

### 歧义状态

**已确认。** 具体 command 的候选数据源在各命令小节单独定义。

---

# 第二部分：已经映射的命令

## `build`

**状态：** 已映射到 colcon。

### 现在

- `build` → `colcon build`
- `build PKG...` → `colcon build --packages-select PKG...`
- `build --up-to PKG...` → `colcon build --packages-up-to PKG...`
- `build PKG` 失败且发现未安装依赖候选时，交互 shell 会询问是否 up-to 重试。
- Tab 候选：`--up-to` 与 workspace package 混列。

### 我的建议

- 保留 `build` 和 `build PKG...` 作为主要路径。
- 已输入的 package 不再重复出现在候选中。
- 空 token 只补 package；输入 `-` 后才补 build flag。
- 继续使用“有依赖证据才询问”的重试逻辑。
- `--up-to` 是否继续公开，由你决定；它不应污染普通 Tab。

### 你的要求

> ✍️ build pkg(1)...
build pkg (tab) -> build pkg up-to(space)(tab for candidate) pkg

### 确认后的规则

- `build` → `colcon build`，构建整个 workspace。
- `build PKG...` → `colcon build --packages-select PKG...`。
- `build up-to PKG...` → `colcon build --packages-up-to PKG...`；`up-to` 必须是 build 后的第一个 token。
- 在独立任务窗口执行；结束后进入 idle。select build 失败且有未安装依赖证据时，仍可在该窗口询问是否按 up-to 重试。

### 补全行为

- `build <Tab>`：`up-to` 与尚未选择的 workspace package。
- `build up-to <Tab>`：workspace package。
- `build PKG <Tab>`：剩余 workspace package，不再显示 `up-to`。

### 歧义状态

**已确认。** 原文中的 `build PKG up-to PKG` 被归一化为 `build up-to PKG...`；这里以后者为准。

---

## `test`

**状态：** 已映射到 colcon。

### 现在

- `test` → `colcon test`
- `test PKG...` → `colcon test --packages-select PKG...`
- 测试后自动运行 `colcon test-result --verbose`。
- Tab 候选：workspace package。

### 我的建议

- 保留 `test [PKG...]`。
- test 和 test-result 属于同一次用户动作，不增加额外 flags。
- 已输入的 package 不再重复补全。
- 测试完成后，失败摘要应该比完整 verbose 输出更先出现。

### 你的要求

> ✍️ 和上面一样

### 确认后的规则

- `test` 测试全部 package；`test PKG...` 只测试所选 package。
- 测试结束后自动运行 `colcon test-result --verbose`，整体失败返回非零。
- 与 build 一样在独立任务窗口执行，结束后进入 idle；不增加 `up-to` 子动作。

### 补全行为

- `test <Tab>` 及后续 package 位置只补尚未选择的 workspace package。

### 歧义状态

**已确认。** “和上面一样”指窗口与 package 补全规则，不表示 test 也拥有 `up-to`。

---

## `test-result`

**状态：** 已映射到 colcon。

### 现在

- `test-result` → `colcon test-result --verbose`
- 无参数。
- 无 Tab 候选。

### 我的建议

- 可以保留：它是零参数、含义清楚的排障入口。
- 不要为它增加 output、format、last 等 Lazy flags。
- 如果未来删掉顶层入口，原生 `colcon test-result` 已经足够清楚。

### 你的要求

> ✍️ testresult (tab)

### 确认后的规则

- 公共命令仍为 `test-result`，映射 `colcon test-result --verbose`。
- `testresult` 不成为第二个别名；原文表示输入命令前缀后按 Tab 补全。

### 补全行为

- `testres<Tab>` 补成 `test-result`；命令完成后没有参数候选。

### 歧义状态

**已确认。** 这是补全示例，不是新增无连字符命令。

---

## `run`

**状态：** 已映射到 ros2cli。

### 现在

- 语法：`run [--window|--here] PKG EXEC [-- ARGS...]`
- 映射：`ros2 run PKG EXEC ARGS...`
- 控制 shell 默认新窗口；普通 CLI 默认当前终端。
- 第一个 Tab：具有 executable 的 package，加上两个窗口 flags。
- 第二个 Tab：所选 package 的 executable。
- `--` 后停止 Lazy 补全。

### 我的建议

- 建议语法：`run PKG EXEC [ARGS...]`
- 控制 shell/普通 CLI 继续自动决定窗口位置。
- 只保留必要的 `--here` 覆盖；移除对称但多余的 `--window`。
- EXEC 后直接透传，不强制额外 `--`。
- 第一个 Tab 只列 runnable package；第二个只列 executable。

### 你的要求

> ✍️ run (pkgname) -> ros2 run (pkgname)
打开子窗口，我不确定你的exec，here是什么意思。避免--

### 确认后的规则

- 语法为 `run PKG [EXEC] [ARGS...]`，在独立任务窗口映射为 `ros2 run PKG EXEC ARGS...`。
- EXEC 是 package 内的可执行程序名。package 只有一个 executable 时，提交 `run PKG` 可自动选择；有多个时必须通过候选选择器选定。
- EXEC 只在命令恰好结束于 PKG 时允许省略；只要还有后续 token，第二个 token 一律解析为 EXEC。需要传 ARGS 时写明 EXEC，避免把第一个应用参数误判为 executable。
- 不公开 `here/window` 参数，也不需要额外 `--`；EXEC 后的 argv 原样透传。

### 补全行为

- `run <Tab>`：只列具有 executable 的 package。
- `run PKG <Tab>`：只列该 package 的 executable；只有一个时直接补全，多个时第二次 Tab 可用方向键选择。
- EXEC 后不伪造应用专属候选，可保留普通文件补全。

### 歧义状态

**已确认。** `run PKG` 不是删除 executable，而是在唯一时由 Lazy 推断、多个时要求选择；带应用参数时必须显式写 EXEC。

---

## `launch`

**状态：** 已映射到 ros2cli。

### 现在

- 语法：`launch [--window|--here] PKG FILE [-- ARGS...]`
- 映射：`ros2 launch PKG FILE ARGS...`
- 第一个 Tab：具有 launch 文件的已安装 package，加上窗口 flags。
- 第二个 Tab：该 package 的 launch basename。
- 相同 package 内重复 basename 会在执行时拒绝。

### 我的建议

- 建议语法：`launch PKG FILE [ARGS...]`
- 与 `run` 使用相同的窗口规则和参数边界。
- 第一个 Tab 只列 launchable package。
- 第二次候选展示应标记重复 basename；不要等执行时才报歧义。
- FILE 后直接透传 launch arguments。

### 你的要求

> 比如 launch launchers all_nodes.py -> launch launchers all_nodes.py。注意tab补齐
args如上

### 确认后的规则

- 语法为 `launch PKG FILE [ARGS...]`，在独立任务窗口映射为 `ros2 launch PKG FILE ARGS...`。
- 不公开窗口参数或额外 `--`；FILE 后的 launch argv 原样透传。
- 同一 package 内重复 basename 必须在选择器中标为歧义并拒绝执行，不能把两个路径静默压成一个候选；用户可重命名文件或直接使用能够表达该路径的原生入口。

### 补全行为

- `launch <Tab>`：只列具有 launch 文件的已安装 package。
- `launch PKG <Tab>`：递归扫描该 package 的 launch 文件并列出 basename。
- FILE 后只提供能够可靠发现的 launch 参数，否则保留普通输入/文件补全。

### 歧义状态

**已确认。** 示例中的 `launchers` 是 package，`all_nodes.py` 是 launch 文件。

---

## `rviz`

**状态：** 已映射到 rviz2。

### 现在

- `rviz` → `rviz2`
- `rviz CONFIG` → `rviz2 -d CONFIG`
- 支持 `--window`、`--here` 和额外 `-- ARGS...`。
- Tab 把 `.rviz` 文件与窗口 flags 混列。

### 我的建议

- 建议语法：`rviz [CONFIG]`。
- 控制 shell 默认窗口。
- 只在用户输入 `-` 后显示 `--here`。
- CONFIG Tab 只显示 `.rviz`，并优先使用 workspace 相对路径。
- 不需要为了和 `run` 对称而保留 `--window`。

### 你的要求

> ✍️ 同时支持rviz和rviz2，rvizb本身应该打开应用窗口。config用补齐

### 确认后的规则

- 同时公开 `rviz [CONFIG]` 和 `rviz2 [CONFIG]`，两者是同义 Lazy 入口。
- 两者都启动检测到的 RViz 2；CONFIG 存在时映射为 `rviz2 -d CONFIG`。
- RViz 自身创建图形界面；Lazy 仍用任务窗口承载启动命令和退出状态，结束后进入 idle。

### 补全行为

- 顶层输入 `rvi<Tab><Tab>` 可看到 `rviz` 与 `rviz2`。
- `rviz <Tab>` 和 `rviz2 <Tab>` 只补 `.rviz` 配置文件，优先显示 workspace 相对路径。

### 歧义状态

**已确认。** 原文的 `rvizb` 按笔误处理；正式入口是 `rviz` 与 `rviz2`。

---

# 第三部分：LazyROS2 自有命令

## `lazy`

**状态：** Lazy 主入口，无直接原生命令。

### 现在

- 在当前 Bash/zsh 中进入控制 shell。
- workspace 来自当前目录并经过 realpath 固定。
- 启动后 `cd` 不改变本实例绑定的 workspace。

### 我的建议

- 保持零参数主入口。
- 不增加 `--workspace PATH`；用户已经用 `cd` 表达上下文。
- 不同终端可以各自启动不同 workspace 的 Lazy 实例。

### 你的要求

> ✍️ 零参数

### 确认后的规则

- workspace 根目录执行零参数 `lazy`，在当前 Bash/zsh 中进入控制 shell。
- workspace 在启动时按 realpath 固定；进入 shell 后 `cd` 不改变该实例绑定的 workspace。
- 不同终端可在不同 workspace 分别运行 Lazy instance，彼此使用各自的配置、缓存与任务视图。

### 补全行为

- shell 外 `lazy <Tab>` 补公共命令；零参数直接启动，不要求 workspace flag。

### 歧义状态

**已确认。** Lazy 支持同时打开多个 workspace，但每个 instance 只绑定启动它的一个 workspace。

---

## `jobs`

**状态：** Lazy 任务注册表命令。

### 现在

- `jobs` 列出当前 workspace 的 Lazy 任务窗口。
- `jobs NUMBER` 只显示指定编号。
- Tab 补当前 workspace 已有任务编号。

### 我的建议

- 保留零参数 `jobs`。
- 如果 NUMBER 只能过滤一行，它不值得占参数位置。
- 只有未来能对任务执行明确动作时，编号补全才真正有价值。

### 你的要求

> ✍️ 没问题，但是也要开子窗口，实时更新jobs类似于node list但是显示pkg和其他lazy进程而不是nodes

### 确认后的规则

- 零参数 `jobs` 打开独立实时任务面板，范围限定为当前 workspace。
- 面板持续显示编号、命令类型、package/目标、running/idle/exit 状态和退出码。
- Ctrl+C 停止刷新并进入该窗口 idle；关闭控制 shell 不关闭面板或其他任务窗口。

### 补全行为

- `jobs` 当前为零参数命令，没有编号候选；未来只有在定义任务动作后才增加编号补全。

### 歧义状态

**已确认。** `jobs` 不再只是一次性文本快照，也不等同 shell builtin `jobs`。

---

## `status`

**状态：** Lazy 环境诊断命令。

### 现在

- 显示 workspace、ROS 工具、overlay、终端、任务和缓存状态。
- 无参数。
- 无 Tab 候选。

### 我的建议

- 保留零参数入口。
- 默认输出短摘要，异常项优先。
- 不把 `ros2 doctor` 的全部职责复制进来。

### 你的要求

> ✍️ 在lazy status， 如果已经在shell则status，下面的命令也是这个规则

### 确认后的规则

- shell 外使用 `lazy status`，控制 shell 内使用 `status`；这一前缀规则适用于其他公共子命令。
- 显示当前 instance 的 workspace、ROS/colcon/RViz、overlay、终端、任务和缓存摘要。
- `status` 是 Lazy 诊断，不替代 `ros2 doctor`。

### 补全行为

- `sta<Tab>` 补成 `status`；命令完成后没有参数候选。

### 歧义状态

**已确认。** shell 外/内只是入口差异，输出语义相同。

---

## `refresh`

**状态：** Lazy 补全缓存维护命令。

### 现在

- 同步刷新 package、runtime、launch 数据。
- 失败保留旧快照并返回非零。
- 无参数。

### 我的建议

- 保留为排障入口，但从日常心智模型中淡化。
- 正常 Tab 应自动维护缓存。
- 不为 refresh 增加 domain、force、timeout 等日常 flags。

### 你的要求

> ✍️ lazy refresh/ refresh

### 确认后的规则

- shell 外使用 `lazy refresh`，控制 shell 内使用 `refresh`。
- 同步原子刷新当前 workspace 的补全缓存；失败保留旧快照并返回非零。

### 补全行为

- `ref<Tab>` 补成 `refresh`；当前不增加 domain/force/timeout 候选。

### 歧义状态

**已确认。** 日常 Tab 自动维护缓存，`refresh` 是显式排障入口。

---

## `config colors`

**状态：** Lazy workspace 配置。

### 现在

- 子动作：`list`、`show`、`set`、`custom`、`reset`、`preview`。
- `set` 需要 SLOT 和 PRESET。
- `custom` 需要 SLOT、背景、前景、强调色。
- Tab 补动作、槽位/预设；不补自定义颜色值。

### 我的建议

- 当前层级和参数明显过多。
- 日常 `config` 应进入一个轻量交互选择流程。
- 非交互高级配置可以保留，但不应污染普通顶层 Tab。
- 自定义颜色是低频功能，不要让它决定整个 config 语法。

### 你的要求

> ✍️ lazy config / config
然后出candidate比如config -> colors，注意config要有preset和自定义

### 确认后的规则

- shell 外使用 `lazy config ...`，控制 shell 内使用 `config ...`。
- `config colors` 必须覆盖预设配色与自定义颜色；具体 set/show/preview 交互尚未定稿。

### 补全行为

- `config <Tab>` 至少显示 `colors` 与 `terminal`。
- `config colors <Tab>` 显示 `preset`、`custom` 及最终确定的查看/重置动作；颜色值只在明确的 custom 流程中输入。

### 歧义状态

**待确认（不阻塞其他 mapping）。** 仍需决定颜色流程是逐 token 命令还是交互选择器，以及 preset/custom 后的准确 token 顺序。

---

## `config terminal`

**状态：** Lazy workspace 配置。

### 现在

- `show` 显示选择结果。
- `set NAME` 设置 `auto` 或内置适配器。
- Tab 补 `show`、`set` 和适配器名称。

### 我的建议

- 自动探测应该覆盖大多数情况。
- `config terminal` 可以直接显示当前值并提供选择，省掉 `show/set` 动作词。
- 脚本接口可以存在，但不必成为主要用户路径。

### 你的要求

> ✍️ 没看懂，但是我觉得config然后show candidate就可以了
比如config
terminal

### 确认后的规则

- `config` 是统一入口，`terminal` 是其候选之一。
- 终端自动探测仍为默认；手动查看和选择适配器的准确语法尚未定稿。

### 补全行为

- `config <Tab>` 显示 `terminal`；`config terminal <Tab>` 以后只显示终端相关动作或适配器，不混入颜色候选。

### 歧义状态

**待确认（不阻塞其他 mapping）。** 需要以后决定 `config terminal` 直接进入选择器，还是保留 `show/set NAME`。

---

## `help`

**状态：** Lazy 帮助命令。

### 现在

- `help` 显示总帮助。
- `help COMMAND` 显示命令帮助。
- Tab 补所有公共命令。

### 我的建议

- 保留。
- 帮助应优先展示最短正常路径和 Tab 示例。
- 不要把全部底层 flags 倾倒到第一页。

### 你的要求

> ✍️ 所有补齐/candidate支持help

### 确认后的规则

- `help` 必须能够解释所有公开命令、子动作和候选类型，并优先展示最短路径与 Tab 示例。
- 是否同时支持 `help COMMAND`、`COMMAND help` 或上下文选择器，尚未确定。

### 补全行为

- `help <Tab>` 至少补所有顶层公共命令；更深层 help 候选等待语法确认。

### 歧义状态

**待确认（不阻塞其他 mapping）。** “所有 candidate 支持 help”可能表示集中式 `help PATH...`，也可能表示每层都有 `help` token；两者会改变 parser 树。

---

## `about`

**状态：** Lazy 项目信息命令。

### 现在

- 显示版本、版权、许可、无担保和源码地址。
- 无参数。

### 我的建议

- 可以合并进更详细的 `lazy --version` 或 `help about`。
- 如果 legal notice 必须容易发现，则保留。
- 不应仅为了“命令看起来完整”占一个顶层词。

### 你的要求

> ✍️ lazy version
lazy
about

### 确认后的规则

- shell 外使用 `lazy about`，控制 shell 内使用 `about`。
- `about` 显示详细版本、唯一作者、版权、AGPL-3.0-or-later、无担保声明和源码地址。
- 简短机器可读版本由 `version` 提供，两者不合并。

### 补全行为

- `abo<Tab>` 补成 `about`；命令完成后没有参数候选。

### 歧义状态

**已确认。** 原文中的 `lazy version` 和 `about` 是两个入口，不是同一个命令的两种写法。

---

## `exit`

**状态：** 控制 shell 生命周期命令。

### 现在

- 调用 shell builtin `exit`。
- 已创建的任务窗口继续存在。

### 我的建议

- 保留。
- 它符合 shell 直觉，不需要参数或特殊补全。

### 你的要求

> ✍️ exit, 不要terminates子命令。避免车还在跑突然断命令

### 确认后的规则

- `exit` 只退出当前 Lazy 控制 shell并返回父终端。
- 已创建的 build/test/run/launch/RViz、graph 和 jobs 窗口继续存在；不提供隐式 terminate-all。

### 补全行为

- `exi<Tab>` 补成 `exit`；无参数候选。

### 歧义状态

**已确认。** 退出控制界面与停止机器人任务严格分离。

---

## `uninstall`

**状态：** Lazy 安装生命周期命令。

### 现在

- `uninstall` 删除受管安装，保留状态。
- `--purge` 清除状态。
- `--force` 处理受管文件 hash 改动。
- 当前没有 Tab 候选。

### 我的建议

- 默认 `uninstall` 应交互说明“保留状态”还是“全部清除”。
- 危险 flags 不进入普通 Tab 候选。
- 自动化 flags 可以保留在安装器底层接口。
- 整体安装生命周期重设计由 Issue #18 跟踪。

### 你的要求

> ✍️ lazy
unins

### 确认后的规则

- shell 外公共入口为 `lazy uninstall`；控制 shell 内可输入 `uninstall`。
- `unins` 不是别名，只是按 Tab 前的命令前缀。
- 保留状态还是 purge、受管文件改动和活动任务处理继续由安装生命周期设计决定。

### 补全行为

- `unins<Tab>` 补成 `uninstall`；危险选项不进入普通候选。

### 歧义状态

**待确认（不阻塞命令命名）。** 命令名已确认，卸载交互细节继续由安装简化工作单独设计。

---

## `version`（当前为 `lazy --version`）

**状态：** Lazy 顶层 option。

### 现在

- 输出 `lazy 0.1.0`。
- 不属于子命令列表。

### 我的建议

- 保留。
- 不需要进入公共命令 Tab。
- 输出保持单行，便于脚本读取。

### 你的要求

> ✍️ lazy
version

### 确认后的规则

- shell 外使用 `lazy version`，控制 shell 内使用 `version`。
- 输出保持单行 `lazy 0.1.0`；公共文档不再要求用户记 `--version`。
- 当前实现的 `lazy --version` 在迁移完成前可保留兼容，但不进入主要候选。

### 补全行为

- `ver<Tab>` 补成 `version`；无参数候选。

### 歧义状态

**已确认。** 目标公共入口是子命令 `version`，不是 flag。

---

# 第四部分：ROS 2 尚未映射的命令

## `node`

**原生命令：** `ros2 node list`、`ros2 node info`

### 现在

- Lazy 无 node 公共命令。
- Lazy 不采集 node 名称补全。

### 我的建议

- 高优先级。
- 候选语法：`nodes` 列表，`node NODE` 查看信息。
- `node <Tab>` 从短 TTL graph cache 返回 node 名。
- 第一击不允许同步等待 ROS graph。

### 你的要求

> ✍️ 新窗口，显示node
如果用户在lazy里面输入node,空格则显示更多candidate

### 确认后的规则

- 零参数 `node` 打开独立窗口，实时刷新当前 ROS graph 的 node 列表。
- `node ACTION ...` 进入 node 子命令树；至少需要支持查看单个 node 信息，准确动作名在实现 issue 前确认。
- Ctrl+C 停止刷新并进入 idle，不关闭窗口。

### 补全行为

- `node <Tab>` 显示 node 动作候选；进入需要 node 的位置后，从短 TTL graph cache 补 node 名。
- 第一次 Tab 不同步阻塞等待 ROS graph，失败时使用 stale 快照。

### 歧义状态

**待确认（不阻塞零参数 mapping）。** `node` 的实时列表已确认；深层动作采用 `node info NODE` 还是更短的 `node NODE` 尚未确认。

---

## `topic`

**原生命令：** `list`、`echo`、`info`、`type`、`find`、`hz`、`bw`、`delay`、`pub`

### 现在

- Lazy 无 topic 公共命令。
- Lazy 不采集 topic 或 message type 补全。

### 我的建议

- 最高优先级。
- 高频读操作使用短词：`topics`、`echo TOPIC`、`hz TOPIC`、`bw TOPIC`。
- Tab 只补 topic 名；type 自动推断。
- `pub` 的消息内容复杂。除非能生成安全、可编辑的模板，否则继续使用原生 `ros2 topic pub`。

### 你的要求

> ✍️ 同上

### 确认后的规则

- 零参数 `topic` 打开独立窗口，实时刷新 topic 列表。
- `topic ACTION ...` 进入 topic 子命令树；echo/info/hz/bw 等只读动作优先，复杂 pub 语法不在本轮定稿。

### 补全行为

- `topic <Tab>` 显示动作候选；需要 topic 的位置补实时 topic 名，能够可靠推断时不要求重复输入消息类型。

### 歧义状态

**待确认（不阻塞零参数 mapping）。** 实时列表和上下文补全已确认；pub 的 request 模板与准确动作集合以后确认。

---

## `service`

**原生命令：** `list`、`type`、`find`、`call`、`echo`、`info`

### 现在

- Lazy 无 service 公共命令。
- Lazy 不采集 service 或 service type 补全。

### 我的建议

- 高优先级。
- 候选语法：`services`、`call SERVICE [REQUEST]`。
- Tab 补 service 名；type 自动查询，不要求用户输入。
- Tab 不自动插入大段 request YAML。

### 你的要求

> ✍️ 同上

### 确认后的规则

- 零参数 `service` 打开独立窗口，实时刷新 service 列表。
- `service ACTION ...` 进入 service 子命令树；info/call 等动作复用实时对象和 type 查询。

### 补全行为

- `service <Tab>` 显示动作候选；需要 service 的位置补 service 名，type 可推断时不要求用户再次输入。

### 歧义状态

**待确认（不阻塞零参数 mapping）。** 实时列表已确认；call request 的模板交互与完整动作集合以后确认。

---

## `action`

**原生命令：** `list`、`info`、`type`、`send_goal`

### 现在

- Lazy 无 action 公共命令。
- Lazy 不采集 action 或 action type 补全。

### 我的建议

- 高优先级。
- 候选语法：`actions`、`send ACTION [GOAL]`。
- Tab 补 action 名；type 自动推断。
- 复杂 goal 使用显式模板动作或原生命令。

### 你的要求

> ✍️ 同上

### 确认后的规则

- 零参数 `action` 打开独立窗口，实时刷新 action 列表。
- `action ACTION ...` 进入 action 子命令树；info/send 等动作复用实时对象和 type 查询。

### 补全行为

- `action <Tab>` 显示动作候选；需要 action 的位置补 action 名，复杂 goal 不由普通 Tab 自动写入。

### 歧义状态

**待确认（不阻塞零参数 mapping）。** 实时列表已确认；send goal 模板和准确动作命名以后确认。

---

## `param`

**原生命令：** `list`、`get`、`set`、`delete`、`describe`、`dump`、`load`

### 现在

- Lazy 无 parameter 公共命令。
- Lazy 不采集 node/parameter 关系。

### 我的建议

- 高优先级，但不要直接增加七个顶层命令。
- 先补 node，再按 node 补 parameter。
- set 时保留或推断参数类型，不要求用户重复写 type。
- 可以考虑 `param NODE` 进入上下文，再执行 `get/set/dump`。

### 你的要求

> ✍️ 同上

### 确认后的规则

- 零参数 `param` 打开独立窗口，实时刷新 node 与 parameter 列表。
- `param ACTION ...` 进入 parameter 子命令树；参数操作必须先确定 node，再确定 parameter。

### 补全行为

- `param <Tab>` 显示动作候选；node 位置补 node，随后只补该 node 的 parameter。
- set 的值不由普通 Tab 猜测；已知类型可用于验证或显式模板。

### 歧义状态

**待确认（不阻塞零参数 mapping）。** 实时列表和 node→parameter 补全顺序已确认；get/set/dump/load 的准确短语法以后确认。

---

## `bag`

**原生命令：** `record`、`play`、`info`、`list`、`burst`、`convert`、`reindex`

### 现在

- Lazy 无 rosbag 公共命令。
- Lazy 不补 topic 或 bag 路径。

### 我的建议

- 优先覆盖 `record`、`play`、`info`。
- `record <Tab>` 补 topic。
- `play <Tab>` 补 bag 目录或文件。
- `convert/reindex/list/burst` 低频，继续使用原生入口。

### 你的要求

> ✍️ bag record默认根目录。 bag 空格 让用户选择目录
play默认一样，play要支持某个topic比如play node xxx
等等

### 确认后的规则

- `bag record TOPIC...` 在独立任务窗口录制，输出目录默认位于本 instance 的 workspace 根。
- `bag play BAG [TOPIC...]` 在独立任务窗口播放；先选择 bag，再可选限制其中的 topic。
- 原文的 `play node xxx` 归一化为按 topic 过滤；bag play 的首要对象始终是 bag 路径，不是 node。

### 补全行为

- `bag <Tab>` 至少显示 `record`、`play`、`info`。
- `bag record <Tab>` 补实时 topic；已选 topic 不重复出现。
- `bag play <Tab>` 补 workspace 根下可识别的 bag；选定 BAG 后补该 bag 内的 topic。

### 歧义状态

**已确认。** 默认目录、play 的 bag→topic 顺序均已确认；具体 ROS 发行版的 topic-filter argv 兼容性需在实现时验证，但不改变公共语法。

---

## `lifecycle`

**原生命令：** `get`、`list`、`nodes`、`set`

### 现在

- Lazy 无 lifecycle 映射。

### 我的建议

- 中优先级。
- Tab 先补 lifecycle node，再补当前状态允许的 transition。
- 不要把所有 transition 字符串不加过滤地列给用户。

### 你的要求

> ✍️ 不优先

### 确认后的规则

- v0.1 后续 mapping 不优先；当前继续使用原生 `ros2 lifecycle`。

### 补全行为

- 在公共入口确定前，不向顶层候选加入 lifecycle 子动作。

### 歧义状态

**待确认（不阻塞）。** 是否公开 `lifecycle` 以及其短语法以后决定。

---

## `component`

**原生命令：** `list`、`load`、`unload`、`types`、`standalone`

### 现在

- Lazy 无 component 映射。

### 我的建议

- 只有真实项目高频使用 composition 时再加入。
- load 需要 container、package、plugin 多段上下文，很容易重新变长。
- 如果 Tab 不能可靠完成三段对象，保留原生入口更诚实。

### 你的要求

> ✍️ 不优先

### 确认后的规则

- v0.1 后续 mapping 不优先；当前继续使用原生 `ros2 component`。

### 补全行为

- 在公共入口确定前，不向顶层候选加入 component 子动作。

### 歧义状态

**待确认（不阻塞）。** 是否公开 `component` 以及 load/unload 的上下文语法以后决定。

---

## `interface`

**原生命令：** `list`、`package`、`packages`、`proto`、`show`

### 现在

- Lazy 无 interface 映射。

### 我的建议

- 中优先级。
- 候选语法：`interface TYPE` 直接 show。
- Tab 补 msg/srv/action 类型。
- `proto` 可保留为明确动作；完整 list 仍可用原生入口。

### 你的要求

> ✍️ interface tyoe

### 确认后的规则

- 语法归一化为 `interface TYPE`，直接显示 msg/srv/action interface 定义。
- `tyoe` 按 `type` 的笔误处理，不建立名为 `tyoe` 的动作或别名。

### 补全行为

- `interface <Tab>` 补当前环境可发现的完整 interface type；候选标明 msg/srv/action 类型。

### 歧义状态

**已确认。** `TYPE` 是 interface 名称（例如 `std_msgs/msg/String`），不是额外的 `type` 子命令。

---

## `pkg`

**原生命令：** `list`、`executables`、`prefix`、`xml`、`create`

### 现在

- Lazy 没有 pkg 公共命令。
- Lazy 内部已经使用 package/executable 查询做补全。

### 我的建议

- 可考虑 `packages` 和 `pkg PKG`。
- 候选应复用现有 cache，不能再次扫描。
- `create` 参数多且模板复杂，继续使用原生 `ros2 pkg create`。

### 你的要求

> ✍️ pkg，记得create补齐区分python和C++, 比如shell中pkg create ament_cmake/cmake dep/dependencies 等等，你可以澄清这个问题

### 确认后的规则

- `pkg list` 显示当前 ROS/overlay 环境中已安装的 package，与 workspace `list` 分开。
- `pkg create NAME TYPE [DEP...]` 创建 package；TYPE 公共值为 `python` 或 `cpp`，内部分别映射 `ament_python` 与 `ament_cmake`。
- DEP 是零个或多个 ROS package dependency；不要求用户记原生 `--build-type`、`--dependencies` flags。

### 补全行为

- `pkg <Tab>` 至少显示 `list`、`create`。
- `pkg create NAME <Tab>` 只显示 `python`、`cpp`。
- TYPE 之后补可用 dependency package，并排除已经选择的依赖。

### 歧义状态

**已确认。** token 顺序为 NAME→TYPE→DEP；公共类型名不是 `ament_cmake/cmake` 两套同义输入。

---

## `doctor` / `wtf`

**原生命令：** `ros2 doctor`、`ros2 wtf`、`ros2 doctor hello`

### 现在

- Lazy `status` 只诊断 Lazy 环境，不等同 ROS doctor。

### 我的建议

- 不复制 doctor 的检查实现。
- `status` 发现 ROS 异常时可以建议运行 `ros2 doctor`。
- 如果一定要映射，只提供零参数直通，不增加 Lazy flags。

### 你的要求

> ✍️ 开doctor就是新窗口ros2 docktor等，status如上

### 确认后的规则

- `doctor` 在独立任务窗口直通 `ros2 doctor`；`wtf` 可作为同义兼容入口直通 `ros2 wtf`。
- Lazy `status` 保持自己的环境摘要语义，不映射为 doctor。
- 命令结束后窗口进入 idle。

### 补全行为

- `doc<Tab>` 补成 `doctor`，`wt<Tab>` 补成 `wtf`；零参数版本不提供更多候选。

### 歧义状态

**已确认。** 原文的 `docktor` 按 `doctor` 的笔误处理；`status` 与 doctor 不冲突。

---

## `daemon`

**原生命令：** `start`、`status`、`stop`

### 现在

- Lazy 无 daemon 映射。

### 我的建议

- 默认不映射。
- daemon 管理不够高频，并且具有隐藏的全局副作用。

### 你的要求

> ✍️ 暂时留空

### 确认后的规则

- 暂不建立 Lazy `daemon` mapping；继续使用原生 `ros2 daemon`。

### 补全行为

- 未公开前不进入 Lazy 顶层候选。

### 歧义状态

**待确认（不阻塞）。** start/status/stop 的公共入口以后决定。

---

## `multicast`

**原生命令：** `send`、`receive`

### 现在

- Lazy 无 multicast 映射。

### 我的建议

- 默认不映射。
- 它是窄用途网络诊断，不应占顶层 Lazy 补全。

### 你的要求

> ✍️ 暂时留空

### 确认后的规则

- 暂不建立 Lazy `multicast` mapping；继续使用原生 `ros2 multicast`。

### 补全行为

- 未公开前不进入 Lazy 顶层候选。

### 歧义状态

**待确认（不阻塞）。** receive/send 的公共入口以后决定。

---

## `security`

**原生命令：** `create_enclave`、`create_keystore`、`create_permission`、`generate_artifacts`、`generate_policy`、`list_enclaves`

实机还包含 deprecated 的 `create_key` 和 `list_keys`。

### 现在

- Lazy 无 security 映射。

### 我的建议

- 默认不映射。
- 不隐藏 keystore、identity 或 policy 参数。
- Lazy 可以做环境诊断，但不应替代 ROS 2 security 工作流。

### 你的要求

> ✍️ 暂时留空

### 确认后的规则

- 暂不建立 Lazy `security` mapping；继续使用原生 `ros2 security`。

### 补全行为

- 未公开前不进入 Lazy 顶层候选。

### 歧义状态

**待确认（不阻塞）。** 安全 keystore/key/enclave 操作不在当前短命令设计内。

---

## `plugin`

**原生命令：** `list`

### 现在

- Lazy 无 plugin 映射。

### 我的建议

- 默认不映射。
- 仅在 Lazy 自身排查扩展冲突时读取。

### 你的要求

> ✍️ 暂时留空

### 确认后的规则

- 暂不建立 Lazy `plugin` mapping；继续使用原生 `ros2 plugin`。

### 补全行为

- 未公开前不进入 Lazy 顶层候选。

### 歧义状态

**待确认（不阻塞）。** list/prefix 的公共入口以后决定。

---

# 第五部分：colcon 尚未公开映射的命令

## `colcon list`

### 现在

- Lazy 内部用它发现 workspace package。
- 没有公共入口。

### 我的建议

- 可考虑零参数 `packages`。
- 直接复用补全 cache，不再次启动 colcon。
- 输出应为人类阅读优化，而不是复制原生表格。

### 你的要求

> ✍️ 同时支持list和package: list package, list pkg

### 确认后的规则

- 顶层 `list` 显示当前 workspace 中 `colcon list` 可发现的 package。
- `pkg list` 显示当前 ROS/overlay 已安装的 package；两者不做同义别名，也不静默合并来源。
- shell 外分别写作 `lazy list`、`lazy pkg list`。

### 补全行为

- `lis<Tab>` 补成 `list`，命令完成后无参数候选。
- `pkg <Tab>` 显示 `list` 与其他已确认 pkg 动作。

### 歧义状态

**已确认。** workspace package 与 installed package 使用两个入口，避免相同名称掩盖来源。

---

## `colcon info`

### 现在

- Lazy 无公共映射。

### 我的建议

- 可以与 `pkg PKG` 合并。
- Tab 补 workspace package。
- 不同时提供 `info` 和 `pkg info` 两套入口。

### 你的要求

> ✍️ 开新窗口

### 确认后的规则

- 如果公开 colcon info mapping，必须在独立任务窗口执行并在结束后进入 idle。
- Lazy 的最终公共命令名称尚未确定，当前继续使用原生 `colcon info`。

### 补全行为

- 最终入口确定后，package 位置补 workspace package；在此之前不加入顶层候选。

### 歧义状态

**待确认（不阻塞）。** 候选包括顶层 `info PKG` 与 `pkg info PKG`；两者会占用不同 parser 分支。

---

## `colcon graph`

### 现在

- Lazy 无公共映射。

### 我的建议

- 如果依赖诊断高频，可考虑 `deps PKG`。
- 默认显示目标附近的依赖，不直接生成整张图。
- 图形输出和高级参数继续使用原生 colcon。

### 你的要求

> ✍️ 开新窗口

### 确认后的规则

- 如果公开 colcon graph mapping，必须在独立任务窗口执行并在结束后进入 idle。
- Lazy 的最终公共命令名称尚未确定，当前继续使用原生 `colcon graph`。

### 补全行为

- 最终入口确定后再定义 graph 范围与 package 候选；当前不加入顶层候选。

### 歧义状态

**待确认（不阻塞）。** 尚未决定公开顶层 `graph`，还是把它放进 workspace/package 上下文。

---

## `colcon metadata`

### 现在

- Lazy 无映射。

### 我的建议

- 默认不映射。
- 它是低频 workspace 维护能力。

### 你的要求

> ✍️ 开新窗口

### 确认后的规则

- 如果公开 colcon metadata mapping，必须在独立任务窗口执行并在结束后进入 idle。
- Lazy 的最终公共命令名称尚未确定，当前继续使用原生 `colcon metadata`。

### 补全行为

- 最终入口及安全写操作范围确定前，不加入 Lazy 候选。

### 歧义状态

**待确认（不阻塞）。** metadata 含 list/add/remove 等不同风险动作，不能仅凭“开新窗口”决定公共语法。

---

## `colcon extension-points` / `colcon extensions`

### 现在

- Lazy 无映射。

### 我的建议

- 默认不映射。
- 可以作为 `status` 深度诊断的数据源，但不进入日常命令。

### 你的要求

> ✍️ 暂时留空

### 确认后的规则

- 暂不公开 extension-points/extensions mapping，继续使用原生 colcon。

### 补全行为

- 未公开前不进入 Lazy 顶层候选。

### 歧义状态

**待确认（不阻塞）。** 两个原生入口是否需要一个 Lazy 名称以后决定。

---

## `colcon version-check`

### 现在

- Lazy 无映射。

### 我的建议

- 默认不映射。
- 它比较本地 package 与 PyPI 版本，不属于主要 ROS workspace 开发闭环。

### 你的要求

> ✍️ 暂时留空

### 确认后的规则

- 暂不公开 version-check mapping，继续使用原生 colcon。

### 补全行为

- 未公开前不进入 Lazy 顶层候选。

### 歧义状态

**待确认（不阻塞）。** 是否值得占用公共命令名称以后决定。

---

# 第六部分：建议实施顺序

## 阶段 1 — 先修现有命令

### 现在

- 现有 build/run/launch/rviz 的参数和 Tab 仍有明显摩擦。

### 我的建议

- 先分离 flags 与业务候选。
- 决定是否删除强制 `--`。
- 统一 Bash/zsh 双 Tab。
- 在这些问题解决前，不增加新的 ROS command group。

### 你的要求

> ✍️ 避免--

### 确认后的规则

- 第一阶段先移除日常 `--window`、`--here` 和额外 `--`，完成窗口模型与现有命令的无 flag 语法。

### 补全行为

- 优先实现统一的第一次/第二次 Tab 选择器，再接 build/test/run/launch/RViz 的对象数据源。

### 歧义状态

**已确认。** 复杂底层选项不强塞进 Lazy；高级用法继续使用原生命令。

---

## 阶段 2 — 高频 ROS graph

### 现在

- node/topic/service/action/param 名称仍需要从其他终端复制。

### 我的建议

- 按真实使用频率加入 node、topic、service、action、param。
- 名称必须可补，type 尽量推断。
- 如果一个命令不能减少输入和记忆，就暂时不加入。

### 你的要求

> ✍️ 默认和ros2一样，加入空格 补齐live

### 确认后的规则

- 第二阶段实现 node/topic/service/action/param 的零参数实时窗口，再逐步确认深层动作。

### 补全行为

- 空格后的动作和对象来自 ROS graph 短 TTL cache；不让第一次 Tab 阻塞等待实时查询。

### 歧义状态

**已确认阶段目标，深层语法待确认。**

---

## 阶段 3 — Bag 与结构管理

### 现在

- bag、lifecycle、component、interface、pkg 全部依赖原生命令。

### 我的建议

- 先做 bag 的 record/play/info。
- lifecycle/component 只在真实项目证明高频后加入。
- 不追求覆盖全部 ros2cli。

### 你的要求

> ✍️ 如上

### 确认后的规则

- 第三阶段优先实现 bag record/play/info 与 pkg list/create；低频结构管理命令继续使用原生入口。

### 补全行为

- bag 按 topic、bag、bag 内 topic 的上下文补全；pkg create 按 NAME、TYPE、DEP 补全。

### 歧义状态

**已确认阶段目标。** lifecycle/component 等低优先级命令仍为待确认。

---

# 第七部分：下一轮审核

下一轮写 issue 前，按以下顺序审核：

1. 先确定顶层命令名称。
2. 为每个命令写出最短正常路径和最大参数数量。
3. 为每一个 token 位置定义 Tab 候选。
4. 明确候选数据源、缓存 TTL、延迟预算和失败行为。
5. 再讨论 parser、shell adapter 和兼容迁移。

不能先改 parser，再把 Tab 当作收尾工作补上。

## 严重歧义记录规则

发现严重歧义时，不允许静默选择一种解释。报告必须同时写明：

1. 涉及的准确命令和 token 位置；
2. 两种或以上可能解释；
3. 每种解释如何改变 parser、Tab 候选或运行位置；
4. 解除阻塞所需的最小维护者决定。

当前没有尚未上报的严重歧义。`help`、config 交互、低优先级 ROS 命令及 colcon info/graph/metadata 的名称均已显式标为“待确认（不阻塞）”，不会据此提前创建 mapping。
