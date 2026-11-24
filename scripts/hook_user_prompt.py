#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from route import ENTRY_PHRASE, strip_entry_phrase
from conductor.runtime import hook_interpreter, quote_argv


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def python_command(script: Path) -> str:
    return quote_argv([*hook_interpreter(), str(script)])


def emit(additional_context: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": additional_context,
        }
    }, ensure_ascii=False))


def build_context(prompt: str, cwd: Path) -> str:
    requirement = strip_entry_phrase(prompt)
    if not requirement:
        return (
            "用户使用了 `编码总控` 入口，但没有提供需求。"
            "只问一个问题：请描述你要实现或修改的内容。"
            "不要创建状态、不要开始实现、不要要求用户输入命令。"
        )

    state_path = cwd / ".codex/delivery-state.json"
    route = subprocess.run(
        [
            sys.executable,
            str(PLUGIN_ROOT / "scripts/route.py"),
            "--task",
            requirement,
            "--provisional",
            "--state",
            str(state_path),
            "--base-dir",
            str(cwd),
        ],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if route.returncode != 0:
        return (
            "`编码总控` 入口已触发，但自动路由失败。必须停止并报告错误，不得静默改用普通流程。\n"
            f"错误：{route.stderr.strip() or route.stdout.strip()}"
        )

    route_line = route.stdout.splitlines()[0] if route.stdout.strip() else "Route: unknown"
    gate = subprocess.run(
        [
            sys.executable,
            str(PLUGIN_ROOT / "scripts/gate.py"),
            "--state",
            str(state_path),
            "--base-dir",
            str(cwd),
            "--json",
        ],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    next_gate = "unknown"
    try:
        next_gate = json.loads(gate.stdout).get("next_gate") or "none"
    except (json.JSONDecodeError, AttributeError):
        pass

    skill_path = PLUGIN_ROOT / "skills/delivery-conductor/SKILL.md"
    try:
        skill = skill_path.read_text(encoding="utf-8")
    except OSError as error:
        return (
            "`编码总控` 入口已触发，但总控技能文件不可读。必须停止并报告错误。\n"
            f"错误：{error}"
        )

    return f"""<delivery-conductor-trigger>
用户已使用 `编码总控` 入口。这是强制触发，不是建议。

预路由建议（provisional）：
{route_line}
State: {state_path}
Next required gate: {next_gate}
Requirement: {requirement}

立即执行以下规则：
- 当前路由只是关键词预路由，不得直接开始实现，也不得把预路由等级当成最终等级。
- 先只读检查需求和相关仓库文件，判断真实范围，再完成语义评估。
- 用下面的命令提交结构化评估；成功后 route.py 会输出最终 Route 行：

```bash
{python_command(PLUGIN_ROOT / "scripts/route.py")} --assess-json '<JSON>' --state "{state_path}" --base-dir "{cwd}"
```

评估 JSON 必须包含：
{{"summary":"中文摘要","scope":"trivial|localized|multi_module|architecture","behavior_change":false,"ui":false,"design_change":false,"new_capability":false,"data_model_change":false,"multi_page":false,"high_risk":false,"affected_areas":["路径或模块"],"reasons":["分级依据"],"unknowns":[],"confidence":"high|medium|low"}}

- T0 只用于无行为变化的单文件文案、格式或注释；局部行为修改通常为 T1。
- 新能力、跨模块、数据模型变化、多页面流程为 T2；架构调整、跨系统重构或高风险迁移为 T3。
- 设计结构变化时设置 `design_change=true`；复用现有设计时保持 false。
- `low` 置信度必须向用户提出一个聚焦问题，不能提交最终路由。
- 路由完成后先向用户输出 route.py 返回的最终 Route 行。
- 必须加载或遵守下面注入的 delivery-conductor 技能内容。
- 不得要求用户学习或输入插件名、脚本名、参数或 flags。
- 不得跳过门禁；每一步完成后记录真实证据并运行 gate.py。
- 不得自行写审批证据；只能运行 approve.py，由用户在交互终端中明确批准。
- 到达 spec_approved、design_approved、plan_approved、delivery_approved 时必须停下并报告“需要你做”。
- verified 之后必须通过 consistency_verified：完成的任务和计划步骤必须勾选，计划必须有最终执行状态，审查报告必须存在，一致性审计必须为零发现。
- 不得为了保持审批摘要而留下虚假的未勾选任务；计划复选框和执行状态行不参与审批摘要，正文变化才需要重新审批。
- UI 设计审核前必须在聊天中展示 design-review.md、桌面预览和 390px 移动预览，再请求 design_approved。
- T2/T3 在规格和验收标准确认前不得开始实现。
- 涉及 UI 时必须先使用 UI UX Pro Max，再使用 Superpowers 和 ECC。
- 所有生成的文档默认使用简体中文；OpenSpec、Superpowers、ECC 和 UI UX Pro Max 的文档产物都必须遵守，代码、命令、路径和标识符保持原样。
- 未通过最终验证不得声称完成。

<delivery-conductor-skill>
{skill}
</delivery-conductor-skill>
</delivery-conductor-trigger>"""


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0

    prompt = str(payload.get("prompt", ""))
    if not prompt.lstrip().startswith(ENTRY_PHRASE):
        return 0

    cwd_value = payload.get("cwd") or os.getcwd()
    cwd = Path(str(cwd_value)).expanduser().resolve()
    if not cwd.is_dir():
        cwd = Path.cwd()
    try:
        context = build_context(prompt, cwd)
    except Exception as error:
        context = (
            "`编码总控` 入口已触发，但 Hook 内部错误。必须停止并报告错误，不得静默改用普通流程。\n"
            f"错误：{type(error).__name__}: {error}"
        )
    try:
        emit(context)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
