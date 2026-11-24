# 编码总控（Delivery Conductor）

编码总控是面向 Codex 的四层交付编排插件。用户在聊天中以“编码总控”开头提出需求，插件会自动判断任务等级，按需调用 OpenSpec、UI UX Pro Max、Superpowers 和 ECC，并把阶段、证据和人工审批门禁保存到项目状态文件中。

插件本身负责路由、状态、证据、审批暂停和失败关闭，不替代四层技能：

```text
OpenSpec -> UI UX Pro Max -> Superpowers -> ECC
```

主技能提供路由和阶段规则，Python 脚本负责持久化状态和失败关闭的门禁。

## 功能

- **自然语言入口**：输入 `编码总控 <需求>` 即可启动，不需要记住技能名、脚本名或参数。
- **自动分级**：区分 T0、T1、T2、T3，并按任务范围决定是否启用 OpenSpec 和 UI UX Pro Max。
- **四层编排**：把需求、视觉、计划与实现、语言专项审查分配给对应技能。
- **持久化门禁**：规格、UI 设计、实现计划和最终交付四道人工审批必须由用户亲自确认。
- **写入隔离**：在分级中或等待审批时，阻止常规实现写入和未分类命令。
- **证据绑定**：审批绑定一次性令牌和产物摘要，产物变化后旧审批自动失效。
- **一致性审计**：验证任务勾选、计划执行状态、审查报告、交付口径和 OpenSpec 归档是否一致。
- **跨平台运行**：同一套 Python 实现支持 macOS、Linux 和 Windows，不依赖 Bash、WSL 或 Git Bash。

## 子技能与子插件依赖

本插件内置 `delivery-conductor` 主技能和门禁脚本，但**不捆绑**以下外部能力。安装本插件不等于安装这些依赖；缺少必需依赖时，编码总控必须停止并报告，不能静默降级成普通模型回答。

| 依赖 | 是否随本插件安装 | 何时必需 | 作用与检查方式 |
|---|---:|---|---|
| Codex CLI 或 Codex 桌面端 | 否 | 始终 | 必须支持插件、Hooks 和 `commandWindows`。最低已验收版本为 `codex-cli 0.171.0`；可用 `codex --version` 检查。 |
| Python 3.10+ | 否 | 始终 | 运行路由、门禁、审批和诊断脚本。macOS/Linux 使用 `python3`，Windows 使用 `py -3`。 |
| `delivery-conductor` 主技能 | 是 | 始终 | 本插件自带，负责入口识别、路由规则和门禁协议。 |
| OpenSpec 命令行与 `openspec-*` 技能 | 否 | T2/T3，或 T1 改变行为契约时 | 管理需求、规格、验收标准、变更和归档；可用 `openspec --version` 检查。 |
| Superpowers 技能或插件 | 否 | T1/T2/T3 | 负责计划、测试驱动开发、调试、执行编排和分支收尾。 |
| ECC 技能或插件 | 否 | T1/T2/T3 | 负责语言专项最佳实践、代码审查、安全审查、可访问性和构建错误处理。 |
| UI UX Pro Max 技能 | 否 | `design_change=true` 的 UI 任务 | 负责视觉方向、配色、字体、布局、图表和动效。守卫允许运行其 `scripts/search.py`。 |
| Chrome CDP 工具 `chrome-cdp.mjs` | 否 | 需要桌面或移动端 UI 截图时 | 生成设计审核和可访问性验证所需的真实页面预览；默认路径为 `~/.codex/tools/chrome-cdp.mjs`。 |
| `tomli` | 否 | Python 3.10 且没有 `tomllib` 时 | 读取 Codex TOML 配置；运行 Python 3.11+ 时通常不需要。 |

这些外部组件需要按各自官方方式安装，并在 Codex 中确认可见或可用；本仓库不提供它们的安装包。Superpowers、ECC 和 UI UX Pro Max 的可用性通过 Codex 的技能或插件列表确认；OpenSpec 可用 `openspec --version` 检查。

依赖判定规则：

- T0 直接执行，不启用四层子技能。
- T1 默认启用 Superpowers 和 ECC；只有行为契约变化时才启用 OpenSpec。
- T2/T3 启用 OpenSpec、Superpowers 和 ECC。
- 只有结构性 UI 或交互设计变化才启用 UI UX Pro Max；复用现有设计时仍需预览图和 ECC 可访问性检查。
- Codex、Python、主技能或当前任务必需的子技能缺失时，停止并说明缺失项。

