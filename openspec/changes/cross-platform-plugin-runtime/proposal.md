# Proposal

## Why

插件清单把产品描述为 macOS/Linux 可用，但实际运行时仍依赖 POSIX Shell：

- `hooks/codex-hooks.json` 用 `sh -c`、`2>/dev/null` 和 `|| true` 启动钩子。
- Hook 命令硬编码 `python3`，而原生 Windows 通常使用 `py -3` 或 `python`。
- `scripts/install_linux.sh` 依赖 bash、`cp -a`、`find`、`chmod` 和 POSIX 引号规则。
- 信任哈希按 POSIX 路径分隔符和原始 Hook 声明计算，未覆盖 `commandWindows` 的平台解析。
- 守卫使用 POSIX `shlex.split` 解析命令，不能可靠处理 Windows cmd/PowerShell 命令行。
- CI 只跑 Ubuntu，文档和技能示例默认使用 `python3`、`$PLUGIN_ROOT` 与 Unix 路径。

结果是：即使 Windows 能安装插件，Hook 也无法可靠启动，审批门禁因此失效。不能通过再写一套 Bash/PowerShell 实现来修复；那会产生三套行为和安全边界。

## What Changes

1. 保留一套 Python 业务实现，把所有 Hook 入口改为直接调用对应 `.py` 文件。
2. 使用 Codex 原生 Hook 字段 `commandWindows` 声明 Windows 解释器入口；macOS/Linux 使用 `command`。两者调用同一 Python 文件，不复制逻辑。
3. 移除 `sh -c`、`2>/dev/null`、`|| true` 和 `${PLUGIN_ROOT:-.}`；改用 Codex 会替换的 `${PLUGIN_ROOT}`。
4. 用 `scripts/install.py` 替换 `scripts/install_linux.sh`，仅使用 Python 标准库完成校验、复制、缓存排除和下一步提示。
5. 修正信任自检：按当前平台解析 `commandWindows`，对齐 Codex 的声明哈希与插件内容哈希规则。
6. 把命令解析、路径处理、安装和诊断统一到平台适配层，避免在业务逻辑中散落 `os.name` 分支。
7. 文档明确“同一套代码、不同启动命令”；所有脚本示例标注 Windows 使用 `py -3`。
8. CI 增加 macOS、Ubuntu、Windows 三系统矩阵，并增加 Hook 入口、安装器、信任哈希和命令守卫的跨平台测试。

## Capabilities

### New Capabilities

- `plugin-runtime-compatibility`: 定义同一套 Delivery Conductor 代码在 macOS、Linux 和原生 Windows 上的 Hook 启动、安装、路径、信任和验证行为。

### Modified Capabilities

- 无。当前仓库尚未建立 OpenSpec 基线能力；本次以新能力记录跨平台契约。

## Impact

- 清单与 Hook：`hooks/codex-hooks.json`、`.codex-plugin/plugin.json`
- 运行时：`scripts/*.py`、`scripts/conductor/*.py`
- 安装：删除 `scripts/install_linux.sh`，新增 `scripts/install.py`
- 测试：`tests/test_conductor.py`、`.github/workflows/test.yml`
- 文档与技能：`README.md`、`skills/delivery-conductor/SKILL.md`
- 用户环境：macOS/Linux 需要 `python3`；Windows 需要 Python 3.10+ 与 `py` 启动器。Python 仍是唯一业务运行时，不要求 Bash、WSL 或 PowerShell 版本。

## Research Basis

- `codex-rs/protocol/src/hooks.rs` 定义 `command`、`commandWindows`（兼容别名 `command_windows`）。
- `codex-rs/hooks/src/engine/discovery.rs` 证明 Codex 在计算声明哈希前按平台选择 Windows 覆盖命令，并替换 `${PLUGIN_ROOT}` 等插件环境变量。
- `codex-rs/hooks/src/engine/plugin_fingerprint.rs` 定义插件内容哈希，使用原生命名空间路径字节和 `symlink_metadata` 语义。
- Codex issue #34690、#38295、#47810 记录 Windows 上 `%PLUGIN_ROOT%`、WSL `bash` 和 Store PowerShell 的真实故障，支持“禁止依赖 bash、使用 `${PLUGIN_ROOT}`、避免 PowerShell 包装”的决策。
- 跨平台同类实现 `24kchengYe/codex-safety-hook` 使用绝对解释器路径、`subprocess.list2cmdline` 与 `shlex.join` 生成平台命令，验证了 Python 单实现思路。
