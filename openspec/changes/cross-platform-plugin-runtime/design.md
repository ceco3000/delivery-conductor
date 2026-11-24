# Design

## Context

当前插件的业务、状态机、证据和门禁均由 Python 实现，问题集中在宿主边界：Codex 用 `sh`/`cmd`/PowerShell 运行 Hook，而插件把 Shell 当成可移植运行时。Codex 已提供平台声明能力，因此正确方向是把平台差异收敛到 Hook 声明和少量适配函数，而不是复制业务流程。

约束：

- Codex Hook 的 `command` 和 `commandWindows` 都受插件信任哈希保护。
- `commandWindows` 在信任哈希计算前已按平台替换；运行时不应再自行判断命令文本。
- `${PLUGIN_ROOT}`、`${PLUGIN_DATA}` 由 Codex 在启动 Hook 前替换；不得使用 `%PLUGIN_ROOT%` 或 `${PLUGIN_ROOT:-.}`。
- Windows 默认环境和用户自选 Shell 都可能变化，不能依赖 Bash、Git Bash、WSL、PowerShell 7 或 Store PowerShell。
- 审批必须继续失败关闭，不能因为跨平台而退化为“无 TTY 自动批准”或图形对话框。

## Goals / Non-Goals

**Goals:**

- 一套 Python 业务实现同时运行在 macOS、Linux、Windows。
- Hook 在三个平台都能直接进入 Python，不经过 POSIX 包装器。
- 安装、诊断、路径、信任校验和命令守卫在三个平台行为一致。
- CI 能在每次变更中发现平台回归。
- 保持现有状态格式、门禁顺序和审批安全属性不变。

**Non-Goals:**

- 不重写 OpenSpec、Superpowers、ECC 或 UI UX Pro Max 的业务逻辑。
- 不引入 Node.js、Rust 二进制、WSL、PowerShell 脚本或第二套安装器。
- 不支持无 Python 环境；缺少 Python 时由诊断明确失败，不静默降级。
- 不改变审批令牌、TTY 失败关闭、证据摘要或一致性审计规则。
- 不在本次方案阶段编写实现代码。

## Decisions

### 1. Python 单核心 + `commandWindows` 声明

采用一套 Python 入口：

```text
macOS/Linux: python3 "${PLUGIN_ROOT}/scripts/<hook>.py"
Windows:     py -3 "${PLUGIN_ROOT}/scripts/<hook>.py"
```

`command` 与 `commandWindows` 指向同一个 `.py` 文件。平台差异仅存在于声明式启动串，不形成三套逻辑。

备选方案：

- Bash + PowerShell + Python：拒绝，产生三套行为与安全边界。
- 全平台 PowerShell Core：拒绝，不能假定已安装，且 Windows Store PowerShell 已有 Hook 启动故障。
- 全平台 Node.js：拒绝，插件宿主不保证提供 Node，现有代码也已经是 Python。
- 所有平台都使用 `python3`：拒绝，Windows 上不保证存在。

### 2. Hook 不再经过 Shell 包装器

删除 `sh -c`、stderr 重定向和 `|| true`。各 Hook Python 入口自身必须：

- 对畸形 stdin、缺失状态、读取失败返回明确的非阻塞结果或失败关闭结果。
- 不把 traceback 输出给宿主；用结构化输出或安静的非零退出表达失败。
- 使用 `sys.executable` 调起内部脚本，避免再次寻找 `python3`。

这样 macOS/Linux 不再依赖 `/bin/sh` 语法，Windows 不再依赖 `cmd` 参数展开。

### 3. 安装器改为 Python

新增 `scripts/install.py`，删除 `scripts/install_linux.sh`。安装器使用 `pathlib` 和 `shutil`：

- 支持 `--target` 与默认 `Path.home() / "delivery-conductor"`。
- 使用 `shutil.copytree(..., ignore=...)` 排除 `__pycache__`、`.pyc`、`.git`。
- 不做 `chmod`，因为 Windows 不使用 POSIX 执行位；Python 通过解释器启动。
- 复制后调用同一 `doctor.py`，输出跨平台安装和信任提示。
- 目标目录非空时失败，避免覆盖用户文件。

### 4. 平台适配集中化

新增或内聚到 `scripts/conductor/platform.py`（名称可在计划阶段确定）：

- Python 启动器探测：Unix 用 `python3`，Windows 优先 `py -3`。
- 命令展示：Unix 用 `shlex.join`，Windows 用 `subprocess.list2cmdline`。
- 路径比较：统一 `Path.resolve()`、`CODEX_HOME` 与插件缓存根。
- 可执行文件探测：使用 `shutil.which`，尊重 Windows `PATHEXT`。

