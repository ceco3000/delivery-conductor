#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conductor.gates import advance_state, evaluate_all, evaluate_gate, next_gate
from conductor.model import GATE_ORDER, load_state, save_state
from conductor.runtime import configure_stdio
from host_trust import HostTrustError, enforce_host_trust


def print_human(results: dict[str, dict[str, object]], state: dict[str, object]) -> None:
    print(f"Phase: {state.get('phase')}")
    for gate in GATE_ORDER:
        if gate not in results:
            continue
        item = results[gate]
        marker = "PASS" if item["ok"] else "FAIL"
        print(f"[{marker}] {gate}")
        for reason in item["reasons"]:  # type: ignore[index]
            print(f"  - {reason}")


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Evaluate delivery-conductor gates.")
    parser.add_argument("--state", default=".codex/delivery-state.json")
    parser.add_argument("--gate", choices=GATE_ORDER)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--advance", action="store_true")
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

    if state.get("tier") != "T0":
        try:
            enforce_host_trust()
        except HostTrustError as error:
            print(str(error), file=sys.stderr)
            return 4

    if args.gate:
        passed, reasons = evaluate_gate(state, args.gate, base_dir)
        result = {args.gate: {"ok": passed, "reasons": reasons}}
    elif args.advance:
        result = advance_state(state, base_dir, state_path)
    else:
        result = evaluate_all(state, base_dir)
        state["next_gate"] = next_gate(state, base_dir)
        save_state(state_path, state)

    if args.json:
        print(json.dumps({"phase": state.get("phase"), "next_gate": next_gate(state, base_dir), "gates": result}, ensure_ascii=False, indent=2))
    else:
        print_human(result, state)
        pending = next_gate(state, base_dir)
        if pending:
            print(f"Next required gate: {pending}")
        else:
            print("All required gates passed.")

    return 0 if all(item["ok"] for item in result.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