## 目录结构

```text
delivery-conductor/
├── .codex-plugin/plugin.json
├── .agents/plugins/marketplace.json
├── hooks/codex-hooks.json
├── skills/delivery-conductor/SKILL.md
├── scripts/
│   ├── route.py
│   ├── approve.py
│   ├── record.py
│   ├── gate.py
│   ├── guard_tool.py
│   ├── host_trust.py
│   ├── verify_evidence.py
│   ├── simulate.py
│   ├── doctor.py
│   ├── install.py
│   └── conductor/
│       ├── evidence.py
│       ├── gates.py
│       ├── model.py
│       └── runtime.py
└── tests/
```

## 安装

### 运行环境

- macOS / Linux：Python 3.10+，推荐 3.11+，使用 `python3`。
- Windows：Python 3.10+，使用 Python Launcher `py -3`；不要求 Bash、WSL 或 Git Bash。
- Codex：最低已验收基线为 `codex-cli 0.171.0`，且必须支持插件 Hooks 的 `commandWindows` 字段。更旧版本未验收；升级 Codex 或插件后都要重新确认 Hooks 信任。
- 审批只使用交互终端：运行 `approve.py` 后必须精确输入 `批准 <token>`；无 TTY 时失败关闭，远程会话使用 `ssh -t`。
- Python 3.10 环境如果没有 `tomllib`，请运行 `python -m pip install tomli`，或改用 Python 3.11+。
- 安装或升级后必须重启 Codex 并在 Hooks 中信任三个钩子，否则门禁不会生效。

安装前先运行诊断：

```bash
python3 scripts/doctor.py
```

Windows PowerShell：

```powershell
py -3 scripts/doctor.py
```

诊断会检查 Python、TOML 解析、Codex 命令、终端审批、TTY、插件清单和钩子信任。任意平台可加 `--strict` 把警告视为失败：

```bash
python3 scripts/doctor.py --strict
```

### macOS / Linux

```bash
python3 scripts/install.py
```

### Windows PowerShell

```powershell
py -3 scripts/install.py
```

插件内只有一套 Python 实现。安装器只负责复制、依赖检查和安装命令提示，不会写入 `~/.codex/config.toml`，也不会伪造钩子信任。

Hook 清单位于 `hooks/codex-hooks.json`。macOS/Linux 使用 `command` 的 `python3` 入口，Windows 使用 `commandWindows` 的 `py -3` 入口；两者运行同一个 `.py` 文件。`${PLUGIN_ROOT}` 由 Codex 在执行 Hook 时替换，禁止写成 `${PLUGIN_ROOT:-.}` 或 `%PLUGIN_ROOT%`。信任校验使用替换前的声明模板，因此 `host_trust.py` 不会把插件绝对路径带入声明哈希。

从 GitHub 安装：

```bash
git clone https://github.com/ceco3000/delivery-conductor.git
cd delivery-conductor
codex plugin marketplace add .
codex plugin add delivery-conductor@delivery-conductor
```

从插件父目录安装：

```bash
codex plugin marketplace add ./delivery-conductor
codex plugin add delivery-conductor@delivery-conductor
```

如果 Codex 没有自动启用插件，请在插件界面中手动启用。

### 首次启用或升级后：信任钩子

插件依赖 Codex 的 `UserPromptSubmit`、`PreToolUse` 和 `SessionStart` 钩子。Codex 不会自动信任新安装或内容已变化的钩子；未信任时，宿主会静默跳过钩子，审批门禁也就无法生效。

安装或升级后，请重启 Codex，打开 Hooks，选择 Review hooks，确认三个钩子后选择 Trust all。信任记录绑定插件声明和整包内容哈希；插件内容变化后必须重新确认。macOS、Linux 与 Windows 各自维护自己的信任记录，不能复用另一台机器的记录。

`route.py`、`gate.py` 和 `approve.py` 会在开始工作前运行 `host_trust.py` 自检。只要检测到已安装插件存在未信任、被禁用或内容已变化的钩子，命令立即退出非零并要求停止，不会继续创建状态、推进门禁或发起审批。

## 使用方式

### 任务分级原则

等级由任务规模和风险共同决定，不按单个流程关键词机械升级：

| 等级 | 适用情况 |
|---|---|
| T0 | 单文件文案、格式、注释，无行为变化 |
| T1 | 单模块局部行为修改，复用现有 UI 结构和视觉 |
| T2 | 新能力、跨模块、数据模型变化或多页面流程 |
| T3 | 架构调整、跨系统重构或高风险迁移 |