业务模块不得散落 `sys.platform == "win32"` 分支。

### 5. 信任哈希对齐 Codex

`host_trust.py` 需要复现 Codex 当前平台的规范化语义：

- Windows 上若存在 `commandWindows`，声明哈希使用该命令；其他平台使用 `command`。
- 哈希前移除仅用于平台选择的 `commandWindows` 字段。
- 替换 `${PLUGIN_ROOT}`、`${PLUGIN_DATA}` 等插件变量，匹配 Codex 的 discovery 顺序。
- 插件内容哈希使用原生相对路径字节（Windows 为反斜杠路径），不使用 `as_posix()`。
- 使用 `lstat`/`symlink_metadata` 语义；不支持的文件类型失败关闭。
- 为声明哈希和内容哈希保存官方运行时生成的固定夹具，三平台 CI 同时验证。

### 6. 守卫命令解析平台化

`guard_tool.py` 的 Shell 允许列表改为显式平台解析器：

- Unix：现有 POSIX token 规则。
- Windows：分别识别 cmd 与 PowerShell 的安全子集，拒绝无法可靠解析的包装器。
- 无把握解析时失败关闭，尤其是审批等待期。
- 增加 `Get-Content`、`Select-String`、`Get-ChildItem`、`Test-Path`、`Resolve-Path`、`where.exe` 等只读命令测试。

### 7. 三系统验证矩阵

GitHub Actions 使用 `ubuntu-latest`、`macos-latest`、`windows-latest`：

- `python -m unittest discover -s tests -v`
- `python scripts/simulate.py`
- `python scripts/doctor.py --json`
- 直接解析 `hooks/codex-hooks.json` 并运行当前平台选中的 Hook 入口，提供样例 stdin，断言输出 JSON 或失败关闭结果。
- 验证安装器在临时目标目录可复制且无缓存文件。

不把“能否在 Windows 打开 Codex 桌面端并点击信任”放进自动 CI；该项作为人工发布验收清单。

## Risks / Trade-offs

| 风险 | 影响 | 缓解 |
|---|---|---|
| Windows 只有 Microsoft Store Python，没有 `py` | Hook 无法启动 | `doctor.py` 明确检测 `py -3`；文档要求 python.org 安装器或提供 `py` 启动器；安装阶段失败关闭 |
| 旧版 Codex 不识别 `commandWindows` | Windows 仍执行 POSIX 命令 | 在发布说明中声明最低 Codex 版本，并让 `doctor.py` 检查运行时版本/字段支持 |
| 删除 Shell 包装后 Hook 错误暴露给宿主 | 用户看到 Hook 失败 | Python 入口内部捕获并输出稳定结构化错误；保留失败关闭，不伪造成功 |
| 信任哈希与 Codex 内部实现偏差 | 插件被误判为未信任 | 使用官方源码夹具与三平台 CI 验证；无法匹配时拒绝继续，而不是放行 |
| PowerShell/cmd 语法复杂 | 守卫可能误判命令 | 只允许可证明安全的静态子集；无法解析时拒绝 |
| `py -3` 不是唯一 Windows Python 启动方式 | 少数环境无法运行 | 诊断给出明确修复建议；后续可在计划中评估无 Shell 的 .cmd 引导器，但不得复制业务逻辑 |

## Migration Plan

1. 先在测试中锁定现有 macOS/Linux 行为。
2. 增加平台适配层和 Windows 声明，不改状态结构。
3. 替换安装器与 Hook 包装器。
4. 修正信任哈希和守卫解析。
5. 扩展 CI 到三系统。
6. 更新 README、技能和清单描述。
7. 在 macOS 本机、Linux CI、Windows CI 全部通过后，才允许进行桌面端人工信任验收。

## References

- OpenAI Codex: `codex-rs/protocol/src/hooks.rs`
- OpenAI Codex: `codex-rs/hooks/src/engine/discovery.rs`
- OpenAI Codex: `codex-rs/hooks/src/engine/plugin_fingerprint.rs`
- OpenAI Codex issue #34690: `%PLUGIN_ROOT%` 在 `commandWindows` 中的展开差异
- OpenAI Codex issue #38295: Windows PATH 中 WSL `bash` 导致 Claude 插件 Hook 失败
- OpenAI Codex issue #47810: Store/MSIX PowerShell 导致命令 Hook 启动失败
- `24kchengYe/codex-safety-hook`: Python 跨平台 Hook 的 `sys.executable` 与平台引号实践
- Codex GitHub README: Windows CLI 使用 PowerShell 安装，macOS/Linux 使用 shell 安装，宿主平台差异真实存在
