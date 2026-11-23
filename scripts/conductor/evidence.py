from __future__ import annotations

from typing import Any

EVIDENCE_KINDS = (
    "spec_artifacts",
    "user_approval",
    "approval",
    "ui_direction",
    "plan",
    "command",
    "screenshot",
    "review",
    "consistency_audit",
    "archive",
)

APPROVAL_METHODS = frozenset({"tty"})


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item.strip() for item in value)


def validate_evidence(kind: str, data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if kind not in EVIDENCE_KINDS:
        return [f"unknown evidence kind: {kind}"]

    if kind == "spec_artifacts":
        if not _is_string_list(data.get("paths")):
            errors.append("spec_artifacts.paths must be a non-empty list of paths")
    elif kind == "approval":
        for field in ("gate", "scope", "token", "artifact_digest", "requested_at", "approved_at"):
            if not isinstance(data.get(field), str) or not data.get(field, "").strip():
                errors.append(f"approval.{field} must be a non-empty string")
        if data.get("approved") is not True:
            errors.append("approval.approved must be true")
        if data.get("approved_by") != "user":
            errors.append("approval.approved_by must be 'user'")
        if data.get("explicit") is not True:
            errors.append("approval.explicit must be true")
        if data.get("method") not in APPROVAL_METHODS:
            errors.append("approval.method must be 'tty'")
    elif kind == "user_approval":
        if data.get("approved") is not True:
            errors.append("user_approval.approved must be true")
        if not isinstance(data.get("approved_by"), str) or not data.get("approved_by", "").strip():
            errors.append("user_approval.approved_by must be a non-empty string")
        if not isinstance(data.get("scope"), str) or not data.get("scope", "").strip():
            errors.append("user_approval.scope must be a non-empty string")
    elif kind == "ui_direction":
        for field in ("style", "palette", "typography", "layout", "source"):
            if not isinstance(data.get(field), str) or not data.get(field, "").strip():
                errors.append(f"ui_direction.{field} must be a non-empty string")
        if "responsive" in data and (not isinstance(data["responsive"], str) or not data["responsive"].strip()):
            errors.append("ui_direction.responsive must be a non-empty string when present")
        if "design_review_path" in data and (
            not isinstance(data["design_review_path"], str) or not data["design_review_path"].strip()
        ):
            errors.append("ui_direction.design_review_path must be a non-empty string when present")
        for field in ("desktop_previews", "mobile_previews", "references"):
            if field in data and not _is_string_list(data[field]):
                errors.append(f"ui_direction.{field} must be a non-empty list of paths when present")
    elif kind == "plan":
        if not isinstance(data.get("path"), str) or not data.get("path", "").strip():
            errors.append("plan.path must be a non-empty string")
        if not _is_string_list(data.get("test_strategy")):
            errors.append("plan.test_strategy must be a non-empty list of strings")
        if not isinstance(data.get("source"), str) or not data.get("source", "").strip():
            errors.append("plan.source must be a non-empty string")
    elif kind == "command":
        if not isinstance(data.get("command"), str) or not data.get("command", "").strip():
            errors.append("command.command must be a non-empty string")
        if data.get("result") not in {"passed", "failed"}:
            errors.append("command.result must be 'passed' or 'failed'")
        if not isinstance(data.get("exit_code"), int) or isinstance(data.get("exit_code"), bool):
            errors.append("command.exit_code must be an integer")
    elif kind == "screenshot":
        if not isinstance(data.get("path"), str) or not data.get("path", "").strip():
            errors.append("screenshot.path must be a non-empty string")
        if data.get("viewport") not in {"desktop", "mobile", "both"}:
            errors.append("screenshot.viewport must be 'desktop', 'mobile', or 'both'")
        if not isinstance(data.get("label"), str) or not data.get("label", "").strip():
            errors.append("screenshot.label must be a non-empty string")
    elif kind == "review":
        if not isinstance(data.get("reviewer"), str) or not data.get("reviewer", "").strip():
            errors.append("review.reviewer must be a non-empty string")
        for field in ("findings_resolved", "security_checked"):
            if not isinstance(data.get(field), bool):
                errors.append(f"review.{field} must be a boolean")
        if "a11y_checked" in data and not isinstance(data["a11y_checked"], bool):
            errors.append("review.a11y_checked must be a boolean when present")
        if "report" in data and (not isinstance(data["report"], str) or not data["report"].strip()):
            errors.append("review.report must be a non-empty string when present")
        if "open_findings" in data and (
            not isinstance(data["open_findings"], int)
            or isinstance(data["open_findings"], bool)
            or data["open_findings"] < 0
        ):
            errors.append("review.open_findings must be a non-negative integer when present")
    elif kind == "consistency_audit":
        if not isinstance(data.get("report"), str) or not data.get("report", "").strip():
            errors.append("consistency_audit.report must be a non-empty string")
        if data.get("findings_resolved") is not True:
            errors.append("consistency_audit.findings_resolved must be true")
        if (
            not isinstance(data.get("open_findings"), int)
            or isinstance(data.get("open_findings"), bool)
            or data["open_findings"] != 0
        ):
            errors.append("consistency_audit.open_findings must be the integer 0")
        if "scope_paths" in data and not _is_string_list(data["scope_paths"]):
            errors.append("consistency_audit.scope_paths must be a non-empty list when present")
        checks = data.get("checks", [])
        if not isinstance(checks, list):
            errors.append("consistency_audit.checks must be a list")
        else:
            for index, check in enumerate(checks, 1):
                if not isinstance(check, dict):
                    errors.append(f"consistency_audit.checks[{index}] must be an object")
                    continue
                if not isinstance(check.get("path"), str) or not check.get("path", "").strip():
                    errors.append(f"consistency_audit.checks[{index}].path must be non-empty")
                if not isinstance(check.get("pattern"), str) or not check.get("pattern"):
                    errors.append(f"consistency_audit.checks[{index}].pattern must be non-empty")
                if check.get("expect") not in {"present", "absent"}:
                    errors.append(f"consistency_audit.checks[{index}].expect must be 'present' or 'absent'")
                if "description" in check and not isinstance(check["description"], str):
                    errors.append(f"consistency_audit.checks[{index}].description must be a string")
    elif kind == "archive":
        for field in ("tasks_updated", "openspec_archived"):
            if not isinstance(data.get(field), bool):
                errors.append(f"archive.{field} must be a boolean")

    return errors