- 局部 `behavior_change` 保持 T1，不再自动升级 T2。
- `ui` 表示触碰 UI；只有 `design_change` 才触发 UI UX Pro Max 设计门禁。
- T1 不要求独立计划文件；规格中的任务清单足以指导局部实现。
- 复用既有设计时仍要求桌面/390px 截图和 ECC 可访问性审查。
- `tasks.md` 的勾选状态、Superpowers 计划的步骤勾选和执行状态行不参与审批摘要；正文变化仍会使审批失效。这样完成工作后可以如实回写状态，而不必为了保留审批摘要留下虚假的未勾选项。

### 中文聊天入口

在 Codex 聊天框直接输入：

```text
编码总控 实现一个带登录和注册的页面，桌面和移动端都要好看
```

不需要输入脚本、参数或技能名。`编码总控` 后面的内容会被当作需求，`route.py` 会移除入口词并自动判断 T0–T3、是否需要 OpenSpec 和 UI UX Pro Max。自动判断默认开启，`--auto` 只是兼容写法。

插件包含一个 `UserPromptSubmit` 钩子。消息以 `编码总控` 开头时，钩子会先创建 `ROUTING` 临时状态、保存关键词启发式建议，并把语义评估协议和完整的 `delivery-conductor` 工作流注入本轮上下文。这样入口不依赖模型“想起来”或用户记住技能名。

关键词预路由只提供建议，不是最终等级。模型必须先只读检查仓库，再通过结构化评估完成最终路由：

```bash
python3 "$PLUGIN_ROOT/scripts/route.py" --assess-json '{
  "summary": "只修改按钮显示文案",
  "scope": "trivial",
  "behavior_change": false,
  "ui": true,
  "design_change": false,
  "new_capability": false,
  "data_model_change": false,
  "multi_page": false,
  "high_risk": false,
  "affected_areas": ["src/ui/main_window.py"],
  "reasons": ["不改变交互和业务行为"],
  "unknowns": [],
  "confidence": "high"
}' --state .codex/delivery-state.json --base-dir .
```

`--assess-json` 只在 `route_status=provisional` 时可用。`scope` 分为 `trivial`、`localized`、`multi_module`、`architecture`；新能力、数据模型变化和多页面流程会推导为 T2，高风险迁移会推导为 T3。低置信度、字段缺失、设计变化与 UI 状态矛盾、以及无充分置信度却降低预路由等级时，命令会拒绝完成路由。

在 `ROUTING` 阶段，守卫只允许只读检查和 `route.py --assess-json`，因此模型不能一边声称“分级中”一边修改实现。

如果只输入 `编码总控`，插件会先询问需求，不会直接开始实现。

兼容说明：直接运行 `route.py --task ...` 仍会生成传统的一次性路由结果；聊天入口和新版使用说明采用 `--provisional` 加 `--assess-json` 的两阶段流程。

### 进度汇报格式

编码总控的每一次进度更新和最终回复都必须先使用三段式结构：

```text
**已完成**
- 已经产生的结果、文件或门禁状态

**需要你做**
- 需要你审核、确认或提供的内容；没有时写“无需操作”

**下一步**
- 接下来要执行的具体动作，以及是否需要进入下一道门禁
```

这样用户不需要从过程描述里猜测当前进度、是否需要审核，以及下一步是什么。该规则由 `tests/test_conductor.py` 做回归检查。

也可以直接运行任务路由命令；入口词会被自动移除：

```bash
python3 scripts/route.py --task "编码总控 实现一个登录页面"
```

### 人工审核门禁

插件现在有四个必须由用户明确确认的门禁：

| 门禁 | 审核内容 | 适用任务 |
|---|---|---|
| `spec_approved` | 需求、范围、验收标准 | T2/T3；T1 行为契约变化时 |
| `design_approved` | UI 视觉方向、布局、配色、字体、响应式、交互状态、无障碍和预览图 | 结构性视觉或交互设计变化 |
| `plan_approved` | 实现计划与测试策略 | T2/T3 |
| `delivery_approved` | 最终交付结果和验证证据 | T1/T2/T3 |

除人工审批外，`verified` 之后还有自动门禁 `consistency_verified`。它会独立检查：

- 所有相关 `tasks.md` 没有未完成项；
- Superpowers 计划有最终执行状态，且标记完成的计划没有未勾选步骤；
- ECC 审查报告存在、发现项为零；
- 交付文档中声明的旧数字或旧口径已按 `checks` 规则消失；
- 归档后再次检查归档路径，防止归档阶段重新引入不一致。

