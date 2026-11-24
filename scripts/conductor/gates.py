from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from .consistency import audit_consistency
from .evidence import APPROVAL_METHODS
from .model import GATE_ORDER, GATE_TO_PHASE, TIERS, append_evidence, now_iso, save_state, set_phase

APPROVAL_GATES = (
    "spec_approved",
    "design_approved",
    "plan_approved",
    "delivery_approved",
)

REQUIREMENT_BY_GATE = {
    "spec_ready": "spec",
    "spec_approved": "spec_approval",
    "design_ready": "ui_design",
    "design_approved": "design_approval",
    "plan_ready": "plan",
    "plan_approved": "plan_approval",
    "reviewed": "review",
    "verified": "verification",
    "consistency_verified": "consistency",
    "delivery_approved": "delivery_approval",
    "archived": "archive",
}


def _resolve(base_dir: str | Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = Path(base_dir) / path
    return path.resolve()


def _resolve_archived_change(base_dir: str | Path, path: Path) -> Path | None:
    parts = path.parts
    marker = next(
        (index for index in range(len(parts) - 2) if parts[index : index + 2] == ("openspec", "changes")),
        None,
    )
    if marker is None or len(parts) < marker + 4 or parts[marker + 2] == "archive":
        return None
    change_name = parts[marker + 2]
    suffix = Path(*parts[marker + 3 :])
    project_root = Path(*parts[:marker]) if marker else Path(".")
    archive_root = project_root / "openspec" / "changes" / "archive"
    if not archive_root.is_dir():
        return None
    candidates = sorted(
        candidate
        for candidate in archive_root.iterdir()
        if candidate.is_dir()
        and (candidate.name == change_name or candidate.name.endswith(f"-{change_name}"))
    )
    for candidate in reversed(candidates):
        archived_path = candidate / suffix
        if archived_path.is_file():
            return archived_path
    return None


def _resolve_existing(base_dir: str | Path, value: str) -> Path:
    path = _resolve(base_dir, value)
    if path.exists():
        return path
    return _resolve_archived_change(base_dir, path) or path


def _of_kind(state: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [entry for entry in state.get("evidence", []) if entry.get("kind") == kind]


def _latest_commands(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for entry in state.get("evidence", []):
        if entry.get("kind") == "command":
            latest[str(entry.get("command", ""))] = entry
    return latest


def gate_is_required(state: dict[str, Any], gate: str) -> bool:
    if gate == "route":
        return True
    requirement = REQUIREMENT_BY_GATE.get(gate)
    if requirement is None:
        return False
    return bool(state.get("requires", {}).get(requirement))


def _check_paths(base_dir: str | Path, paths: list[str], reasons: list[str], label: str) -> None:
    for value in paths:
        path = _resolve_existing(base_dir, value)
        if not path.exists():
            reasons.append(f"{label} path missing: {value}")


def _evaluate_route(state: dict[str, Any], reasons: list[str]) -> None:
    tier = state.get("tier")
    if tier not in TIERS:
        reasons.append(f"invalid tier: {tier!r}")
    if state.get("route_status", "assessed") != "assessed":
        reasons.append("semantic route assessment is pending")
    if tier != "T0" and not state.get("components"):
        reasons.append("components must be non-empty for T1-T3")
    if tier == "T0" and state.get("components"):
        reasons.append("T0 must not require component workflow")


def _evaluate_spec_ready(state: dict[str, Any], base_dir: str | Path, reasons: list[str]) -> None:
    if not state.get("requires", {}).get("spec"):
        return
    entries = _of_kind(state, "spec_artifacts")
    if not entries:
        reasons.append("missing spec_artifacts evidence")
        return
    paths: list[str] = []
    for entry in entries:
        paths.extend(entry.get("paths", []))
    if not paths:
        reasons.append("spec_artifacts.paths is empty")
        return
    _check_paths(base_dir, paths, reasons, "spec_artifacts")
    names = {Path(value).name for value in paths}
    for required in ("proposal.md", "design.md", "tasks.md"):
        if required not in names:
            reasons.append(f"spec_artifacts missing {required}")
    normalized = [str(value).replace("\\", "/") for value in paths]
    if not any("openspec/" in value for value in normalized):
        reasons.append("spec_artifacts must reference an openspec/ path")
    if not any("specs/" in value and value.endswith(".md") for value in normalized):
        reasons.append("spec_artifacts missing a specs/**/spec.md delta")


def validate_design_package(state: dict[str, Any], base_dir: str | Path, reasons: list[str]) -> None:
    entries = _of_kind(state, "ui_direction")
    if not entries:
        reasons.append("missing ui_direction evidence")
        return
    package = entries[-1]
    for field in ("style", "palette", "typography", "layout", "source"):
        if not package.get(field):
            reasons.append(f"ui_direction.{field} is missing")
    if package.get("source") != "ui-ux-pro-max":
        reasons.append("ui_direction.source must be ui-ux-pro-max")
    if not package.get("responsive"):
        reasons.append("ui_direction.responsive is missing")

    review_path = package.get("design_review_path")
    if not review_path or not _resolve(base_dir, str(review_path)).is_file():
        reasons.append("missing design-review.md for design_approved")
    for field in ("desktop_previews", "mobile_previews"):
        paths = package.get(field, [])
        if not paths:
            reasons.append(f"missing {field} for design_approved")
            continue
        for value in paths:
            if not _resolve(base_dir, value).is_file():
                reasons.append(f"design preview path missing: {value}")
    for value in package.get("references", []):
        text = str(value)
        if text.startswith(("http://", "https://")):
            continue
        if not _resolve(base_dir, text).exists():
            reasons.append(f"design reference path missing: {text}")


def _evaluate_design_ready(state: dict[str, Any], base_dir: str | Path, reasons: list[str]) -> None:
    if not state.get("requires", {}).get("ui_design"):
        return
    validate_design_package(state, base_dir, reasons)


def _evaluate_plan_ready(state: dict[str, Any], base_dir: str | Path, reasons: list[str]) -> None:
    if not state.get("requires", {}).get("plan"):
        return
    entries = _of_kind(state, "plan")
    if not entries:
        reasons.append("missing plan evidence")
        return
    latest = entries[-1]
    if not latest.get("path"):
        reasons.append("plan.path is missing")
    else:
        _check_paths(base_dir, [latest["path"]], reasons, "plan")
    if not latest.get("test_strategy"):
        reasons.append("plan.test_strategy is empty")
    if latest.get("source") != "superpowers":
        reasons.append("plan.source must be superpowers")


def _evaluate_reviewed(state: dict[str, Any], reasons: list[str]) -> None:
    if not state.get("requires", {}).get("review"):
        return
    entries = _of_kind(state, "review")
    if not entries:
        reasons.append("missing review evidence")
        return
    latest = entries[-1]
    if "ecc" not in str(latest.get("reviewer", "")).lower():
        reasons.append("review.reviewer must identify ecc")
    if not latest.get("findings_resolved"):
        reasons.append("review.findings_resolved is false")
    if not latest.get("security_checked"):
        reasons.append("review.security_checked is false")
    if state.get("ui") and not latest.get("a11y_checked"):
        reasons.append("review.a11y_checked is false for a UI task")
    report = latest.get("report")
    if not isinstance(report, str) or not report.strip():
        reasons.append("review.report is missing")
    open_findings = latest.get("open_findings")
    if open_findings is not None and open_findings != 0:
        reasons.append("review.open_findings must be zero")


def _evaluate_verified(state: dict[str, Any], base_dir: str | Path, reasons: list[str]) -> None:
    if not state.get("requires", {}).get("verification"):
        return
    commands = _latest_commands(state)
    if not commands:
        reasons.append("missing command evidence")
    for command, entry in commands.items():
        if entry.get("result") != "passed" or entry.get("exit_code") != 0:
            reasons.append(f"command did not pass: {command}")
    if state.get("ui"):
        screenshots = _of_kind(state, "screenshot")
        if not screenshots:
            reasons.append("missing screenshot evidence for UI task")
            return
        paths = [entry.get("path") for entry in screenshots if entry.get("path")]
        if not paths:
            reasons.append("screenshot.path is missing")
        else:
            _check_paths(base_dir, paths, reasons, "screenshot")
        viewports = {entry.get("viewport") for entry in screenshots}
        if "desktop" not in viewports and "both" not in viewports:
            reasons.append("missing desktop screenshot")
        if "mobile" not in viewports and "both" not in viewports:
            reasons.append("missing mobile screenshot")


def _evaluate_consistency_verified(
    state: dict[str, Any],
    base_dir: str | Path,
    reasons: list[str],
) -> None:
    if not state.get("requires", {}).get("consistency"):
        return
    entries = _of_kind(state, "consistency_audit")
    if not entries:
        reasons.append("missing consistency_audit evidence")
        return
    latest = entries[-1]
    if latest.get("findings_resolved") is not True:
        reasons.append("consistency_audit.findings_resolved is false")
    if latest.get("open_findings") != 0:
        reasons.append("consistency_audit.open_findings must be zero")
    reasons.extend(audit_consistency(state, base_dir, latest))


def _evaluate_archived(state: dict[str, Any], base_dir: str | Path, reasons: list[str]) -> None:
    if not state.get("requires", {}).get("archive"):
        return
    entries = _of_kind(state, "archive")
    if not entries:
        reasons.append("missing archive evidence")
        return
    latest = entries[-1]
    if not latest.get("tasks_updated"):
        reasons.append("archive.tasks_updated is false")
    if not latest.get("openspec_archived"):
        reasons.append("archive.openspec_archived is false")
    if state.get("requires", {}).get("consistency"):
        audits = _of_kind(state, "consistency_audit")
        if not audits:
            reasons.append("archive requires prior consistency_audit evidence")
        else:
            reasons.extend(audit_consistency(state, base_dir, audits[-1]))


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _meaningful_evidence(entry: dict[str, Any]) -> dict[str, Any]:
    ignored = {"id", "timestamp", "kind"}
    return {"kind": entry.get("kind"), **{key: value for key, value in entry.items() if key not in ignored}}


def _file_digest(base_dir: str | Path, value: str) -> str | None:
    if value.startswith(("http://", "https://")):
        return None
    path = _resolve_existing(base_dir, value)
    if not path.is_file():
        return None
    is_plan = "plans" in {part.casefold() for part in path.parts}
    if path.name == "tasks.md" or is_plan:
        normalized = re.sub(
            r"(?m)^(\s*[-*]\s*)\[[ xX]\]",
            r"\1[ ]",
            path.read_text(encoding="utf-8"),
        )
        if is_plan:
            normalized = re.sub(
                r"(?m)^(\s*>?\s*\**\s*(?:执行状态|execution\s+status|status)\s*\**\s*[:：]).*$",
                r"\1 <status>",
                normalized,
                flags=re.IGNORECASE,
            )
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evidence_paths(state: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for entry in state.get("evidence", []):
        if entry.get("kind") in {"approval", "user_approval", "archive"}:
            continue
        if entry.get("kind") == "spec_artifacts":
            paths.extend(str(value) for value in entry.get("paths", []))
        elif entry.get("path"):
            paths.append(str(entry["path"]))
    return paths


def _git_workspace_digest(base_dir: str | Path) -> dict[str, Any]:
    try:
        root_result = subprocess.run(
            ["git", "-C", str(base_dir), "rev-parse", "--show-toplevel"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=10,
        )
        if root_result.returncode != 0:
            return {"available": False}
        root = Path(root_result.stdout.strip())
        files_result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            check=False,
            timeout=20,
        )
        if files_result.returncode != 0:
            return {"available": True, "error": "unable to enumerate files"}
        entries: list[dict[str, str]] = []
        for raw in files_result.stdout.split(b"\0"):
            if not raw:
                continue
            relative = raw.decode("utf-8", errors="surrogateescape").replace("\\", "/")
            if relative == "openspec" or relative.startswith(("openspec/", ".codex/", "artifacts/")):
                continue
            path = root / relative
            if not path.is_file():
                continue
            entries.append({
                "path": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
        entries.sort(key=lambda item: item["path"])
        return {"available": True, "files": entries}
    except (OSError, subprocess.SubprocessError):
        return {"available": False}


def _approval_inputs(state: dict[str, Any], gate: str) -> Any:
    if gate == "spec_approved":
        return [_meaningful_evidence(entry) for entry in _of_kind(state, "spec_artifacts")]
    if gate == "design_approved":
        entries = _of_kind(state, "ui_direction")
        return _meaningful_evidence(entries[-1]) if entries else {}
    if gate == "plan_approved":
        entries = _of_kind(state, "plan")
        return _meaningful_evidence(entries[-1]) if entries else {}
    if gate == "delivery_approved":
        return [
            _meaningful_evidence(entry)
            for entry in state.get("evidence", [])
            if entry.get("kind") not in {"approval", "user_approval", "archive"}
        ]
    return {}


def _approval_artifacts(state: dict[str, Any], gate: str) -> list[str]:
    if gate == "spec_approved":
        return [str(path) for entry in _of_kind(state, "spec_artifacts") for path in entry.get("paths", [])]
    if gate == "design_approved":
        entries = _of_kind(state, "ui_direction")
        if not entries:
            return []
        package = entries[-1]
        return [
            str(package.get("design_review_path", "")),
            *[str(value) for value in package.get("desktop_previews", [])],
            *[str(value) for value in package.get("mobile_previews", [])],
            *[str(value) for value in package.get("references", [])],
        ]
    if gate == "plan_approved":
        entries = _of_kind(state, "plan")
        return [str(entries[-1]["path"])] if entries and entries[-1].get("path") else []
    if gate == "delivery_approved":
        return _evidence_paths(state)
    return []


def approval_digest(state: dict[str, Any], gate: str, base_dir: str | Path = ".") -> str:
    artifacts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in _approval_artifacts(state, gate):
        if not value or value in seen:
            continue
        seen.add(value)
        artifacts.append({"path": value, "sha256": _file_digest(base_dir, value)})
    artifacts.sort(key=lambda item: item["path"])
    payload: dict[str, Any] = {
        "gate": gate,
        "inputs": _approval_inputs(state, gate),
        "artifacts": artifacts,
    }
    if gate == "delivery_approved":
        payload["workspace"] = _git_workspace_digest(base_dir)
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def valid_approval(state: dict[str, Any], gate: str, base_dir: str | Path = ".") -> tuple[bool, list[str]]:
    requests = [item for item in state.get("approval_requests", []) if item.get("gate") == gate]
    if not requests:
        return False, [f"missing interactive approval request for {gate}"]
    approvals = [item for item in _of_kind(state, "approval") if item.get("gate") == gate]
    if not approvals:
        return False, [f"missing interactive approval for {gate}"]

    latest_request = requests[-1]
    latest = approvals[-1]
    reasons: list[str] = []
    if latest.get("token") != latest_request.get("token"):
        reasons.append(f"approval token is stale for {gate}")
    if latest.get("requested_at") != latest_request.get("requested_at"):
        reasons.append(f"approval request timestamp does not match for {gate}")
    current_digest = approval_digest(state, gate, base_dir)
    if latest_request.get("artifact_digest") != current_digest:
        reasons.append(f"approval request digest is stale for {gate}")
    if latest.get("artifact_digest") != current_digest:
        reasons.append(f"approval digest does not match current artifacts for {gate}")
    if latest.get("approved") is not True:
        reasons.append(f"approval must be approved for {gate}")
    if latest.get("approved_by") != "user" or latest.get("explicit") is not True:
        reasons.append(f"approval must be explicit user approval for {gate}")
    if latest.get("method") not in APPROVAL_METHODS:
        reasons.append(f"approval method is not interactive for {gate}")
    return not reasons, reasons


def append_approval(
    state: dict[str, Any],
    gate: str,
    base_dir: str | Path,
    token: str,
    method: str,
) -> dict[str, Any]:
    if method not in APPROVAL_METHODS:
        raise ValueError(f"approval method must be 'tty': {method}")
    requests = [item for item in state.get("approval_requests", []) if item.get("gate") == gate]
    if not requests:
        raise ValueError(f"missing approval request for {gate}")
    request = requests[-1]
    if request.get("token") != token:
        raise ValueError(f"approval token is stale for {gate}")
    if request.get("artifact_digest") != approval_digest(state, gate, base_dir):
        raise ValueError(f"approval digest is stale for {gate}")
    return append_evidence(state, {
        "kind": "approval",
        "gate": gate,
        "scope": gate,
        "approved": True,
        "approved_by": "user",
        "explicit": True,
        "token": token,
        "artifact_digest": request["artifact_digest"],
        "method": method,
        "requested_at": request["requested_at"],
        "approved_at": now_iso(),
    })


def _evaluate_approval(state: dict[str, Any], gate: str, base_dir: str | Path, reasons: list[str]) -> None:
    requirement = REQUIREMENT_BY_GATE[gate]
    if not state.get("requires", {}).get(requirement):
        return
    if gate == "design_approved":
        validate_design_package(state, base_dir, reasons)
    ok, approval_reasons = valid_approval(state, gate, base_dir)
    if not ok:
        reasons.extend(approval_reasons)


def evaluate_gate(state: dict[str, Any], gate: str, base_dir: str | Path = ".") -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if gate == "route":
        _evaluate_route(state, reasons)
    elif gate == "spec_ready":
        _evaluate_spec_ready(state, base_dir, reasons)
    elif gate in APPROVAL_GATES:
        _evaluate_approval(state, gate, base_dir, reasons)
    elif gate == "design_ready":
        _evaluate_design_ready(state, base_dir, reasons)
    elif gate == "plan_ready":
        _evaluate_plan_ready(state, base_dir, reasons)
    elif gate == "reviewed":
        _evaluate_reviewed(state, reasons)
    elif gate == "verified":
        _evaluate_verified(state, base_dir, reasons)
    elif gate == "consistency_verified":
        _evaluate_consistency_verified(state, base_dir, reasons)
    elif gate == "archived":
        _evaluate_archived(state, base_dir, reasons)
    else:
        raise ValueError(f"unknown gate: {gate}")
    return (not reasons, reasons)


def evaluate_all(state: dict[str, Any], base_dir: str | Path = ".") -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for gate in GATE_ORDER:
        passed, reasons = evaluate_gate(state, gate, base_dir)
        state.setdefault("gates", {})[gate] = passed
        results[gate] = {"ok": passed, "reasons": reasons}
    return results


def next_gate(state: dict[str, Any], base_dir: str | Path = ".") -> str | None:
    for gate in GATE_ORDER:
        if not gate_is_required(state, gate):
            continue
        passed, _ = evaluate_gate(state, gate, base_dir)
        if not passed:
            return gate
    return None


def next_human_approval_gate(state: dict[str, Any], base_dir: str | Path = ".") -> str | None:
    for gate in GATE_ORDER:
        if not gate_is_required(state, gate):
            continue
        passed, _ = evaluate_gate(state, gate, base_dir)
        if passed:
            continue
        return gate if gate in APPROVAL_GATES else None
    return None


def advance_state(state: dict[str, Any], base_dir: str | Path = ".", path: str | Path | None = None) -> dict[str, Any]:
    results = evaluate_all(state, base_dir)
    for gate in GATE_ORDER:
        if not gate_is_required(state, gate):
            continue
        if not results[gate]["ok"]:
            break
        set_phase(state, GATE_TO_PHASE[gate])
    if path is not None:
        save_state(path, state)
    return results
