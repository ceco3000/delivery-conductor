# Spec Delta

## Purpose

定义 Delivery Conductor 在 macOS、Linux 和原生 Windows 上共享同一套 Python 实现时的 Hook、安装、信任、路径、审批和验证契约。

## ADDED Requirements

### Requirement: 跨平台 Hook 入口

插件 MUST 以同一组 Python 入口服务三个平台。macOS/Linux 的 Hook 命令不得依赖 `sh -c`，Windows MUST 使用 Codex 支持的 `commandWindows` 声明；两个声明 MUST 调用同一业务文件。

#### Scenario: macOS Hook 启动
- **WHEN** Codex 在 macOS 上触发 `UserPromptSubmit`、`PreToolUse` 或 `SessionStart`
- **THEN** 插件直接以 `python3 "${PLUGIN_ROOT}/scripts/<hook>.py"` 启动对应入口
- **AND** 启动过程不包含 `sh -c`、`/dev/null`、`|| true` 或 `${PLUGIN_ROOT:-.}`

#### Scenario: Linux Hook 启动
- **WHEN** Codex 在 Linux 上触发任一插件 Hook
- **THEN** 插件以与 macOS 相同的 Python 入口和逻辑运行
- **AND** 不要求 Bash 以外的额外 Shell 或图形组件

#### Scenario: Windows Hook 启动
- **WHEN** Codex 在原生 Windows 上触发任一插件 Hook
- **THEN** `commandWindows` 使用 `py -3 "${PLUGIN_ROOT}/scripts/<hook>.py"` 启动同一入口
- **AND** 不执行 Bash、WSL、Git Bash 或独立 PowerShell 业务脚本

### Requirement: 单一业务实现

三个平台 MUST 共享状态机、门禁、证据、审批、路由和一致性审计实现。平台差异只允许出现在启动声明、解释器发现、命令分词和路径显示等宿主适配层。

#### Scenario: 平台分支受限
- **WHEN** 审查 `scripts/conductor/` 中的业务模块
- **THEN** 不得存在重复的 Windows、macOS、Linux 业务逻辑副本
- **AND** 平台判断只能出现在单一适配模块或明确的启动声明中

#### Scenario: 行为一致性
- **WHEN** 同一输入在 macOS、Linux、Windows 上执行
- **THEN** 路由等级、门禁顺序、证据结构和审批失败关闭行为一致

### Requirement: 跨平台安装与诊断

系统 MUST 提供一个仅依赖 Python 标准库的安装器，替换 Bash 专属安装脚本；诊断 MUST 识别当前平台、Python 版本、Hook 启动器和 Codex 可执行文件。

#### Scenario: Unix 安装
- **WHEN** 用户在 macOS 或 Linux 运行 `python3 scripts/install.py --target <目录>`
- **THEN** 插件被复制到目标目录
- **AND** `__pycache__`、`.pyc` 和 `.git` 不会进入安装副本
- **AND** 不要求 `chmod` 或外部 `find`/`cp` 命令

#### Scenario: Windows 安装
- **WHEN** 用户在原生 Windows 运行 `py -3 scripts/install.py --target <目录>`
- **THEN** 安装器完成与 Unix 相同的校验和复制
- **AND** 不调用 Bash、`cp -a`、`find` 或 `chmod`

#### Scenario: 缺少运行时
- **WHEN** 当前平台无法找到可用的 Python 3.10+ 或 Windows `py -3`
- **THEN** 诊断和安装失败关闭
- **AND** 输出可操作的平台无关修复说明

### Requirement: 信任与路径完整性

Hook 信任自检 MUST 复现 Codex 当前平台选中的命令和插件内容哈希语义，避免 Windows 因路径分隔符、`commandWindows` 或符号链接处理差异而误判。

#### Scenario: Windows 声明哈希
- **WHEN** 当前平台为 Windows 且 Hook 声明包含 `commandWindows`
- **THEN** 声明哈希使用 `commandWindows` 计算
- **AND** 平台选择字段不重复参与哈希

#### Scenario: 插件内容哈希
- **WHEN** 插件位于 Windows 路径
- **THEN** 内容哈希使用原生相对路径字节，而不是 POSIX 正斜杠
- **AND** 非普通文件或不可识别链接按失败关闭处理

#### Scenario: 信任不一致
- **WHEN** 声明哈希或内容哈希与 Codex 信任记录不一致
- **THEN** `route.py`、`gate.py` 和 `approve.py` 停止推进
- **AND** 不得把不一致降级为警告

### Requirement: 跨平台命令守卫

人工审批等待期间，命令守卫 MUST 按当前平台解析只读命令和写入命令；无法可靠解析时 MUST 拒绝。

#### Scenario: Unix 只读命令
- **WHEN** 门禁等待期间执行 `git status`、`rg` 或 `sed` 读取文件
- **THEN** 守卫按现有 POSIX 规则允许

#### Scenario: Windows 只读命令
- **WHEN** 门禁等待期间执行 `Get-Content`、`Get-ChildItem`、`Select-String` 或 `Test-Path`
- **THEN** 守卫在不包含写入、删除或执行副作用时允许

#### Scenario: 无法解析的命令
- **WHEN** 命令包含平台无关解析器无法可靠展开的包装器、重定向或动态执行
- **THEN** 守卫拒绝并保持门禁

### Requirement: 三系统验证

持续集成 MUST 覆盖 macOS、Ubuntu 和 Windows。测试 MUST 验证 Hook 入口、安装器、信任哈希、路由/门禁和模拟流程在三个平台一致。

#### Scenario: CI 矩阵
- **WHEN** 提交或合并变更
- **THEN** `ubuntu-latest`、`macos-latest`、`windows-latest` 均运行单元测试和交付模拟
- **AND** 任一平台失败会阻止完成声明

#### Scenario: Hook 冒烟测试
- **WHEN** CI 在某一平台运行 Hook 冒烟测试
- **THEN** 测试从 `hooks/codex-hooks.json` 选择当前平台实际命令
- **AND** 向入口提供样例 JSON stdin，断言输出为合法 Hook JSON 或预期的失败关闭结果

#### Scenario: 文档一致性
- **WHEN** 用户按 README 或技能说明安装和使用
- **THEN** 文档明确同一套代码在 Windows 使用 `py -3`、macOS/Linux 使用 `python3`
- **AND** 不再宣称仅支持 macOS/Linux
