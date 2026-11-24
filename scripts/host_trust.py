#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conductor.runtime import configure_stdio, is_windows, relative_name_bytes

try:
    import tomllib as _toml

    TOML_BACKEND: str | None = "tomllib"
except ModuleNotFoundError:
    try:
        import tomli as _toml

        TOML_BACKEND = "tomli"
    except ModuleNotFoundError:
        _toml = None
        TOML_BACKEND = None

PLUGIN_ID = "delivery-conductor@delivery-conductor"
HOOKS_RELATIVE_PATH = "hooks/codex-hooks.json"
EVENT_LABELS = {
    "PreModelRequest": "pre_model_request",
    "PostModelRequest": "post_model_request",
    "PreToolUse": "pre_tool_use",
    "PermissionRequest": "permission_request",
    "PostToolUse": "post_tool_use",
    "PreCompact": "pre_compact",
    "PostCompact": "post_compact",
    "SessionStart": "session_start",
    "SessionEnd": "session_end",
    "UserPromptSubmit": "user_prompt_submit",
    "SubagentStart": "subagent_start",
    "SubagentStop": "subagent_stop",
    "Stop": "stop",
    "Interrupt": "interrupt",
}
MATCHERLESS_EVENTS = {
    "pre_model_request",
    "post_model_request",
    "user_prompt_submit",
    "stop",
    "interrupt",
}
SKIPPED_NAMES = {"__pycache__", ".venv"}


class HostTrustError(RuntimeError):
    """Raised when the host has not trusted the hooks that enforce the gates."""


def parse_toml(text: str, module: Any | None) -> dict[str, Any]:
    if module is None:
        raise HostTrustError(
            "无法读取 Codex 的 TOML 配置：当前 Python 没有 tomllib，也没有 tomli。"
            f"当前 Python: {sys.version.split()[0]}。"
            "请安装 Python 3.11+，或运行 `python -m pip install tomli`。"
        )
    return module.loads(text)


def load_toml(text: str) -> dict[str, Any]:
    return parse_toml(text, _toml)


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def _declaration_command(
    handler: dict[str, Any],
    *,
    platform: str | None,
) -> str:
    command = str(handler.get("command", ""))
    if is_windows(platform):
        command = str(handler.get("commandWindows", handler.get("command_windows", command)))
    return command


def _normalized_command_handler(
    handler: dict[str, Any],
    *,
    platform: str | None = None,
) -> dict[str, Any]:
    timeout = handler.get("timeout")
    timeout = 600 if timeout is None else max(1, int(timeout))
    normalized: dict[str, Any] = {
        "type": "command",
        "command": _declaration_command(handler, platform=platform),
        "timeout": timeout,
        "async": bool(handler.get("async", False)),
    }
    status_message = handler.get("statusMessage")
    if status_message is not None:
        normalized["statusMessage"] = str(status_message)
    on_failure = handler.get("onFailure")
    if on_failure is not None:
        normalized["onFailure"] = str(on_failure)
    additional_context_limit = handler.get("additionalContextLimit")
    if additional_context_limit is not None:
        limit = int(additional_context_limit)
        if limit != 2500:
            normalized["additionalContextLimit"] = limit
    return normalized


