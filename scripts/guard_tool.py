#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conductor.gates import evaluate_gate, next_human_approval_gate
from conductor.model import load_state

HANDSHAKE = {"approve.py", "gate.py", "record.py", "verify_evidence.py", "host_trust.py"}
READ_ONLY = {"cat", "echo", "file", "find", "jq", "ls", "pwd", "rg", "sed", "stat", "tail", "head", "wc", "which"}
READ_TOOLS = {
    "Read",
    "read_file",
    "view_image",
    "list_directory",
    "list_dir",
    "glob",
    "grep_search",
    "file_search",
}
WRITE_TOOL_MARKERS = (
    "write",
    "edit",
    "patch",
    "create_file",
    "replace_file",
    "notebookedit",
    "delete_file",
    "remove_file",
    "move_file",
    "rename_file",
    "make_directory",
    "mkdir",
)
ARTIFACT_PREFIXES = (
    ("openspec",),
    ("docs", "plans"),
    ("docs", "superpowers", "plans"),
    ("artifacts", "design"),
)
ARTIFACT_FILE_COMMANDS = {"mkdir", "touch", "cp", "mv"}
IMAGE_COMMANDS = {"sips", "magick", "convert"}
FILE_PATH_KEYS = ("file_path", "path", "target_file", "notebook_path", "file")
PATCH_PATH_RE = re.compile(
    r"^\*\*\* (?:Add File|Update File|Delete File|Move to):[ \t]*(.+?)[ \t]*$",
    re.MULTILINE,
)


