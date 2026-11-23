from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 3
SUPPORTED_SCHEMA_VERSIONS = (1, 2, SCHEMA_VERSION)
TIERS = ("T0", "T1", "T2", "T3")
GATE_ORDER = (
    "route",
    "spec_ready",
    "spec_approved",
    "design_ready",
    "design_approved",
    "plan_ready",
    "plan_approved",
    "reviewed",
    "verified",
    "consistency_verified",
    "delivery_approved",
    "archived",
)
PHASES = (
    "ROUTING",
    "ROUTED",
    "SPEC_READY",
    "SPEC_APPROVED",
    "DESIGN_READY",
    "DESIGN_APPROVED",
    "PLAN_READY",
    "PLAN_APPROVED",
    "IMPLEMENTING",
    "REVIEWED",
    "VERIFIED",
    "CONSISTENCY_VERIFIED",
    "DELIVERY_APPROVED",
    "ARCHIVED",
)
GATE_TO_PHASE = {
    "route": "ROUTED",
    "spec_ready": "SPEC_READY",
    "spec_approved": "SPEC_APPROVED",
    "design_ready": "DESIGN_READY",
    "design_approved": "DESIGN_APPROVED",
    "plan_ready": "PLAN_READY",
    "plan_approved": "PLAN_APPROVED",
    "reviewed": "REVIEWED",
    "verified": "VERIFIED",
    "consistency_verified": "CONSISTENCY_VERIFIED",
    "delivery_approved": "DELIVERY_APPROVED",
    "archived": "ARCHIVED",
}
COMPONENT_LABELS = {
    "openspec": "OpenSpec",
    "superpowers": "Superpowers",
    "ecc": "ECC",
    "ui-ux-pro-max": "UI UX Pro Max",
}
COMPONENT_ORDER = ("openspec", "ui-ux-pro-max", "superpowers", "ecc")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def derive_requirements(
    tier: str,
    ui: bool = False,
    behavior_change: bool = False,
    design_change: bool | None = None,
) -> dict[str, bool]:
    if tier not in TIERS:
        raise ValueError(f"unknown tier: {tier}")
    if design_change is None:
        design_change = ui
    if tier == "T0":
        return {
            "spec": False,
            "spec_approval": False,
            "ui_design": False,
            "design_approval": False,
            "plan": False,
            "plan_approval": False,
            "review": False,
            "verification": False,
            "consistency": False,
            "delivery_approval": False,
            "archive": False,
        }
    if tier == "T1":
        return {
            "spec": bool(behavior_change),
            "spec_approval": bool(behavior_change),
            "ui_design": bool(design_change),
            "design_approval": bool(design_change),
            "plan": False,
            "plan_approval": False,
            "review": True,
            "verification": True,
            "consistency": True,
            "delivery_approval": True,
            "archive": bool(behavior_change),
        }
    return {
        "spec": True,
        "spec_approval": True,
        "ui_design": bool(design_change),
        "design_approval": bool(design_change),
        "plan": True,
        "plan_approval": True,
        "review": True,
        "verification": True,
        "consistency": True,
        "delivery_approval": True,
        "archive": True,
    }


def components_for(
    tier: str,
    ui: bool = False,
    *,
    design_change: bool | None = None,
    behavior_change: bool = False,
) -> list[str]:
    if tier == "T0":
        return []
    if design_change is None:
        design_change = ui
    selected = {"superpowers", "ecc"}
    if tier in {"T2", "T3"} or behavior_change:
        selected.add("openspec")
    if design_change:
        selected.add("ui-ux-pro-max")
    return [component for component in COMPONENT_ORDER if component in selected]


def build_state(
    task: str,
    tier: str,
    components: list[str],
    *,
    ui: bool = False,
    behavior_change: bool = False,
    design_change: bool | None = None,
    new_capability: bool = False,
    route_status: str = "assessed",
    route_source: str = "explicit",
    heuristic: dict[str, Any] | None = None,
    assessment: dict[str, Any] | None = None,
    routing_notes: list[str] | None = None,
) -> dict[str, Any]:
    timestamp = now_iso()
    if design_change is None:
        design_change = ui
    if route_status not in {"provisional", "assessed"}:
        raise ValueError(f"unknown route status: {route_status}")
    phase = "ROUTED" if route_status == "assessed" else "ROUTING"
    return {
        "schema_version": SCHEMA_VERSION,
        "task": task,
        "tier": tier,
        "components": list(components),
        "ui": bool(ui),
        "behavior_change": bool(behavior_change),
        "design_change": bool(design_change),
        "new_capability": bool(new_capability),
        "route_status": route_status,
        "route_source": route_source,
        "heuristic": heuristic,
        "assessment": assessment,
        "routing_notes": list(routing_notes or []),
        "phase": phase,
        "requires": derive_requirements(
            tier,
            ui=ui,
            behavior_change=behavior_change,
            design_change=design_change,
        ),
        "gates": {gate: False for gate in GATE_ORDER},
        "evidence": [],
        "approval_requests": [],
        "created_at": timestamp,
        "updated_at": timestamp,
        "history": [{"phase": phase, "timestamp": timestamp}],
    }


def component_line(state: dict[str, Any]) -> str:
    tier = state.get("tier", "")
    if tier == "T0":
        return "Route: T0 | direct"
    labels = [COMPONENT_LABELS.get(component, component) for component in state.get("components", [])]
    return f"Route: {tier} | " + " + ".join(labels)


def load_state(path: str | os.PathLike[str]) -> dict[str, Any]:
    state_path = Path(path)
    if not state_path.exists():
        raise FileNotFoundError(f"state file not found: {state_path}")
    with state_path.open("r", encoding="utf-8") as handle:
        state = json.load(handle)
    schema_version = state.get("schema_version")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(f"unsupported state schema: {schema_version!r}")
    if schema_version in (1, 2):
        state["schema_version"] = SCHEMA_VERSION
    if schema_version == 1:
        state.setdefault("route_status", "assessed")
        state.setdefault("route_source", "legacy")
        state.setdefault("heuristic", None)
        state.setdefault("assessment", None)
        state.setdefault("routing_notes", [])
    state.setdefault("gates", {gate: False for gate in GATE_ORDER})
    state.setdefault("evidence", [])
    state.setdefault("approval_requests", [])
    state.setdefault("history", [])
    tier = state.get("tier")
    if tier in TIERS:
        state["requires"] = derive_requirements(
            tier,
            ui=bool(state.get("ui")),
            behavior_change=bool(state.get("behavior_change")),
            design_change=state.get("design_change"),
        )
    return state


def save_state(path: str | os.PathLike[str], state: dict[str, Any]) -> None:
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = now_iso()
    temporary = state_path.with_suffix(state_path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(state_path)


def append_evidence(state: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    recorded = dict(entry)
    recorded["id"] = f"ev-{len(state.get('evidence', [])) + 1:04d}"
    recorded.setdefault("timestamp", now_iso())
    state.setdefault("evidence", []).append(recorded)
    return recorded


def set_phase(state: dict[str, Any], phase: str, note: str = "") -> None:
    if phase not in PHASES:
        raise ValueError(f"unknown phase: {phase}")
    if state.get("phase") == phase:
        return
    state["phase"] = phase
    event = {"phase": phase, "timestamp": now_iso()}
    if note:
        event["note"] = note
    state.setdefault("history", []).append(event)
