from __future__ import annotations

import json
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import approve as approve_module
import install as install_module
from approve import PrerequisiteError, approve_gate, confirm_with_user
from conductor.evidence import validate_evidence
from conductor.gates import (
    advance_state,
    approval_digest,
    evaluate_all,
    evaluate_gate,
    next_human_approval_gate,
    valid_approval,
)
from conductor.model import (
    GATE_ORDER,
    append_evidence,
    build_state,
    components_for,
    load_state,
    save_state,
)
from conductor.runtime import (
    has_shell_control,
    hook_interpreter,
    program_name,
    quote_argv,
    relative_name_bytes,
    split_command,
)
from host_trust import (
    EVENT_LABELS,
    TOML_BACKEND,
    HostTrustError,
    declaration_hash,
    enforce_host_trust,
    load_toml,
    parse_toml,
    plugin_content_hash,
    trust_report,
)
from guard_tool import (
    allowed_read_only,
    image_command_allowed,
    routing_inspection_command_allowed,
    semantic_assessment_command_allowed,
    shell_allowed,
)
from route import infer_from_text, infer_tier, strip_entry_phrase


def write(path: Path, content: str = "content\n") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


def host_trust_fixture(base: Path, *, tamper: str | None = None) -> tuple[Path, Path]:
    """Create a fake Codex home with one installed plugin and hook trust state."""
    codex_home = base / "codex-home"
    version = json.loads((PLUGIN_ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
    root = (codex_home / "plugins/cache/delivery-conductor/delivery-conductor" / version).resolve()
    hooks = json.loads((PLUGIN_ROOT / "hooks/codex-hooks.json").read_text(encoding="utf-8"))
    write(root / "hooks/codex-hooks.json", json.dumps(hooks, ensure_ascii=False))
    write(root / "README.md", "fixture bundle\n")
    content_hash = plugin_content_hash(root)

    entries: list[tuple[str, str, str]] = []
    for event_name, groups in hooks["hooks"].items():
        label = EVENT_LABELS[event_name]
        for group_index, group in enumerate(groups):
            for handler_index, handler in enumerate(group.get("hooks", [])):
                if handler.get("type") != "command":
                    continue
                key = (
                    "delivery-conductor@delivery-conductor:"
                    f"hooks/codex-hooks.json:{label}:{group_index}:{handler_index}"
                )
                trusted = declaration_hash(
                    label,
                    group.get("matcher"),
                    handler,
                    plugin_root=root,
                    platform="nt" if os.name == "nt" else "posix",
                )
                entries.append((key, trusted, content_hash))

    if tamper == "declaration":
        key, _, trusted_content = entries[0]
        entries[0] = (key, "sha256:" + "0" * 64, trusted_content)

    lines: list[str] = []
    for key, trusted, trusted_content in entries:
        lines.append(f'[hooks.state."{key}"]')
        lines.append(f'trusted_hash = "{trusted}"')
        lines.append(f'trusted_content_hash = "{trusted_content}"')
        if tamper == "disabled" and key.endswith("pre_tool_use:0:0"):
            lines.append("enabled = false")
        lines.append("")
    config_path = codex_home / "config.toml"
    write(config_path, "\n".join(lines))

    if tamper == "content":
        write(root / "CHANGED.md", "bundle changed after trust\n")
    if tamper == "missing-config":
        config_path.unlink()
    return codex_home, root


_ORIGINAL_CODEX_HOME = os.environ.get("CODEX_HOME")
_MODULE_TEMP: Path | None = None


def setUpModule() -> None:
    global _MODULE_TEMP
    _MODULE_TEMP = Path(tempfile.mkdtemp(prefix="delivery-conductor-tests-"))
    codex_home, _ = host_trust_fixture(_MODULE_TEMP)
    os.environ["CODEX_HOME"] = str(codex_home)


def tearDownModule() -> None:
    if _ORIGINAL_CODEX_HOME is None:
        os.environ.pop("CODEX_HOME", None)
    else:
        os.environ["CODEX_HOME"] = _ORIGINAL_CODEX_HOME
    if _MODULE_TEMP is not None:
        shutil.rmtree(_MODULE_TEMP, ignore_errors=True)


def add_specs(state: dict, base: Path, approval: bool = False) -> None:
    paths = [
        write(base / "openspec/changes/example/proposal.md"),
        write(base / "openspec/changes/example/design.md"),
        write(base / "openspec/changes/example/tasks.md"),
        write(base / "openspec/changes/example/specs/user-auth/spec.md"),
    ]
    append_evidence(state, {"kind": "spec_artifacts", "paths": paths})
    if approval:
        approve(state, "spec_approved", base)


def add_design(state: dict, base: Path, approval: bool = False) -> None:
    design_review = write(
        base / "artifacts/design/design-review.md",
        "# Design Review\n\n- Responsive: 390px and desktop\n- Accessibility: WCAG AA\n",
    )
    desktop = write(base / "artifacts/design/desktop-home.png", "desktop-preview\n")
    mobile = write(base / "artifacts/design/mobile-home.png", "mobile-preview\n")
    append_evidence(state, {
        "kind": "ui_direction",
        "style": "editorial-minimal",
        "palette": "warm neutral",
        "typography": "humanist sans",
        "layout": "single column",
        "responsive": "single column below 768px; 390px mobile verified",
        "source": "ui-ux-pro-max",
        "design_review_path": design_review,
        "desktop_previews": [desktop],
        "mobile_previews": [mobile],
        "references": [],
    })
    if approval:
        approve(state, "design_approved", base)


def add_plan(state: dict, base: Path, approval: bool = False) -> None:
    plan = write(
        base / "docs/superpowers/plans/example-plan.md",
        "# Plan\n\n> **执行状态：已完成并归档。**\n\n"
        "- [x] Step 1: add tests\n- [x] Step 2: implement\n",
    )
    append_evidence(state, {
        "kind": "plan",
        "path": plan,
        "test_strategy": ["unit tests", "component tests"],
        "source": "superpowers",
    })
    if approval:
        approve(state, "plan_approved", base)


def add_review_with_report(state: dict, base: Path, ui: bool = False) -> None:
    report = write(
        base / "docs/superpowers/reviews/example-review.md",
        "# ECC Review\n\nFindings resolved: 0 open.\n",
    )
    append_evidence(state, {
        "kind": "review",
        "reviewer": "ecc",
        "findings_resolved": True,
        "security_checked": True,
        "a11y_checked": ui,
        "open_findings": 0,
        "report": report,
    })


def add_verification(state: dict, base: Path, ui: bool = False) -> None:
    append_evidence(state, {
        "kind": "command",
        "command": "python3 -m unittest",
        "result": "passed",
        "exit_code": 0,
    })
    if ui:
        desktop = base / "screenshots/desktop.png"
        mobile = base / "screenshots/mobile.png"
        desktop.parent.mkdir(parents=True, exist_ok=True)
        desktop.write_bytes(b"desktop-png")
        mobile.write_bytes(b"mobile-png")
        append_evidence(state, {"kind": "screenshot", "path": str(desktop), "viewport": "desktop", "label": "desktop"})
        append_evidence(state, {"kind": "screenshot", "path": str(mobile), "viewport": "mobile", "label": "mobile"})


def add_consistency(state: dict, base: Path, *, stale_marker: str = "48 / 24") -> None:
    deliverables = base / "deliverables" / "交付清单.md"
    deliverables.parent.mkdir(parents=True, exist_ok=True)
    if not deliverables.exists():
        deliverables.write_text("48 / 25 通过\n", encoding="utf-8")
    report = write(
        base / "docs/superpowers/reviews/example-consistency.md",
        "# Consistency Audit\n\n- Open tasks: 0\n- Open plans: 0\n- Open findings: 0\n",
    )
    append_evidence(state, {
        "kind": "consistency_audit",
        "report": report,
        "findings_resolved": True,
        "open_findings": 0,
        "checks": [{
            "path": "deliverables/交付清单.md",
            "pattern": stale_marker,
            "expect": "absent",
            "description": "旧测试数量或旧口径不得残留在交付清单",
        }],
    })


def approve(state: dict, gate: str, base: Path, method: str = "tty") -> None:
    approved = approve_gate(
        state,
        gate,
        base,
        confirm=lambda _summary, _token: (True, method),
    )
    if not approved:
        raise AssertionError(f"test approval was not recorded for {gate}")


def run_prompt_hook(prompt: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    payload = {
        "cwd": str(cwd),
        "hook_event_name": "UserPromptSubmit",
        "model": "test",
        "permission_mode": "default",
        "prompt": prompt,
        "session_id": "test-session",
        "transcript_path": None,
        "turn_id": "test-turn",
    }
    environment = dict(os.environ)
    environment["PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "hook_user_prompt.py")],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )


def run_hook(
    script: str,
    payload: dict,
    *,
    cwd: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict:
    environment = dict(os.environ)
    environment["PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    if extra_env:
        environment.update(extra_env)
    completed = subprocess.run(
        [sys.executable, str(SCRIPTS / script)],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        cwd=cwd,
    )
    if not completed.stdout.strip():
        return {}
    return json.loads(completed.stdout)


def run_manifest_hook(event: str, payload: dict, cwd: Path) -> subprocess.CompletedProcess[str]:
    hooks = json.loads((PLUGIN_ROOT / "hooks/codex-hooks.json").read_text(encoding="utf-8"))
    handler = hooks["hooks"][event][0]["hooks"][0]
    platform = "nt" if os.name == "nt" else "posix"
    command = handler["commandWindows"] if platform == "nt" else handler["command"]
    argv = split_command(command.replace("${PLUGIN_ROOT}", str(PLUGIN_ROOT)), platform=platform)
    environment = dict(os.environ)
    environment["PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    return subprocess.run(
        argv,
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
        env=environment,
    )


def guard_output(
    base: Path,
    state: dict | None = None,
    *,
    command: str | None = None,
    tool_name: str = "Bash",
    tool_input: dict | None = None,
) -> dict:
    if state is not None:
        save_state(base / ".codex/delivery-state.json", state)
    if tool_input is None:
        tool_input = {"command": command} if command is not None else {}
    return run_hook("guard_tool.py", {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "cwd": str(base),
    })


class ConductorTests(unittest.TestCase):
    def test_session_start_hook_returns_valid_json_without_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = run_hook(
                "hook_session_start.py",
                {
                    "hook_event_name": "SessionStart",
                    "session_id": "test-session",
                    "cwd": str(base),
                },
                cwd=base,
                extra_env={
                    "DELIVERY_CONDUCTOR_STATE": str(base / ".codex/delivery-state.json")
                },
            )

        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn(
            "Use this conductor for T2/T3 tasks",
            output["hookSpecificOutput"]["additionalContext"],
        )

    def test_session_start_hook_reports_normal_state_as_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state_path = base / ".codex/delivery-state.json"
            state = build_state("example", "T2", components_for("T2"))
            save_state(state_path, state)
            output = run_hook(
                "hook_session_start.py",
                {
                    "hook_event_name": "SessionStart",
                    "session_id": "test-session",
                    "cwd": str(base),
                },
                cwd=base,
                extra_env={"DELIVERY_CONDUCTOR_STATE": str(state_path)},
            )

        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn(f"phase={state['phase']}", context)
        self.assertIn(f"tier={state['tier']}", context)
        self.assertIn("next_gate=", context)

    def test_session_start_hook_reports_load_failure_as_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state_path = base / ".codex/delivery-state.json"
            write(state_path, "{invalid json")
            output = run_hook(
                "hook_session_start.py",
                {
                    "hook_event_name": "SessionStart",
                    "session_id": "test-session",
                    "cwd": str(base),
                },
                cwd=base,
                extra_env={"DELIVERY_CONDUCTOR_STATE": str(state_path)},
            )

        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn(
            "reminder unavailable:",
            output["hookSpecificOutput"]["additionalContext"],
        )

    def test_t2_ui_requires_four_human_approvals(self) -> None:
        state = build_state("ui", "T2", components_for("T2", ui=True), ui=True, behavior_change=True)
        approval_gates = [gate for gate in GATE_ORDER if gate.endswith("_approved")]
        self.assertEqual(
            approval_gates,
            ["spec_approved", "design_approved", "plan_approved", "delivery_approved"],
        )
        for name in ("spec_approval", "design_approval", "plan_approval", "delivery_approval"):
            self.assertTrue(state["requires"][name])

    def test_t2_non_ui_skips_design_approval(self) -> None:
        state = build_state("api", "T2", components_for("T2"), behavior_change=True)
        self.assertFalse(state["requires"]["design_approval"])
        self.assertTrue(state["requires"]["spec_approval"])
        self.assertTrue(state["requires"]["plan_approval"])
        self.assertTrue(state["requires"]["delivery_approval"])

    def test_t1_requires_delivery_approval_without_behavior_change(self) -> None:
        state = build_state("fix", "T1", components_for("T1"))
        self.assertFalse(state["requires"]["spec_approval"])
        self.assertFalse(state["requires"]["plan_approval"])
        self.assertFalse(state["requires"]["plan"])
        self.assertTrue(state["requires"]["delivery_approval"])

    def test_localized_behavior_ui_reuses_design_and_stays_t1(self) -> None:
        flags = infer_from_text("修改启动器，把无来源的 ROM 全部隐藏")
        args = type("Args", (), flags)()
        tier, _notes = infer_tier(args)
        state = build_state(
            "hide unsourced games",
            tier,
            components_for(
                tier,
                ui=flags["ui"],
                design_change=flags["design_change"],
                behavior_change=flags["behavior_change"],
            ),
            ui=flags["ui"],
            behavior_change=flags["behavior_change"],
            design_change=flags["design_change"],
        )

        self.assertEqual(tier, "T1")
        self.assertTrue(flags["behavior_change"])
        self.assertTrue(flags["ui"])
        self.assertFalse(flags["design_change"])
        self.assertTrue(state["requires"]["spec_approval"])
        self.assertFalse(state["requires"]["design_approval"])
        self.assertFalse(state["requires"]["plan_approval"])
        self.assertFalse(state["requires"]["plan"])
        self.assertNotIn("ui-ux-pro-max", state["components"])

    def test_new_capability_stays_t2(self) -> None:
        flags = infer_from_text("新增完整下载中心和来源同步功能")
        tier, _notes = infer_tier(type("Args", (), flags)())
        self.assertEqual(tier, "T2")

    def test_visual_redesign_requires_design_gate(self) -> None:
        flags = infer_from_text("重做游戏库页面布局、组件样式和响应式视觉")
        tier, _notes = infer_tier(type("Args", (), flags)())
        self.assertTrue(flags["design_change"])
        self.assertIn("ui-ux-pro-max", components_for(
            tier,
            ui=flags["ui"],
            design_change=flags["design_change"],
            behavior_change=flags["behavior_change"],
        ))

    def test_t0_requires_no_human_approval(self) -> None:
        state = build_state("docs", "T0", components_for("T0"))
        for name in ("spec_approval", "design_approval", "plan_approval", "delivery_approval"):
            self.assertFalse(state["requires"][name])

    def test_t2_ui_components(self) -> None:
        self.assertEqual(
            components_for("T2", ui=True),
            ["openspec", "ui-ux-pro-max", "superpowers", "ecc"],
        )
        self.assertEqual(components_for("T0", ui=True), [])

    def test_gate_fails_without_evidence(self) -> None:
        state = build_state("example", "T2", components_for("T2", ui=True), ui=True)
        ok, reasons = evaluate_gate(state, "spec_ready", ".")
        self.assertFalse(ok)
        self.assertIn("missing spec_artifacts evidence", reasons)

    def test_full_flow_passes_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state(
                "example",
                "T2",
                components_for("T2", ui=True),
                ui=True,
                behavior_change=True,
            )
            add_specs(state, base)
            advance_state(state, base)
            self.assertEqual(state["phase"], "SPEC_READY")
            approve(state, "spec_approved", base)
            advance_state(state, base)
            self.assertEqual(state["phase"], "SPEC_APPROVED")

            add_design(state, base)
            advance_state(state, base)
            self.assertEqual(state["phase"], "DESIGN_READY")
            approve(state, "design_approved", base)
            advance_state(state, base)
            self.assertEqual(state["phase"], "DESIGN_APPROVED")

            add_plan(state, base)
            advance_state(state, base)
            self.assertEqual(state["phase"], "PLAN_READY")
            approve(state, "plan_approved", base)
            advance_state(state, base)
            self.assertEqual(state["phase"], "PLAN_APPROVED")

            add_review_with_report(state, base, ui=True)
            add_verification(state, base, ui=True)
            advance_state(state, base)
            self.assertEqual(state["phase"], "VERIFIED")
            self.assertEqual(next_human_approval_gate(state, base), None)
            add_consistency(state, base)
            advance_state(state, base)
            self.assertEqual(state["phase"], "CONSISTENCY_VERIFIED")
            approve(state, "delivery_approved", base)
            advance_state(state, base)
            self.assertEqual(state["phase"], "DELIVERY_APPROVED")

            append_evidence(state, {"kind": "archive", "tasks_updated": True, "openspec_archived": True})
            results = advance_state(state, base)
            self.assertTrue(all(item["ok"] for item in results.values()), results)
            self.assertEqual(state["phase"], "ARCHIVED")

    def test_missing_screenshot_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("example", "T2", components_for("T2", ui=True), ui=True)
            add_plan(state, base)
            add_review_with_report(state, base, ui=True)
            append_evidence(state, {"kind": "command", "command": "test", "result": "passed", "exit_code": 0})
            ok, reasons = evaluate_gate(state, "verified", base)
            self.assertFalse(ok)
            self.assertTrue(any("screenshot" in reason for reason in reasons), reasons)

    def test_failed_command_then_pass(self) -> None:
        state = build_state("example", "T2", components_for("T2"))
        append_evidence(state, {"kind": "command", "command": "test", "result": "failed", "exit_code": 1})
        ok, _ = evaluate_gate(state, "verified", ".")
        self.assertFalse(ok)
        append_evidence(state, {"kind": "command", "command": "test", "result": "passed", "exit_code": 0})
        ok, reasons = evaluate_gate(state, "verified", ".")
        self.assertTrue(ok, reasons)

    def test_evidence_validation(self) -> None:
        self.assertEqual(validate_evidence("command", {"command": "test", "result": "passed", "exit_code": 0}), [])
        errors = validate_evidence("command", {"command": "", "result": "ok", "exit_code": "0"})
        self.assertGreaterEqual(len(errors), 2)
        consistency = {
            "report": "report.md",
            "findings_resolved": True,
            "open_findings": 0,
            "checks": [{
                "path": "deliverables/list.md",
                "pattern": "48 / 24",
                "expect": "absent",
            }],
        }
        self.assertEqual(validate_evidence("consistency_audit", consistency), [])
        invalid = dict(consistency)
        invalid["open_findings"] = 1
        self.assertTrue(any("open_findings" in error for error in validate_evidence("consistency_audit", invalid)))

    def test_approval_evidence_validation(self) -> None:
        approval = {
            "gate": "spec_approved",
            "scope": "spec_approved",
            "approved": True,
            "approved_by": "user",
            "explicit": True,
            "token": "token",
            "artifact_digest": "digest",
            "method": "tty",
            "requested_at": "2026-11-15T10:00:00+08:00",
            "approved_at": "2026-11-15T10:00:01+08:00",
        }
        self.assertEqual(validate_evidence("approval", approval), [])
        errors = validate_evidence("approval", {"approved": True})
        self.assertGreaterEqual(len(errors), 4)

    def test_native_dialog_approval_evidence_is_rejected(self) -> None:
        approval = {
            "gate": "spec_approved",
            "scope": "spec_approved",
            "approved": True,
            "approved_by": "user",
            "explicit": True,
            "token": "token",
            "artifact_digest": "digest",
            "method": "native_dialog",
            "requested_at": "2026-11-15T10:00:00+08:00",
            "approved_at": "2026-11-15T10:00:01+08:00",
        }
        errors = validate_evidence("approval", approval)
        self.assertTrue(any("method" in error for error in errors), errors)

    def test_non_tty_approval_evidence_is_rejected(self) -> None:
        approval = {
            "gate": "spec_approved",
            "scope": "spec_approved",
            "approved": True,
            "approved_by": "user",
            "explicit": True,
            "token": "token",
            "artifact_digest": "digest",
            "method": "test",
            "requested_at": "2026-11-15T10:00:00+08:00",
            "approved_at": "2026-11-15T10:00:01+08:00",
        }
        errors = validate_evidence("approval", approval)
        self.assertTrue(any("method" in error for error in errors), errors)

    def test_native_dialog_method_cannot_be_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("ui", "T2", components_for("T2", ui=True), ui=True, behavior_change=True)
            add_specs(state, base)
            with self.assertRaises(ValueError):
                approve_gate(
                    state,
                    "spec_approved",
                    base,
                    confirm=lambda _summary, _token: (True, "native_dialog"),
                )

    def test_manually_injected_native_dialog_approval_fails_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("ui", "T2", components_for("T2", ui=True), ui=True, behavior_change=True)
            add_specs(state, base)
            digest = approval_digest(state, "spec_approved", base)
            requested_at = "2026-11-15T10:00:00+08:00"
            state["approval_requests"] = [{
                "gate": "spec_approved",
                "token": "token",
                "requested_at": requested_at,
                "artifact_digest": digest,
            }]
            append_evidence(state, {
                "kind": "approval",
                "gate": "spec_approved",
                "scope": "spec_approved",
                "approved": True,
                "approved_by": "user",
                "explicit": True,
                "token": "token",
                "artifact_digest": digest,
                "method": "native_dialog",
                "requested_at": requested_at,
                "approved_at": "2026-11-15T10:00:01+08:00",
            })
            ok, reasons = evaluate_gate(state, "spec_approved", base)
            self.assertFalse(ok)
            self.assertTrue(any("method" in reason for reason in reasons), reasons)

    def test_legacy_user_approval_does_not_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("ui", "T2", components_for("T2", ui=True), ui=True, behavior_change=True)
            add_specs(state, base)
            append_evidence(state, {
                "kind": "user_approval",
                "approved": True,
                "approved_by": "user",
                "scope": "spec",
            })
            ok, reasons = evaluate_gate(state, "spec_approved", base)
            self.assertFalse(ok)
            self.assertTrue(any("interactive" in reason for reason in reasons), reasons)

    def test_record_rejects_self_approval(self) -> None:
        for kind in ("approval", "user_approval"):
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "record.py"),
                    "--kind",
                    kind,
                    "--data",
                    '{"approved": true}',
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("interactive approval command", result.stderr)
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "record.py"),
                "--kind",
                "command",
                "--data",
                '{"kind": "approval", "command": "echo ok", "result": "passed", "exit_code": 0}',
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reserved fields", result.stderr)

    def test_approval_digest_changes_when_spec_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("ui", "T2", components_for("T2", ui=True), ui=True, behavior_change=True)
            proposal = write(base / "openspec/changes/x/proposal.md", "version one\n")
            append_evidence(state, {"kind": "spec_artifacts", "paths": [proposal]})
            before = approval_digest(state, "spec_approved", base)
            Path(proposal).write_text("version two\n", encoding="utf-8")
            self.assertNotEqual(before, approval_digest(state, "spec_approved", base))

    def test_task_checkbox_status_does_not_invalidate_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state(
                "localized",
                "T1",
                components_for("T1", behavior_change=True),
                behavior_change=True,
            )
            tasks = Path(write(
                base / "openspec/changes/x/tasks.md",
                "- [ ] 1.1 Implement behavior\n",
            ))
            append_evidence(state, {"kind": "spec_artifacts", "paths": [str(tasks)]})
            before = approval_digest(state, "spec_approved", base)
            tasks.write_text("- [x] 1.1 Implement behavior\n", encoding="utf-8")
            self.assertEqual(before, approval_digest(state, "spec_approved", base))

    def test_plan_checkbox_status_does_not_invalidate_plan_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("plan", "T2", components_for("T2"), behavior_change=True)
            plan = Path(write(
                base / "docs/superpowers/plans/example.md",
                "# Plan\n\n> **执行状态：进行中。**\n\n- [ ] Step 1\n- [ ] Step 2\n",
            ))
            append_evidence(state, {
                "kind": "plan",
                "path": str(plan),
                "test_strategy": ["unit tests"],
                "source": "superpowers",
            })
            before = approval_digest(state, "plan_approved", base)
            plan.write_text(
                "# Plan\n\n> **执行状态：已完成并归档。**\n\n- [x] Step 1\n- [x] Step 2\n",
                encoding="utf-8",
            )
            self.assertEqual(before, approval_digest(state, "plan_approved", base))
            plan.write_text(
                "# Plan\n\n> **执行状态：已完成并归档。**\n\n- [x] Different step\n- [x] Step 2\n",
                encoding="utf-8",
            )
            self.assertNotEqual(before, approval_digest(state, "plan_approved", base))

    def test_consistency_gate_rejects_completed_but_unchecked_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("plan", "T2", components_for("T2"), behavior_change=True)
            plan = write(
                base / "docs/superpowers/plans/example.md",
                "# Plan\n\n> **执行状态：已完成并归档。**\n\n- [ ] Step 1\n",
            )
            append_evidence(state, {
                "kind": "plan",
                "path": plan,
                "test_strategy": ["unit tests"],
                "source": "superpowers",
            })
            add_review_with_report(state, base)
            add_consistency(state, base)
            ok, reasons = evaluate_gate(state, "consistency_verified", base)
            self.assertFalse(ok)
            self.assertTrue(any("unchecked item" in reason for reason in reasons), reasons)

    def test_consistency_gate_rejects_unchecked_task_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("tasks", "T2", components_for("T2"), behavior_change=True)
            tasks = write(
                base / "openspec/changes/example/tasks.md",
                "# Tasks\n\n- [ ] 1.1 Still open\n",
            )
            append_evidence(state, {"kind": "spec_artifacts", "paths": [tasks]})
            add_plan(state, base)
            add_review_with_report(state, base)
            add_consistency(state, base)
            ok, reasons = evaluate_gate(state, "consistency_verified", base)
            self.assertFalse(ok)
            self.assertTrue(any("tasks.md" in reason and "unchecked" in reason for reason in reasons), reasons)

    def test_consistency_gate_rejects_stale_delivery_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("plan", "T2", components_for("T2"), behavior_change=True)
            add_plan(state, base)
            add_review_with_report(state, base)
            add_consistency(state, base)
            write(base / "deliverables/交付清单.md", "后端测试 / 前端测试 | 48 / 24 通过\n")
            ok, reasons = evaluate_gate(state, "consistency_verified", base)
            self.assertFalse(ok)
            self.assertTrue(any("forbidden pattern still present" in reason for reason in reasons), reasons)

    def test_consistency_gate_passes_after_plan_and_claim_are_fixed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("plan", "T2", components_for("T2"), behavior_change=True)
            add_plan(state, base)
            add_review_with_report(state, base)
            add_consistency(state, base)
            ok, reasons = evaluate_gate(state, "consistency_verified", base)
            self.assertTrue(ok, reasons)

    def test_consistency_gate_resolves_archived_task_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("archive", "T2", components_for("T2"), behavior_change=True)
            tasks = Path(write(
                base / "openspec/changes/example/tasks.md",
                "# Tasks\n\n- [x] 1.1 Done\n",
            ))
            append_evidence(state, {"kind": "spec_artifacts", "paths": [str(tasks)]})
            add_plan(state, base)
            add_review_with_report(state, base)
            add_consistency(state, base)
            archive = base / "openspec/changes/archive/2026-11-23-example"
            archive.parent.mkdir(parents=True, exist_ok=True)
            tasks.parent.rename(archive)
            ok, reasons = evaluate_gate(state, "consistency_verified", base)
            self.assertTrue(ok, reasons)

    def test_consistency_gate_requires_review_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("plan", "T2", components_for("T2"), behavior_change=True)
            add_plan(state, base)
            append_evidence(state, {
                "kind": "review",
                "reviewer": "ecc",
                "findings_resolved": True,
                "security_checked": True,
                "a11y_checked": False,
            })
            add_consistency(state, base)
            ok, reasons = evaluate_gate(state, "consistency_verified", base)
            self.assertFalse(ok)
            self.assertTrue(any("review.report is missing" in reason for reason in reasons), reasons)

    def test_delivery_approval_is_blocked_before_consistency_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("plan", "T2", components_for("T2"), behavior_change=True)
            add_specs(state, base, approval=True)
            add_plan(state, base, approval=True)
            add_review_with_report(state, base)
            add_verification(state, base)
            with self.assertRaises(PrerequisiteError) as raised:
                approve_gate(
                    state,
                    "delivery_approved",
                    base,
                    confirm=lambda _summary, _token: (True, "tty"),
                )
            self.assertIn("consistency_verified", str(raised.exception))

    def test_stale_token_fails_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("ui", "T2", components_for("T2", ui=True), ui=True, behavior_change=True)
            proposal = write(base / "openspec/changes/x/proposal.md", "content\n")
            append_evidence(state, {"kind": "spec_artifacts", "paths": [proposal]})
            state["approval_requests"] = [{
                "gate": "spec_approved",
                "token": "current",
                "requested_at": "2026-11-15T10:00:00+08:00",
                "artifact_digest": approval_digest(state, "spec_approved", base),
            }]
            append_evidence(state, {
                "kind": "approval",
                "gate": "spec_approved",
                "scope": "spec_approved",
                "approved": True,
                "approved_by": "user",
                "explicit": True,
                "token": "old",
                "artifact_digest": approval_digest(state, "spec_approved", base),
                "method": "tty",
                "requested_at": "2026-11-15T10:00:00+08:00",
                "approved_at": "2026-11-15T10:00:01+08:00",
            })
            ok, reasons = valid_approval(state, "spec_approved", base)
            self.assertFalse(ok)
            self.assertTrue(any("stale" in reason for reason in reasons), reasons)

    def test_approve_non_interactive_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state_path = base / ".codex/delivery-state.json"
            state = build_state("ui", "T2", components_for("T2", ui=True), ui=True, behavior_change=True)
            add_specs(state, base)
            save_state(state_path, state)
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "approve.py"),
                    "--gate",
                    "spec_approved",
                    "--state",
                    str(state_path),
                    "--base-dir",
                    str(base),
                ],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                env=dict(os.environ),
                check=False,
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("confirmation is required", result.stderr.lower())
            self.assertIn("ssh -t", result.stderr)

    def test_approve_records_explicit_tty_evidence_when_confirmation_is_stubbed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("ui", "T2", components_for("T2", ui=True), ui=True, behavior_change=True)
            add_specs(state, base)
            approve(state, "spec_approved", base, method="tty")
            approvals = [item for item in state["evidence"] if item["kind"] == "approval"]
            self.assertEqual(approvals[-1]["method"], "tty")
            self.assertTrue(approvals[-1]["explicit"])
            self.assertEqual(approvals[-1]["gate"], "spec_approved")
            self.assertTrue(approvals[-1]["token"])
            self.assertTrue(approvals[-1]["artifact_digest"])

    def test_approve_refuses_failed_prerequisite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("ui", "T2", components_for("T2", ui=True), ui=True, behavior_change=True)
            with self.assertRaises(PrerequisiteError):
                approve_gate(
                    state,
                    "spec_approved",
                    base,
                    confirm=lambda _summary, _token: (True, "tty"),
                )
            self.assertFalse(any(item["kind"] == "approval" for item in state["evidence"]))

    def test_advance_stops_before_spec_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("ui", "T2", components_for("T2", ui=True), ui=True, behavior_change=True)
            add_specs(state, base)
            advance_state(state, base)
            self.assertEqual(state["phase"], "SPEC_READY")
            self.assertEqual(next_human_approval_gate(state, base), "spec_approved")

    def test_advance_stops_before_design_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("ui", "T2", components_for("T2", ui=True), ui=True, behavior_change=True)
            add_specs(state, base)
            approve(state, "spec_approved", base)
            add_design(state, base)
            advance_state(state, base)
            self.assertEqual(state["phase"], "DESIGN_READY")
            self.assertEqual(next_human_approval_gate(state, base), "design_approved")

    def test_advance_stops_before_plan_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("ui", "T2", components_for("T2", ui=True), ui=True, behavior_change=True)
            add_specs(state, base)
            approve(state, "spec_approved", base)
            add_design(state, base)
            approve(state, "design_approved", base)
            add_plan(state, base)
            advance_state(state, base)
            self.assertEqual(state["phase"], "PLAN_READY")
            self.assertEqual(next_human_approval_gate(state, base), "plan_approved")

    def test_advance_stops_before_delivery_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("fix", "T1", components_for("T1"))
            add_plan(state, base)
            add_review_with_report(state, base)
            add_verification(state, base, ui=False)
            advance_state(state, base)
            self.assertEqual(state["phase"], "VERIFIED")
            add_consistency(state, base)
            advance_state(state, base)
            self.assertEqual(state["phase"], "CONSISTENCY_VERIFIED")
            self.assertEqual(next_human_approval_gate(state, base), "delivery_approved")

    def test_t1_delivery_approval_completes_without_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("fix", "T1", components_for("T1"))
            add_plan(state, base)
            add_review_with_report(state, base)
            add_verification(state, base, ui=False)
            advance_state(state, base)
            add_consistency(state, base)
            advance_state(state, base)
            approve(state, "delivery_approved", base)
            results = advance_state(state, base)
            self.assertTrue(all(result["ok"] for result in results.values()), results)
            self.assertEqual(state["phase"], "DELIVERY_APPROVED")

    def test_phase_moves_back_when_approved_spec_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("api", "T2", components_for("T2"), behavior_change=True)
            add_specs(state, base)
            approve(state, "spec_approved", base)
            add_plan(state, base)
            approve(state, "plan_approved", base)
            add_review_with_report(state, base)
            add_verification(state, base, ui=False)
            add_consistency(state, base)
            approve(state, "delivery_approved", base)
            advance_state(state, base)
            self.assertEqual(state["phase"], "DELIVERY_APPROVED")
            Path(base / "openspec/changes/example/proposal.md").write_text("changed spec\n", encoding="utf-8")
            advance_state(state, base)
            self.assertEqual(state["phase"], "SPEC_READY")

    def test_archive_move_preserves_spec_approval_by_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("api", "T2", components_for("T2"), behavior_change=True)
            add_specs(state, base, approval=True)
            before = approval_digest(state, "spec_approved", base)

            source = base / "openspec/changes/example"
            target = base / "openspec/changes/archive/2026-11-15-example"
            target.parent.mkdir(parents=True)
            source.rename(target)

            after = approval_digest(state, "spec_approved", base)
            self.assertEqual(before, after)
            ready, ready_reasons = evaluate_gate(state, "spec_ready", base)
            approved, approval_reasons = valid_approval(state, "spec_approved", base)
            self.assertTrue(ready, ready_reasons)
            self.assertTrue(approved, approval_reasons)

            plugin_dir = base / "plugin"
            plugin_dir.mkdir()
            self.assertEqual(before, approval_digest(state, "spec_approved", plugin_dir))
            ready, ready_reasons = evaluate_gate(state, "spec_ready", plugin_dir)
            approved, approval_reasons = valid_approval(state, "spec_approved", plugin_dir)
            self.assertTrue(ready, ready_reasons)
            self.assertTrue(approved, approval_reasons)

    def test_t0_only_needs_route(self) -> None:
        state = build_state("docs", "T0", components_for("T0"))
        results = evaluate_all(state, ".")
        self.assertTrue(results["route"]["ok"], results)
        self.assertTrue(all(results[gate]["ok"] for gate in results if gate != "route"), results)
        advance_state(state, ".")
        self.assertEqual(state["phase"], "ROUTED")
        self.assertIsNone(next_human_approval_gate(state, "."))

    def test_schema_v1_state_migrates_without_losing_route(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state_path = base / ".codex/delivery-state.json"
            state = build_state("legacy", "T2", components_for("T2"))
            state["schema_version"] = 1
            for key in ("route_status", "route_source", "heuristic", "assessment", "routing_notes"):
                state.pop(key, None)
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

            loaded = load_state(state_path)

            self.assertEqual(loaded["schema_version"], 3)
            self.assertEqual(loaded["route_status"], "assessed")
            self.assertEqual(loaded["route_source"], "legacy")
            passed, reasons = evaluate_gate(loaded, "route", base)
            self.assertTrue(passed, reasons)

    def test_design_gate_rejects_missing_mobile_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("ui", "T2", components_for("T2", ui=True), ui=True, behavior_change=True)
            add_specs(state, base)
            approve(state, "spec_approved", base)
            append_evidence(state, {
                "kind": "ui_direction",
                "style": "minimal",
                "palette": "navy",
                "typography": "sans",
                "layout": "grid",
                "source": "ui-ux-pro-max",
                "design_review_path": write(base / "design-review.md"),
                "desktop_previews": [write(base / "artifacts/design/desktop-home.png", "png")],
                "mobile_previews": [],
                "references": [],
            })
            ok, reasons = evaluate_gate(state, "design_ready", base)
            self.assertFalse(ok)
            self.assertTrue(any("mobile" in reason for reason in reasons), reasons)

    def test_design_gate_rejects_missing_review_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("ui", "T2", components_for("T2", ui=True), ui=True, behavior_change=True)
            add_specs(state, base)
            approve(state, "spec_approved", base)
            append_evidence(state, {
                "kind": "ui_direction",
                "style": "minimal",
                "palette": "navy",
                "typography": "sans",
                "layout": "grid",
                "source": "ui-ux-pro-max",
                "design_review_path": "missing.md",
                "desktop_previews": [write(base / "artifacts/design/desktop-home.png", "png")],
                "mobile_previews": [write(base / "artifacts/design/mobile-home.png", "png")],
                "references": [],
            })
            ok, reasons = evaluate_gate(state, "design_ready", base)
            self.assertFalse(ok)
            self.assertTrue(any("design-review" in reason for reason in reasons), reasons)

    def test_design_approval_invalidates_when_preview_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("ui", "T2", components_for("T2", ui=True), ui=True, behavior_change=True)
            add_specs(state, base)
            approve(state, "spec_approved", base)
            add_design(state, base)
            approve(state, "design_approved", base)
            ok, reasons = valid_approval(state, "design_approved", base)
            self.assertTrue(ok, reasons)
            Path(base / "artifacts/design/desktop-home.png").write_text("changed\n", encoding="utf-8")
            ok, reasons = valid_approval(state, "design_approved", base)
            self.assertFalse(ok)
            self.assertTrue(any("digest" in reason for reason in reasons), reasons)

    def test_component_specific_evidence_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("example", "T2", components_for("T2", ui=True), ui=True)
            append_evidence(state, {
                "kind": "plan",
                "path": write(base / "plan.md"),
                "test_strategy": ["unit tests"],
                "source": "manual",
            })
            ok, reasons = evaluate_gate(state, "plan_ready", base)
            self.assertFalse(ok)
            self.assertIn("plan.source must be superpowers", reasons)

            append_evidence(state, {
                "kind": "ui_direction",
                "style": "manual",
                "palette": "manual",
                "typography": "manual",
                "layout": "manual",
                "source": "manual",
            })
            ok, reasons = evaluate_gate(state, "design_ready", base)
            self.assertFalse(ok)
            self.assertIn("ui_direction.source must be ui-ux-pro-max", reasons)

            append_evidence(state, {
                "kind": "review",
                "reviewer": "someone",
                "findings_resolved": True,
                "security_checked": True,
                "a11y_checked": True,
            })
            ok, reasons = evaluate_gate(state, "reviewed", base)
            self.assertFalse(ok)
            self.assertIn("review.reviewer must identify ecc", reasons)

    def test_auto_inference(self) -> None:
        ui = infer_from_text("实现一个登录页面，桌面和移动端都要好看")
        self.assertTrue(ui["ui"])
        self.assertTrue(ui["behavior_change"])
        self.assertFalse(ui["trivial"])

        documentation = infer_from_text("更新 README 文档和格式")
        self.assertTrue(documentation["trivial"])
        self.assertFalse(documentation["ui"])

    def test_linux_is_not_ui_but_real_ui_is(self) -> None:
        for text in (
            "把插件移植到 Linux 服务器",
            "移植到 Ubuntu 服务器",
            "支持 GNU/Linux",
        ):
            self.assertFalse(infer_from_text(text)["ui"], text)

        for text in (
            "实现一个 UI 页面",
            "优化 UX",
            "使用 ui-ux-pro-max 选择视觉方向",
            "调整组件样式和布局",
        ):
            self.assertTrue(infer_from_text(text)["ui"], text)

    def test_simulation_script(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "simulate.py")],
            cwd=str(PLUGIN_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("Simulation passed", completed.stdout)

    def test_gate_cli_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = base / ".codex/delivery-state.json"
            route = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "route.py"),
                    "--task",
                    "add login page",
                    "--multi-file",
                    "--behavior-change",
                    "--ui",
                    "--state",
                    str(state),
                    "--base-dir",
                    str(base),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(route.returncode, 0, route.stdout + route.stderr)

            before = subprocess.run(
                [sys.executable, str(SCRIPTS / "gate.py"), "--state", str(state), "--base-dir", str(base)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(before.returncode, 1, before.stdout + before.stderr)
            self.assertIn("spec_ready", before.stdout)

            paths = [
                write(base / "openspec/changes/add-login/proposal.md"),
                write(base / "openspec/changes/add-login/design.md"),
                write(base / "openspec/changes/add-login/tasks.md"),
                write(base / "openspec/changes/add-login/specs/user-auth/spec.md"),
            ]
            record = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "record.py"),
                    "--state",
                    str(state),
                    "--base-dir",
                    str(base),
                    "--kind",
                    "spec_artifacts",
                    "--data",
                    json.dumps({"paths": paths}),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(record.returncode, 0, record.stdout + record.stderr)

            after = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "gate.py"),
                    "--state",
                    str(state),
                    "--base-dir",
                    str(base),
                    "--gate",
                    "spec_ready",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(after.returncode, 0, after.stdout + after.stderr)

    def test_route_cli_auto_from_chinese_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state_path = base / ".codex/delivery-state.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "route.py"),
                    "--task",
                    "实现一个登录页面，桌面和移动端都要好看",
                    "--auto",
                    "--state",
                    str(state_path),
                    "--base-dir",
                    str(base),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["tier"], "T2")
            self.assertTrue(state["ui"])
            self.assertTrue(state["behavior_change"])
            self.assertIn("ui-ux-pro-max", state["components"])

    def test_strip_chinese_entry_phrase(self) -> None:
        self.assertEqual(strip_entry_phrase("编码总控：实现登录页"), "实现登录页")
        self.assertEqual(strip_entry_phrase("  编码总控，优化移动端布局  "), "优化移动端布局")
        self.assertEqual(strip_entry_phrase("实现登录页"), "实现登录页")

    def test_route_cli_accepts_raw_chinese_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state_path = base / ".codex/delivery-state.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "route.py"),
                    "--task",
                    "编码总控 实现一个登录页面，桌面和移动端都要好看",
                    "--state",
                    str(state_path),
                    "--base-dir",
                    str(base),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["task"], "实现一个登录页面，桌面和移动端都要好看")
            self.assertEqual(state["tier"], "T2")
            self.assertTrue(state["ui"])
            self.assertTrue(state["behavior_change"])
            self.assertIn("ui-ux-pro-max", state["components"])

    def test_route_rejects_empty_chinese_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state_path = base / ".codex/delivery-state.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "route.py"),
                    "--task",
                    "编码总控：   ",
                    "--state",
                    str(state_path),
                    "--base-dir",
                    str(base),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
            self.assertIn("需要描述", completed.stderr)
            self.assertFalse(state_path.exists())

    def test_prompt_hook_ignores_regular_message(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            completed = run_prompt_hook("实现登录页", Path(temporary))
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.strip(), "")

    def test_prompt_hook_asks_for_missing_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            completed = run_prompt_hook("编码总控：", base)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            output = json.loads(completed.stdout)
            context = output["hookSpecificOutput"]["additionalContext"]
            self.assertIn("请描述你要实现或修改的内容", context)
            self.assertFalse((base / ".codex/delivery-state.json").exists())

    def test_prompt_hook_routes_and_injects_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            completed = run_prompt_hook(
                "编码总控 实现一个登录页面，桌面和移动端都要好看",
                base,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            output = json.loads(completed.stdout)
            self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
            context = output["hookSpecificOutput"]["additionalContext"]
            self.assertIn("Route: pending assessment | heuristic T2", context)
            self.assertIn("Next required gate: route", context)
            self.assertIn("--assess-json", context)
            self.assertIn("<delivery-conductor-skill>", context)
            self.assertIn("不得自行写审批证据", context)
            self.assertIn("所有生成的文档默认使用简体中文", context)
            state = json.loads((base / ".codex/delivery-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["task"], "实现一个登录页面，桌面和移动端都要好看")
            self.assertEqual(state["tier"], "T2")
            self.assertEqual(state["phase"], "ROUTING")
            self.assertEqual(state["route_status"], "provisional")
            self.assertIn("ui-ux-pro-max", state["components"])

    def test_prompt_hook_uses_platform_python_command(self) -> None:
        from hook_user_prompt import python_command

        command = python_command(PLUGIN_ROOT / "scripts/route.py")
        self.assertIn("route.py", command)
        if os.name == "nt":
            self.assertTrue(command.startswith("py -3 "))
        else:
            self.assertTrue(command.startswith("python3 "))

    def test_prompt_hook_never_emits_traceback(self) -> None:
        import hook_user_prompt

        payload = {"cwd": str(PLUGIN_ROOT), "prompt": "编码总控 测试内部异常"}
        stdout = io.StringIO()
        with (
            patch.object(hook_user_prompt, "build_context", side_effect=RuntimeError("boom")),
            patch("sys.stdin", io.StringIO(json.dumps(payload, ensure_ascii=False))),
            patch("sys.stdout", stdout),
        ):
            self.assertEqual(hook_user_prompt.main(), 0)
        text = stdout.getvalue()
        self.assertNotIn("Traceback", text)
        self.assertIn("boom", text)
        self.assertIn("Hook 内部错误", text)

    def test_prompt_hook_creates_provisional_semantic_assessment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            completed = run_prompt_hook("编码总控 修改启动按钮文案", base)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            context = json.loads(completed.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("provisional", context.lower())
            self.assertIn("--assess-json", context)
            self.assertIn("语义", context)

            state = json.loads((base / ".codex/delivery-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["phase"], "ROUTING")
            self.assertEqual(state["route_status"], "provisional")
            self.assertEqual(state["heuristic"]["tier"], "T1")

    def test_semantic_assessment_corrects_copy_change_to_t0(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state_path = base / ".codex/delivery-state.json"
            provisional = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "route.py"),
                    "--task",
                    "修改启动按钮文案",
                    "--provisional",
                    "--state",
                    str(state_path),
                    "--base-dir",
                    str(base),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(provisional.returncode, 0, provisional.stdout + provisional.stderr)

            assessment = {
                "summary": "只修改按钮显示文案，不改变交互或业务行为",
                "scope": "trivial",
                "behavior_change": False,
                "ui": True,
                "design_change": False,
                "new_capability": False,
                "data_model_change": False,
                "multi_page": False,
                "high_risk": False,
                "affected_areas": ["src/mame_launcher/ui/main_window.py"],
                "reasons": ["现有按钮文案替换，无需新增流程或改变状态"],
                "confidence": "high",
            }
            assessed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "route.py"),
                    "--assess-json",
                    json.dumps(assessment, ensure_ascii=False),
                    "--state",
                    str(state_path),
                    "--base-dir",
                    str(base),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(assessed.returncode, 0, assessed.stdout + assessed.stderr)
            self.assertIn("Route: T0", assessed.stdout)

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["tier"], "T0")
            self.assertEqual(state["phase"], "ROUTED")
            self.assertEqual(state["route_status"], "assessed")
            self.assertEqual(state["route_source"], "model_assessment")
            passed, reasons = evaluate_gate(state, "route", base)
            self.assertTrue(passed, reasons)

    def test_semantic_assessment_promotes_new_capability_to_t2(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state_path = base / ".codex/delivery-state.json"
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "route.py"),
                    "--task",
                    "添加导出功能",
                    "--provisional",
                    "--state",
                    str(state_path),
                    "--base-dir",
                    str(base),
                ],
                check=True,
            )

            assessment = {
                "summary": "为现有游戏库增加可用的 ROM 导出能力",
                "scope": "multi_module",
                "behavior_change": True,
                "ui": True,
                "design_change": False,
                "new_capability": True,
                "data_model_change": False,
                "multi_page": False,
                "high_risk": False,
                "affected_areas": ["src/mame_launcher/ui", "src/mame_launcher/services/export.py"],
                "reasons": ["新增导出命令并连接现有界面入口"],
                "confidence": "high",
            }
            assessed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "route.py"),
                    "--assess-json",
                    json.dumps(assessment, ensure_ascii=False),
                    "--state",
                    str(state_path),
                    "--base-dir",
                    str(base),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(assessed.returncode, 0, assessed.stdout + assessed.stderr)
            self.assertIn("Route: T2", assessed.stdout)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["heuristic"]["tier"], "T1")
            self.assertEqual(state["tier"], "T2")
            self.assertTrue(state["requires"]["plan_approval"])

    def test_semantic_assessment_rejects_low_confidence_and_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state_path = base / ".codex/delivery-state.json"
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "route.py"),
                    "--task",
                    "实现一个登录页面",
                    "--provisional",
                    "--state",
                    str(state_path),
                    "--base-dir",
                    str(base),
                ],
                check=True,
            )
            assessment = {
                "summary": "尚不清楚是否需要新会话流程",
                "scope": "localized",
                "behavior_change": True,
                "ui": True,
                "design_change": False,
                "new_capability": False,
                "data_model_change": False,
                "multi_page": False,
                "high_risk": False,
                "affected_areas": ["src/ui/login.py"],
                "reasons": ["需求存在歧义"],
                "confidence": "low",
            }
            low_confidence = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "route.py"),
                    "--assess-json",
                    json.dumps(assessment, ensure_ascii=False),
                    "--state",
                    str(state_path),
                    "--base-dir",
                    str(base),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(low_confidence.returncode, 0)
            self.assertIn("confidence", low_confidence.stderr)

            assessment["confidence"] = "high"
            assessment["data_model_change"] = True
            assessment["declared_tier"] = "T1"
            conflict = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "route.py"),
                    "--assess-json",
                    json.dumps(assessment, ensure_ascii=False),
                    "--state",
                    str(state_path),
                    "--base-dir",
                    str(base),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(conflict.returncode, 0)
            self.assertIn("declared_tier", conflict.stderr)

    def test_provisional_route_blocks_implementation_but_allows_assessment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state(
                "修改启动按钮文案",
                "T1",
                components_for("T1", ui=True, behavior_change=True),
                ui=True,
                behavior_change=True,
                route_status="provisional",
                heuristic={"tier": "T1", "flags": {"ui": True}},
            )
            save_state(base / ".codex/delivery-state.json", state)
            blocked = guard_output(
                base,
                tool_name="Bash",
                command="touch src/app.py",
            )
            self.assertEqual(blocked["hookSpecificOutput"]["permissionDecision"], "deny")
            self.assertIn("ROUTING", blocked["hookSpecificOutput"]["permissionDecisionReason"])

            allowed = guard_output(
                base,
                tool_name="Bash",
                command=(
                    f"python3 {SCRIPTS}/route.py --assess-json "
                    "'{\"scope\":\"trivial\",\"confidence\":\"high\"}'"
                ),
            )
            self.assertNotIn("permissionDecision", allowed.get("hookSpecificOutput", {}))

            approval = guard_output(
                base,
                tool_name="Bash",
                command=f"python3 {SCRIPTS}/approve.py --gate spec_approved",
            )
            self.assertEqual(approval["hookSpecificOutput"]["permissionDecision"], "deny")
            self.assertIn("ROUTING", approval["hookSpecificOutput"]["permissionDecisionReason"])

    def test_pretool_blocks_edit_during_spec_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("ui", "T2", components_for("T2", ui=True), ui=True, behavior_change=True)
            add_specs(state, base)
            save_state(base / ".codex/delivery-state.json", state)
            output = run_hook("guard_tool.py", {
                "hook_event_name": "PreToolUse",
                "tool_name": "Write",
                "tool_input": {"file_path": str(base / "src/app.py")},
                "cwd": str(base),
                "session_id": "test",
                "turn_id": "test",
            })
            self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
            self.assertIn("spec_approved", output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_pretool_allows_approve_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("ui", "T2", components_for("T2", ui=True), ui=True, behavior_change=True)
            add_specs(state, base)
            save_state(base / ".codex/delivery-state.json", state)
            output = run_hook("guard_tool.py", {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": f"python3 {SCRIPTS}/approve.py --gate spec_approved"},
                "cwd": str(base),
                "session_id": "test",
                "turn_id": "test",
            })
            self.assertNotIn("permissionDecision", output.get("hookSpecificOutput", {}))

    def test_pretool_blocks_unknown_shell_and_allows_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("ui", "T2", components_for("T2", ui=True), ui=True, behavior_change=True)
            add_specs(state, base)
            save_state(base / ".codex/delivery-state.json", state)
            blocked = run_hook("guard_tool.py", {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "touch src/app.py"},
                "cwd": str(base),
            })
            self.assertEqual(blocked["hookSpecificOutput"]["permissionDecision"], "deny")
            disguised = run_hook("guard_tool.py", {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "touch approve.py"},
                "cwd": str(base),
            })
            self.assertEqual(disguised["hookSpecificOutput"]["permissionDecision"], "deny")
            allowed = run_hook("guard_tool.py", {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "cat openspec/changes/example/proposal.md"},
                "cwd": str(base),
            })
            self.assertNotIn("permissionDecision", allowed.get("hookSpecificOutput", {}))

    def test_pretool_blocks_commit_during_delivery_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("fix", "T1", components_for("T1"))
            add_plan(state, base)
            add_review_with_report(state, base)
            add_verification(state, base, ui=False)
            advance_state(state, base)
            add_consistency(state, base)
            advance_state(state, base)
            save_state(base / ".codex/delivery-state.json", state)
            output = run_hook("guard_tool.py", {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "git commit -m done"},
                "cwd": str(base),
            })
            self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
            self.assertIn("delivery_approved", output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_hook_config_declares_prompt_trigger_and_pretool_guard(self) -> None:
        hooks = json.loads((PLUGIN_ROOT / "hooks/codex-hooks.json").read_text(encoding="utf-8"))
        self.assertIn("UserPromptSubmit", hooks["hooks"])
        prompt_hooks = hooks["hooks"]["UserPromptSubmit"]
        self.assertTrue(any("hook_user_prompt.py" in item["hooks"][0]["command"] for item in prompt_hooks))
        self.assertIn("PreToolUse", hooks["hooks"])
        pretool_hooks = hooks["hooks"]["PreToolUse"]
        self.assertTrue(any("guard_tool.py" in item["hooks"][0]["command"] for item in pretool_hooks))
        self.assertEqual(pretool_hooks[0]["matcher"], "*")

    def test_hook_manifest_has_platform_neutral_python_entries(self) -> None:
        hooks = json.loads((PLUGIN_ROOT / "hooks/codex-hooks.json").read_text(encoding="utf-8"))
        expected = {
            "UserPromptSubmit": "hook_user_prompt.py",
            "PreToolUse": "guard_tool.py",
            "SessionStart": "hook_session_start.py",
        }
        for event, script in expected.items():
            handlers = [item for group in hooks["hooks"][event] for item in group["hooks"]]
            self.assertEqual(len(handlers), 1, event)
            handler = handlers[0]
            self.assertEqual(handler["type"], "command", event)
            self.assertIn(script, handler["command"], event)
            self.assertIn(script, handler["commandWindows"], event)
            self.assertIn('python3 "${PLUGIN_ROOT}/scripts/', handler["command"], event)
            self.assertIn('py -3 "${PLUGIN_ROOT}/scripts/', handler["commandWindows"], event)
            self.assertNotIn("sh -c", handler["command"], event)
            self.assertNotIn("2>/dev/null", handler["command"], event)
            self.assertNotIn("|| true", handler["command"], event)
            self.assertNotIn("${PLUGIN_ROOT:-.}", handler["command"], event)
            self.assertNotIn("${PLUGIN_ROOT:-.}", handler["commandWindows"], event)

    def test_hook_manifest_current_platform_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            cases = {
                "UserPromptSubmit": {
                    "cwd": str(base),
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "编码总控",
                    "session_id": "smoke-session",
                },
                "PreToolUse": {
                    "cwd": str(base),
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": "git status"},
                },
                "SessionStart": {
                    "cwd": str(base),
                    "hook_event_name": "SessionStart",
                    "session_id": "smoke-session",
                },
            }
            for event, payload in cases.items():
                completed = run_manifest_hook(event, payload, base)
                self.assertEqual(completed.returncode, 0, event + completed.stdout + completed.stderr)
                if completed.stdout.strip():
                    output = json.loads(completed.stdout)
                    self.assertEqual(output["hookSpecificOutput"]["hookEventName"], event)
                else:
                    self.assertEqual(event, "PreToolUse")

    def test_docs_describe_windows_python_entrypoint(self) -> None:
        readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
        skill = (PLUGIN_ROOT / "skills/delivery-conductor/SKILL.md").read_text(encoding="utf-8")
        for text in (readme, skill):
            self.assertIn("Windows", text)
            self.assertIn("py -3", text)
            self.assertIn("commandWindows", text)
        self.assertIn("0.171.0", readme)

    def test_skill_documents_human_approval_protocol(self) -> None:
        skill = (PLUGIN_ROOT / "skills/delivery-conductor/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("编码总控", skill)
        self.assertIn("--auto", skill)
        for gate in ("spec_approved", "design_approved", "plan_approved", "delivery_approved"):
            self.assertIn(gate, skill)
        self.assertIn("approve.py", skill)
        self.assertIn("不得自行写审批证据", skill)
        self.assertIn("design-review.md", skill)
        self.assertIn("390px", skill)
        self.assertIn("## 进度汇报契约（强制）", skill)
        self.assertIn("**已完成**", skill)
        self.assertIn("**需要你做**", skill)
        self.assertIn("**下一步**", skill)
        self.assertIn("Host Trust Preflight", skill)
        self.assertIn("host_trust.py", skill)
        self.assertIn("Trust all", skill)
        self.assertIn("Pre-implementation Write Isolation", skill)
        self.assertIn("artifacts/design/", skill)
        self.assertIn("## 文档语言契约（强制）", skill)
        self.assertIn("OpenSpec、Superpowers、ECC 和 UI UX Pro Max", skill)
        self.assertIn("代码、命令、文件路径", skill)
        self.assertIn("consistency_verified", skill)
        self.assertIn("consistency_audit", skill)
        self.assertIn("open_findings", skill)
        self.assertIn("执行状态", skill)
        readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("host_trust.py", readme)
        self.assertIn("Trust all", readme)
        self.assertIn("计划批准前的写入隔离", readme)
        self.assertIn("artifacts/design/", readme)
        manifest = json.loads((PLUGIN_ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertTrue(any("编码总控" in prompt for prompt in manifest["interface"]["defaultPrompt"]))
        self.assertEqual(manifest["version"], "0.5.0")
        marketplace = json.loads((PLUGIN_ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(marketplace["plugins"][0]["version"], manifest["version"])


class PreImplementationGuardTests(unittest.TestCase):
    def assert_denied(self, output: dict, label: str = "") -> None:
        self.assertEqual(
            output.get("hookSpecificOutput", {}).get("permissionDecision"),
            "deny",
            label,
        )

    def assert_allowed(self, output: dict, label: str = "") -> None:
        self.assertNotIn("permissionDecision", output.get("hookSpecificOutput", {}), label)

    def spec_approved_state(self, base: Path) -> dict:
        state = build_state("ui", "T2", components_for("T2", ui=True), ui=True, behavior_change=True)
        add_specs(state, base, approval=True)
        return state

    def plan_approved_state(self, base: Path) -> dict:
        state = build_state("ui", "T2", components_for("T2", ui=True), ui=True, behavior_change=True)
        add_specs(state, base, approval=True)
        add_design(state, base, approval=True)
        add_plan(state, base, approval=True)
        return state

    def test_windows_read_only_commands_are_allowed(self) -> None:
        for command in (
            "git status --short",
            "Get-Content 'C:\\A B\\proposal.md'",
            "Select-String -Pattern trust -Path C:\\A B\\proposal.md",
            "Get-ChildItem C:\\A B",
            "Test-Path C:\\A B\\proposal.md",
            "Resolve-Path C:\\A B\\proposal.md",
        ):
            args = split_command(command, platform="nt")
            self.assertTrue(allowed_read_only(args, platform="nt"), command)

    def test_windows_write_command_is_rejected(self) -> None:
        args = split_command("Set-Content C:\\A B\\proposal.md changed", platform="nt")
        self.assertFalse(allowed_read_only(args, platform="nt"))

    def test_windows_shell_wrapper_is_rejected(self) -> None:
        command = "powershell -NoProfile -Command Get-Content C:\\A B\\proposal.md"
        self.assertFalse(routing_inspection_command_allowed(command, platform="nt"))
        where_command = "where -InputObject 1 { Remove-Item -Recurse -Force C:\\project }"
        self.assertFalse(allowed_read_only(split_command(where_command, platform="nt"), platform="nt"))
        scriptblock_command = "Get-Content { Remove-Item -Recurse -Force C:\\project }"
        self.assertFalse(allowed_read_only(split_command(scriptblock_command, platform="nt"), platform="nt"))

    def test_windows_python_launcher_handshake_and_assessment_are_allowed(self) -> None:
        self.assertTrue(routing_inspection_command_allowed("py -3 scripts/gate.py", platform="nt"))
        self.assertTrue(shell_allowed("py -3 scripts/gate.py", platform="nt"))
        command = "py -3 scripts/route.py --assess-json '{}' --state state.json --base-dir ."
        self.assertTrue(semantic_assessment_command_allowed(command, platform="nt"))

    def test_blocks_implementation_shell_before_plan_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = self.spec_approved_state(base)
            launcher = "py -3" if os.name == "nt" else "python3"
            commands = [
                "touch src/app.py",
                "mkdir -p src",
                "npm install",
                "cargo build",
                f"{launcher} build.py",
                f"{launcher} -c \"open('src/app.py', 'w')\"",
                f"{launcher} -c'touch src/app.py' {SCRIPTS / 'approve.py'}",
                f"{launcher} -cmalicious {SCRIPTS / 'approve.py'}",
                f"{launcher} -Icmalicious {SCRIPTS / 'approve.py'}",
                f"{launcher} -mjson.tool {SCRIPTS / 'approve.py'}",
                f"{launcher} -Imjson.tool {SCRIPTS / 'approve.py'}",
                "bash -lc 'touch src/app.py'",
            ]
            for command in commands:
                self.assert_denied(guard_output(base, state, command=command), command)

    def test_allows_artifact_shell_before_plan_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            subprocess.run(
                ["git", "init", "-q", str(base)],
                capture_output=True,
                check=False,
            )
            state = self.spec_approved_state(base)
            chrome_cdp = Path.home() / ".codex/tools/chrome-cdp.mjs"
            ui_search = Path.home() / ".agents/skills/ui-ux-pro-max/scripts/search.py"
            launcher = "py -3" if os.name == "nt" else "python3"
            commands = [
                f'node "{chrome_cdp}" shot artifacts/design/desktop.png',
                f'{launcher} "{ui_search}" "dashboard" --domain style',
                "git status",
            ]
            if os.name != "nt":
                commands.extend([
                    "touch openspec/changes/example/notes.md",
                    "mkdir -p artifacts/design/references",
                    "cp fixtures/reference.png artifacts/design/references/reference.png",
                    "touch docs/superpowers/plans/plan.md",
                    "cat src/app.py",
                    "sed -n '1,20p' src/app.py",
                    "find . -maxdepth 2 -type f -print",
                ])
            if sys.platform == "darwin":
                commands.append("sips -s format png fixtures/input.png --out artifacts/design/desktop.png")
            for command in commands:
                self.assert_allowed(guard_output(base, state, command=command), command)

    def test_write_tools_are_path_scoped_before_plan_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = self.spec_approved_state(base)
            blocked = [
                {"file_path": "src/app.py"},
                {"file_path": ".codex/delivery-state.json"},
                {},
            ]
            for tool_input in blocked:
                self.assert_denied(
                    guard_output(base, state, tool_name="Write", tool_input=tool_input),
                    str(tool_input),
                )
            allowed = [
                {"file_path": "openspec/changes/example/design.md"},
                {"file_path": "docs/plans/plan.md"},
                {"file_path": "artifacts/design/design-review.md"},
            ]
            for tool_input in allowed:
                self.assert_allowed(
                    guard_output(base, state, tool_name="Write", tool_input=tool_input),
                    str(tool_input),
                )

    def test_apply_patch_is_path_scoped_before_plan_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = self.spec_approved_state(base)
            mixed = """*** Begin Patch
*** Update File: openspec/changes/example/design.md
@@
-old
+new
*** Add File: src/app.py
+print('x')
*** End Patch"""
            self.assert_denied(
                guard_output(base, state, tool_name="apply_patch", tool_input={"input": mixed})
            )
            allowed = """*** Begin Patch
*** Add File: openspec/changes/example/notes.md
+note
*** End Patch"""
            self.assert_allowed(
                guard_output(base, state, tool_name="apply_patch", tool_input={"input": allowed})
            )
            blocked_shell = "apply_patch <<'PATCH'\n*** Begin Patch\n*** Update File: src/app.py\n@@\n-x\n+y\n*** End Patch\nPATCH"
            self.assert_denied(guard_output(base, state, command=blocked_shell))
            allowed_shell = "apply_patch <<'PATCH'\n*** Begin Patch\n*** Add File: openspec/changes/example/notes.md\n+note\n*** End Patch\nPATCH"
            self.assert_allowed(guard_output(base, state, command=allowed_shell))
            chained = "apply_patch <<'PATCH'\n*** Begin Patch\n*** Add File: openspec/x.md\n+x\n*** End Patch\nPATCH && touch src/app.py"
            self.assert_denied(guard_output(base, state, command=chained))
            appended = "apply_patch <<'PATCH'\n*** Begin Patch\n*** Add File: openspec/x.md\n+x\n*** End Patch\nPATCH\ntouch src/app.py"
            self.assert_denied(guard_output(base, state, command=appended))
            replaced = "apply_patch <<'PATCH'\n*** Begin Patch\n*** Add File: openspec/x.md\n+x\n*** End Patch\nPATCH & touch src/app.py"
            self.assert_denied(guard_output(base, state, command=replaced))
            self.assert_denied(guard_output(base, state, command="/tmp/evil/apply_patch <<'PATCH'\n*** Begin Patch\n*** Add File: openspec/x.md\n+x\n*** End Patch\nPATCH"))
            unquoted = "apply_patch <<PATCH\n*** Begin Patch\n*** Add File: openspec/x.md\n+$(touch src/app.py)\n*** End Patch\nPATCH"
            self.assert_denied(guard_output(base, state, command=unquoted))
            double_quoted = 'apply_patch <<"PATCH"\n*** Begin Patch\n*** Add File: openspec/x.md\n+$(touch src/app.py)\n*** End Patch\nPATCH'
            self.assert_denied(guard_output(base, state, command=double_quoted))
            namespaced = (
                "*** Begin Patch\n*** Add File: openspec/changes/example/notes.md\n+note\n*** "
                "End Patch"
            )
            for tool_name in ("mcp__evil__patch", "functions.apply_patch", "evil.patch"):
                self.assert_denied(
                    guard_output(base, state, tool_name=tool_name, tool_input={"input": namespaced}),
                    tool_name,
                )

    def test_copied_executables_cannot_impersonate_allowlisted_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = self.spec_approved_state(base)
            fake = base / "artifacts/design/cat"
            fake.parent.mkdir(parents=True, exist_ok=True)
            fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake.chmod(0o755)
            commands = [
                f'"{fake}" -c "touch src/app.py"',
                '"./artifacts/design/cat" README.md',
                "/bin/cat README.md",
                f'"{sys.executable}" "{SCRIPTS / "approve.py"}"',
                "artifacts/design/apply_patch <<'PATCH'\n*** Begin Patch\n*** Add File: openspec/x.md\n+x\n*** End Patch\nPATCH",
            ]
            for command in commands:
                self.assert_denied(guard_output(base, state, command=command), command)

    def test_imagemagick_output_operators_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = self.spec_approved_state(base)
            commands = [
                "magick fixtures/input.png +write png:src/app.py artifacts/design/out.png",
                "magick fixtures/input.png -write src/app.py artifacts/design/out.png",
                "convert fixtures/input.png +write /tmp/outside.png artifacts/design/out.png",
                "magick artifacts/design/evil.msl artifacts/design/out.png",
                "magick mvg:artifacts/design/evil.txt artifacts/design/out.png",
                "magick MVG:artifacts/design/evil.txt[0] artifacts/design/out.png",
                "magick -process module artifacts/design/out.png",
            ]
            for command in commands:
                self.assert_denied(guard_output(base, state, command=command), command)
            self.assertTrue(
                image_command_allowed(
                    "magick",
                    ["magick", "fixtures/input.png", "artifacts/design/out.png"],
                    base,
                )
            )
            for args in (
                ["magick", "fixtures/input.png", "+write", "png:src/app.py", "artifacts/design/out.png"],
                ["convert", "fixtures/input.png", "-write", "src/app.py", "artifacts/design/out.png"],
                ["magick", "artifacts/design/evil.msl", "artifacts/design/out.png"],
                ["magick", "artifacts/design/evil.msl[0]", "artifacts/design/out.png"],
                ["magick", "artifacts/design/evil.MVG[0]", "artifacts/design/out.png"],
                ["magick", "mvg:artifacts/design/evil.txt", "artifacts/design/out.png"],
                ["magick", "MSL:artifacts/design/evil.txt[0]", "artifacts/design/out.png"],
                ["magick", "-process", "module", "artifacts/design/out.png"],
            ):
                self.assertFalse(image_command_allowed(args[0], args, base), " ".join(args))

    def test_file_compile_options_are_rejected(self) -> None:
        self.assertTrue(allowed_read_only(["file", "src/app.py"], platform="posix"))
        self.assertTrue(allowed_read_only(["file", "-b", "src/app.py"], platform="posix"))
        self.assertTrue(allowed_read_only(["file", "-bi", "--mime", "src/app.py"], platform="posix"))
        denied = [
            ["file", "-C", "-m", "magic", "src/app.py"],
            ["file", "-bC", "src/app.py"],
            ["file", "-bCm", "magic", "src/app.py"],
            ["file", "--compile", "src/app.py"],
            ["file", "--comp", "src/app.py"],
            ["file", "--magic-file", "magic", "src/app.py"],
        ]
        for args in denied:
            self.assertFalse(allowed_read_only(args, platform="posix"), " ".join(args))

    def test_cp_and_mv_directory_destinations_follow_existing_symlinks(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX cp/mv command semantics are verified on POSIX CI runners")
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as outside:
            base = Path(temporary)
            state = self.spec_approved_state(base)
            destination = base / "artifacts/design"
            destination.mkdir(parents=True)
            outside_file = Path(outside) / "a.png"
            outside_file.write_text("outside\n", encoding="utf-8")
            try:
                (destination / "a.png").symlink_to(outside_file)
                (destination / "a\\b.png").symlink_to(outside_file)
                (destination / "escape").symlink_to(Path(outside), target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlink unavailable: {error}")
            commands = [
                "cp fixtures/a.png artifacts/design",
                "cp -t artifacts/design fixtures/a.png",
                "cp -R fixtures/escape artifacts/design",
                "cp -R fixtures/dir artifacts/design",
                "cp 'a\\b.png' artifacts/design",
                "mv fixtures/a.png artifacts/design",
            ]
            for command in commands:
                self.assert_denied(guard_output(base, state, command=command), command)
            self.assert_allowed(
                guard_output(base, state, command="cp -R fixtures/dir artifacts/design/new")
            )

    def test_cp_and_mv_only_write_into_artifact_roots(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX cp/mv command semantics are verified on POSIX CI runners")
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = self.spec_approved_state(base)
            self.assert_allowed(guard_output(base, state, command="cp -t artifacts/design fixtures/a.png"))
            self.assert_allowed(guard_output(base, state, command="cp -vt artifacts/design fixtures/a.png"))
            self.assert_denied(guard_output(base, state, command="cp -t src fixtures/a.png"))
            self.assert_denied(guard_output(base, state, command="cp -t /tmp/outside fixtures/a.png"))
            self.assert_denied(
                guard_output(base, state, command="cp --target-dir=/tmp/outside fixtures/a.png artifacts/design/a.png")
            )
            self.assert_denied(
                guard_output(base, state, command="cp --targ=src fixtures/a.png artifacts/design/a.png")
            )
            self.assert_denied(
                guard_output(base, state, command="cp --parents ../../../outside/evil artifacts/design/out")
            )
            self.assert_allowed(
                guard_output(base, state, command="mv artifacts/design/a.png artifacts/design/b.png")
            )
            self.assert_denied(guard_output(base, state, command="mv /tmp/a.png artifacts/design/a.png"))
            self.assert_denied(
                guard_output(base, state, command="mv --target=/tmp/outside artifacts/design/a.png artifacts/design/b.png")
            )
            self.assert_denied(
                guard_output(base, state, command="mv -vt src artifacts/design/app.py")
            )
            self.assert_denied(guard_output(base, state, command="mv src/app.py artifacts/design/app.py"))

    def test_generators_are_narrow_before_plan_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = self.spec_approved_state(base)
            chrome_cdp = Path.home() / ".codex/tools/chrome-cdp.mjs"
            self.assert_allowed(
                guard_output(base, state, command=f'node "{chrome_cdp}" goto https://example.com')
            )
            with patch.dict(os.environ, {"NODE_OPTIONS": "--require /tmp/evil.js"}):
                self.assert_denied(
                    guard_output(base, state, command=f'node "{chrome_cdp}" goto https://example.com')
                )
            self.assert_denied(
                guard_output(base, state, command=f'node "{chrome_cdp}" shot src/app.png')
            )
            self.assert_denied(guard_output(base, state, command="python3 /tmp/evil.py"))
            self.assert_denied(guard_output(base, state, command="node /tmp/evil/chrome-cdp.mjs goto https://example.com"))
            self.assert_denied(
                guard_output(base, state, command="python3 /tmp/evil/ui-ux-pro-max/scripts/search.py dashboard")
            )
            self.assert_denied(guard_output(base, state, command="python3 scripts/gate.py"))
            self.assert_denied(guard_output(base, state, command="python3 /tmp/evil/route.py --assess-json '{}'"))
            ui_search = Path.home() / ".agents/skills/ui-ux-pro-max/scripts/search.py"
            launcher = "py -3" if os.name == "nt" else "python3"
            for suffix in ("--persist --output-dir /tmp/outside", "-o/tmp/outside", "--force"):
                self.assert_denied(
                    guard_output(base, state, command=f'{launcher} "{ui_search}" "dashboard" {suffix}')
                )
            self.assert_denied(
                guard_output(base, state, command=f'{launcher} "{ui_search}" "dashboard" --pe')
            )

    def test_unknown_tools_fail_closed_before_plan_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = self.spec_approved_state(base)
            self.assert_denied(
                guard_output(base, state, tool_name="mcp__git__commit", tool_input={})
            )
            self.assert_allowed(
                guard_output(base, state, tool_name="update_plan", tool_input={})
            )

    def test_generic_write_tool_cannot_switch_to_patch_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = self.spec_approved_state(base)
            patch_text = "*** Begin Patch\n*** Add File: openspec/changes/example/notes.md\n+x\n*** End Patch"
            self.assert_denied(
                guard_output(
                    base,
                    state,
                    tool_name="Write",
                    tool_input={"file_path": "src/app.py", "input": patch_text},
                )
            )

    def test_unknown_write_tools_and_decoy_paths_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = self.spec_approved_state(base)
            patch_text = (
                "*** Begin Patch\n*** Add File: src/app.py\n+x\n*** "
                "End Patch"
            )
            for tool_name in ("mcp__evil__write", "functions.write", "write_all", "mcp__evil__create_file"):
                self.assert_denied(
                    guard_output(
                        base,
                        state,
                        tool_name=tool_name,
                        tool_input={"file_path": "openspec/changes/example/notes.md", "input": patch_text},
                    ),
                    tool_name,
                )
            self.assert_denied(
                guard_output(
                    base,
                    state,
                    tool_name="move_file",
                    tool_input={"source": "src/app.py", "destination": "openspec/changes/example/moved.py"},
                )
            )
            self.assert_denied(
                guard_output(
                    base,
                    state,
                    tool_name="move_file",
                    tool_input={"source": "artifacts/design/a.png"},
                )
            )
            self.assert_allowed(
                guard_output(
                    base,
                    state,
                    tool_name="write_file",
                    tool_input={"file_path": "openspec/changes/example/notes.md"},
                )
            )
            self.assert_allowed(
                guard_output(
                    base,
                    state,
                    tool_name="move_file",
                    tool_input={"source": "artifacts/design/a.png", "destination": "artifacts/design/b.png"},
                )
            )

    def test_artifact_writes_cannot_escape_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as outside:
            base = Path(temporary)
            outside_file = Path(outside) / "preview.png"
            state = self.spec_approved_state(base)
            commands = [
                f'touch "{outside_file}"',
                f'mkdir -p "{Path(outside) / "preview"}"',
                f'sips -s format png fixtures/input.png --out "{outside_file}"',
                f'cp fixtures/input.png "{outside_file}"',
                f'touch artifacts/design/{{safe,{outside_file}}}',
                'touch artifacts/design/$HOME/pwn.md',
                'cp fixtures/input.png artifacts/design/${TARGET:-../../src/pwn}.md',
            ]
            for command in commands:
                self.assert_denied(guard_output(base, state, command=command), command)
            self.assert_denied(
                guard_output(base, state, tool_name="Write", tool_input={"file_path": str(outside_file)})
            )
            patch = f"*** Begin Patch\n*** Add File: {outside_file}\n+x\n*** End Patch"
            self.assert_denied(guard_output(base, state, tool_name="apply_patch", tool_input={"input": patch}))

    def test_artifact_symlink_escape_is_denied(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as outside:
            base = Path(temporary)
            state = self.spec_approved_state(base)
            link = base / "artifacts/design/escape"
            link.parent.mkdir(parents=True, exist_ok=True)
            try:
                link.symlink_to(Path(outside), target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlink unavailable: {error}")
            self.assert_denied(
                guard_output(base, state, tool_name="Write", tool_input={"file_path": str(link / "pwn.md")})
            )
            self.assert_denied(guard_output(base, state, command=f'touch "{link / "pwn.md"}"'))

    def test_sed_and_find_allow_only_proven_read_only_forms(self) -> None:
        allowed = [
            ["sed", "-n", "1,80p", "src/app.py"],
            ["sed", "-n", "5p", "src/app.py"],
            ["sed", "-n", "1p;20p", "src/app.py"],
            ["find", ".", "-maxdepth", "2", "-type", "f", "-print"],
        ]
        denied = [
            ["sed", "-i", "s/a/b/", "src/app.py"],
            ["sed", "--in-place=.bak", "s/a/b/", "src/app.py"],
            ["sed", "-n", "1w /tmp/pwn", "src/app.py"],
            ["sed", "-n", "1e touch /tmp/pwn", "src/app.py"],
            ["sed", "-n", "s/a/b/w /tmp/pwn", "src/app.py"],
            ["sed", "-f", "commands.sed", "src/app.py"],
            ["find", ".", "-delete"],
            ["find", ".", "-exec", "rm", "{}", "+"],
            ["find", ".", "-execdir", "rm", "{}", "+"],
            ["find", ".", "-fprint0", "/tmp/pwn"],
            ["find", ".", "-fprintf", "/tmp/pwn", "%p"],
            ["find", ".", "-fls", "/tmp/pwn"],
        ]
        platform = "posix"
        for args in allowed:
            self.assertTrue(allowed_read_only(args, platform=platform), " ".join(args))
        for args in denied:
            self.assertFalse(allowed_read_only(args, platform=platform), " ".join(args))

    def test_git_side_effect_options_are_rejected(self) -> None:
        denied = [
            ["git", "diff", "--ext-diff=true"],
            ["git", "diff", "--textconv=true"],
            ["git", "log", "--show-signature"],
            ["git", "log", "--open-files-in-pager=sh -c id"],
            ["git", "status", "--exec-path=/tmp/evil"],
            ["git", "status", "--config-env=diff.external=EVIL"],
            ["git", "status", "--output=/tmp/out"],
            ["git", "--no-pager", "log", "--no-textconv", "--no-ext-diff", "--no-show-signature", "--format=%G?"],
            ["git", "diff"],
            ["git", "show"],
            ["git", "log"],
            ["git", "--no-pager", "diff", "--", "--no-ext-diff", "--no-textconv"],
            ["git", "--no-pager", "log", "--", "--no-ext-diff", "--no-textconv", "--no-show-signature"],
            [
                "git",
                "--no-pager",
                "diff",
                "--word-diff-regex",
                "--no-ext-diff",
                "--ignore-matching-lines",
                "--no-textconv",
                "--",
                "README.md",
            ],
            ["git", "--no-pager", "log", "--format", "--no-ext-diff", "--no-textconv", "--no-show-signature"],
            ["git", "--no-pager", "diff", "--no-ext-diff=1", "--no-textconv=1"],
        ]
        for args in denied:
            self.assertFalse(allowed_read_only(args, platform="posix"), " ".join(args))
        self.assertTrue(
            allowed_read_only(
                ["git", "--no-pager", "diff", "--no-textconv", "--no-ext-diff"],
                platform="posix",
            )
        )
        self.assertTrue(
            allowed_read_only(
                ["git", "--no-pager", "diff", "--no-textconv", "--no-ext-diff", "--", "src"],
                platform="posix",
            )
        )
        self.assertTrue(
            allowed_read_only(
                ["git", "--no-pager", "diff", "--no-textconv", "--no-ext-diff", "--stat"],
                platform="posix",
            )
        )
        self.assertTrue(
            allowed_read_only(
                [
                    "git",
                    "--no-pager",
                    "log",
                    "--no-textconv",
                    "--no-ext-diff",
                    "--no-show-signature",
                    "--oneline",
                    "-5",
                ],
                platform="posix",
            )
        )

    def test_git_status_rejects_fsmonitor_commands(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            subprocess.run(["git", "init", "-q", str(base)], check=False, capture_output=True)
            subprocess.run(
                ["git", "-C", str(base), "config", "core.fsmonitor", "console.exe"],
                check=False,
                capture_output=True,
            )
            self.assertFalse(allowed_read_only(["git", "status", "--short"], cwd=base, platform="posix"))

    def test_implementation_is_allowed_after_plan_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = self.plan_approved_state(base)
            self.assert_allowed(guard_output(base, state, command="touch src/app.py"))
            self.assert_allowed(guard_output(base, state, command="cargo build"))
            self.assert_allowed(
                guard_output(base, state, tool_name="Write", tool_input={"file_path": "src/app.py"})
            )

    def test_policy_does_not_apply_to_t1_or_t0(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            t1 = build_state("fix", "T1", components_for("T1"))
            self.assert_allowed(guard_output(base, t1, command="touch src/app.py"))
            t0 = build_state("docs", "T0", [])
            self.assert_allowed(guard_output(base, t0, command="touch src/app.py"))

    def test_pending_approval_still_blocks_artifact_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = build_state("ui", "T2", components_for("T2", ui=True), ui=True, behavior_change=True)
            add_specs(state, base)
            self.assert_denied(
                guard_output(base, state, command="touch openspec/changes/example/new.md")
            )
            self.assert_denied(
                guard_output(base, state, tool_name="Write", tool_input={"file_path": "openspec/changes/example/new.md"})
            )


class RuntimeCompatibilityTests(unittest.TestCase):
    def test_windows_hook_interpreter_uses_py_launcher(self) -> None:
        self.assertEqual(hook_interpreter(platform="nt"), ["py", "-3"])
        self.assertEqual(hook_interpreter(platform="posix"), ["python3"])

    def test_windows_quote_argv_uses_cmd_quoting(self) -> None:
        self.assertEqual(
            quote_argv(
                [r"C:\Program Files\Python\python.exe", r"C:\A B\hook.py"],
                platform="nt",
            ),
            r'"C:\Program Files\Python\python.exe" "C:\A B\hook.py"',
        )

    def test_windows_program_name_uses_ntpath(self) -> None:
        self.assertEqual(program_name(r"C:\Windows\System32\Git.exe", platform="nt"), "git.exe")

    def test_windows_split_command_preserves_path_spaces(self) -> None:
        self.assertEqual(
            split_command(r'Get-Content "C:\A B\proposal.md"', platform="nt"),
            ["Get-Content", r"C:\A B\proposal.md"],
        )

    def test_windows_relative_name_uses_backslashes(self) -> None:
        self.assertEqual(relative_name_bytes(Path("a/b.txt"), platform="nt"), b"a\\b.txt")
        self.assertEqual(relative_name_bytes(Path("a/b.txt"), platform="posix"), b"a/b.txt")
        if os.name != "nt":
            self.assertEqual(relative_name_bytes(Path(os.fsdecode(b"bad-\xff.txt")), platform="posix"), b"bad-\xff.txt")

    def test_shell_control_characters_are_detected(self) -> None:
        for command in (
            "git status; whoami",
            "a || b",
            "a | b",
            "a & b",
            "Get-Content x & Remove-Item y",
            "echo $(whoami)",
            "a\r\nb",
            "(Remove-Item x)",
        ):
            self.assertTrue(has_shell_control(command), command)
        self.assertFalse(has_shell_control('Get-Content "C:\\A B\\file.txt"'))


class TerminalApprovalTests(unittest.TestCase):
    def test_native_dialog_module_and_api_are_removed(self) -> None:
        self.assertFalse((SCRIPTS / "conductor/platform.py").exists())
        self.assertFalse(hasattr(approve_module, "detect_approval_backend"))
        self.assertFalse(hasattr(approve_module, "run_native_dialog"))

    def test_runtime_code_has_no_gui_backend_names(self) -> None:
        runtime = "\n".join(
            path.read_text(encoding="utf-8") for path in SCRIPTS.rglob("*.py")
        )
        for name in ("osascript", "zenity", "kdialog", "yad"):
            self.assertNotIn(name, runtime)
        self.assertNotIn("DC_APPROVAL_DIALOG", runtime)
        self.assertNotIn("DC_APPROVAL_DISABLE_GUI", runtime)

    def test_terminal_approval_accepts_exact_token(self) -> None:
        with (
            patch("sys.stdin", io.StringIO()),
            patch("sys.stdin.isatty", return_value=True),
            patch("builtins.input", return_value="批准 token"),
            patch("sys.stderr", new=io.StringIO()),
        ):
            self.assertEqual(confirm_with_user("摘要", "token"), (True, "tty"))

    def test_terminal_approval_rejects_wrong_text(self) -> None:
        for typed in ("继续", "批准 wrong", ""):
            with (
                patch("sys.stdin", io.StringIO()),
                patch("sys.stdin.isatty", return_value=True),
                patch("builtins.input", return_value=typed),
                patch("sys.stderr", new=io.StringIO()),
            ):
                self.assertEqual(confirm_with_user("摘要", "token"), (False, "tty"))

    def test_without_tty_rejects_and_points_to_interactive_terminal(self) -> None:
        stderr = io.StringIO()
        with (
            patch("sys.stdin", io.StringIO()),
            patch("sys.stderr", new=stderr),
            patch("builtins.input") as input_mock,
        ):
            result = confirm_with_user("摘要", "token")
        self.assertEqual(result, (False, "unavailable"))
        self.assertIn("ssh -t", stderr.getvalue())
        input_mock.assert_not_called()


class InstallScriptTests(unittest.TestCase):
    def test_installer_copies_plugin_without_caches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "plugin"
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "install.py"), "--target", str(target)],
                cwd=str(PLUGIN_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertTrue((target / ".codex-plugin/plugin.json").is_file())
            self.assertTrue((target / "scripts/doctor.py").is_file())
            self.assertFalse(any(target.rglob("__pycache__")))
            self.assertFalse(any(target.rglob("*.pyc")))

    def test_installer_refuses_non_empty_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "plugin"
            target.mkdir()
            (target / "keep.txt").write_text("keep\n", encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "install.py"), "--target", str(target)],
                cwd=str(PLUGIN_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertTrue((target / "keep.txt").is_file())

    def test_installer_fails_when_doctor_fails(self) -> None:
        target = Path(tempfile.gettempdir()) / "delivery-conductor-doctor-failure"
        stderr = io.StringIO()
        stdout = io.StringIO()
        with (
            patch.object(install_module, "install"),
            patch.object(
                install_module.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 1),
            ),
            patch.object(sys, "argv", ["install.py", "--target", str(target)]),
            patch("sys.stderr", new=stderr),
            patch("sys.stdout", new=stdout),
        ):
            code = install_module.main()
        self.assertEqual(code, 1)
        self.assertIn("doctor.py", stderr.getvalue())
        self.assertNotIn("下一步", stdout.getvalue())


class DoctorTests(unittest.TestCase):
    def run_doctor(self, base: Path, *args: str, **environment: str):
        env = {
            **os.environ,
            "CODEX_HOME": str(base / "codex-home"),
            **environment,
        }
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "doctor.py"), *args],
            cwd=str(PLUGIN_ROOT),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=False,
            env=env,
        )

    def test_doctor_json_reports_environment_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            completed = self.run_doctor(Path(temporary), "--json")
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            report = json.loads(completed.stdout)
            names = {item["name"] for item in report["checks"]}
            self.assertIn("python", names)
            self.assertIn("toml", names)
            self.assertIn("hook_runtime", names)
            self.assertIn("approval_backend", names)
            self.assertIn("hook_trust", names)
            self.assertIn("plugin_manifest", names)

    def test_doctor_uses_cross_platform_tomli_guidance(self) -> None:
        source = (SCRIPTS / "doctor.py").read_text(encoding="utf-8")
        self.assertNotIn("sudo apt install", source)
        self.assertIn("pip install tomli", source)

    def test_doctor_reports_terminal_only_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            completed = self.run_doctor(Path(temporary), "--json")
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            report = json.loads(completed.stdout)
            approval = next(item for item in report["checks"] if item["name"] == "approval_backend")
            self.assertIn("终端", approval["detail"])
            for name in ("osascript", "zenity", "kdialog", "yad", "图形"):
                self.assertNotIn(name, approval["detail"])

    def test_doctor_strict_fails_without_interactive_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            completed = self.run_doctor(Path(temporary), "--strict")
            self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
            self.assertIn("approval_backend", completed.stdout)

    def test_doctor_reports_unexecutable_codex_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            fake_bin = base / "bin"
            fake_bin.mkdir()
            fake_codex = fake_bin / ("codex.exe" if os.name == "nt" else "codex")
            fake_codex.write_text("not an executable format\n", encoding="utf-8")
            fake_codex.chmod(0o755)
            completed = self.run_doctor(
                base,
                "--json",
                PATH=f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            report = json.loads(completed.stdout)
            codex_checks = [item for item in report["checks"] if item["name"] == "codex"]
            self.assertEqual(len(codex_checks), 1)
            self.assertIn("无法执行或超时", codex_checks[0]["detail"])


class HostTrustTests(unittest.TestCase):
    def test_content_hash_uses_windows_relative_path_bytes(self) -> None:
        from host_trust import _relative_hash_name

        self.assertEqual(_relative_hash_name(Path("a/b.txt"), platform="nt"), b"a\\b.txt")
        self.assertEqual(_relative_hash_name(Path("a/b.txt"), platform="posix"), b"a/b.txt")

    def test_content_hash_rejects_symlink_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "plugin"
            root.mkdir()
            (root / "real.txt").write_text("real\n", encoding="utf-8")
            try:
                (root / "link.txt").symlink_to(root / "real.txt")
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is unavailable")
            with self.assertRaises(OSError):
                plugin_content_hash(root)

    def test_windows_declaration_hash_uses_command_windows(self) -> None:
        root = Path(r"C:\plugins\delivery-conductor")
        handler = {
            "type": "command",
            "command": 'python3 "${PLUGIN_ROOT}/scripts/guard_tool.py"',
            "commandWindows": 'py -3 "${PLUGIN_ROOT}/scripts/guard_tool.py"',
        }
        expected = declaration_hash(
            "pre_tool_use",
            "*",
            {
                "type": "command",
                "command": 'py -3 "C:\\plugins\\delivery-conductor/scripts/guard_tool.py"',
            },
            plugin_root=root,
            platform="posix",
        )
        actual = declaration_hash(
            "pre_tool_use",
            "*",
            handler,
            plugin_root=root,
            platform="nt",
        )
        self.assertEqual(actual, expected)

    def test_declaration_hash_substitutes_plugin_root(self) -> None:
        root = Path(tempfile.gettempdir()) / "delivery-conductor"
        templated = {
            "type": "command",
            "command": 'python3 "${PLUGIN_ROOT}/scripts/guard_tool.py"',
        }
        absolute = {
            "type": "command",
            "command": f'python3 "{root}/scripts/guard_tool.py"',
        }
        self.assertEqual(
            declaration_hash("pre_tool_use", "*", templated, plugin_root=root),
            declaration_hash("pre_tool_use", "*", absolute, plugin_root=root),
        )

    def test_toml_backend_is_available(self) -> None:
        self.assertIn(TOML_BACKEND, {"tomllib", "tomli"})
        parsed = load_toml("[hooks]\nstate = {}\n")
        self.assertEqual(parsed["hooks"]["state"], {})

    def test_missing_toml_backend_is_actionable(self) -> None:
        with self.assertRaises(HostTrustError) as raised:
            parse_toml("[hooks]\nstate = {}\n", None)
        message = str(raised.exception)
        self.assertIn("tomli", message)
        self.assertIn("3.11", message)
        self.assertIn("pip install tomli", message)
        self.assertNotIn("sudo apt install", message)

    def test_trusted_declarations_and_content_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            codex_home, root = host_trust_fixture(base)
            report = trust_report(plugin_root=root, config_path=codex_home / "config.toml")
            self.assertTrue(report["ok"], report["errors"])
            self.assertTrue(report["checked"])
            self.assertEqual(
                {item["event"] for item in report["hooks"]},
                {"pre_tool_use", "session_start", "user_prompt_submit"},
            )

    def test_changed_declaration_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            codex_home, root = host_trust_fixture(base, tamper="declaration")
            report = trust_report(plugin_root=root, config_path=codex_home / "config.toml")
            self.assertFalse(report["ok"])
            self.assertTrue(any("declaration" in error for error in report["errors"]))

    def test_changed_bundle_contents_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            codex_home, root = host_trust_fixture(base, tamper="content")
            report = trust_report(plugin_root=root, config_path=codex_home / "config.toml")
            self.assertFalse(report["ok"])
            self.assertTrue(any("contents" in error for error in report["errors"]))

    def test_disabled_hook_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            codex_home, root = host_trust_fixture(base, tamper="disabled")
            report = trust_report(plugin_root=root, config_path=codex_home / "config.toml")
            self.assertFalse(report["ok"])
            self.assertTrue(any("disabled" in error for error in report["errors"]))

    def test_missing_config_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            codex_home, root = host_trust_fixture(base, tamper="missing-config")
            report = trust_report(plugin_root=root, config_path=codex_home / "config.toml")
            self.assertFalse(report["ok"])
            self.assertTrue(any("cannot verify hook trust" in error for error in report["errors"]))

    def test_enforce_raises_when_installed_hooks_are_untrusted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            codex_home, root = host_trust_fixture(base, tamper="content")
            with self.assertRaises(HostTrustError):
                enforce_host_trust(
                    plugin_root=root,
                    config_path=codex_home / "config.toml",
                    allow_unverified_dev=False,
                )

    def test_unverified_dev_checkout_is_skipped_only_when_unchecked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            report = enforce_host_trust(
                plugin_root=base / "missing-plugin",
                config_path=base / "config.toml",
                allow_unverified_dev=True,
            )
            self.assertTrue(report["skipped"])
            self.assertFalse(report["checked"])


class HostTrustCliTests(unittest.TestCase):
    def run_with_codex_home(self, script: str, args: list[str], codex_home: Path, cwd: Path):
        environment = {**os.environ, "CODEX_HOME": str(codex_home)}
        return subprocess.run(
            [sys.executable, str(SCRIPTS / script), *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )

    def test_route_cli_refuses_untrusted_non_t0_task(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            codex_home, _ = host_trust_fixture(base, tamper="content")
            state = base / ".codex/delivery-state.json"
            completed = self.run_with_codex_home(
                "route.py",
                [
                    "--task",
                    "add login page",
                    "--multi-file",
                    "--behavior-change",
                    "--state",
                    str(state),
                    "--base-dir",
                    str(base),
                ],
                codex_home,
                base,
            )
            self.assertEqual(completed.returncode, 4, completed.stdout + completed.stderr)
            self.assertIn("not trusted", completed.stderr)
            self.assertFalse(state.exists())

    def test_route_cli_allows_t0_without_hook_trust(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            codex_home, _ = host_trust_fixture(base, tamper="content")
            state = base / ".codex/delivery-state.json"
            completed = self.run_with_codex_home(
                "route.py",
                [
                    "--task",
                    "update README",
                    "--tier",
                    "T0",
                    "--state",
                    str(state),
                    "--base-dir",
                    str(base),
                ],
                codex_home,
                base,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertTrue(state.is_file())

    def test_gate_and_approve_cli_refuse_untrusted_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            codex_home, _ = host_trust_fixture(base, tamper="content")
            state_path = base / ".codex/delivery-state.json"
            save_state(
                state_path,
                build_state("ui", "T2", components_for("T2", ui=True), ui=True, behavior_change=True),
            )

            gate = self.run_with_codex_home(
                "gate.py",
                ["--state", str(state_path), "--base-dir", str(base)],
                codex_home,
                base,
            )
            self.assertEqual(gate.returncode, 4, gate.stdout + gate.stderr)
            self.assertIn("not trusted", gate.stderr)

            approve = self.run_with_codex_home(
                "approve.py",
                ["--gate", "spec_approved", "--state", str(state_path), "--base-dir", str(base)],
                codex_home,
                base,
            )
            self.assertEqual(approve.returncode, 4, approve.stdout + approve.stderr)
            self.assertIn("not trusted", approve.stderr)

    def test_gate_cli_runs_when_hooks_are_trusted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            codex_home, _ = host_trust_fixture(base)
            state_path = base / ".codex/delivery-state.json"
            save_state(
                state_path,
                build_state("ui", "T2", components_for("T2", ui=True), ui=True, behavior_change=True),
            )
            gate = self.run_with_codex_home(
                "gate.py",
                ["--state", str(state_path), "--base-dir", str(base), "--gate", "spec_ready"],
                codex_home,
                base,
            )
            self.assertEqual(gate.returncode, 1, gate.stdout + gate.stderr)
            self.assertNotIn("not trusted", gate.stderr)


if __name__ == "__main__":
    unittest.main()
