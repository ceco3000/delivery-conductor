"""Final delivery consistency checks shared by gates and simulations."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

CHECKBOX_RE = re.compile(r"^(?P<prefix>\s*[-*]\s+)\[(?P<state>[ xX])\]\s*(?P<text>.*)$")
STATUS_RE = re.compile(
    r"^\s*>?\s*\**\s*(?:执行状态|execution\s+status|status)\s*\**\s*[:：]\s*(?P<value>.+?)\s*$",
    re.IGNORECASE,
)
FINAL_PLAN_MARKERS = (
    "已完成",
    "已归档",
    "completed",
    "done",
    "finished",
)
SUPERSEDED_PLAN_MARKERS = (
    "已取代",
    "已被",
    "取代",
    "superseded",
    "replaced",
    "obsolete",
)
PENDING_PLAN_MARKERS = (
    "进行中",
    "未开始",
    "待办",
    "in progress",
    "pending",
    "active",
)


def _display_path(base_dir: str | Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(Path(base_dir).resolve()).as_posix()
    except ValueError:
        return str(path)


def _visible_lines(text: str) -> Iterable[tuple[int, str]]:
    """Yield non-code-fence lines with their original line numbers."""

    in_fence = False
    for line_number, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if not in_fence:
            yield line_number, line


def checkbox_summary(text: str) -> tuple[int, list[tuple[int, str]]]:
    checked = 0
    open_items: list[tuple[int, str]] = []
    for line_number, line in _visible_lines(text):
        match = CHECKBOX_RE.match(line)
        if not match:
            continue
        if match.group("state") in {"x", "X"}:
            checked += 1
        else:
            open_items.append((line_number, match.group("text").strip()))
    return checked, open_items


def execution_status(text: str) -> str | None:
    for _line_number, line in list(_visible_lines(text))[:60]:
        match = STATUS_RE.match(line)
        if match:
            return match.group("value").strip()
    return None


def _contains_marker(value: str, markers: Iterable[str]) -> bool:
    lowered = value.casefold()
    return any(marker.casefold() in lowered for marker in markers)


def _open_summary(items: list[tuple[int, str]]) -> str:
    preview = "; ".join(f"L{line}: {text[:80]}" for line, text in items[:3])
    suffix = f" (+{len(items) - 3} more)" if len(items) > 3 else ""
    return f"{len(items)} unchecked item(s): {preview}{suffix}"


def _is_plan_path(path: Path) -> bool:
    parts = {part.casefold() for part in path.parts}
    return "plans" in parts or path.name.casefold() == "plan.md"


def audit_tasks_file(path: Path, base_dir: str | Path) -> list[str]:
    if not path.is_file():
        return [f"task file missing: {_display_path(base_dir, path)}"]
    text = path.read_text(encoding="utf-8")
    _checked, open_items = checkbox_summary(text)
    if open_items:
        return [
            f"{_display_path(base_dir, path)} has {_open_summary(open_items)}"
        ]
    return []


def audit_plan_file(path: Path, base_dir: str | Path) -> list[str]:
    if not path.is_file():
        return [f"plan file missing: {_display_path(base_dir, path)}"]
    text = path.read_text(encoding="utf-8")
    checked, open_items = checkbox_summary(text)
    status = execution_status(text)
    display = _display_path(base_dir, path)
    errors: list[str] = []
    if status is None:
        errors.append(f"{display} is missing a final execution status line")
    elif _contains_marker(status, PENDING_PLAN_MARKERS) and not _contains_marker(
        status, FINAL_PLAN_MARKERS
    ):
        errors.append(f"{display} still declares a non-final status: {status}")

    completed = status is not None and _contains_marker(status, FINAL_PLAN_MARKERS)
    superseded = status is not None and _contains_marker(status, SUPERSEDED_PLAN_MARKERS)
    if open_items and completed and not superseded:
        errors.append(
            f"{display} declares completed but has {_open_summary(open_items)}"
        )
    elif open_items and not superseded:
        errors.append(f"{display} has {_open_summary(open_items)}")

    if checked == 0 and not open_items and status is None:
        errors.append(f"{display} has no execution checkboxes or status evidence")
    return errors


def _resolve(base_dir: str | Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = Path(base_dir) / path
    return path.resolve()


def _resolve_existing(base_dir: str | Path, value: str | Path) -> Path:
    """Resolve a path that may have been moved into an OpenSpec archive."""

    path = _resolve(base_dir, str(value))
    if path.exists():
        return path
    parts = path.parts
    marker = next(
        (
            index
            for index in range(len(parts) - 2)
            if parts[index : index + 2] == ("openspec", "changes")
        ),
        None,
    )
    if marker is None or len(parts) < marker + 4 or parts[marker + 2] == "archive":
        return path
    change_name = parts[marker + 2]
    suffix = Path(*parts[marker + 3 :])
    project_root = Path(*parts[:marker]) if marker else Path(".")
    archive_root = project_root / "openspec" / "changes" / "archive"
    if not archive_root.is_dir():
        return path
    for candidate in sorted(
        item
        for item in archive_root.iterdir()
        if item.is_dir() and (item.name == change_name or item.name.endswith(f"-{change_name}"))
    ):
        archived_path = candidate / suffix
        if archived_path.is_file():
            return archived_path.resolve()
    return path


def _unique(paths: Iterable[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(resolved)
    return sorted(result, key=lambda item: item.as_posix())


def collect_task_files(state: dict[str, Any], base_dir: str | Path) -> list[Path]:
    paths: list[Path] = []
    for entry in state.get("evidence", []):
        if entry.get("kind") == "spec_artifacts":
            paths.extend(
                _resolve_existing(base_dir, value)
                for value in entry.get("paths", [])
                if str(value).endswith("tasks.md")
            )
    archive_root = Path(base_dir) / "openspec" / "changes" / "archive"
    if archive_root.is_dir():
        paths.extend(archive_root.glob("*/tasks.md"))
    return _unique(paths)


def collect_plan_files(state: dict[str, Any], base_dir: str | Path) -> list[Path]:
    paths: list[Path] = []
    for entry in state.get("evidence", []):
        if entry.get("kind") == "plan" and entry.get("path"):
            paths.append(_resolve(base_dir, str(entry["path"])))
    plan_root = Path(base_dir) / "docs" / "superpowers" / "plans"
    if plan_root.is_dir():
        paths.extend(plan_root.glob("*.md"))
    return _unique(paths)


def audit_checks(base_dir: str | Path, checks: Any) -> list[str]:
    if checks is None:
        return []
    if not isinstance(checks, list):
        return ["consistency_audit.checks must be a list"]
    errors: list[str] = []
    for index, check in enumerate(checks, 1):
        if not isinstance(check, dict):
            errors.append(f"consistency_audit.checks[{index}] must be an object")
            continue
        value = check.get("path")
        pattern = check.get("pattern")
        expectation = check.get("expect")
        description = str(check.get("description") or "").strip()
        label = f"consistency check {index}"
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{label}: path must be a non-empty string")
            continue
        if not isinstance(pattern, str) or not pattern:
            errors.append(f"{label}: pattern must be a non-empty string")
            continue
        if expectation not in {"present", "absent"}:
            errors.append(f"{label}: expect must be 'present' or 'absent'")
            continue
        path = _resolve(base_dir, value)
        if not path.is_file():
            errors.append(f"{label}: file missing: {value}")
            continue
        try:
            regex = re.compile(pattern, re.MULTILINE)
        except re.error as error:
            errors.append(f"{label}: invalid regex: {error}")
            continue
        matched = regex.search(path.read_text(encoding="utf-8")) is not None
        if expectation == "present" and not matched:
            errors.append(f"{label}: expected pattern was not found in {value}")
        if expectation == "absent" and matched:
            suffix = f" ({description})" if description else ""
            errors.append(f"{label}: forbidden pattern still present in {value}{suffix}")
    return errors


def audit_consistency(
    state: dict[str, Any],
    base_dir: str | Path,
    evidence: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    report = evidence.get("report")
    if not isinstance(report, str) or not report.strip():
        errors.append("consistency_audit.report is missing")
    else:
        report_path = _resolve(base_dir, report)
        if not report_path.is_file():
            errors.append(f"consistency report missing: {report}")
        elif not report_path.read_text(encoding="utf-8").strip():
            errors.append(f"consistency report is empty: {report}")

    for path in collect_task_files(state, base_dir):
        errors.extend(audit_tasks_file(path, base_dir))
    for path in collect_plan_files(state, base_dir):
        errors.extend(audit_plan_file(path, base_dir))
    errors.extend(audit_checks(base_dir, evidence.get("checks")))

    for entry in state.get("evidence", []):
        if entry.get("kind") != "review":
            continue
        review_report = entry.get("report")
        if not isinstance(review_report, str) or not review_report.strip():
            errors.append("review.report is missing from review evidence")
            continue
        review_path = _resolve(base_dir, review_report)
        if not review_path.is_file():
            errors.append(f"review report missing: {review_report}")
        if entry.get("findings_resolved") is not True:
            errors.append(f"review report has unresolved findings: {review_report}")
    return errors