审批只能通过交互命令完成：

```bash
python3 scripts/approve.py --gate spec_approved
```

审批没有图形后端，所有平台统一使用交互终端：

| 平台 | 后端 | 说明 |
|---|---|---|
| 任意平台 | 交互终端 | 必须精确输入 `批准 <token>` |

审批必须在交互终端中运行。没有 TTY 时失败关闭；远程 Linux 会话使用 `ssh -t` 申请交互终端。旧版 `DC_APPROVAL_DIALOG` 和 `DC_APPROVAL_DISABLE_GUI` 已不再生效。

只有终端输入完全匹配时才记录审批。超时、非 TTY、错误令牌和普通“继续”都不会被视为批准。`record.py` 会拒绝写入 `approval` 和 `user_approval`，因此执行代理不能自行伪造审批。

每次审批都绑定门禁名、一次性 token 和当前产物的 SHA-256 摘要。规格、计划、UI 预览或交付证据变化后，旧审批自动失效，必须重新审核。OpenSpec 归档只移动同内容文件时，摘要会从归档副本复算，不会把有效审批误判为过期。

### 文档语言

除非用户在当前任务中明确要求其他语言，编码总控及其管理的 OpenSpec、Superpowers、ECC 和 UI UX Pro Max 生成的需求、规格、计划、审查、设计审核、验证和交付文档统一使用简体中文。代码、命令、文件路径、API 名称、协议名、配置键和日志原文保持原样。

### 钩子信任自检

```bash
python3 scripts/host_trust.py
```

返回 0 表示当前安装的三个钩子均已启用、声明哈希和整包内容哈希都与信任记录一致。返回非零时必须停止，按提示重启 Codex 并在 Hooks > Review hooks 中重新选择 Trust all。源码开发目录在完全没有已安装插件时可以跳过该项；一旦发现已安装版本，任何不匹配都会失败关闭。

### UI 设计审核包

在请求 `design_approved` 前，必须准备并在聊天中展示：

- `artifacts/design/design-review.md`：视觉方向、页面/组件布局、配色 token、字体层级、间距、响应式规则、交互状态、无障碍说明和参考链接。
- `artifacts/design/desktop-*.png`：桌面预览图。
- `artifacts/design/mobile-*.png`：至少一张 390px 移动端预览图。
- `artifacts/design/references/`：可选的本地参考图或用户提供的参考文件。
- `ui_direction` 证据：记录风格、配色、字体、布局、响应式规则、预览路径和 `source: "ui-ux-pro-max"`。

聊天中必须显示可点击的图片预览和简明规格表，然后才运行 `approve.py --gate design_approved`。缺少 `design-review.md`、桌面预览或移动预览时，门禁不会通过。

查看当前状态和下一道门禁：

```bash
python3 scripts/gate.py --state .codex/delivery-state.json
```

记录证据：

```bash
python3 scripts/record.py --kind command --data '{"command":"npm test","result":"passed","exit_code":0}'
```

验证全部证据：

```bash
python3 scripts/verify_evidence.py --state .codex/delivery-state.json
```

交付审批前记录一致性审计：

```bash
python3 scripts/record.py --kind consistency_audit --data '{
  "report": "docs/superpowers/reviews/change-consistency.md",
  "findings_resolved": true,
  "open_findings": 0,
  "checks": [{
    "path": "deliverables/交付清单.md",
    "pattern": "48 / 24",
    "expect": "absent",
    "description": "旧测试数量不得残留"
  }]
}'
```

运行完整流程模拟：

```bash
python3 scripts/simulate.py
```

运行单元测试：

```bash
python3 -m unittest discover -s tests -v
```

## 状态文件

默认状态文件是 `.codex/delivery-state.json`，记录以下内容：

- 路由结果和任务等级；
- 参与的四层组件；
- 当前阶段；
- 必需门禁；
- 待审批请求和交互终端审批证据；
- 带时间戳的证据记录。

门禁只能按顺序推进。`gate.py --advance` 会在第一道必需的失败门禁停下；缺少审批时不能从 `ROUTED` 直接跳到 `ARCHIVED`。

### 写入拦截

