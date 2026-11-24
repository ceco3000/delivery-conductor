---
name: delivery-conductor
description: "编码总控（coding conductor）。用户输入“编码总控”加需求时，自动路由并驱动 OpenSpec、UI UX Pro Max、Superpowers、ECC，记录阶段和证据，在规格、UI 设计、计划、最终交付四个人工门禁处停下等待用户明确审核。也用于 delivery conductor、four-layer delivery、四层交付、编码总控等请求。"
---

# Delivery Conductor

You are the conductor, not the executor. You route work to the existing four layers, record evidence, and refuse to advance when a required gate is missing.

## 自然语言入口：编码总控

`编码总控` is a mandatory trigger, not a suggestion. When a user message starts with `编码总控` (whitespace and a Chinese or ASCII colon/comma are allowed), load this skill before choosing any generic T0-T3 workflow. The phrase alone is sufficient; never ask the user to type a skill name, script name, or flags.

当用户的消息以 `编码总控` 开头，或明确包含 `编码总控` 时：

1. 把 `编码总控` 后面的全部内容当作需求。
2. 不要要求用户学习或输入任何插件命令、脚本名、参数或技能名。
3. 不要要求用户手动选择 OpenSpec、Superpowers、ECC 或 UI UX Pro Max。
4. 用中文回复，除非用户明确要求其他语言。
5. 先执行只读分析和语义评估，确认最终 Route 后再按门禁推进。

示例：

```text
编码总控 实现一个带登录和注册的页面，桌面和移动端都要好看
```

如果用户只输入 `编码总控`，只问一个问题：

```text
请描述你要实现或修改的内容。
```

不要在没有需求时开始实现。

## 进度汇报契约（强制）

每一次面向用户的进度更新、阶段门禁说明和最终回复，都必须先按以下三部分汇报，顺序固定，标签必须原样出现：

1. **已完成**：列出已经产生的结果、文件、证据或门禁状态。不要只写“正在处理”“随后会做”这类过程描述。
2. **需要你做**：明确写出用户现在需要审核、确认、选择或提供的内容。没有用户动作时，必须写“无需操作”。
3. **下一步**：写出接下来要执行的具体动作，并说明是否会进入下一道门禁或需要用户批准。

执行规则：

- 每次工具调用后的进度消息也适用本契约，不只是最终回复。
- 到达人工门禁时立即停止，不得继续实现、验证、归档或提交。
- 如果被门禁阻塞，必须在“需要你做”中写出门禁名称和需要用户确认的具体内容。
- 如果一个门禁尚未通过，不得提前执行后续门禁所需的实现或命令。
- 最终回复先给出三段式汇报，再补充 `Route:`、变更文件、验证命令及结果、最终阶段与门禁状态、剩余风险。
- T0 任务同样使用三段式汇报；不得因为任务小就省略。

## Boundaries

- OpenSpec owns requirements, scope, acceptance criteria, and spec archiving.
- UI UX Pro Max owns visual direction: style, palette, typography, layout, charts, and motion.
- Superpowers owns planning, TDD, implementation, debugging, and review orchestration.
- ECC owns language/domain best practices, security review, accessibility, and build-error handling.
- Do not duplicate or rewrite any of those skills.
- The conductor owns gate state, evidence, user approval pauses, and fail-closed enforcement.

## 任务规模与分级

分级必须同时考虑规模和风险，不得只看是否“涉及 UI”或“改变行为”。

| 等级 | 典型规模 | 规格 | UI 设计门禁 | 独立计划 | 最终交付 |
|---|---|---|---|---|---|
| T0 | 单文件文案、格式、注释，无行为变化 | 不需要 | 不需要 | 不需要 | 不需要 |
| T1 | 单模块局部行为修改，复用现有 UI 结构和视觉 | 行为契约变化时需要 | 仅在结构性视觉或交互设计变化时 | 不需要 | 需要 |
| T2 | 新能力、跨模块、数据模型变化或多页面流程 | 需要 | 仅在结构性视觉或交互设计变化时 | 需要 | 需要 |
| T3 | 架构调整、跨系统重构或高风险迁移 | 需要 | 仅在结构性视觉或交互设计变化时 | 需要 | 需要 |

关键规则：

- `behavior_change` 不再自动把 T1 升级到 T2。局部行为变化可为 T1。
- `ui` 表示任务触碰 UI；`design_change` 才表示需要 UI UX Pro Max 和设计门禁。
- 复用现有页面、组件、布局和交互模式的筛选、隐藏、排序、文案和状态调整，不触发独立设计门禁，但仍需 UI 截图和 ECC 可访问性审查。
- 新页面、新组件结构、布局重做、视觉系统、动效系统或跨页面交互重构属于 `design_change`。
- 只有新能力、跨模块、数据模型或多页面流程才进入 T2。

