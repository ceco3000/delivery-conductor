# Cross-Platform Delivery Conductor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用同一套 Python 业务代码让 Delivery Conductor 在 macOS、Linux 和原生 Windows 上完成 Hook 启动、安装、信任校验、命令守卫和验证。

**Architecture:** 业务逻辑继续使用现有 Python 模块；新增一个很薄的 `conductor/runtime.py` 集中处理解释器、命令分词、平台路径和可执行文件名。Hook 通过 Codex 原生 `command`/`commandWindows` 声明进入同一 Python 文件。安装器改为 Python，信任哈希按当前平台解析 Hook 声明并对齐 Codex 的原生路径字节语义。

**Tech Stack:** Python 3.10+ 标准库、Codex 插件清单与 Hooks、OpenSpec、unittest、GitHub Actions。

**Spec:** `openspec/changes/cross-platform-plugin-runtime/design.md` 与 `openspec/changes/cross-platform-plugin-runtime/specs/plugin-runtime-compatibility/spec.md`

> **执行状态：待批准。**

## Global Constraints

- 唯一业务运行时是 Python 3.10+；不新增 Bash、PowerShell、Node.js 或 Rust 实现。
- Hook 不得使用 `sh -c`、`2>/dev/null`、`|| true` 或 `${PLUGIN_ROOT:-.}`。
- macOS/Linux Hook 使用 `python3`，Windows Hook 使用 `py -3`；两者调用同一个 `.py` 文件。
- Codex 在计算信任哈希前选择 `commandWindows`，并在命令中替换 `${PLUGIN_ROOT}`；插件不得自行改用 `%PLUGIN_ROOT%`。
- 审批继续只在交互式 TTY 中进行；无 TTY、哈希不一致或无法解析时失败关闭。
- 所有新增文档使用简体中文；代码、命令、路径和标识符保持原样。
- 每个任务先运行失败测试，再写最小实现；测试未失败前不得写生产代码。
- 所有工作完成后必须运行 `python -m unittest discover -s tests -v`、`python scripts/simulate.py`、`python scripts/doctor.py --json` 和 OpenSpec 严格校验。

## File Structure

| 文件 | 责任 |
|---|---|
| `scripts/conductor/runtime.py` | 解释器、平台命令分词、可执行文件名、原生相对路径字节、Shell 控制符检测 |
| `hooks/codex-hooks.json` | 每个 Hook 的 `command` 与 `commandWindows` 声明 |
| `scripts/hook_user_prompt.py` | 使用平台安全命令串注入语义评估协议 |
| `scripts/host_trust.py` | 平台感知的声明哈希与 Codex 等价内容哈希 |
| `scripts/guard_tool.py` | 跨平台只读命令守卫，无法解析时拒绝 |
| `scripts/install.py` | 唯一的 Python 安装器 |
| `scripts/doctor.py` | 三平台环境、Hook 解释器、Codex 与信任诊断 |
| `tests/test_conductor.py` | 全部行为测试，不依赖 Bash 才能运行 |
| `.github/workflows/test.yml` | Ubuntu/macOS/Windows 测试矩阵 |
| `README.md`、`skills/delivery-conductor/SKILL.md` | 三平台安装与启动说明 |
| `.codex-plugin/plugin.json`、`.agents/plugins/marketplace.json` | 版本与跨平台描述 |

---

### Task 1: 平台适配模块

**Files:**
- Create: `scripts/conductor/runtime.py`
- Modify: `tests/test_conductor.py`（新增导入与测试类）