def declaration_hash(
    event_label: str,
    matcher: str | None,
    handler: dict[str, Any],
    *,
    plugin_root: str | os.PathLike[str] | None = None,
    platform: str | None = None,
) -> str:
    # Codex hashes the unexpanded hook declaration, then expands runtime variables
    # only when the hook command is executed. Keep plugin_root for caller compatibility.
    del plugin_root
    normalized = _normalized_command_handler(
        handler,
        platform=platform,
    )
    if event_label in MATCHERLESS_EVENTS:
        matcher = None
    identity: dict[str, Any] = {"event_name": event_label}
    if matcher is not None:
        identity["matcher"] = matcher
    identity["hooks"] = [normalized]
    payload = json.dumps(_canonical(identity), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _relative_hash_name(path: Path, *, platform: str | None = None) -> bytes:
    return relative_name_bytes(path, platform=platform)


def plugin_content_hash(root: str | os.PathLike[str], *, platform: str | None = None) -> str:
    root_path = Path(root).expanduser().absolute()
    root_metadata = root_path.lstat()
    if not stat.S_ISDIR(root_metadata.st_mode):
        raise OSError(f"plugin root is not a directory: {root_path}")
    digest = hashlib.sha256()
    digest.update(b"codex-plugin-bundle-v1")
    pending = [root_path]
    while pending:
        path = pending.pop()
        relative = path.relative_to(root_path)
        name = "" if path == root_path else relative.name
        if name in SKIPPED_NAMES or name.endswith(".pyc") or name.endswith(".egg-info"):
            continue
        encoded_name = b"" if path == root_path else _relative_hash_name(relative, platform=platform)
        digest.update(len(encoded_name).to_bytes(8, "little"))
        digest.update(encoded_name)
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            digest.update(b"directory")
            entries = sorted(path.iterdir(), key=lambda item: os.fsencode(item.name))
            pending.extend(reversed(entries))
        elif stat.S_ISREG(metadata.st_mode):
            digest.update(b"file")
            length = metadata.st_size
            digest.update(length.to_bytes(8, "little"))
            with path.open("rb") as handle:
                remaining = length
                while remaining:
                    chunk = handle.read(min(64 * 1024, remaining))
                    if not chunk:
                        raise OSError(f"plugin file changed while hashing: {path}")
                    digest.update(chunk)
                    remaining -= len(chunk)
                if handle.read(1):
                    raise OSError(f"plugin file changed while hashing: {path}")
        else:
            raise OSError(f"unsupported plugin entry type: {path}")
    return "sha256:" + digest.hexdigest()


def _config_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    codex_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
    return (codex_home / "config.toml").resolve()


def installed_plugin_root(plugin_root: str | os.PathLike[str] | None = None) -> Path | None:
    if plugin_root:
        return Path(plugin_root).expanduser().resolve()
    current = Path(__file__).resolve().parents[1]
    version_path = current / ".codex-plugin" / "plugin.json"
    try:
        version = json.loads(version_path.read_text(encoding="utf-8"))["version"]
    except (OSError, KeyError, ValueError):
        return None
    name, marketplace = PLUGIN_ID.split("@", 1)
    codex_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
    cached = codex_home / "plugins" / "cache" / marketplace / name / str(version)
    if current == cached.resolve():
        return current
    return cached.resolve() if cached.is_dir() else None


def trust_report(
    plugin_root: str | os.PathLike[str] | None = None,
    config_path: str | os.PathLike[str] | None = None,
    plugin_id: str = PLUGIN_ID,
) -> dict[str, Any]:
    root = installed_plugin_root(plugin_root)
    report: dict[str, Any] = {
        "ok": True,
        "checked": False,
        "plugin_root": str(root) if root else None,
        "config_path": str(_config_path(config_path)),
        "errors": [],
        "hooks": [],
    }
    if root is None or not root.is_dir():
        report["errors"].append("installed plugin root was not found; hook trust cannot be verified")
        report["ok"] = False
        return report
    report["checked"] = True

    hooks_file = root / HOOKS_RELATIVE_PATH
    config_file = _config_path(config_path)
    try:
        content_hash = plugin_content_hash(root)
        hooks_config = json.loads(hooks_file.read_text(encoding="utf-8"))
        config = load_toml(config_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, HostTrustError) as error:
        report["errors"].append(f"cannot verify hook trust: {error}")
        report["ok"] = False
        return report

    report["content_hash"] = content_hash
    states = config.get("hooks", {}).get("state", {})
    command_hooks = 0
    for event_name, groups in hooks_config.get("hooks", {}).items():
        event_label = EVENT_LABELS.get(event_name)
        if event_label is None:
            report["errors"].append(f"unknown hook event in plugin manifest: {event_name}")
            continue
        for group_index, group in enumerate(groups):
            matcher = group.get("matcher")
            for handler_index, handler in enumerate(group.get("hooks", [])):
                if handler.get("type") != "command":
                    continue
                command_hooks += 1
                key = f"{plugin_id}:{HOOKS_RELATIVE_PATH}:{event_label}:{group_index}:{handler_index}"
                state = states.get(key, {})
                expected_declaration = declaration_hash(
                    event_label,
                    matcher,
                    handler,
                )
                errors = []
                if state.get("enabled") is False:
                    errors.append("hook is disabled")
                if state.get("trusted_hash") != expected_declaration:
                    errors.append("hook declaration is not trusted or has changed")
                if state.get("trusted_content_hash") != content_hash:
                    errors.append("installed plugin contents are not trusted or have changed")
                item = {
                    "key": key,
                    "event": event_label,
                    "ok": not errors,
                    "errors": errors,
                }
                report["hooks"].append(item)
                report["errors"].extend(f"{key}: {error}" for error in errors)

    if command_hooks == 0:
        report["errors"].append("plugin declares no command hooks")
    if report["errors"]:
        report["ok"] = False
    return report


def ensure_host_trust(
    *,
    plugin_root: str | os.PathLike[str] | None = None,
    config_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    return trust_report(plugin_root=plugin_root, config_path=config_path)


def plugin_is_installed_copy() -> bool:
    """Return True when this script is running from a Codex plugin cache."""
    current = Path(__file__).resolve().parents[1]
    version_path = current / ".codex-plugin" / "plugin.json"
    try:
        version = json.loads(version_path.read_text(encoding="utf-8"))["version"]
    except (OSError, KeyError, ValueError):
        return False
    name, marketplace = PLUGIN_ID.split("@", 1)
    codex_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
    cached = (codex_home / "plugins" / "cache" / marketplace / name / str(version)).resolve()
    return current == cached


def enforce_host_trust(
    *,
    plugin_root: str | os.PathLike[str] | None = None,
    config_path: str | os.PathLike[str] | None = None,
    allow_unverified_dev: bool | None = None,
) -> dict[str, Any]:
    """Return a passing trust report or raise before any gated work can start.

    A source checkout used for local tests is allowed to skip only when there is
    no installed plugin to verify. Once an installed copy is found, any missing,
    disabled, or stale trust entry is fatal.
    """
    report = trust_report(plugin_root=plugin_root, config_path=config_path)
    if report["ok"]:
        return report
    if allow_unverified_dev is None:
        allow_unverified_dev = not plugin_is_installed_copy()
    if allow_unverified_dev and not report["checked"]:
        report["skipped"] = True
        return report
    raise HostTrustError(format_report(report))


def format_report(report: dict[str, Any]) -> str:
    if report["ok"]:
        return "Delivery Conductor hooks are trusted and enabled; approval gates are enforceable."
    if report.get("skipped"):
        return "No installed plugin was found; the hook trust check was skipped for source development."
    lines = [
        "Delivery Conductor hooks are not trusted, so approval gates cannot be enforced.",
        "Stop now. Do not continue implementation or claim completion.",
    ]
    lines.extend(f"- {error}" for error in report["errors"])
    lines.append("Restart Codex, open Hooks > Review hooks, choose Trust all, then retry.")
    return "\n".join(lines)


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Verify that Codex trusts the installed Delivery Conductor hooks.")
    parser.add_argument("--plugin-root")
    parser.add_argument("--config")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        report = enforce_host_trust(plugin_root=args.plugin_root, config_path=args.config)
    except HostTrustError as error:
        if args.json:
            print(json.dumps({"ok": False, "errors": str(error).splitlines()}, ensure_ascii=False, indent=2))
        else:
            print(str(error))
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
    return 0 if report["ok"] or report.get("skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
