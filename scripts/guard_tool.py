#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conductor.gates import evaluate_gate, next_human_approval_gate
from conductor.model import load_state
from conductor.runtime import configure_stdio, has_shell_control, program_name, split_command

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HANDSHAKE = {"approve.py", "gate.py", "record.py", "verify_evidence.py", "host_trust.py"}
READ_ONLY = {
    "cat",
    "echo",
    "file",
    "find",
    "jq",
    "ls",
    "pwd",
    "rg",
    "sed",
    "stat",
    "tail",
    "head",
    "wc",
    "which",
    "get-content",
    "get-childitem",
    "select-string",
    "test-path",
    "resolve-path",
    "where.exe",
}
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
SAFE_NON_MUTATING_TOOLS = {
    "request_user_input",
    "update_plan",
    "todowrite",
    "todo_write",
    "web_search",
    "websearch",
    "web.run",
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
COPY_LIKE_SAFE_SHORT_OPTIONS = {
    "cp": frozenset("finpRruvT"),
    "mv": frozenset("finuvT"),
}
GIT_READ_ONLY_SUBCOMMANDS = {"ls-files", "rev-parse", "status", "diff", "log", "show"}
GIT_HARDENED_DIFF_SUBCOMMANDS = {"diff", "log", "show"}
GIT_REQUIRED_DIFF_OPTIONS = {"--no-ext-diff", "--no-textconv"}
GIT_REQUIRED_LOG_OPTIONS = {"--no-show-signature"}
FILE_SAFE_SHORT_OPTIONS = frozenset("biIklLnNprsvz")
FILE_SAFE_LONG_OPTIONS = frozenset({
    "--brief",
    "--dereference",
    "--help",
    "--keep-going",
    "--list",
    "--mime",
    "--mime-type",
    "--no-buffer",
    "--no-pad",
    "--preserve-date",
    "--raw",
    "--special-files",
    "--uncompress",
    "--version",
})
APPLY_PATCH_TOOL_NAMES = frozenset({"apply_patch", "applypatch", "patch"})
UI_SEARCH_FLAG_OPTIONS = {"--design-system", "-ds", "--full", "--json"}
UI_SEARCH_VALUE_OPTIONS = {
    "--density",
    "--domain",
    "--format",
    "--max-results",
    "--motion",
    "--project-name",
    "--stack",
    "--variance",
    "-d",
    "-f",
    "-n",
    "-p",
    "-s",
}
UI_SEARCH_SHORT_VALUE_OPTIONS = frozenset({"-d", "-f", "-n", "-p", "-s"})
FILE_PATH_KEYS = ("file_path", "path", "target_file", "notebook_path", "file")
SINGLE_PATH_WRITE_TOOLS = frozenset({
    "create_file",
    "createfile",
    "delete_file",
    "deletefile",
    "edit",
    "edit_file",
    "editfile",
    "make_directory",
    "makedirectory",
    "mkdir",
    "multi_edit",
    "multiedit",
    "notebook_edit",
    "notebookedit",
    "remove_file",
    "removefile",
    "replace_file",
    "replacefile",
    "write",
    "write_file",
    "writefile",
})
DUAL_PATH_WRITE_TOOLS = frozenset({"move_file", "movefile", "rename_file", "renamefile"})
DUAL_WRITE_PATH_KEYS = (
    *FILE_PATH_KEYS,
    "source",
    "source_path",
    "src",
    "from",
    "old_path",
    "target",
    "destination",
    "destination_path",
    "dst",
    "to",
    "new_path",
)
WRITE_TOOL_PATH_KEYS = {
    **{name: FILE_PATH_KEYS for name in SINGLE_PATH_WRITE_TOOLS},
    **{name: DUAL_WRITE_PATH_KEYS for name in DUAL_PATH_WRITE_TOOLS},
}
PATCH_PATH_RE = re.compile(
    r"^\*\*\* (?:Add File|Update File|Delete File|Move to):[ \t]*(.+?)[ \t]*$",
    re.MULTILINE,
)
PATCH_HEREDOC_PREFIX_RE = re.compile(
    r"^[ \t]*apply_patch[ \t]+<<-?[ \t]*(?P<quote>')"
    r"(?P<delimiter>[A-Za-z_][A-Za-z0-9_]*)(?P=quote)[ \t]*(?:\r?\n)?$"
)
SED_SAFE_SCRIPT_RE = re.compile(
    r"(?:(?:\d+|\$)(?:,(?:\d+|\$))?p)"
    r"(?:[ \t]*;[ \t]*(?:\d+|\$)(?:,(?:\d+|\$))?p)*"
)
SED_SAFE_OPTIONS = {
    "-n",
    "--quiet",
    "--silent",
    "-u",
    "--unbuffered",
    "-E",
    "-r",
    "--regexp-extended",
}
FIND_WRITE_OR_EXECUTE_PREFIXES = ("-delete", "-exec", "-ok", "-fprint", "-fls")
SHELL_EXPANSION_CHARACTERS = frozenset("$`{}*?[]!")
POWERSHELL_READ_ONLY_PROGRAMS = {
    "get-content",
    "get-childitem",
    "select-string",
    "test-path",
    "resolve-path",
}
SHELL_BUILTIN_PROGRAMS = {"echo", "pwd"}
IMAGE_WRITE_OPERATOR_PREFIXES = (
    "-write",
    "+write",
    "--write",
    "++write",
    "-output",
    "+output",
    "--output",
    "++output",
)
IMAGE_SCRIPT_OR_PROCESS_OPTIONS = {
    "-script",
    "+script",
    "--script",
    "++script",
    "-process",
    "+process",
    "--process",
    "++process",
}
IMAGE_SCENE_SUFFIX_RE = re.compile(r"(?:\[[^]]*\])+$")
IMAGE_SCRIPT_CODER_PREFIXES = ("msl:", "mvg:")
GIT_FORBIDDEN_OPTION_PREFIXES = (
    "--ext-diff",
    "--textconv",
    "--exec-path",
    "--config-env",
    "--output",
    "--show-signature",
    "--verify-signatures",
    "--open-files-in-pager",
    "--paginate",
)
GIT_SAFE_LONG_FLAGS = frozenset({
    "--abbrev",
    "--abbrev-commit",
    "--all",
    "--binary",
    "--boundary",
    "--branches",
    "--cached",
    "--check",
    "--cherry-mark",
    "--cherry-pick",
    "--color",
    "--color-words",
    "--compact-summary",
    "--date-order",
    "--decorate",
    "--exit-code",
    "--find-copies",
    "--find-copies-harder",
    "--find-renames",
    "--first-parent",
    "--follow",
    "--full-diff",
    "--full-history",
    "--full-index",
    "--graph",
    "--histogram",
    "--ignore-all-space",
    "--ignore-blank-lines",
    "--ignore-cr-at-eol",
    "--ignore-space-at-eol",
    "--ignore-space-change",
    "--ignore-submodules",
    "--indent-heuristic",
    "--ita-invisible-in-index",
    "--ita-visible-in-index",
    "--left-only",
    "--left-right",
    "--merges",
    "--minimal",
    "--name-only",
    "--name-status",
    "--no-abbrev",
    "--no-abbrev-commit",
    "--no-color",
    "--no-decorate",
    "--no-ext-diff",
    "--no-indent-heuristic",
    "--no-merges",
    "--no-notes",
    "--no-patch",
    "--no-prefix",
    "--no-relative",
    "--no-renames",
    "--no-show-signature",
    "--no-standard-notes",
    "--no-textconv",
    "--no-walk",
    "--no-use-mailmap",
    "--notes",
    "--numstat",
    "--oneline",
    "--ours",
    "--patch",
    "--patience",
    "--quiet",
    "--raw",
    "--recurse-submodules",
    "--relative",
    "--remotes",
    "--reverse",
    "--right-only",
    "--shortstat",
    "--show-notes",
    "--simplify-by-decoration",
    "--simplify-merges",
    "--source",
    "--staged",
    "--standard-notes",
    "--stat",
    "--submodule",
    "--summary",
    "--tags",
    "--text",
    "--theirs",
    "--topo-order",
    "--use-mailmap",
    "--word-diff",
})
GIT_SAFE_SHORT_FLAGS = frozenset({"-M", "-R", "-C", "-b", "-m", "-p", "-s", "-u", "-w"})
GIT_SELF_CONTAINED_SHORT_OPTION_RE = re.compile(r"-(?:U\d+|\d+|n\d+)")


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


def _trusted_program_token(token: str, *, cwd: Path | None = None, platform: str | None = None) -> bool:
    text = str(token).strip()
    if not text or "/" in text or "\\" in text:
        return False
    program = program_name(text, platform=platform)
    if program in POWERSHELL_READ_ONLY_PROGRAMS or program in SHELL_BUILTIN_PROGRAMS:
        return True
    requested_windows = (platform or os.name) == "nt"
    if requested_windows != (os.name == "nt"):
        return True
    resolved = shutil.which(text)
    if not resolved:
        return program == "apply_patch"
    executable = Path(resolved).resolve()
    base = (cwd or Path.cwd()).resolve()
    try:
        executable.relative_to(base)
    except ValueError:
        return True
    return False


def _git_fsmonitor_disabled(cwd: Path | None) -> bool:
    environment = os.environ.copy()
    environment.update({
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "LC_ALL": "C",
        "PAGER": "cat",
    })
    try:
        completed = subprocess.run(
            ["git", "-C", str(cwd or Path.cwd()), "config", "--null", "--list"],
            capture_output=True,
            check=False,
            env=environment,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if completed.returncode != 0:
        return False
    for entry in completed.stdout.split(b"\0"):
        if not entry:
            continue
        raw = entry.decode("utf-8", errors="surrogateescape")
        key, separator, value = raw.partition("\n")
        if key.casefold() != "core.fsmonitor":
            continue
        if not separator or value.strip().casefold() not in {"0", "false", "no", "off"}:
            return False
    return True


def _git_option_flags(values: list[str]) -> set[str] | None:
    flags: set[str] = set()
    for value in values:
        lowered = value.casefold()
        if "%G" in value or lowered.startswith(GIT_FORBIDDEN_OPTION_PREFIXES) or value.startswith("-c"):
            return None
        if lowered.startswith("--"):
            if "=" in lowered:
                name = lowered.split("=", 1)[0]
                if name not in GIT_REQUIRED_DIFF_OPTIONS and name not in GIT_REQUIRED_LOG_OPTIONS:
                    flags.add(name)
                continue
            if lowered not in GIT_SAFE_LONG_FLAGS:
                return None
            flags.add(lowered)
            continue
        if value.startswith("-") and value != "-":
            if value in GIT_SAFE_SHORT_FLAGS or GIT_SELF_CONTAINED_SHORT_OPTION_RE.fullmatch(value):
                flags.add(value)
                continue
            return None
    return flags


def _git_read_only(args: list[str], *, cwd: Path | None = None) -> bool:
    index = 1
    no_pager = False
    while index < len(args) and args[index].startswith("-"):
        if args[index] == "--no-pager":
            no_pager = True
            index += 1
            continue
        return False
    if index >= len(args):
        return False
    subcommand = args[index]
    if subcommand not in GIT_READ_ONLY_SUBCOMMANDS:
        return False
    remaining = args[index + 1:]
    option_values = remaining[: remaining.index("--")] if "--" in remaining else remaining
    if any(
        value.casefold().startswith(GIT_FORBIDDEN_OPTION_PREFIXES)
        or value.startswith("-c")
        or "%G" in value
        for value in option_values
    ):
        return False
    if subcommand in {"ls-files", "rev-parse"}:
        return True
    if subcommand == "status":
        return _git_fsmonitor_disabled(cwd)
    flags = _git_option_flags(option_values)
    if flags is None:
        return False
    if not no_pager:
        return False
    if not GIT_REQUIRED_DIFF_OPTIONS.issubset(flags):
        return False
    if subcommand in {"log", "show"} and not GIT_REQUIRED_LOG_OPTIONS.issubset(flags):
        return False
    return True


def allowed_read_only(
    args: list[str],
    *,
    platform: str | None = None,
    cwd: Path | None = None,
) -> bool:
    if not args or not _trusted_program_token(args[0], cwd=cwd, platform=platform):
        return False
    program = program_name(args[0], platform=platform)
    if program in POWERSHELL_READ_ONLY_PROGRAMS and any(
        "{" in value or "}" in value for value in args[1:]
    ):
        return False
    if program == "git":
        return _git_read_only(args, cwd=cwd)
    if program == "openspec":
        return len(args) >= 2 and args[1] in {"diff", "list", "show", "status", "validate"}
    if program == "find":
        return _find_read_only(args)
    if program == "sed":
        return _sed_read_only(args)
    if program == "file":
        return _file_read_only(args)
    if program == "rg":
        return not any(
            value.startswith(("--pre", "--hostname-bin", "-z", "--search-zip"))
            for value in args[1:]
        )
    return program in READ_ONLY


def _find_read_only(args: list[str]) -> bool:
    return not any(
        value.split("=", 1)[0].startswith(FIND_WRITE_OR_EXECUTE_PREFIXES)
        for value in args[1:]
    )


def _file_read_only(args: list[str]) -> bool:
    end_options = False
    for value in args[1:]:
        if end_options:
            continue
        if value == "--":
            end_options = True
            continue
        if value.startswith("--"):
            if value.split("=", 1)[0] not in FILE_SAFE_LONG_OPTIONS:
                return False
            continue
        if value.startswith("-") and value != "-":
            if any(option not in FILE_SAFE_SHORT_OPTIONS for option in value[1:]):
                return False
    return True


def _sed_read_only(args: list[str]) -> bool:
    scripts: list[str] = []
    files: list[str] = []
    index = 1
    while index < len(args):
        value = args[index]
        if value in SED_SAFE_OPTIONS:
            index += 1
            continue
        if value in {"-e", "--expression"}:
            if index + 1 >= len(args):
                return False
            scripts.append(args[index + 1])
            index += 2
            continue
        if value.startswith("--expression="):
            scripts.append(value.split("=", 1)[1])
            index += 1
            continue
        if value.startswith("-"):
            return False
        if not scripts:
            scripts.append(value)
        else:
            files.append(value)
        index += 1
    return bool(scripts and files) and all(SED_SAFE_SCRIPT_RE.fullmatch(script) for script in scripts)


def _python_launcher(program: str) -> bool:
    return program in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"} or bool(
        re.match(r"^python3(?:\.\d+)?(?:\.exe)?$", program)
    )


def _interpreter_script(args: list[str], *, platform: str | None = None) -> str | None:
    if not args or not _python_launcher(program_name(args[0], platform=platform)):
        return None
    launcher = program_name(args[0], platform=platform)
    index = 1
    while index < len(args):
        token = args[index]
        if token == "--":
            index += 1
            continue
        if launcher in {"py", "py.exe"} and re.fullmatch(
            r"-3(?:\.\d+)?(?:-(?:32|64|arm64))?",
            token,
        ):
            index += 1
            continue
        if token.startswith("--") and token != "--":
            return None
        if token.startswith("-"):
            if any(character in token[1:].casefold() for character in ("c", "m")):
                return None
            index += 1
            continue
        return token
    return None


def _canonical_path(value: str, *, cwd: Path | None = None, plugin_root: Path | None = None) -> Path | None:
    text = str(value).strip()
    if not text:
        return None
    root = (plugin_root or PLUGIN_ROOT).resolve()
    for marker in ("${PLUGIN_ROOT}", "$PLUGIN_ROOT", "%PLUGIN_ROOT%"):
        text = text.replace(marker, str(root))
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = (cwd or Path.cwd()) / path
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return None


def _trusted_plugin_script(
    value: str,
    *,
    allowed: set[str] | None = None,
    cwd: Path | None = None,
    plugin_root: Path | None = None,
) -> bool:
    path = _canonical_path(value, cwd=cwd, plugin_root=plugin_root)
    if path is None:
        return False
    root = (plugin_root or PLUGIN_ROOT).resolve()
    if path.parent != (root / "scripts").resolve():
        return False
    return allowed is None or path.name in allowed


def handshake_or_read_only(
    args: list[str],
    *,
    platform: str | None = None,
    cwd: Path | None = None,
    plugin_root: Path | None = None,
) -> bool:
    if _trusted_plugin_script(args[0], allowed=HANDSHAKE, cwd=cwd, plugin_root=plugin_root):
        return True
    if not _trusted_program_token(args[0], cwd=cwd, platform=platform):
        return False
    script = _interpreter_script(args, platform=platform)
    if script is not None and _trusted_plugin_script(
        script,
        allowed=HANDSHAKE,
        cwd=cwd,
        plugin_root=plugin_root,
    ):
        return True
    return allowed_read_only(args, platform=platform, cwd=cwd)


def semantic_assessment_command_allowed(
    command: str,
    *,
    platform: str | None = None,
    cwd: Path | None = None,
    plugin_root: Path | None = None,
) -> bool:
    if not command.strip() or has_shell_control(command):
        return False
    try:
        args = split_command(command, platform=platform)
    except ValueError:
        return False
    if len(args) < 3:
        return False
    if not _trusted_program_token(args[0], cwd=cwd, platform=platform):
        return False
    if not _python_launcher(program_name(args[0], platform=platform)):
        return False
    script = _interpreter_script(args, platform=platform)
    if script is None or not _trusted_plugin_script(
        script,
        allowed={"route.py"},
        cwd=cwd,
        plugin_root=plugin_root,
    ):
        return False
    if "--assess-json" not in args:
        return False
    forbidden = {"--task", "--tier", "--provisional", "--force", "--force-reassess"}
    return not any(value in forbidden for value in args[2:])


def routing_inspection_command_allowed(
    command: str,
    *,
    platform: str | None = None,
    cwd: Path | None = None,
    plugin_root: Path | None = None,
) -> bool:
    if not command.strip() or has_shell_control(command):
        return False
    try:
        args = split_command(command, platform=platform)
    except ValueError:
        return False
    if not args:
        return False
    if not _trusted_program_token(args[0], cwd=cwd, platform=platform):
        return False
    if allowed_read_only(args, platform=platform, cwd=cwd):
        return True
    script = _interpreter_script(args, platform=platform)
    return script is not None and _trusted_plugin_script(
        script,
        allowed={"gate.py", "host_trust.py"},
        cwd=cwd,
        plugin_root=plugin_root,
    )


def shell_allowed(
    command: str,
    *,
    platform: str | None = None,
    cwd: Path | None = None,
    plugin_root: Path | None = None,
) -> bool:
    if not command.strip() or has_shell_control(command):
        return False
    try:
        args = split_command(command, platform=platform)
    except ValueError:
        return False
    if not args:
        return False
    return handshake_or_read_only(args, platform=platform, cwd=cwd, plugin_root=plugin_root)


def artifact_path_allowed(base_dir: Path, value: str) -> bool:
    base = base_dir.resolve()
    path = _canonical_path(value, cwd=base)
    if path is None:
        return False
    try:
        relative = path.relative_to(base)
    except ValueError:
        return False
    parts = relative.parts
    if not parts:
        return False
    return any(parts[: len(prefix)] == prefix for prefix in ARTIFACT_PREFIXES)


def command_artifact_path_allowed(base_dir: Path, value: str) -> bool:
    if any(character in value for character in SHELL_EXPANSION_CHARACTERS):
        return False
    return artifact_path_allowed(base_dir, value)


def _non_option_args(args: list[str]) -> list[str]:
    return [arg for arg in args[1:] if not arg.startswith("-")]


def _parse_copy_like(
    program: str,
    args: list[str],
) -> tuple[str | None, list[str]] | None:
    safe_short_options = COPY_LIKE_SAFE_SHORT_OPTIONS[program]
    target: str | None = None
    operands: list[str] = []
    index = 1
    end_options = False
    while index < len(args):
        argument = args[index]
        if not end_options and argument == "--":
            end_options = True
            index += 1
            continue
        if not end_options and argument in {"-t", "--target-directory"}:
            if index + 1 >= len(args):
                return None
            target = args[index + 1]
            index += 2
            continue
        if not end_options and argument.startswith("--target-directory="):
            target = argument.split("=", 1)[1]
            index += 1
            continue
        if not end_options and argument.startswith("--"):
            return None
        if not end_options and argument.startswith("-") and argument != "-":
            cluster = argument[1:]
            cluster_index = 0
            while cluster_index < len(cluster):
                option = cluster[cluster_index]
                if option == "t":
                    attached = cluster[cluster_index + 1:]
                    if attached:
                        target = attached
                    else:
                        if index + 1 >= len(args):
                            return None
                        target = args[index + 1]
                        index += 1
                    cluster_index = len(cluster)
                    break
                if option not in safe_short_options:
                    return None
                cluster_index += 1
            index += 1
            continue
        operands.append(argument)
        index += 1
    return target, operands


def _operand_basename(value: str) -> str | None:
    separators = "/\\" if os.name == "nt" else "/"
    trimmed = value.rstrip(separators)
    if not trimmed:
        return None
    name = trimmed.replace("\\", "/").rsplit("/", 1)[-1] if os.name == "nt" else trimmed.rsplit("/", 1)[-1]
    if name in {"", ".", ".."}:
        return None
    return name


def _copy_destinations_allowed(base_dir: Path, target: str, sources: list[str]) -> bool:
    if not command_artifact_path_allowed(base_dir, target):
        return False
    target_path = _canonical_path(target, cwd=base_dir.resolve())
    if target_path is None:
        return False
    target_is_directory = target.endswith(("/", "\\")) or target_path.is_dir()
    if not target_is_directory:
        return True
    for source in sources:
        name = _operand_basename(source)
        if name is None or not artifact_path_allowed(base_dir, str(target_path / name)):
            return False
    return True


def _copy_is_recursive(args: list[str]) -> bool:
    for argument in args[1:]:
        if argument == "--":
            break
        if not argument.startswith("-") or argument.startswith("--"):
            continue
        cluster = argument[1:].split("t", 1)[0]
        if "R" in cluster or "r" in cluster:
            return True
    return False


def artifact_file_command_allowed(program: str, args: list[str], base_dir: Path) -> bool:
    if program in {"mkdir", "touch"}:
        values = _non_option_args(args)
        return bool(values) and all(command_artifact_path_allowed(base_dir, value) for value in values)
    if program == "cp":
        parsed = _parse_copy_like(program, args)
        if parsed is None:
            return False
        target, operands = parsed
        if target is None:
            if len(operands) < 2:
                return False
            sources, destination = operands[:-1], operands[-1]
        else:
            if not operands:
                return False
            sources, destination = operands, target
        if _copy_is_recursive(args):
            target_path = _canonical_path(destination, cwd=base_dir.resolve())
            if target_path is not None and (
                destination.endswith(("/", "\\")) or target_path.is_dir()
            ):
                return False
        return _copy_destinations_allowed(base_dir, destination, sources)
    if program == "mv":
        parsed = _parse_copy_like(program, args)
        if parsed is None:
            return False
        target, operands = parsed
        if target is None:
            if len(operands) < 2:
                return False
            sources, destination = operands[:-1], operands[-1]
        else:
            if not operands:
                return False
            sources, destination = operands, target
        if not sources:
            return False
        if not all(command_artifact_path_allowed(base_dir, value) for value in operands):
            return False
        return _copy_destinations_allowed(base_dir, destination, sources)
    return False


def image_command_allowed(program: str, args: list[str], base_dir: Path) -> bool:
    if program == "sips":
        target: str | None = None
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
            index += 1
        return target is not None and command_artifact_path_allowed(base_dir, target)
    if any(
        value.casefold().startswith(IMAGE_WRITE_OPERATOR_PREFIXES)
        for value in args[1:]
    ):
        return False
    for value in args[1:]:
        lowered = value.casefold()
        option_name = lowered.split("=", 1)[0]
        if option_name in IMAGE_SCRIPT_OR_PROCESS_OPTIONS or value.startswith("@"):
            return False
        normalized = IMAGE_SCENE_SUFFIX_RE.sub("", lowered)
        if normalized.startswith(IMAGE_SCRIPT_CODER_PREFIXES):
            return False
        if not value.startswith("-") and (normalized.endswith(".msl") or normalized.endswith(".mvg")):
            return False
    values = _non_option_args(args)
    return len(values) >= 2 and command_artifact_path_allowed(base_dir, values[-1])


def _ui_search_arguments_allowed(args: list[str]) -> bool:
    index = 0
    has_query = False
    while index < len(args):
        value = args[index]
        if value == "--":
            has_query = has_query or bool(args[index + 1:])
            return has_query
        if value in UI_SEARCH_FLAG_OPTIONS:
            index += 1
            continue
        if value in UI_SEARCH_VALUE_OPTIONS:
            if index + 1 >= len(args):
                return False
            index += 2
            continue
        if value.startswith("--"):
            name, separator, _ = value.partition("=")
            if separator and name in UI_SEARCH_VALUE_OPTIONS:
                index += 1
                continue
            return False
        if value.startswith("-") and value != "-":
            if value[:2] in UI_SEARCH_SHORT_VALUE_OPTIONS and len(value) > 2:
                index += 1
                continue
            return False
        has_query = True
        index += 1
    return has_query


def generator_allowed(
    args: list[str],
    base_dir: Path,
    *,
    platform: str | None = None,
    cwd: Path | None = None,
) -> bool:
    if not _trusted_program_token(args[0], cwd=cwd, platform=platform):
        return False
    program = program_name(args[0], platform=platform)
    home = Path.home().resolve()
    search_script = (home / ".agents/skills/ui-ux-pro-max/scripts/search.py").resolve()
    chrome_script = (home / ".codex/tools/chrome-cdp.mjs").resolve()
    if _python_launcher(program):
        script = _interpreter_script(args, platform=platform)
        if script is None:
            return False
        script_path = _canonical_path(script, cwd=cwd)
        if script_path != search_script:
            return False
        try:
            script_index = args.index(script)
        except ValueError:
            return False
        return _ui_search_arguments_allowed(args[script_index + 1:])
    if program in {"node", "node.exe"}:
        if os.environ.get("NODE_OPTIONS", "").strip():
            return False
        if len(args) < 2 or _canonical_path(args[1], cwd=cwd) != chrome_script:
            return False
        remaining = args[2:]
        if not remaining:
            return True
        subcommand = remaining[0]
        if subcommand == "shot":
            return len(remaining) >= 2 and command_artifact_path_allowed(base_dir, remaining[1])
        return subcommand in {"eval", "goto", "list"}
    return False


def is_apply_patch_command(command: str) -> bool:
    stripped = command.strip()
    if not stripped:
        return False
    first = stripped.split(None, 1)[0]
    return first == "apply_patch"


def apply_patch_command_allowed(command: str, base_dir: Path) -> bool:
    if command.count("*** Begin Patch") != 1 or command.count("*** End Patch") != 1:
        return False
    prefix, _, remainder = command.partition("*** Begin Patch")
    match = PATCH_HEREDOC_PREFIX_RE.fullmatch(prefix)
    if match is None:
        return False
    suffix = remainder.split("*** End Patch", 1)[1]
    if suffix.strip(" \t\r\n") != match.group("delimiter"):
        return False
    paths = PATCH_PATH_RE.findall(command)
    return bool(paths) and all(artifact_path_allowed(base_dir, path) for path in paths)


def pre_implementation_shell_allowed(command: str, base_dir: Path) -> bool:
    if not command.strip():
        return False
    if is_apply_patch_command(command):
        return _trusted_program_token("apply_patch", cwd=base_dir) and apply_patch_command_allowed(command, base_dir)
    if has_shell_control(command):
        return False
    try:
        args = split_command(command)
    except ValueError:
        return False
    if not args:
        return False
    if handshake_or_read_only(args, cwd=base_dir):
        return True
    if not _trusted_program_token(args[0], cwd=base_dir):
        return False
    program = program_name(args[0])
    if program in ARTIFACT_FILE_COMMANDS:
        return artifact_file_command_allowed(program, args, base_dir)
    if program in IMAGE_COMMANDS:
        return image_command_allowed(program, args, base_dir)
    if program in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe", "node", "node.exe"} or re.match(
        r"^python3(?:\.\d+)?(?:\.exe)?$", program
    ):
        return generator_allowed(args, base_dir, cwd=base_dir)
    return False