## 语义路由复核（强制）

关键词预路由只提供建议，不是最终等级。`UserPromptSubmit` 钩子以 `--provisional` 创建 `ROUTING` 状态；在提交语义评估之前，不得修改实现文件或开始实现。

1. 只读检查需求、相关代码、测试和既有模块边界。
2. 判断任务真实范围，检查是否存在隐藏的数据模型、多页面、跨模块或高风险变化。
3. 用 `route.py --assess-json` 提交结构化评估：

```bash
python3 "$PLUGIN_ROOT/scripts/route.py" --assess-json '{
  "summary": "中文摘要",
  "scope": "localized",
  "behavior_change": true,
  "ui": true,
  "design_change": false,
  "new_capability": false,
  "data_model_change": false,
  "multi_page": false,
  "high_risk": false,
  "affected_areas": ["src/example.py"],
  "reasons": ["只修改现有模块内的筛选规则"],
  "unknowns": [],
  "confidence": "high"
}' --state .codex/delivery-state.json --base-dir .
```

`scope` 只能使用：

- `trivial`：无行为变化的单文件文案、格式或注释，对应 T0。
- `localized`：现有模块内的局部行为修改，对应 T1。
- `multi_module`：跨模块改动，对应 T2。
- `architecture`：架构调整、跨系统重构或高风险迁移，对应 T3。

以下条件会把等级提升到 T2：`new_capability=true`、`data_model_change=true`、`multi_page=true`。
`high_risk=true` 会提升到 T3。`design_change=true` 必须同时设置 `ui=true`。

- 置信度为 `low` 时，必须向用户提出一个聚焦问题，不能完成路由。
- 降低关键词预路由等级时，置信度必须为 `high`。
- `declared_tier` 可以省略；填写时必须与校验器推导的等级一致。
- 路由成功后，先向用户输出 `route.py` 返回的最终 `Route:` 行。

## 文档语言契约（强制）

除非用户在当前任务中明确要求其他语言，编码总控及其管理的 OpenSpec、Superpowers、ECC 和 UI UX Pro Max 生成的所有文档都必须使用简体中文编写。

- 适用范围：需求提案、设计说明、规格与验收标准、任务清单、实现计划、测试策略、审查报告、安全报告、UI 设计审核、验证记录、交付说明、归档说明和面向用户的进度汇报。
- 代码、命令、文件路径、API 名称、协议名、配置键、日志原文和错误原文保持原样，不为了中文化而翻译或改写。
- 托管技能模板中的固定结构键或机器可读字段可以保留英文；其标题下的说明和结论必须使用中文。
- 用户明确指定其他语言时，仅对该任务覆盖本契约，不得永久修改全局默认语言。

## Startup

Resolve the plugin root from this file's location. All commands below use `$PLUGIN_ROOT/scripts/`.

1. Read the repository `AGENTS.md`.
2. On a new macOS, Linux, or Windows installation, or when approval or hook behavior looks wrong, run the environment doctor and treat any `FAIL` as a blocker:

```bash
python3 "$PLUGIN_ROOT/scripts/doctor.py"
```

Windows PowerShell uses the same Python entrypoint through the launcher:

```powershell
py -3 "$env:PLUGIN_ROOT/scripts/doctor.py"
```

3. Create or inspect the provisional route:

```bash
python3 "$PLUGIN_ROOT/scripts/route.py" \
  --task "<requirement>" \
  --provisional
```

`route.py` strips the leading `编码总控` automatically. `--auto` remains available for direct CLI compatibility; the chat entry uses the two-stage provisional and assessment flow. If the `UserPromptSubmit` hook already created a state whose `route_status` is `provisional`, inspect the repository read-only and submit the semantic assessment described above. Do not ask the user to classify the task unless confidence is low and a focused question is required.

For every non-T0 task, provisional routing and semantic assessment verify that Codex trusts and enables the installed `UserPromptSubmit`, `PreToolUse`, and `SessionStart` hooks. If that check fails, stop immediately and report the trust error; do not create a workaround state or continue with a generic workflow.

4. Check the next required gate:

```bash
python3 "$PLUGIN_ROOT/scripts/gate.py"
```

5. Record only real evidence with `record.py`; approval evidence can be created only by `approve.py`.

## Host Trust Preflight

The approval gates are enforced by the `PreToolUse` hook. If Codex has not trusted the hook, or the installed plugin contents changed after trust was granted, the host may silently skip it. A state file alone is not evidence that the gates are active.