插件注册了 `PreToolUse` Hook。在 `ROUTING` 阶段，它只允许只读检查和 `route.py --assess-json`，拒绝实现类工具。等待人工审批时，它会拒绝文件修改工具，以及窄名单之外的 Shell 命令；审批握手脚本（`approve.py`、`gate.py`、`record.py`、`verify_evidence.py`）和只读检查命令仍可使用。归档、提交、发布和部署命令在 `delivery_approved` 之前都会被阻止。

这项保护只有在 Codex 已信任并启用 Hook 时才有效。因此，当已安装的 Hook 声明或整包内容未受信任时，启动自检会拒绝为非 T0 任务执行路由、推进或审批。

### 计划批准前的写入隔离

为 T2/T3 任务增加了范围很窄的实现写入隔离，只在 `plan_approved` 之前生效。它不会改变 T0/T1，也不会限制计划批准后的正常开发。

在 `plan_approved` 之前，以下路径允许写入：

- `openspec/`：需求、规格和变更文档。
- `docs/plans/`、`docs/superpowers/plans/`：实现计划和测试策略。
- `artifacts/design/`：设计说明、桌面/移动预览和参考图。

同时允许只读检查、审批握手脚本、`mkdir`/`touch`/`cp`/`mv` 等产物文件操作、`magick`/`convert`（Linux）或 `sips`（macOS）图片转换、UI UX Pro Max 搜索脚本，以及写入 `artifacts/design/` 的 Chrome CDP 截图。项目外的临时路径不受项目实现写入限制。

其他会修改项目的操作会被拒绝，包括写入 `src/`、`tests/`、`Cargo.toml`、`package.json`、`.codex/`，未知 shell 命令，以及没有可验证路径的写工具。审批门禁正在等待时，更严格的原有规则仍然优先，连 `openspec/` 和 `artifacts/design/` 也不能修改，以免审核摘要变化。

该策略是协作门禁，不是密码学沙箱。刻意混淆的脚本仍可能绕过路径检查；它的目标是阻止正常工具路径下的提前实现，而不是防御恶意用户。

状态文件是协作辅助，不是密码学安全边界。交互终端令牌可以防止代理把普通的“继续”消息变成审批，Hook 会在审批等待期间阻止常规工具路径；但用户主动修改本地状态或运行绕过脚本时，仍可以绕过这些限制。

## 三平台排障与回滚

### 常见问题

| 现象 | 处理 |
|---|---|
| `approve.py` 提示需要交互终端 | 在本地终端运行；远程服务器使用 SSH `-t` 申请 TTY |
| `python3 scripts/host_trust.py` 报缺少 `tomllib` | 运行 `python -m pip install tomli`，或安装 Python 3.11+ |
| `host_trust.py` 报钩子未信任 | 重启 Codex，打开 Hooks > Review hooks，选择 Trust all，再重试 |
| `codex plugin add` 找不到插件 | 确认在插件父目录运行 `codex plugin marketplace add ./delivery-conductor`，再执行安装 |
| 路由把文本改动误判为 UI | 运行 `python -m unittest tests.test_conductor.ConductorTests.test_linux_is_not_ui_but_real_ui_is -v` |
| 审批后摘要失效 | 审批后不要修改规格、计划、预览或交付证据；修改后必须重新审批 |

### 回滚

```bash
codex plugin remove delivery-conductor@delivery-conductor
```

然后移除本次添加的 marketplace 条目，并删除当前 `CODEX_HOME` 下 `plugins/cache/delivery-conductor/` 中本次安装的版本。如果修改过 `~/.codex/config.toml`，从安装前备份恢复。Windows 默认位置是 `%USERPROFILE%\.codex`，不要删除其他平台或其他版本的缓存。

## 限制与边界

- 提示词 Hook 会确定性识别入口并注入工作流。`PreToolUse` 守卫和失败关闭门禁会阻止审批等待期间的常规继续操作，但它们不是密码学意义上的不可抵赖系统。
- 语义路由会记录模型给出的范围、理由、受影响区域、置信度、未知项和关键词启发式快照。评估仍然是模型判断；校验器只检查结构和矛盾，不能证明判断在事实上完全正确。
- Hook 信任由 Codex 宿主维护。缺少信任、信任过期或被禁用时，编码总控会停止，而不是假装门禁已经生效。
- 会话 Hook 只提供提示，不阻塞执行。
- 编码总控不替代 OpenSpec 的需求权威、Superpowers 的计划与执行、ECC 的审查结论或 UI UX Pro Max 的设计决策。
- 所有平台审批只依赖交互终端；没有 TTY 时按安全默认失败关闭，不会自动批准。