**Interfaces:**
- Consumes: Python 标准库 `os`、`ntpath`、`shlex`、`subprocess`、`sys`、`pathlib`。
- Produces: `is_windows(platform: str | None = None) -> bool`、`program_name(value: str, *, platform: str | None = None) -> str`、`quote_argv(argv: list[str], *, platform: str | None = None) -> str`、`hook_interpreter(*, platform: str | None = None) -> list[str]`、`split_command(command: str, *, platform: str | None = None) -> list[str]`、`has_shell_control(command: str) -> bool`、`relative_name_bytes(path: Path, *, platform: str | None = None) -> bytes`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_conductor.py` 的导入区加入：

```python
from conductor.runtime import (
    hook_interpreter,
    program_name,
    quote_argv,
    relative_name_bytes,
    split_command,
)
```

在 `TerminalApprovalTests` 前新增：

```python
class RuntimeCompatibilityTests(unittest.TestCase):
    def test_windows_hook_interpreter_uses_py_launcher(self) -> None:
        self.assertEqual(hook_interpreter(platform="nt"), ["py", "-3"])
        self.assertEqual(hook_interpreter(platform="posix"), [sys.executable])

    def test_windows_quote_argv_uses_cmd_quoting(self) -> None:
        self.assertEqual(
            quote_argv(
                [r"C:\Program Files\Python\python.exe", r"C:\A B\hook.py"],
                platform="nt",
            ),
            r'"C:\Program Files\Python\python.exe" "C:\A B\hook.py"',
        )

    def test_windows_program_name_uses_ntpath(self) -> None:
        self.assertEqual(program_name(r"C:\Windows\System32\Git.exe", platform="nt"), "git.exe")

    def test_windows_split_command_preserves_path_spaces(self) -> None:
        self.assertEqual(
            split_command(r'Get-Content "C:\A B\proposal.md"', platform="nt"),
            ["Get-Content", r"C:\A B\proposal.md"],
        )

    def test_windows_relative_name_uses_backslashes(self) -> None:
        self.assertEqual(relative_name_bytes(Path("a/b.txt"), platform="nt"), b"a\\b.txt")
        self.assertEqual(relative_name_bytes(Path("a/b.txt"), platform="posix"), b"a/b.txt")

    def test_shell_control_characters_are_detected(self) -> None:
        for command in ("git status; whoami", "a || b", "a | b", "echo $(whoami)", "a\r\nb"):
            self.assertTrue(has_shell_control(command), command)
        self.assertFalse(has_shell_control('Get-Content "C:\\A B\\file.txt"'))
```

同时把测试导入改为：

```python
from conductor.runtime import (
    has_shell_control,
    hook_interpreter,
    program_name,
    quote_argv,
    relative_name_bytes,
    split_command,
)
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
python -m unittest tests.test_conductor.RuntimeCompatibilityTests -v
```

Expected: FAIL，错误包含 `ModuleNotFoundError: No module named 'conductor.runtime'`。

- [ ] **Step 3: 写最小实现**

创建 `scripts/conductor/runtime.py`：

```python
from __future__ import annotations

import ntpath
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path


CONTROL_PATTERN = re.compile(r"%[^%\r\n]+%")


def is_windows(platform: str | None = None) -> bool:
    return (platform or os.name) == "nt"


def program_name(value: str, *, platform: str | None = None) -> str:
    if is_windows(platform):
        return ntpath.basename(value).lower()
    return Path(value).name


def quote_argv(argv: list[str], *, platform: str | None = None) -> str:
    if is_windows(platform):
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def hook_interpreter(*, platform: str | None = None) -> list[str]:
    if is_windows(platform):
        return ["py", "-3"]
    return [sys.executable]


def _strip_outer_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def split_command(command: str, *, platform: str | None = None) -> list[str]:
    if not command.strip():
        return []
    use_posix = not is_windows(platform)
    tokens = shlex.split(command, posix=use_posix)
    if use_posix:
        return tokens
    return [_strip_outer_quotes(token) for token in tokens]


def has_shell_control(command: str) -> bool:
    if any(token in command for token in (";", "&&", "||", "|", ">", "<", "`", "$(", "\n", "\r")):
        return True
    return bool(CONTROL_PATTERN.search(command))


def relative_name_bytes(path: Path, *, platform: str | None = None) -> bytes:
    value = str(path).replace("/", "\\") if is_windows(platform) else path.as_posix()
    return value.encode("utf-8", "surrogatepass")