def _patch_text(tool_input: dict) -> str:
    for key in ("input", "patch", "command", "cmd"):
        value = tool_input.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "\n".join(str(item) for item in value)
    return ""


def extract_write_paths(tool_input: dict, path_keys: tuple[str, ...] = FILE_PATH_KEYS) -> list[str]:
    paths: list[str] = []
    for key in path_keys:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            paths.append(value)
    files = tool_input.get("files")
    if isinstance(files, list):
        for item in files:
            if isinstance(item, str):
                paths.append(item)
            elif isinstance(item, dict):
                for key in path_keys:
                    value = item.get(key)
                    if isinstance(value, str) and value:
                        paths.append(value)
    return paths


def write_tool_allowed(tool: str, tool_input: dict, base_dir: Path) -> bool:
    name = str(tool).strip().casefold()
    if name in APPLY_PATCH_TOOL_NAMES:
        patch_text = _patch_text(tool_input)
        paths = PATCH_PATH_RE.findall(patch_text)
        return bool(paths) and all(artifact_path_allowed(base_dir, path) for path in paths)
    path_keys = WRITE_TOOL_PATH_KEYS.get(name)
    if path_keys is None:
        return False
    paths = extract_write_paths(tool_input, path_keys)
    if name in DUAL_PATH_WRITE_TOOLS and len(set(paths)) < 2:
        return False
    return bool(paths) and all(artifact_path_allowed(base_dir, path) for path in paths)


def plan_approval_required(state: dict, base_dir: Path) -> bool:
    if not state.get("requires", {}).get("plan_approval"):
        return False
    passed, _ = evaluate_gate(state, "plan_approved", base_dir)
    return not passed


def pre_implementation_tool_allowed(tool: str, lowered: str, tool_input: dict, base_dir: Path) -> bool:
    if tool in READ_TOOLS:
        return True
    if tool in SAFE_NON_MUTATING_TOOLS or lowered in SAFE_NON_MUTATING_TOOLS:
        return True
    if any(marker in lowered for marker in WRITE_TOOL_MARKERS):
        return write_tool_allowed(tool, tool_input, base_dir)
    if lowered in {"bash", "shell", "exec_command", "exec"}:
        command = tool_input.get("command", tool_input.get("cmd", ""))
        return isinstance(command, str) and pre_implementation_shell_allowed(command, base_dir)
    return False


def main() -> int:
    configure_stdio()
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
            if isinstance(command, str) and routing_inspection_command_allowed(command, cwd=cwd):
                return 0
            if isinstance(command, str) and semantic_assessment_command_allowed(command, cwd=cwd):
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
            if isinstance(command, str) and shell_allowed(command, cwd=cwd):
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
