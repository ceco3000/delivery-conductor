#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conductor.runtime import configure_stdio


def emit_additional_context(message: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": message,
                }
            },
            ensure_ascii=False,
        )
    )


def main() -> int:
    configure_stdio()
    try:
        from conductor.gates import next_gate
        from conductor.model import load_state

        state_path = Path(os.environ.get("DELIVERY_CONDUCTOR_STATE", ".codex/delivery-state.json"))
        if not state_path.exists():
            emit_additional_context(
                "[delivery-conductor] Use this conductor for T2/T3 tasks; no state file yet."
            )
            return 0
        state = load_state(state_path)
        pending = next_gate(state, state_path.parent.parent)
        emit_additional_context(
            f"[delivery-conductor] phase={state.get('phase')} "
            f"tier={state.get('tier')} next_gate={pending or 'none'}"
        )
    except Exception as error:  # Hook output must never block the session.
        emit_additional_context(f"[delivery-conductor] reminder unavailable: {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
