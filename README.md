# Delivery Conductor

An executable conductor for the four-layer delivery protocol:

```text
OpenSpec -> UI UX Pro Max -> Superpowers -> ECC
```

The skill provides routing and phase guidance. The Python scripts provide persistent state and fail-closed gates.

## Layout

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
│   ├── install_linux.sh
│   └── conductor/
│       ├── evidence.py
│       ├── gates.py
│       └── model.py
└── tests/
```

## Install

### 运行环境

- macOS / Ubuntu / Linux：Python 3.10+，推荐 3.11+。
- 审批只使用交互终端：运行 `approve.py` 后必须精确输入 `批准 <token>`；无 TTY 时失败关闭，远程会话使用 `ssh -t`。
- Ubuntu 22.04 等 Python 3.10 环境如果没有 `tomllib`，请安装 `python3-tomli`，或改用 Python 3.11+。
- 安装或升级后必须重启 Codex 并在 Hooks 中信任三个钩子，否则门禁不会生效。

安装前先运行诊断：

```bash
python3 scripts/doctor.py
```

诊断会检查 Python、TOML 解析、Codex 命令、终端审批、TTY、插件清单和钩子信任。任意平台可加 `--strict` 把警告视为失败：

```bash
python3 scripts/doctor.py --strict
```

Ubuntu 上也可以使用安装辅助脚本：

```bash
bash scripts/install_linux.sh
```

脚本只负责复制、权限、依赖检查和安装命令提示，不会写入 `~/.codex/config.toml`，也不会伪造钩子信任。

从 GitHub 安装：

```bash
git clone https://github.com/ceco3000/delivery-conductor.git
cd delivery-conductor
codex plugin marketplace add .
codex plugin add delivery-conductor@delivery-conductor
```

From the parent directory of this plugin:

```bash
codex plugin marketplace add ./delivery-conductor
codex plugin add delivery-conductor@delivery-conductor
```

Then enable the plugin in Codex if it is not enabled automatically.

### 首次启用或升级后：信任钩子

插件依赖 Codex 的 `UserPromptSubmit`、`PreToolUse` 和 `SessionStart` 钩子。Codex 不会自动信任新安装或内容已变化的钩子；未信任时，宿主会静默跳过钩子，审批门禁也就无法生效。

安装或升级后，请重启 Codex，打开 Hooks，选择 Review hooks，确认三个钩子后选择 Trust all。信任记录绑定插件声明和整包内容哈希；插件内容变化后必须重新确认。macOS 与 Linux 各自维护自己的信任记录，不能复用另一台机器的记录。

`route.py`、`gate.py` 和 `approve.py` 会在开始工作前运行 `host_trust.py` 自检。只要检测到已安装插件存在未信任、被禁用或内容已变化的钩子，命令立即退出非零并要求停止，不会继续创建状态、推进门禁或发起审批。

## Usage

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

Route a task (the entry phrase is stripped automatically):

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

Check gates:

```bash
python3 scripts/gate.py --state .codex/delivery-state.json
```

Record evidence:

```bash
python3 scripts/record.py --kind command --data '{"command":"npm test","result":"passed","exit_code":0}'
```

Verify all evidence:

```bash
python3 scripts/verify_evidence.py --state .codex/delivery-state.json
```

Record a consistency audit before delivery approval:

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

Run the simulation:

```bash
python3 scripts/simulate.py
```

Run tests:

```bash
python3 -m unittest discover -s tests -v
```

## State File

The default state file is `.codex/delivery-state.json`. It records:

- route and tier
- participating layers
- current phase
- required gates
- pending approval requests and interactive approval evidence
- evidence entries with timestamps

Gate advancement is sequential. `gate.py --advance` stops at the first required gate that fails; it cannot jump from `ROUTED` to `ARCHIVED` while an approval is missing.

### PreToolUse blocking

The plugin registers a `PreToolUse` hook. During `ROUTING`, it allows read-only inspection and `route.py --assess-json`, and denies implementation tools. While a human approval gate is pending, it denies file mutation tools and shell commands outside a narrow approval/read-only allowlist. Approval handshake scripts (`approve.py`, `gate.py`, `record.py`, `verify_evidence.py`) and read-only inspection commands remain available. Archive, commit, publish, and deployment commands are blocked until `delivery_approved`.

This protection only works when Codex trusts and enables the hook. The startup self-check therefore refuses to route, advance, or approve non-T0 work when the installed hook declarations or bundle contents are not trusted.

### 计划批准前的写入隔离

为 T2/T3 任务增加了范围很窄的实现写入隔离，只在 `plan_approved` 之前生效。它不会改变 T0/T1，也不会限制计划批准后的正常开发。

在 `plan_approved` 之前，以下路径允许写入：

- `openspec/`：需求、规格和变更文档。
- `docs/plans/`、`docs/superpowers/plans/`：实现计划和测试策略。
- `artifacts/design/`：设计说明、桌面/移动预览和参考图。

同时允许只读检查、审批握手脚本、`mkdir`/`touch`/`cp`/`mv` 等产物文件操作、`magick`/`convert`（Linux）或 `sips`（macOS）图片转换、UI UX Pro Max 搜索脚本，以及写入 `artifacts/design/` 的 Chrome CDP 截图。项目外的临时路径不受项目实现写入限制。

其他会修改项目的操作会被拒绝，包括写入 `src/`、`tests/`、`Cargo.toml`、`package.json`、`.codex/`，未知 shell 命令，以及没有可验证路径的写工具。审批门禁正在等待时，更严格的原有规则仍然优先，连 `openspec/` 和 `artifacts/design/` 也不能修改，以免审核摘要变化。

该策略是协作门禁，不是密码学沙箱。刻意混淆的脚本仍可能绕过路径检查；它的目标是阻止正常工具路径下的提前实现，而不是防御恶意用户。

The state file is a coordination aid, not a cryptographic security boundary. The interactive terminal token prevents an agent from turning a generic “继续” message into approval, and the hook blocks ordinary tool paths while approval is pending. A user who deliberately edits local state or runs bypass scripts can still do so.

## Linux 排障与回滚

### 常见问题

| 现象 | 处理 |
|---|---|
| `approve.py` 提示需要交互终端 | 在本地终端运行；远程服务器使用 SSH `-t` 申请 TTY |
| `python3 scripts/host_trust.py` 报缺少 `tomllib` | 安装 Python 3.11+，或运行 `sudo apt install python3-tomli` |
| `host_trust.py` 报钩子未信任 | 重启 Codex，打开 Hooks > Review hooks，选择 Trust all，再重试 |
| `codex plugin add` 找不到插件 | 确认在插件父目录运行 `codex plugin marketplace add ./delivery-conductor`，再执行安装 |
| 路由把 Linux 任务识别成 UI | 确认使用本移植包；运行 `python3 -m unittest tests.test_conductor.ConductorTests.test_linux_is_not_ui_but_real_ui_is -v` |
| 审批后摘要失效 | 审批后不要修改规格、计划、预览或交付证据；修改后必须重新审批 |

### 回滚

```bash
codex plugin remove delivery-conductor@delivery-conductor
```

然后移除本次添加的 marketplace 条目，并删除 `~/.codex/plugins/cache/delivery-conductor/` 下本次安装的版本。如果修改过 `~/.codex/config.toml`，从安装前备份恢复。macOS 本机插件缓存未被修改，不需要为本移植回滚 macOS。

## Limits

- The prompt hook deterministically recognizes the entry and injects the workflow. The `PreToolUse` guard and fail-closed gates block normal attempts to continue while approval is pending, but they are not a cryptographic non-repudiation system.
- Semantic route assessment is recorded with the model’s scope, reasons, affected areas, confidence, unknowns, and the keyword heuristic snapshot. The assessment remains a model judgment; the validator checks structure and contradictions rather than proving the judgment is factually complete.
- Hook trust is a Codex host setting. If it is missing, stale, or disabled, the conductor stops rather than pretending the gate is active.
- The session hook is advisory and intentionally non-blocking.
- The conductor does not replace OpenSpec requirements, Superpowers plans, ECC reviews, or UI UX Pro Max design decisions.
- 所有平台审批只依赖交互终端；没有 TTY 时按安全默认失败关闭，不会自动批准。