def deny(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def emit_denial(reason: str) -> None:
    print(json.dumps(deny(reason), ensure_ascii=False))


def load_state_for_payload(payload: dict) -> tuple[dict | None, str | None]:
    cwd = Path(str(payload.get("cwd") or os.getcwd())).expanduser().resolve()
    configured = os.environ.get("DELIVERY_CONDUCTOR_STATE", ".codex/delivery-state.json")
    state_path = Path(configured)
    if not state_path.is_absolute():
        state_path = cwd / state_path
    if not state_path.exists():
        return None, None
    try:
        return load_state(state_path), None
    except (OSError, ValueError) as error:
        return None, f"Cannot read conductor state {state_path}: {error}"


def has_shell_control(command: str) -> bool:
    return any(token in command for token in (";", "&&", "||", ">", "<", "`", "$("))


def allowed_read_only(args: list[str]) -> bool:
    program = Path(args[0]).name
    if program == "git":
        forbidden = {"--ext-diff", "--textconv", "--exec-path", "--config-env"}
        return (
            len(args) >= 2
            and args[1] in {"diff", "log", "ls-files", "rev-parse", "show", "status"}
            and not any(value.startswith("--output") or value in forbidden or value.startswith("-c") for value in args[2:])
        )
    if program == "openspec":
        return len(args) >= 2 and args[1] in {"diff", "list", "show", "status", "validate"}
    if program == "find":
        return not any(
            value in {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fls"}
            for value in args[1:]
        )
    if program == "sed":
        return not any(
            value == "--in-place" or bool(re.match(r"^-[A-Za-z]*i", value))
            for value in args[1:]
        )
    if program == "rg":
        return not any(value.startswith(("--pre", "--hostname-bin")) for value in args[1:])
    return program in READ_ONLY


def handshake_or_read_only(args: list[str]) -> bool:
    program = Path(args[0]).name
    if program in HANDSHAKE:
        return True
    if (
        program in {"python", "python3"} or re.match(r"^python3\.\d+$", program)
    ) and len(args) >= 2 and Path(args[1]).name in HANDSHAKE:
        return True
    return allowed_read_only(args)


def semantic_assessment_command_allowed(command: str) -> bool:
    if not command.strip() or has_shell_control(command):
        return False
    try:
        args = shlex.split(command)
    except ValueError:
        return False
    if len(args) < 3:
        return False
    program = Path(args[0]).name
    if program not in {"python", "python3"} and not re.match(r"^python3\.\d+$", program):
        return False
    if Path(args[1]).name != "route.py":
        return False
    if "--assess-json" not in args:
        return False
    forbidden = {"--task", "--tier", "--provisional", "--force", "--force-reassess"}
    return not any(value in forbidden for value in args[2:])


def routing_inspection_command_allowed(command: str) -> bool:
    if not command.strip() or has_shell_control(command):
        return False
    try:
        args = shlex.split(command)
    except ValueError:
        return False
    if not args:
        return False
    if allowed_read_only(args):
        return True
    program = Path(args[0]).name
    return (
        (program in {"python", "python3"} or re.match(r"^python3\.\d+$", program))
        and len(args) >= 2
        and Path(args[1]).name in {"gate.py", "host_trust.py"}
    )


def shell_allowed(command: str) -> bool:
    if not command.strip() or has_shell_control(command):
        return False
    try:
        args = shlex.split(command)
    except ValueError:
        return False
    if not args:
        return False
    return handshake_or_read_only(args)


def _resolve_project_path(base_dir: Path, value: str) -> Path | None:
    text = str(value).strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def artifact_path_allowed(base_dir: Path, value: str) -> bool:
    path = _resolve_project_path(base_dir, value)
    if path is None:
        return False
    try:
        relative = path.relative_to(base_dir)
    except ValueError:
        return True
    parts = relative.parts
    if not parts:
        return False
    return any(parts[: len(prefix)] == prefix for prefix in ARTIFACT_PREFIXES)


def _non_option_args(args: list[str]) -> list[str]:
    return [arg for arg in args[1:] if not arg.startswith("-")]


def artifact_file_command_allowed(program: str, args: list[str], base_dir: Path) -> bool:
    if program in {"mkdir", "touch"}:
        values = _non_option_args(args)
        return bool(values) and all(artifact_path_allowed(base_dir, value) for value in values)
    if program == "cp":
        target: str | None = None
        sources: list[str] = []
        index = 1
        while index < len(args):
            arg = args[index]
            if arg in {"-t", "--target-directory"} and index + 1 < len(args):
                target = args[index + 1]
                index += 2
                continue
            if arg.startswith("--target-directory="):
                target = arg.split("=", 1)[1]
                index += 1
                continue
            if arg.startswith("-"):
                index += 1
                continue
            sources.append(arg)
            index += 1
        if not sources:
            return False
        if target is not None:
            return artifact_path_allowed(base_dir, target)
        return artifact_path_allowed(base_dir, sources[-1])
    if program == "mv":
        values = _non_option_args(args)
        return bool(values) and all(artifact_path_allowed(base_dir, value) for value in values)
    return False


def image_command_allowed(program: str, args: list[str], base_dir: Path) -> bool:
    if program == "sips":
        target: str | None = None
        values: list[str] = []
        index = 1
        while index < len(args):
            arg = args[index]
            if arg in {"--out", "-o"} and index + 1 < len(args):
                target = args[index + 1]
                index += 2
                continue
            if arg.startswith("--out="):
                target = arg.split("=", 1)[1]
                index += 1
                continue
            if not arg.startswith("-"):
                values.append(arg)
            index += 1
        if target is None:
            target = values[-1] if values else None
        return target is not None and artifact_path_allowed(base_dir, target)
    values = _non_option_args(args)
    return len(values) >= 2 and artifact_path_allowed(base_dir, values[-1])


def _first_non_option(args: list[str]) -> str:
    return next((arg for arg in args[1:] if not arg.startswith("-")), "")


def generator_allowed(args: list[str], base_dir: Path) -> bool:
    script = _first_non_option(args).replace("\\", "/")
    if script.endswith("ui-ux-pro-max/scripts/search.py"):
        return True
    if script.endswith("chrome-cdp.mjs"):
        remaining = args[2:]
        if not remaining:
            return True
        subcommand = remaining[0]
        if subcommand == "shot":
            return len(remaining) >= 2 and artifact_path_allowed(base_dir, remaining[1])
        return subcommand in {"eval", "goto", "list"}
    return False


def is_apply_patch_command(command: str) -> bool:
    stripped = command.strip()
    if not stripped:
        return False
    first = stripped.split(None, 1)[0]
    return Path(first).name == "apply_patch"


def apply_patch_command_allowed(command: str, base_dir: Path) -> bool:
    if "*** Begin Patch" not in command or "*** End Patch" not in command:
        return False
    prefix, _, remainder = command.partition("*** Begin Patch")
    if any(token in prefix for token in ("&&", "||", ";", "`", "$(")):
        return False
    suffix = remainder.split("*** End Patch", 1)[1]
    if any(token in suffix for token in ("&&", "||", ";", "|", "`", "$(")):
        return False
    paths = PATCH_PATH_RE.findall(command)
    return bool(paths) and all(artifact_path_allowed(base_dir, path) for path in paths)


def pre_implementation_shell_allowed(command: str, base_dir: Path) -> bool:
    if not command.strip():
        return False
    if is_apply_patch_command(command):
        return apply_patch_command_allowed(command, base_dir)
    if has_shell_control(command):
        return False
    try:
        args = shlex.split(command)
    except ValueError:
        return False
    if not args:
        return False
    if handshake_or_read_only(args):
        return True
    program = Path(args[0]).name
    if program in ARTIFACT_FILE_COMMANDS:
        return artifact_file_command_allowed(program, args, base_dir)
    if program in IMAGE_COMMANDS:
        return image_command_allowed(program, args, base_dir)
    if program in {"python", "python3", "node"} or re.match(r"^python3\.\d+$", program):
        return generator_allowed(args, base_dir)
    return False


def _patch_text(tool_input: dict) -> str:
    for key in ("input", "patch", "command", "cmd"):
        value = tool_input.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "\n".join(str(item) for item in value)
    return ""


def extract_write_paths(tool_input: dict) -> list[str]:
    paths: list[str] = []
    for key in FILE_PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            paths.append(value)
    files = tool_input.get("files")
    if isinstance(files, list):
        for item in files:
            if isinstance(item, str):
                paths.append(item)
            elif isinstance(item, dict):
                for key in FILE_PATH_KEYS:
                    value = item.get(key)
                    if isinstance(value, str) and value:
                        paths.append(value)
    return paths


def write_tool_allowed(tool: str, tool_input: dict, base_dir: Path) -> bool:
    lowered = tool.lower()
    patch_text = _patch_text(tool_input)
    if "apply_patch" in lowered or "patch" in lowered or "*** Begin Patch" in patch_text:
        paths = PATCH_PATH_RE.findall(patch_text)
        return bool(paths) and all(artifact_path_allowed(base_dir, path) for path in paths)
    paths = extract_write_paths(tool_input)
    return bool(paths) and all(artifact_path_allowed(base_dir, path) for path in paths)


def plan_approval_required(state: dict, base_dir: Path) -> bool:
    if not state.get("requires", {}).get("plan_approval"):
        return False
    passed, _ = evaluate_gate(state, "plan_approved", base_dir)
    return not passed


def pre_implementation_tool_allowed(tool: str, lowered: str, tool_input: dict, base_dir: Path) -> bool:
    if tool in READ_TOOLS:
        return True
    if any(marker in lowered for marker in WRITE_TOOL_MARKERS):
        return write_tool_allowed(tool, tool_input, base_dir)
    if lowered in {"bash", "shell", "exec_command", "exec"}:
        command = tool_input.get("command", tool_input.get("cmd", ""))
        return isinstance(command, str) and pre_implementation_shell_allowed(command, base_dir)
    return True


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        emit_denial("Could not parse the PreToolUse payload; refusing to continue while approval may be pending.")
        return 0
    if not isinstance(payload, dict):
        emit_denial("Invalid PreToolUse payload; refusing to continue while approval may be pending.")
        return 0

    state, state_error = load_state_for_payload(payload)
    if state_error:
        emit_denial(state_error)
        return 0
    if state is None:
        return 0

    cwd = Path(str(payload.get("cwd") or os.getcwd())).expanduser().resolve()
    tool = str(payload.get("tool_name", ""))
    lowered = tool.lower()
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}

    if state.get("route_status") == "provisional" or state.get("phase") == "ROUTING":
        if tool in READ_TOOLS:
            return 0
        if lowered in {"bash", "shell", "exec_command", "exec"}:
            command = tool_input.get("command", tool_input.get("cmd", ""))
            if isinstance(command, str) and routing_inspection_command_allowed(command):
                return 0
            if isinstance(command, str) and semantic_assessment_command_allowed(command):
                return 0
        emit_denial(
            "ROUTING assessment is pending. Before writing or implementing, inspect the task and "
            "repository read-only, then run route.py with --assess-json."
        )
        return 0

    pending = next_human_approval_gate(state, cwd)
    if pending is not None:
        if tool in READ_TOOLS:
            return 0
        if any(marker in lowered for marker in WRITE_TOOL_MARKERS):
            emit_denial(f"Approval gate {pending} is pending. File modifications are blocked until the user approves.")
            return 0
        if lowered in {"bash", "shell", "exec_command", "exec"}:
            command = tool_input.get("command", tool_input.get("cmd", ""))
            if isinstance(command, str) and shell_allowed(command):
                return 0
            emit_denial(
                f"Approval gate {pending} is pending. This shell command is not on the read-only or approval allowlist."
            )
            return 0
        emit_denial(f"Approval gate {pending} is pending. Tool {tool or '<unknown>'} is not allowed until approval.")
        return 0

    if plan_approval_required(state, cwd) and not pre_implementation_tool_allowed(tool, lowered, tool_input, cwd):
        emit_denial(
            "Plan approval is required before implementation. Before plan_approved, only "
            "openspec/, docs/plans/, docs/superpowers/plans/, and artifacts/design/ may be modified; "
            "read-only inspection, approval handshakes, and design preview generation remain allowed."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
