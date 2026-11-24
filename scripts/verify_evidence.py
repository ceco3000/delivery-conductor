#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conductor.gates import evaluate_all, next_gate
from conductor.model import GATE_ORDER, load_state
from conductor.runtime import configure_stdio


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Verify that all required evidence exists and passes.")
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

    results = evaluate_all(state, base_dir)
    ok = all(results[gate]["ok"] for gate in GATE_ORDER)
    payload = {
        "ok": ok,
        "phase": state.get("phase"),
        "next_gate": next_gate(state, base_dir),
        "gates": results,
        "evidence_count": len(state.get("evidence", [])),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Evidence entries: {payload['evidence_count']}")
        print(f"Phase: {payload['phase']}")
        for gate in GATE_ORDER:
            marker = "PASS" if results[gate]["ok"] else "FAIL"
            print(f"[{marker}] {gate}")
            for reason in results[gate]["reasons"]:
                print(f"  - {reason}")
        if payload["next_gate"]:
            print(f"Next required gate: {payload['next_gate']}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