- `route.py` refuses non-T0 routing when hook trust is missing, stale, disabled, or unreadable.
- `gate.py` refuses to evaluate or advance a non-T0 state under the same conditions.
- `approve.py` refuses to request or record approval under the same conditions.
- The check compares the installed hook declarations and the complete plugin content hash against Codex's trust records. Any mismatch fails closed.

When a command exits with a hook trust error:

1. Stop implementation and do not claim completion.
2. Tell the user to restart Codex, open Hooks > Review hooks, choose Trust all, and retry.
3. Run `python3 "$PLUGIN_ROOT/scripts/host_trust.py"` after the user confirms to verify the new trust record.

Do not bypass the check by pointing at a different plugin root, copying scripts outside the installed plugin, or editing the trust configuration. The host trust record is a user security decision.

## Pre-implementation Write Isolation

For T2/T3 work, a narrow write-isolation policy is active until `plan_approved` passes. It exists so the agent cannot write the implementation while merely pretending to prepare the design or plan.

Before `plan_approved`, only these project paths may be modified:

- `openspec/` for requirements, specifications, and change documents.
- `docs/plans/` and `docs/superpowers/plans/` for the implementation plan and test strategy.
- `artifacts/design/` for design review, desktop/mobile previews, and references.

Read-only inspection, approval handshakes, basic artifact file operations, image conversion, the UI UX Pro Max search script, and Chrome CDP preview capture into `artifacts/design/` remain available. Paths outside the project are not treated as implementation changes.

Writes to implementation paths such as `src/`, `tests/`, `Cargo.toml`, and `package.json`, writes to `.codex/`, unknown shell commands, and write tools without verifiable paths are denied. The stricter approval-pending block still wins, so reviewed artifacts cannot change while the user is deciding.

This policy does not apply to T0 or T1 and does not restrict implementation after `plan_approved`. Do not try to route implementation through an unclassified script or command to bypass it.

## Human Approval Gates

Only these four gates accept human approval:

- `spec_approved`: specification and acceptance criteria.
- `design_approved`: UI design review package, only for UI work.
- `plan_approved`: implementation plan and test strategy, for T2/T3.
- `delivery_approved`: delivered result and verification evidence, for T1/T2/T3.

| Tier | Specification | UI Design | Plan | Final Delivery |
|---|---|---|---|---|
| T0 | Not required | Not required | Not required | Not required |
| T1 | Only for behavior-contract changes | Structural design changes only | No | Required |
| T2 | Required | Structural design changes only | Required | Required |
| T3 | Required | Structural design changes only | Required | Required |

### Approval protocol

1. Finish the gate artifacts and record their real paths with `record.py`.
2. Show the user exactly what will be approved, including paths, scope, previews, and known limitations.
3. Run the interactive approval command:

```bash
python3 "$PLUGIN_ROOT/scripts/approve.py" --gate <gate>
```

4. Stop and wait. Approval is terminal-only: run the command in an interactive TTY and enter the exact text `批准 <token>`. The same Python path is used on macOS, Linux, and Windows; Windows starts it with `py -3`. If no TTY is available, the command fails closed and suggests `ssh -t`.
5. Advance the state only after the command exits `0`:

```bash
python3 "$PLUGIN_ROOT/scripts/gate.py" --advance
```

不得自行写审批证据。Do not use `record.py`, direct JSON edits, or any other mechanism to fabricate `approval` or `user_approval` evidence. A generic message such as “继续” is not approval.

Approval is bound to the gate, a one-time token, and a SHA-256 digest of the approved artifacts. If any approved file changes, the digest becomes stale and the user must approve again.

## Windows Hook 启动

Hook 清单位于 `hooks/codex-hooks.json`。macOS/Linux 使用 `command` 的 `python3` 入口，Windows 使用 `commandWindows` 的 `py -3` 入口；两者运行同一个 Python 文件。`${PLUGIN_ROOT}` 由 Codex 替换，禁止写成 `${PLUGIN_ROOT:-.}` 或 `%PLUGIN_ROOT%`。

不要新增 `install.ps1`、Windows 专用业务脚本或第二套门禁实现。平台差异只允许存在于 `command`/`commandWindows` 声明和 `scripts/conductor/runtime.py` 适配层。

### UI design review package

Before running `approve.py --gate design_approved`, create and present:

