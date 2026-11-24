#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conductor.evidence import EVIDENCE_KINDS, validate_evidence
from conductor.model import append_evidence, load_state, save_state
from conductor.runtime import configure_stdio


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Record delivery evidence into the conductor state file.")
    parser.add_argument("--state", default=".codex/delivery-state.json")
    parser.add_argument("--kind", required=True, choices=EVIDENCE_KINDS)
    parser.add_argument("--data", default="{}", help="JSON object with evidence fields")
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.kind in {"user_approval", "approval"}:
        print("Approval evidence must be recorded by the interactive approval command.", file=sys.stderr)
        return 2

    base_dir = Path(args.base_dir).resolve()
    state_path = Path(args.state)
    if not state_path.is_absolute():
        state_path = base_dir / state_path

    try:
        data = json.loads(args.data)
    except json.JSONDecodeError as error:
        print(f"Invalid JSON for --data: {error}", file=sys.stderr)
        return 2
    if not isinstance(data, dict):
        print("--data must be a JSON object", file=sys.stderr)
        return 2
    reserved = sorted({"kind", "id", "timestamp"}.intersection(data))
    if reserved:
        print(
            "Evidence data cannot override reserved fields: " + ", ".join(reserved),
            file=sys.stderr,
        )
        return 2

    errors = validate_evidence(args.kind, data)
    if errors:
        for error in errors:
            print(f"Evidence error: {error}", file=sys.stderr)
        return 2

    state = load_state(state_path)
    entry = append_evidence(state, {**data, "kind": args.kind})
    save_state(state_path, state)

    if args.json:
        print(json.dumps({"recorded": entry, "phase": state.get("phase")}, ensure_ascii=False, indent=2))
    else:
        print(f"Recorded {args.kind}: {entry['id']} (phase: {state.get('phase')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