```

- [ ] **Step 4: 运行测试并确认通过**

Run:

```bash
python -m unittest tests.test_conductor.RuntimeCompatibilityTests -v
```

Expected: 6 tests PASS。

- [ ] **Step 5: 提交**

```bash
git add scripts/conductor/runtime.py tests/test_conductor.py
git commit -m "feat: add cross-platform runtime helpers"
```

---

### Task 2: Hook 声明改为单 Python 入口

**Files:**
- Modify: `hooks/codex-hooks.json`
- Modify: `tests/test_conductor.py`（`ConductorTests.test_hook_config_declares_prompt_trigger_and_pretool_guard`）

**Interfaces:**
- Consumes: Codex Hook 字段 `command`、`commandWindows`、`${PLUGIN_ROOT}`。
- Produces: 三个 Hook 事件均使用同一 `.py` 文件的 POSIX/Windows 双声明。

- [ ] **Step 1: 写失败测试**

新增测试：

```python
def test_hook_manifest_has_platform_neutral_python_entries(self) -> None:
    hooks = json.loads((PLUGIN_ROOT / "hooks/codex-hooks.json").read_text(encoding="utf-8"))
    expected = {
        "UserPromptSubmit": "hook_user_prompt.py",
        "PreToolUse": "guard_tool.py",
        "SessionStart": "hook_session_start.py",
    }
    for event, script in expected.items():
        handlers = [item for group in hooks["hooks"][event] for item in group["hooks"]]
        self.assertEqual(len(handlers), 1, event)
        handler = handlers[0]
        self.assertEqual(handler["type"], "command", event)
        self.assertIn(script, handler["command"], event)
        self.assertIn(script, handler["commandWindows"], event)
        self.assertIn('python3 "${PLUGIN_ROOT}/scripts/', handler["command"], event)
        self.assertIn('py -3 "${PLUGIN_ROOT}/scripts/', handler["commandWindows"], event)
        self.assertNotIn("sh -c", handler["command"], event)
        self.assertNotIn("2>/dev/null", handler["command"], event)
        self.assertNotIn("|| true", handler["command"], event)
        self.assertNotIn("${PLUGIN_ROOT:-.}", handler["command"], event)
        self.assertNotIn("${PLUGIN_ROOT:-.}", handler["commandWindows"], event)
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
python -m unittest tests.test_conductor.ConductorTests.test_hook_manifest_has_platform_neutral_python_entries -v
```

Expected: FAIL，`KeyError: 'commandWindows'`。

- [ ] **Step 3: 写最小实现**

把三个 Handler 改为：

```json
{
  "type": "command",
  "command": "python3 \"${PLUGIN_ROOT}/scripts/hook_user_prompt.py\"",
  "commandWindows": "py -3 \"${PLUGIN_ROOT}/scripts/hook_user_prompt.py\""
}
```

```json
{
  "type": "command",
  "command": "python3 \"${PLUGIN_ROOT}/scripts/guard_tool.py\"",
  "commandWindows": "py -3 \"${PLUGIN_ROOT}/scripts/guard_tool.py\""
}
```

```json
{
  "type": "command",
  "command": "python3 \"${PLUGIN_ROOT}/scripts/hook_session_start.py\"",
  "commandWindows": "py -3 \"${PLUGIN_ROOT}/scripts/hook_session_start.py\""
}
```

- [ ] **Step 4: 运行测试并确认通过**

Run:

```bash
python -m unittest tests.test_conductor.ConductorTests.test_hook_manifest_has_platform_neutral_python_entries tests.test_conductor.ConductorTests.test_hook_config_declares_prompt_trigger_and_pretool_guard -v
```

Expected: 2 tests PASS。

- [ ] **Step 5: 提交**

```bash
git add hooks/codex-hooks.json tests/test_conductor.py
git commit -m "feat: declare cross-platform hook entrypoints"
```

---

### Task 3: 提示 Hook 生成平台安全的命令

**Files:**
- Modify: `scripts/hook_user_prompt.py`
- Modify: `tests/test_conductor.py`（`ConductorTests`）

**Interfaces:**
- Consumes: `conductor.runtime.hook_interpreter()` 与 `quote_argv()`。
- Produces: `python_command(script: Path) -> str`，在当前平台生成可直接执行的绝对命令。

- [ ] **Step 1: 写失败测试**

新增：

```python
def test_prompt_hook_uses_platform_python_command(self) -> None:
    from hook_user_prompt import python_command

    command = python_command(PLUGIN_ROOT / "scripts/route.py")
    self.assertIn("route.py", command)
    if os.name == "nt":
        self.assertTrue(command.startswith('py -3 '))
    else:
        self.assertTrue(command.startswith(sys.executable))
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
python -m unittest tests.test_conductor.ConductorTests.test_prompt_hook_uses_platform_python_command -v
```

Expected: FAIL，`ImportError: cannot import name 'python_command'`。

- [ ] **Step 3: 写最小实现**

在 `scripts/hook_user_prompt.py` 增加：

```python
from conductor.runtime import hook_interpreter, quote_argv


def python_command(script: Path) -> str:
    return quote_argv([*hook_interpreter(), str(script)])