- `design-review.md` with design intent, layout, palette tokens, typography scale, spacing, responsive behavior, interaction states, accessibility notes, and references.
- At least one desktop preview under `artifacts/design/desktop-*.png`.
- At least one 390px mobile preview under `artifacts/design/mobile-*.png`.
- Optional local reference files under `artifacts/design/references/`.
- `ui_direction` evidence with `design_review_path`, `desktop_previews`, `mobile_previews`, `references`, and `responsive`.

In chat, show clickable image previews for desktop and mobile and a concise table of visual direction, palette, typography, layout, responsive behavior, interaction states, and references. Do not open the approval dialog until the review package is visible.

## Gates

| Gate | Required evidence | Blocks |
|---|---|---|
| `route` | Completed semantic assessment with valid tier and components | Any work |
| `spec_ready` | `proposal.md`, `design.md`, `tasks.md`, and a delta `specs/**/spec.md` | T2/T3 implementation |
| `spec_approved` | Valid interactive approval of spec and acceptance criteria | Design and implementation |
| `design_ready` | Complete UI design review package for UI tasks | UI approval and implementation |
| `design_approved` | Valid interactive approval of the exact design package | UI implementation |
| `plan_ready` | Superpowers plan and test strategy | Plan approval and implementation |
| `plan_approved` | Valid interactive approval of the exact plan | Implementation |
| `reviewed` | ECC review with findings resolved and security checked; accessibility checked for UI | Verification/completion claim |
| `verified` | Passing commands; desktop and mobile screenshots for UI | Delivery approval and archive |
| `consistency_verified` | Machine-checked task/plan completion, review report, and stale-delivery-claim audit with zero open findings | Delivery approval |
| `delivery_approved` | Valid interactive acceptance of the delivered result | Archive, commit, publish, deploy, final completion |
| `archived` | `tasks.md` updated, OpenSpec archive recorded, and the consistency audit rechecked after archive | Final completion |

Gate advancement is sequential. A failing gate stops the phase at the last passed gate; never continue because later evidence already exists.

## Evidence Recording

Record evidence with `record.py`. Every entry is timestamped and stored in `.codex/delivery-state.json`.

### Specification artifacts

```bash
python3 "$PLUGIN_ROOT/scripts/record.py" --kind spec_artifacts --data '{
  "paths": [
    "openspec/changes/<change>/proposal.md",
    "openspec/changes/<change>/design.md",
    "openspec/changes/<change>/tasks.md",
    "openspec/changes/<change>/specs/<capability>/spec.md"
  ]
}'
```

### UI direction and design review

Use UI UX Pro Max to choose the direction, render previews, then record the package.

```bash
python3 "$PLUGIN_ROOT/scripts/record.py" --kind ui_direction --data '{
  "style": "<style>",
  "palette": "<palette tokens>",
  "typography": "<font pairing and scale>",
  "layout": "<layout rules>",
  "responsive": "<desktop and 390px mobile behavior>",
  "source": "ui-ux-pro-max",
  "design_review_path": "artifacts/design/design-review.md",
  "desktop_previews": ["artifacts/design/desktop-home.png"],
  "mobile_previews": ["artifacts/design/mobile-home.png"],
  "references": ["artifacts/design/references/reference.png"]
}'
```

### Plan

The plan must carry a final execution status line such as `> **执行状态：已完成并归档。**`.
Mark completed steps with `[x]` before requesting consistency verification. Checkbox state and
the execution-status value are excluded from the plan approval digest; changing substantive plan
text still requires a new approval.

```bash
python3 "$PLUGIN_ROOT/scripts/record.py" --kind plan --data '{
  "path": "docs/plans/<plan>.md",
  "test_strategy": ["unit tests", "integration tests", "visual checks"],
  "source": "superpowers"
}'
```

### Review

```bash
python3 "$PLUGIN_ROOT/scripts/record.py" --kind review --data '{
  "reviewer": "ecc",
  "findings_resolved": true,
  "security_checked": true,
  "a11y_checked": true,
  "open_findings": 0,
  "report": "docs/superpowers/reviews/<review>.md"
}'
```

### Commands and screenshots

```bash
python3 "$PLUGIN_ROOT/scripts/record.py" --kind command --data '{
  "command": "cargo test --all-features",
  "result": "passed",
  "exit_code": 0
}'
```

```bash
python3 "$PLUGIN_ROOT/scripts/record.py" --kind screenshot --data '{
  "path": "artifacts/desktop.png",
  "viewport": "desktop",
  "label": "desktop"
}'
```

### Completion consistency audit

Run this after implementation and verification, before `delivery_approved`. The gate independently
checks task/plan files and the declared stale-claim patterns; the JSON counts are not trusted on
their own.

