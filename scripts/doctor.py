#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import platform as platform_module
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from host_trust import (
    TOML_BACKEND,
    HostTrustError,
    enforce_host_trust,
    plugin_is_installed_copy,
)
from conductor.runtime import configure_stdio, hook_interpreter

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def _add(checks: list[Check], name: str, status: str, detail: str) -> None:
    checks.append(Check(name=name, status=status, detail=detail))


def _python_check(checks: list[Check]) -> None:
    version = sys.version_info
    detail = f"{version.major}.{version.minor}.{version.micro} ({sys.executable})"
    if version >= (3, 10):
        _add(checks, "python", "ok", detail)
    else:
        _add(checks, "python", "fail", f"{detail}；需要 Python 3.10+")


def _hook_runtime_check(checks: list[Check]) -> None:
    launcher = hook_interpreter()
    try:
        completed = subprocess.run(
            [*launcher, "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        _add(checks, "hook_runtime", "fail", f"Hook 解释器不可用: {' '.join(launcher)}: {error}")
        return
    status = "ok" if completed.returncode == 0 else "fail"
    _add(checks, "hook_runtime", status, f"{' '.join(launcher)} (Python 3.10+)")


def _toml_check(checks: list[Check]) -> None:
    if TOML_BACKEND:
        _add(checks, "toml", "ok", TOML_BACKEND)
        return
    _add(
        checks,
        "toml",
        "fail",
        "缺少 tomllib/tomli；请安装 Python 3.11+，或运行 `python -m pip install tomli`",
    )


def _approval_check(checks: list[Check]) -> None:
    if sys.stdin.isatty():
        _add(checks, "approval_backend", "ok", "审批仅使用交互终端；当前标准输入是 TTY")
    else:
        _add(
            checks,
            "approval_backend",
            "warn",
            "审批仅使用交互终端；当前标准输入不是 TTY；请通过 ssh -t 或其他交互终端重试",
        )


def _codex_check(checks: list[Check]) -> None:
    executable = shutil.which("codex")
    if not executable:
        _add(checks, "codex", "warn", "未找到 codex 命令；安装插件前需要 Codex CLI 或桌面端")
        return
    try:
        completed = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        _add(checks, "codex", "warn", f"{executable} 无法执行或超时: {error}")
        return
    if completed.returncode == 0:
        _add(checks, "codex", "ok", completed.stdout.strip() or executable)
    else:
        _add(checks, "codex", "warn", f"{executable} --version 返回 {completed.returncode}")


def _manifest_check(checks: list[Check]) -> None:
    manifest = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
    hooks = PLUGIN_ROOT / "hooks" / "codex-hooks.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        hook_data = json.loads(hooks.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        _add(checks, "plugin_manifest", "fail", f"插件清单不可读: {error}")
        return
    events = set(hook_data.get("hooks", {}))
    required = {"UserPromptSubmit", "PreToolUse", "SessionStart"}
    missing = sorted(required - events)
    if missing:
        _add(checks, "plugin_manifest", "fail", f"钩子清单缺少: {', '.join(missing)}")
        return
    _add(
        checks,
        "plugin_manifest",
        "ok",
        f"{data.get('name', 'delivery-conductor')} {data.get('version', '')}；钩子 {len(events)} 个事件",
    )


def _trust_check(checks: list[Check]) -> None:
    if not plugin_is_installed_copy():
        _add(checks, "hook_trust", "warn", "源码目录；安装到 Codex 插件缓存后再验证钩子信任")
        return
    try:
        report = enforce_host_trust()
    except HostTrustError as error:
        _add(checks, "hook_trust", "fail", str(error))
        return
    if report.get("skipped"):
        _add(checks, "hook_trust", "warn", "源码目录未安装，跳过钩子信任检查")
        return
    if report.get("ok"):
        _add(checks, "hook_trust", "ok", f"content {report.get('content_hash', '')[:19]}...")
        return
    _add(checks, "hook_trust", "fail", "; ".join(report.get("errors", [])) or "钩子未信任")


def collect_checks() -> list[Check]:
    checks: list[Check] = []
    _add(
        checks,
        "platform",
        "ok",
        f"{platform_module.system()} {platform_module.release()} ({sys.platform})",
    )
    _python_check(checks)
    _hook_runtime_check(checks)
    _toml_check(checks)
    _approval_check(checks)
    _codex_check(checks)
    _manifest_check(checks)
    _trust_check(checks)
    _add(checks, "source", "ok", str(PLUGIN_ROOT))
    _add(
        checks,
        "tty",
        "ok" if sys.stdin.isatty() else "warn",
        "当前标准输入是交互终端" if sys.stdin.isatty() else "当前标准输入不是交互终端",
    )
    _add(
        checks,
        "install_mode",
        "ok" if plugin_is_installed_copy() else "warn",
        "运行自 Codex 插件缓存" if plugin_is_installed_copy() else "运行自源码目录",
    )
    return checks


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="诊断编码总控插件的跨平台运行环境。")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true", help="把警告视为失败")
    args = parser.parse_args()

    checks = collect_checks()
    failed = any(item.status == "fail" for item in checks)
    warned = any(item.status == "warn" for item in checks)
    ok = not failed and not (args.strict and warned)

    if args.json:
        payload: dict[str, Any] = {
            "ok": ok,
            "strict": args.strict,
            "checks": [
                {"name": item.name, "status": item.status, "detail": item.detail}
                for item in checks
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("编码总控环境诊断")
        for item in checks:
            print(f"[{item.status.upper()}] {item.name}: {item.detail}")
        print("结论：" + ("通过" if ok else "存在阻塞项"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