```

把注入字符串中的：

```python
python3 "{PLUGIN_ROOT / "scripts/route.py"}" --assess-json '<JSON>' --state "{state_path}" --base-dir "{cwd}"
```

改为：

```python
{python_command(PLUGIN_ROOT / "scripts/route.py")} --assess-json '<JSON>' --state "{state_path}" --base-dir "{cwd}"
```

- [ ] **Step 4: 运行测试并确认通过**

Run:

```bash
python -m unittest tests.test_conductor.ConductorTests.test_prompt_hook_uses_platform_python_command tests.test_conductor.ConductorTests.test_prompt_hook_routes_and_injects_skill -v
```

Expected: 2 tests PASS。

- [ ] **Step 5: 提交**

```bash
git add scripts/hook_user_prompt.py tests/test_conductor.py
git commit -m "fix: generate platform-safe hook commands"
```

---

### Task 4: 信任声明哈希按平台解析

**Files:**
- Modify: `scripts/host_trust.py`
- Modify: `tests/test_conductor.py`（`host_trust_fixture` 与 `HostTrustTests`）

**Interfaces:**
- Consumes: `conductor.runtime.is_windows()`。
- Produces: `declaration_hash(event_label, matcher, handler, plugin_root=None, platform=None) -> str`。

- [ ] **Step 1: 写失败测试**

在 `HostTrustTests` 增加：

```python
def test_windows_declaration_hash_uses_command_windows(self) -> None:
    root = Path(r"C:\plugins\delivery-conductor")
    handler = {
        "type": "command",
        "command": 'python3 "${PLUGIN_ROOT}/scripts/guard_tool.py"',
        "commandWindows": 'py -3 "${PLUGIN_ROOT}/scripts/guard_tool.py"',
    }
    expected = declaration_hash(
        "pre_tool_use",
        "*",
        {
            "type": "command",
            "command": 'py -3 "C:\\plugins\\delivery-conductor/scripts/guard_tool.py"',
        },
        plugin_root=root,
        platform="posix",
    )
    actual = declaration_hash(
        "pre_tool_use",
        "*",
        handler,
        plugin_root=root,
        platform="nt",
    )
    self.assertEqual(actual, expected)

def test_declaration_hash_substitutes_plugin_root(self) -> None:
    root = Path("/tmp/delivery-conductor")
    templated = {
        "type": "command",
        "command": 'python3 "${PLUGIN_ROOT}/scripts/guard_tool.py"',
    }
    absolute = {
        "type": "command",
        "command": 'python3 "/tmp/delivery-conductor/scripts/guard_tool.py"',
    }
    self.assertEqual(
        declaration_hash("pre_tool_use", "*", templated, plugin_root=root),
        declaration_hash("pre_tool_use", "*", absolute, plugin_root=root),
    )
```

把 `host_trust_fixture()` 中的调用改为：

```python
trusted = declaration_hash(
    label,
    group.get("matcher"),
    handler,
    plugin_root=root,
    platform="nt" if os.name == "nt" else "posix",
)
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
python -m unittest tests.test_conductor.HostTrustTests.test_windows_declaration_hash_uses_command_windows -v
```

Expected: FAIL，`TypeError: declaration_hash() got an unexpected keyword argument 'plugin_root'`。

- [ ] **Step 3: 写最小实现**

在 `scripts/host_trust.py` 导入并修改：

```python
from conductor.runtime import is_windows


def _resolved_command(
    handler: dict[str, Any],
    *,
    plugin_root: Path | None,
    platform: str | None,
) -> str:
    command = str(handler.get("command", ""))
    if is_windows(platform):
        command = str(handler.get("commandWindows", handler.get("command_windows", command)))
    if plugin_root is not None:
        command = command.replace("${PLUGIN_ROOT}", str(plugin_root))
        command = command.replace("${PLUGIN_DATA}", str(plugin_root))
    return command