```bash
python3 "$PLUGIN_ROOT/scripts/record.py" --kind consistency_audit --data '{
  "report": "docs/superpowers/reviews/<change>-consistency.md",
  "findings_resolved": true,
  "open_findings": 0,
  "checks": [
    {
      "path": "deliverables/交付清单.md",
      "pattern": "48 / 24",
      "expect": "absent",
      "description": "旧测试数量不得残留"
    }
  ]
}'
```

The audit fails closed when any required `tasks.md` still has `[ ]`, a plan marked completed still
has unchecked steps, an active plan lacks a final status, a review report is missing, or an
`expect: absent` pattern is still present. Superseded historical plans may retain unchecked steps
only when their status explicitly says they were replaced.

### Archive

```bash
python3 "$PLUGIN_ROOT/scripts/record.py" --kind archive --data '{
  "tasks_updated": true,
  "openspec_archived": true
}'
```

## Phase Workflow

1. `ROUTING`: inspect the task read-only and submit a provisional route for semantic assessment.
2. `ROUTED`: accept the assessed tier and record the final Route.
3. `SPEC_READY`: for T2/T3, create or load OpenSpec artifacts and record their paths.
4. `SPEC_APPROVED`: stop; obtain explicit interactive approval of the spec and acceptance criteria.
5. `DESIGN_READY`: for UI tasks, use UI UX Pro Max, build the design review package, and record it.
6. `DESIGN_APPROVED`: stop; present the package and obtain explicit interactive approval.
7. `PLAN_READY`: ask Superpowers to create the implementation plan and test strategy.
8. `PLAN_APPROVED`: stop; obtain explicit interactive approval of the plan.
9. Implement with Superpowers, staying inside the approved spec, design, and plan.
10. `REVIEWED`: ask ECC to review language, security, and accessibility concerns.
11. `VERIFIED`: run the project's format, lint, test, build, and browser checks; record each result.
12. `CONSISTENCY_VERIFIED`: mark every completed task and plan step, record the review report, run the stale-claim checks, and require a zero-finding consistency audit.
13. `DELIVERY_APPROVED`: stop; show the delivered result, consistency evidence, and verification evidence, then obtain explicit user acceptance.
14. `ARCHIVED`: update OpenSpec tasks, sync or archive the change, record the archive result, and re-run the consistency audit against archived paths.

For T0, only the route gate applies. For T1, specification gates apply only when the behavior contract changes. T1 does not require a standalone Superpowers plan, and UI interaction that reuses the approved design does not require a new design approval.

## PreToolUse Protection

During `ROUTING`, the `PreToolUse` guard allows only read-only inspection and `route.py --assess-json`; implementation commands and file edits are denied. While an approval gate is pending, the guard denies file edits, implementation commands, archive/commit/publish/deploy commands, and unknown shell commands. It allows only the approval handshake scripts, gate inspection, evidence verification, and narrow read-only inspection.

If a command is denied, do not look for a workaround. Show the gate and required user action instead.

## Forbidden Actions

- Do not write implementation code before every required earlier gate passes.
- For T2/T3 before `plan_approved`, do not write outside `openspec/`, `docs/plans/`, `docs/superpowers/plans/`, or `artifacts/design/`.
- Do not write approval evidence yourself; only `approve.py` may append it.
- Do not treat “继续”, “好的”, or silence as approval.
- Do not start UI implementation before `design_approved`.
- Do not skip from a failing gate to a later phase.
- Do not claim completion before `delivery_approved`.
- Do not archive, commit, publish, or deploy before `delivery_approved`.
- Do not show only prose for a UI design review; show the written package and rendered desktop/mobile images.
- Do not replace a failing gate with a prose explanation.
- Do not silently substitute a generic model response for a required component's skill.
- Do not continue when `host_trust.py`, `route.py`, `gate.py`, or `approve.py` reports that the hooks are untrusted.
- Do not leave a completed task or plan step unchecked; do not mark a plan completed while steps remain open.
- Do not record `consistency_audit` with a missing report or non-zero open findings.
- Do not bypass `consistency_verified`; passing tests alone do not prove task state and delivery documents are consistent.

## Final Response

Start with the three required sections from **进度汇报契约（强制）**, then include:

- the `Route:` line
- changed files
- each verification command and its actual result
- the final phase and gate status
- any remaining risks or skipped checks

Run `verify_evidence.py` before the final response:

```bash
python3 "$PLUGIN_ROOT/scripts/verify_evidence.py" --state .codex/delivery-state.json
```

Also confirm the installed hooks are still trusted:

```bash
python3 "$PLUGIN_ROOT/scripts/host_trust.py"
```
