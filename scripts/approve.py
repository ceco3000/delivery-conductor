#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conductor.gates import (
    APPROVAL_GATES,
    _approval_artifacts,
    append_approval,
    approval_digest,
    evaluate_gate,
    gate_is_required,
)
from conductor.model import GATE_ORDER, load_state, now_iso, save_state
from conductor.runtime import configure_stdio
from host_trust import HostTrustError, enforce_host_trust

Confirm = Callable[[str, str], tuple[bool, str]]


class PrerequisiteError(RuntimeError):
    """Raised when an earlier gate is not ready or changes invalidate approval."""


def confirm_with_user(summary: str, token: str) -> tuple[bool, str]:
    if not sys.stdin.isatty():
        print(
            "User confirmation is required, but no interactive terminal is available. "
            "Re-run in an interactive terminal (for remote Linux use `ssh -t`).",
            file=sys.stderr,
        )
        return False, "unavailable"

    print(summary, file=sys.stderr)
    try:
        typed = input(f"输入 批准 {token} 以确认，其他输入均拒绝: ").strip()
    except (EOFError, OSError):
        print(
            "User confirmation is required, but the interactive terminal closed before confirmation. "
            "Re-run in an interactive terminal (for remote Linux use `ssh -t`).",
            file=sys.stderr,
        )
        return False, "unavailable"
    return typed == f"批准 {token}", "tty"


def _ensure_prerequisites(state: dict, gate: str, base_dir: str | Path) -> None:
    if gate not in APPROVAL_GATES:
        raise ValueError(f"unknown approval gate: {gate}")
    if not gate_is_required(state, gate):
        raise PrerequisiteError(f"approval gate is not required: {gate}")
    for earlier in GATE_ORDER:
        if earlier == gate:
            break
        if not gate_is_required(state, earlier):
            continue
        passed, reasons = evaluate_gate(state, earlier, base_dir)
        if not passed:
            detail = "; ".join(reasons) if reasons else "gate did not pass"
            raise PrerequisiteError(f"prerequisite gate failed: {earlier}: {detail}")


def _summary(state: dict, gate: str, base_dir: str | Path, token: str, digest: str) -> str:
    paths = [value for value in _approval_artifacts(state, gate) if value]
    visible_paths = "\n".join(f"- {value}" for value in paths[:12])
    if len(paths) > 12:
        visible_paths += f"\n- ... {len(paths) - 12} more"
    return (
        "编码总控需要你的明确审核。\n\n"
        f"门禁: {gate}\n"
        f"一次性令牌: {token}\n"
        f"产物摘要: {digest}\n"
        f"审核产物:\n{visible_paths or '- 当前证据集合'}\n\n"
        "只有输入精确的确认文本，才会记录审批。"
    )


def approve_gate(
    state: dict,
    gate: str,
    base_dir: str | Path,
    confirm: Confirm = confirm_with_user,
) -> bool:
    _ensure_prerequisites(state, gate, base_dir)
    digest = approval_digest(state, gate, base_dir)
    request = {
        "gate": gate,
        "token": secrets.token_urlsafe(12),
        "requested_at": now_iso(),
        "artifact_digest": digest,
    }
    state.setdefault("approval_requests", []).append(request)
    approved, method = confirm(_summary(state, gate, base_dir, request["token"], digest), request["token"])
    if not approved:
        return False
    if approval_digest(state, gate, base_dir) != digest:
        raise PrerequisiteError("approved artifacts changed while waiting for confirmation")
    append_approval(state, gate, base_dir, request["token"], method)
    return True


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Request explicit human approval for a conductor gate.")
    parser.add_argument("--gate", required=True, choices=APPROVAL_GATES)
    parser.add_argument("--state", default=".codex/delivery-state.json")
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    base_dir = Path(args.base_dir).resolve()
    state_path = Path(args.state)
    if not state_path.is_absolute():
        state_path = base_dir / state_path

    try:
        state = load_state(state_path)
    except (FileNotFoundError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2

    try:
        enforce_host_trust()
    except HostTrustError as error:
        print(str(error), file=sys.stderr)
        return 4

    try:
        approved = approve_gate(state, args.gate, base_dir)
    except PrerequisiteError as error:
        print(str(error), file=sys.stderr)
        return 3
    except (OSError, ValueError) as error:
        print(f"Approval failed: {error}", file=sys.stderr)
        return 2
    finally:
        save_state(state_path, state)

    if not approved:
        print("Approval was not granted. The gate remains blocked.", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"approved": True, "gate": args.gate}, ensure_ascii=False, indent=2))
    else:
        print(f"Approved {args.gate}. The conductor may continue to the next gate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