def _normalized_command_handler(
    handler: dict[str, Any],
    *,
    plugin_root: Path | None = None,
    platform: str | None = None,
) -> dict[str, Any]:
    timeout = handler.get("timeout")
    timeout = 600 if timeout is None else max(1, int(timeout))
    normalized: dict[str, Any] = {
        "type": "command",
        "command": _resolved_command(handler, plugin_root=plugin_root, platform=platform),
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
    normalized = _normalized_command_handler(
        handler,
        plugin_root=Path(plugin_root).resolve() if plugin_root is not None else None,
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
```

把 `trust_report()` 中的调用改为：

```python
expected_declaration = declaration_hash(
    event_label,
    matcher,
    handler,
    plugin_root=root,
)
```

- [ ] **Step 4: 运行测试并确认通过**

Run:

```bash
python -m unittest tests.test_conductor.HostTrustTests -v
```

Expected: 全部 PASS，原 macOS/Linux 信任夹具仍通过。

- [ ] **Step 5: 提交**

```bash
git add scripts/host_trust.py tests/test_conductor.py
git commit -m "fix: resolve platform hook declarations for trust"
```

---

### Task 5: 对齐 Codex 插件内容哈希

**Files:**
- Modify: `scripts/host_trust.py`
- Modify: `tests/test_conductor.py`（`HostTrustTests`）

**Interfaces:**
- Consumes: `conductor.runtime.relative_name_bytes()`。
- Produces: `plugin_content_hash(root, platform=None)`，使用原生相对路径字节与 `lstat` 类型判断。

- [ ] **Step 1: 写失败测试**

新增：

```python
def test_content_hash_uses_windows_relative_path_bytes(self) -> None:
    from host_trust import _relative_hash_name

    self.assertEqual(_relative_hash_name(Path("a/b.txt"), platform="nt"), b"a\\b.txt")
    self.assertEqual(_relative_hash_name(Path("a/b.txt"), platform="posix"), b"a/b.txt")

def test_content_hash_rejects_symlink_entries(self) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "plugin"
        root.mkdir()
        (root / "real.txt").write_text("real\n", encoding="utf-8")
        try:
            (root / "link.txt").symlink_to(root / "real.txt")
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable")
        with self.assertRaises(OSError):
            plugin_content_hash(root)
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
python -m unittest tests.test_conductor.HostTrustTests.test_content_hash_uses_windows_relative_path_bytes -v
```

Expected: FAIL，`ImportError: cannot import name '_relative_hash_name'`。

- [ ] **Step 3: 写最小实现**

在 `scripts/host_trust.py` 增加 `import stat` 和 `relative_name_bytes` 导入，并修改哈希遍历：

```python
def _relative_hash_name(path: Path, *, platform: str | None = None) -> bytes:
    return relative_name_bytes(path, platform=platform)


def plugin_content_hash(root: str | os.PathLike[str], *, platform: str | None = None) -> str:
    root_path = Path(root).resolve()
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
```

- [ ] **Step 4: 运行测试并确认通过**

Run:

```bash
python -m unittest tests.test_conductor.HostTrustTests -v
```

Expected: 全部 PASS；不支持符号链接的环境显示 skip，其余测试通过。

- [ ] **Step 5: 提交**

```bash
git add scripts/host_trust.py tests/test_conductor.py
git commit -m "fix: align plugin content hash with Codex"
```

---

### Task 6: 守卫使用平台命令解析

**Files:**
- Modify: `scripts/guard_tool.py`
- Modify: `tests/test_conductor.py`（新增导入与 `PreImplementationGuardTests`）

**Interfaces:**
- Consumes: `conductor.runtime.program_name()`、`split_command()`、`has_shell_control()`。
- Produces: `allowed_read_only(args, *, platform=None) -> bool` 与 `routing_inspection_command_allowed(command, *, platform=None) -> bool`。

- [ ] **Step 1: 写失败测试**

在测试导入区加入：

```python
from guard_tool import allowed_read_only, routing_inspection_command_allowed
```

在 `PreImplementationGuardTests` 增加：

```python
def test_windows_read_only_commands_are_allowed(self) -> None:
    for command in (
        "git status --short",
        "Get-Content 'C:\\A B\\proposal.md'",
        "Select-String -Pattern trust -Path C:\\A B\\proposal.md",
        "Get-ChildItem C:\\A B",
        "Test-Path C:\\A B\\proposal.md",
        "Resolve-Path C:\\A B\\proposal.md",
    ):
        args = split_command(command, platform="nt")
        self.assertTrue(allowed_read_only(args, platform="nt"), command)

def test_windows_write_command_is_rejected(self) -> None:
    args = split_command("Set-Content C:\\A B\\proposal.md changed", platform="nt")
    self.assertFalse(allowed_read_only(args, platform="nt"))

def test_windows_shell_wrapper_is_rejected(self) -> None:
    command = "powershell -NoProfile -Command Get-Content C:\\A B\\proposal.md"
    self.assertFalse(routing_inspection_command_allowed(command, platform="nt"))
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
python -m unittest tests.test_conductor.PreImplementationGuardTests.test_windows_read_only_commands_are_allowed -v
```

Expected: FAIL，`TypeError: allowed_read_only() got an unexpected keyword argument 'platform'`。

- [ ] **Step 3: 写最小实现**

在 `scripts/guard_tool.py` 中导入：

```python
from conductor.runtime import has_shell_control, program_name, split_command
```

把 `READ_ONLY` 改为：

```python
READ_ONLY = {
    "cat", "echo", "file", "find", "jq", "ls", "pwd", "rg", "sed", "stat", "tail", "head", "wc", "which",
    "get-content", "get-childitem", "select-string", "test-path", "resolve-path", "where", "where.exe",
}
```

替换命令拆分和只读判断：

```python
def allowed_read_only(args: list[str], *, platform: str | None = None) -> bool:
    program = program_name(args[0], platform=platform)
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
        return not any(value == "--in-place" or bool(re.match(r"^-[A-Za-z]*i", value)) for value in args[1:])
    if program == "rg":
        return not any(value.startswith(("--pre", "--hostname-bin")) for value in args[1:])
    return program in READ_ONLY


def routing_inspection_command_allowed(command: str, *, platform: str | None = None) -> bool:
    if not command.strip() or has_shell_control(command):
        return False
    try:
        args = split_command(command, platform=platform)
    except ValueError:
        return False
    if not args:
        return False
    if allowed_read_only(args, platform=platform):
        return True
    program = program_name(args[0], platform=platform)
    return (
        program in {"python", "python3", "py", "node"}
        and len(args) >= 2
        and Path(args[1]).name in HANDSHAKE
    )
```

同时把 `semantic_assessment_command_allowed()` 与 `pre_implementation_shell_allowed()` 中的 `shlex.split()` 替换为 `split_command()`，把 `Path(args[0]).name` 替换为 `program_name(args[0])`。

- [ ] **Step 4: 运行测试并确认通过**

Run:

```bash
python -m unittest tests.test_conductor.PreImplementationGuardTests tests.test_conductor.ConductorTests.test_pretool_blocks_unknown_shell_and_allows_read_only -v
```

Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add scripts/guard_tool.py tests/test_conductor.py
git commit -m "fix: parse guard commands per platform"
```

---

### Task 7: Python 安装器替换 Bash 脚本

**Files:**
- Create: `scripts/install.py`
- Delete: `scripts/install_linux.sh`
- Modify: `tests/test_conductor.py`（`InstallScriptTests`）

**Interfaces:**
- Consumes: `pathlib`、`shutil`、`argparse`、`subprocess`、`sys`。
- Produces: `install(source: Path, target: Path) -> None` 与跨平台 CLI `--target`。

- [ ] **Step 1: 写失败测试**

把 `InstallScriptTests` 替换为：

```python
class InstallScriptTests(unittest.TestCase):
    def test_installer_copies_plugin_without_caches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "plugin"
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "install.py"), "--target", str(target)],
                cwd=str(PLUGIN_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertTrue((target / ".codex-plugin/plugin.json").is_file())
            self.assertTrue((target / "scripts/doctor.py").is_file())
            self.assertFalse(any(target.rglob("__pycache__")))
            self.assertFalse(any(target.rglob("*.pyc")))

    def test_installer_refuses_non_empty_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "plugin"
            target.mkdir()
            (target / "keep.txt").write_text("keep\n", encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "install.py"), "--target", str(target)],
                cwd=str(PLUGIN_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertTrue((target / "keep.txt").is_file())
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
python -m unittest tests.test_conductor.InstallScriptTests -v
```

Expected: FAIL，脚本 `scripts/install.py` 不存在。

- [ ] **Step 3: 写最小实现**

创建 `scripts/install.py`：

```python
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".venv")


def install(source: Path, target: Path) -> None:
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"目标目录已存在且非空: {target}")
    if target.exists():
        target.rmdir()
    shutil.copytree(source, target, ignore=IGNORED)


def main() -> int:
    parser = argparse.ArgumentParser(description="Install Delivery Conductor on macOS, Linux, or Windows.")
    parser.add_argument("--target", type=Path, default=Path.home() / "delivery-conductor")
    args = parser.parse_args()
    target = args.target.expanduser().resolve()
    try:
        install(PLUGIN_ROOT, target)
    except (OSError, ValueError) as error:
        print(f"安装失败: {error}", file=sys.stderr)
        return 1
    print(f"已复制编码总控插件到: {target}")
    doctor = target / "scripts" / "doctor.py"
    subprocess.run([sys.executable, str(doctor)], cwd=str(target), check=False)
    parent = target.parent
    print("下一步:")
    print(f"  cd {parent}")
    print(f"  codex plugin marketplace add ./{target.name}")
    print("  codex plugin add delivery-conductor@delivery-conductor")
    print("  重启 Codex，打开 Hooks > Review hooks，确认三个钩子后选择 Trust all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

删除 `scripts/install_linux.sh`。

- [ ] **Step 4: 运行测试并确认通过**

Run:

```bash
python -m unittest tests.test_conductor.InstallScriptTests -v
```

Expected: 2 tests PASS。

- [ ] **Step 5: 提交**

```bash
git add scripts/install.py tests/test_conductor.py
git rm scripts/install_linux.sh
git commit -m "feat: replace bash installer with python installer"
```

---

### Task 8: 诊断与插件清单

**Files:**
- Modify: `scripts/doctor.py`
- Modify: `.codex-plugin/plugin.json`
- Modify: `.agents/plugins/marketplace.json`
- Modify: `tests/test_conductor.py`（`DoctorTests` 与清单测试）

**Interfaces:**
- Consumes: `conductor.runtime.hook_interpreter()`。
- Produces: `doctor.py --json` 新检查项 `hook_runtime`，以及 0.5.0 跨平台插件描述。

- [ ] **Step 1: 写失败测试**

在 `DoctorTests.test_doctor_json_reports_environment_checks` 增加：

```python
self.assertIn("hook_runtime", names)
```

新增：

```python
def test_doctor_uses_cross_platform_tomli_guidance(self) -> None:
    source = (SCRIPTS / "doctor.py").read_text(encoding="utf-8")
    self.assertNotIn("sudo apt install", source)
    self.assertIn("pip install tomli", source)
```

在清单测试中把版本断言改为：

```python
self.assertEqual(manifest["version"], "0.5.0")
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
python -m unittest tests.test_conductor.DoctorTests.test_doctor_json_reports_environment_checks tests.test_conductor.ConductorTests.test_skill_documents_human_approval_protocol -v
```

Expected: FAIL，`hook_runtime` 不存在且版本仍为 `0.4.2`。

- [ ] **Step 3: 写最小实现**

在 `scripts/doctor.py` 导入：

```python
from conductor.runtime import hook_interpreter
```

新增：

```python
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
```

在 `collect_checks()` 中 `_python_check(checks)` 后调用 `_hook_runtime_check(checks)`。

把 TOML 提示改为：

```text
缺少 tomllib/tomli；请安装 Python 3.11+，或运行 `python -m pip install tomli`
```

把 `.codex-plugin/plugin.json` 与 `.agents/plugins/marketplace.json` 的版本改为 `0.5.0`，并更新描述为 macOS、Linux 和 Windows。

- [ ] **Step 4: 运行测试并确认通过**

Run:

```bash
python -m unittest tests.test_conductor.DoctorTests tests.test_conductor.ConductorTests.test_skill_documents_human_approval_protocol -v
```

Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add scripts/doctor.py .codex-plugin/plugin.json .agents/plugins/marketplace.json tests/test_conductor.py
git commit -m "feat: report and ship cross-platform runtime"
```

---

### Task 9: 文档、CI 与完整验证

**Files:**
- Modify: `README.md`
- Modify: `skills/delivery-conductor/SKILL.md`
- Modify: `.github/workflows/test.yml`
- Modify: `openspec/changes/cross-platform-plugin-runtime/tasks.md`

**Interfaces:**
- Consumes: 前八项任务完成的代码、测试和插件入口。
- Produces: 三平台 CI 与用户可执行文档。

- [ ] **Step 1: 写失败检查**

在 `tests/test_conductor.py` 增加：

```python
def test_docs_describe_windows_python_entrypoint(self) -> None:
    readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
    skill = (PLUGIN_ROOT / "skills/delivery-conductor/SKILL.md").read_text(encoding="utf-8")
    for text in (readme, skill):
        self.assertIn("Windows", text)
        self.assertIn("py -3", text)
        self.assertIn("commandWindows", text)
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
python -m unittest tests.test_conductor.ConductorTests.test_docs_describe_windows_python_entrypoint -v
```

Expected: FAIL，README 尚无 `commandWindows` 说明。

- [ ] **Step 3: 写最小实现**

README 的安装段改为：

```markdown
### macOS / Linux

```bash
python3 scripts/install.py
```

### Windows PowerShell

```powershell
py -3 scripts/install.py
```

插件内只有一套 Python 实现。Hook 清单使用 `command` 和 `commandWindows` 调用同一入口；不要把它们改写成 Bash 或 PowerShell 业务脚本。
```

技能文件增加：

```markdown
## Windows Hook 启动

Hook 清单位于 `hooks/codex-hooks.json`。macOS/Linux 使用 `command` 的 `python3` 入口，Windows 使用 `commandWindows` 的 `py -3` 入口；两者运行同一个 Python 文件。`${PLUGIN_ROOT}` 由 Codex 替换，禁止写成 `${PLUGIN_ROOT:-.}` 或 `%PLUGIN_ROOT%`。
```

把 `.github/workflows/test.yml` 改为：

```yaml
name: test

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Run unit tests
        run: python -m unittest discover -s tests -v

      - name: Run delivery simulation
        run: python scripts/simulate.py

      - name: Run environment doctor
        run: python scripts/doctor.py --json
```

- [ ] **Step 4: 运行完整验证**

Run:

```bash
python -m unittest discover -s tests -v
python scripts/simulate.py
python scripts/doctor.py --json
openspec validate cross-platform-plugin-runtime --strict
```

Expected: 单元测试 0 failures、模拟输出 `Simulation passed`、诊断返回 0、OpenSpec 返回 valid。

- [ ] **Step 5: 提交**

```bash
git add README.md skills/delivery-conductor/SKILL.md .github/workflows/test.yml tests/test_conductor.py
git commit -m "docs: document and verify three-platform runtime"
```

---

### Task 10: 三平台与真实宿主验收

**Files:**
- Modify: `openspec/changes/cross-platform-plugin-runtime/tasks.md`
- Create: `deliverables/跨平台验证记录.md`

**Interfaces:**
- Consumes: GitHub Actions 三平台结果与真实 Windows Codex 运行结果。
- Produces: 可审计的验证证据和最终交付状态。

- [ ] **Step 1: 记录本机结果**

在 `deliverables/跨平台验证记录.md` 写入实际命令、退出码和输出摘要：

```markdown
# 跨平台验证记录

## macOS

- 命令：`python -m unittest discover -s tests -v`
- 结果：待执行后填写实际通过数量

## Linux

- 来源：GitHub Actions `ubuntu-latest`
- 结果：待执行后填写运行链接与通过状态

## Windows

- 来源：GitHub Actions `windows-latest`
- 结果：待执行后填写运行链接与通过状态

## Windows Codex 桌面端

- 安装命令：`py -3 scripts/install.py`
- Hook 信任：待完成真实界面验收后填写
- 冒烟场景：编码总控入口、PreToolUse 拒绝写入、SessionStart 提示
```

- [ ] **Step 2: 运行本机验证**

Run:

```bash
python -m unittest discover -s tests -v
python scripts/simulate.py
python scripts/doctor.py --json
```

Expected: 所有命令退出码为 0；记录实际测试数量，不复制旧数量。

- [ ] **Step 3: 等待三平台 CI**

Run:

```bash
gh run list --workflow test --limit 1
gh run view --log
```

Expected: `ubuntu-latest`、`macos-latest`、`windows-latest` 三个 job 全部成功。

- [ ] **Step 4: 在真实 Windows Codex 中验收**

执行：

```powershell
py -3 scripts/install.py
```

然后重启 Codex，打开 Hooks > Review hooks，确认三个 Hook 并选择 Trust all。验证：

```text
编码总控 检查跨平台 Hook 是否启动
```

Expected: `UserPromptSubmit` 注入路由上下文，`SessionStart` 输出状态提醒，等待审批时写文件被拒绝。

- [ ] **Step 5: 更新任务与提交**

勾选所有已完成的 OpenSpec 任务和本计划步骤，把本计划头部执行状态改为“已完成并验证”，然后：

```bash
git add openspec/changes/cross-platform-plugin-runtime/tasks.md deliverables/跨平台验证记录.md docs/superpowers/plans/2026-11-24-cross-platform-plugin-runtime.md
git commit -m "docs: record three-platform verification"
```

## Self-Review

### Spec Coverage

| 规格要求 | 对应任务 |
|---|---|
| 跨平台 Hook 入口 | Task 2、Task 3、Task 9 |
| 单一业务实现 | Task 1、Task 6 |
| 跨平台安装与诊断 | Task 7、Task 8 |
| 信任与路径完整性 | Task 4、Task 5 |
| 跨平台命令守卫 | Task 6 |
| 三系统验证 | Task 9、Task 10 |

### Placeholder Scan

- 无 `TBD`、`TODO`、`implement later` 或“类似 Task N”。
- 每个测试步骤包含实际测试代码或明确断言。
- 每个实现步骤包含可执行代码或完整命令。
- 验证记录中的运行链接和测试数量必须在执行时填写真实值，不得预先伪造。

### Type Consistency

- 所有平台能力统一由 `conductor/runtime.py` 提供，业务模块不重复定义。
- `hook_interpreter()` 始终返回 argv 列表；`quote_argv()` 始终返回单条命令字符串。
- `declaration_hash()` 的新参数均为关键字参数，旧调用通过 `plugin_root` 显式升级。
- `plugin_content_hash()` 的平台参数只影响路径编码，不改变文件遍历顺序或哈希前缀。
