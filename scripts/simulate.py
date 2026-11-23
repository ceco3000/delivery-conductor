#!/usr/bin/env python3
from __future__ import annotations

import base64
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from approve import approve_gate
from conductor.gates import advance_state
from conductor.model import append_evidence, build_state, component_line, components_for, save_state

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def write(path: Path, content: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


def record(state: dict, kind: str, data: dict) -> None:
    append_evidence(state, {"kind": kind, **data})


def snapshot(label: str, state: dict, base_dir: Path) -> dict:
    results = advance_state(state, base_dir, base_dir / ".codex/delivery-state.json")
    print(f"\n{label}")
    print("-" * len(label))
    for gate, result in results.items():
        marker = "PASS" if result["ok"] else "FAIL"
        print(f"  [{marker}] {gate}")
    print(f"  phase: {state.get('phase')}")
    return results


def simulate_approval(state: dict, gate: str, base_dir: Path) -> None:
    approved = approve_gate(
        state,
        gate,
        base_dir,
        confirm=lambda _summary, _token: (True, "tty"),
    )
    assert approved, f"{gate} simulation approval should succeed"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="delivery-conductor-sim-") as temporary:
        base_dir = Path(temporary)
        state_path = base_dir / ".codex/delivery-state.json"

        state = build_state(
            "add-login-ui",
            "T2",
            components_for("T2", ui=True),
            ui=True,
            behavior_change=True,
        )
        save_state(state_path, state)
        print(component_line(state))

        results = snapshot("1. Route only", state, base_dir)
        assert results["route"]["ok"], "route gate should pass"
        assert not results["spec_ready"]["ok"], "spec gate should fail before artifacts"

        proposal = write(base_dir / "openspec/changes/add-login-ui/proposal.md", "# Proposal\n")
        design = write(base_dir / "openspec/changes/add-login-ui/design.md", "# Design\n")
        tasks = write(base_dir / "openspec/changes/add-login-ui/tasks.md", "# Tasks\n- [ ] 1.1 Build login\n")
        delta = write(base_dir / "openspec/changes/add-login-ui/specs/user-auth/spec.md", "# Spec Delta\n")
        record(state, "spec_artifacts", {"paths": [proposal, design, tasks, delta]})
        results = snapshot("2. Spec artifacts recorded", state, base_dir)
        assert results["spec_ready"]["ok"], "spec gate should pass"
        assert not results["spec_approved"]["ok"], "spec approval gate should fail"

        simulate_approval(state, "spec_approved", base_dir)
        results = snapshot("3. Spec approved by simulated user confirmation", state, base_dir)
        assert results["spec_approved"]["ok"], "spec approval gate should pass"
        assert not results["design_ready"]["ok"], "design gate should fail"

        design_review = write(
            base_dir / "artifacts/design/design-review.md",
            "# Design Review\n\n- Layout: single column\n- Palette: warm neutral\n"
            "- Typography: humanist sans\n- Responsive: 390px and desktop\n- Accessibility: WCAG AA\n",
        )
        desktop_preview = write(base_dir / "artifacts/design/desktop-login.png", "desktop preview\n")
        mobile_preview = write(base_dir / "artifacts/design/mobile-login.png", "390px mobile preview\n")
        record(state, "ui_direction", {
            "style": "editorial-minimal",
            "palette": "warm neutral with one accent",
            "typography": "humanist sans + serif display",
            "layout": "single-column form with strong hierarchy",
            "responsive": "single column below 768px; 390px mobile reviewed",
            "source": "ui-ux-pro-max",
            "design_review_path": design_review,
            "desktop_previews": [desktop_preview],
            "mobile_previews": [mobile_preview],
            "references": [],
        })
        results = snapshot("4. UI design review package recorded", state, base_dir)
        assert results["design_ready"]["ok"], "design gate should pass"
        assert not results["design_approved"]["ok"], "design approval gate should fail"

        simulate_approval(state, "design_approved", base_dir)
        results = snapshot("5. UI design approved by simulated user confirmation", state, base_dir)
        assert results["design_approved"]["ok"], "design approval gate should pass"
        assert not results["plan_ready"]["ok"], "plan gate should fail"

        plan = write(
            base_dir / "docs/superpowers/plans/add-login-ui.md",
            "# Plan\n\n> **执行状态：进行中。**\n\n"
            "- [ ] Step 1: add tests\n- [ ] Step 2: implement form\n",
        )
        record(state, "plan", {
            "path": plan,
            "test_strategy": ["unit test validation", "component test submit flow"],
            "source": "superpowers",
        })
        results = snapshot("6. Plan recorded", state, base_dir)
        assert results["plan_ready"]["ok"], "plan gate should pass"
        assert not results["plan_approved"]["ok"], "plan approval gate should fail"

        simulate_approval(state, "plan_approved", base_dir)
        results = snapshot("7. Plan approved by simulated user confirmation", state, base_dir)
        assert results["plan_approved"]["ok"], "plan approval gate should pass"
        assert not results["reviewed"]["ok"], "review gate should fail"

        write(base_dir / "openspec/changes/add-login-ui/tasks.md", "# Tasks\n- [x] 1.1 Build login\n")
        write(
            Path(plan),
            "# Plan\n\n> **执行状态：已完成并归档。**\n\n"
            "- [x] Step 1: add tests\n- [x] Step 2: implement form\n",
        )
        review_report = write(
            base_dir / "docs/superpowers/reviews/add-login-ui-ecc.md",
            "# ECC Review\n\n- Findings resolved: yes\n- Open findings: 0\n",
        )
        record(state, "review", {
            "reviewer": "ecc",
            "findings_resolved": True,
            "security_checked": True,
            "a11y_checked": True,
            "open_findings": 0,
            "report": review_report,
        })
        record(state, "command", {
            "command": "python3 -m unittest discover -s tests",
            "result": "passed",
            "exit_code": 0,
        })
        desktop = base_dir / "screenshots/desktop.png"
        mobile = base_dir / "screenshots/mobile.png"
        desktop.parent.mkdir(parents=True, exist_ok=True)
        desktop.write_bytes(PNG_1X1)
        mobile.write_bytes(PNG_1X1)
        record(state, "screenshot", {"path": str(desktop), "viewport": "desktop", "label": "login desktop"})
        record(state, "screenshot", {"path": str(mobile), "viewport": "mobile", "label": "login mobile"})
        results = snapshot("8. Review and verification evidence recorded", state, base_dir)
        assert results["verified"]["ok"], "verification gate should pass"
        assert not results["consistency_verified"]["ok"], "consistency gate should fail before audit"
        assert not results["delivery_approved"]["ok"], "delivery approval gate should fail"

        deliverables = write(
            base_dir / "deliverables/交付清单.md",
            "# 交付清单\n\n后端测试 / 前端测试 | 48 / 24 通过\n",
        )
        consistency_report = write(
            base_dir / "docs/superpowers/reviews/add-login-ui-consistency.md",
            "# Consistency Audit\n\n- Open tasks: 0\n- Open plans: 0\n- Open findings: 0\n",
        )
        record(state, "consistency_audit", {
            "report": consistency_report,
            "findings_resolved": True,
            "open_findings": 0,
            "checks": [{
                "path": deliverables,
                "pattern": "48 / 24",
                "expect": "absent",
                "description": "旧测试数量不得残留",
            }],
        })
        results = snapshot("9. Consistency audit detects stale delivery claim", state, base_dir)
        assert not results["consistency_verified"]["ok"], "stale delivery claim must block consistency"
        write(Path(deliverables), "# 交付清单\n\n后端测试 / 前端测试 | 48 / 25 通过\n")
        results = snapshot("10. Consistent delivery state recorded", state, base_dir)
        assert results["consistency_verified"]["ok"], "consistency gate should pass after fixes"
        assert state["phase"] == "CONSISTENCY_VERIFIED", "state should stop at consistency gate"

        simulate_approval(state, "delivery_approved", base_dir)
        results = snapshot("11. Delivery approved by simulated user confirmation", state, base_dir)
        assert results["delivery_approved"]["ok"], "delivery approval gate should pass"
        assert not results["archived"]["ok"], "archive gate should fail"

        record(state, "archive", {"tasks_updated": True, "openspec_archived": True})
        results = snapshot("12. Archive recorded", state, base_dir)
        assert all(result["ok"] for result in results.values()), "all gates should pass"
        assert state["phase"] == "ARCHIVED", "state should be ARCHIVED"

        print("\nSimulation passed: approvals, consistency blocking, stale-claim rejection, and archive behaved as expected.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
