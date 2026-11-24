# 跨平台运行时安全审查记录

- 变更：`cross-platform-plugin-runtime`
- 审查者：ECC 安全审查代理（只读复核）
- 日期：2026-11-24
- 最终结论：**ALLOW**（第七轮）
- 审查方式：只读代码审查、攻击串静态推演、本机只读探针；审查代理未修改仓库

## 阻断项与修复

| 轮次 | 严重度 | 问题 | 修复位置 | 回归测试 |
|---|---|---|---|---|
| 第五轮 | CRITICAL | `record.py --data` 可覆盖 `kind` 伪造审批证据 | `scripts/record.py` 拒绝 `kind`、`id`、`timestamp` 保留字段 | `test_record_rejects_self_approval` |
| 第五轮 | HIGH | `cp`/`mv` 目标目录内既有 symlink 可把最终 destination 指向项目外 | `_copy_destinations_allowed` 校验 `target/source_basename` 的 canonical 路径 | `test_cp_and_mv_directory_destinations_follow_existing_symlinks` |
| 第五轮 | HIGH | Git 安全令牌出现在 `--` 之后时被当作 pathspec，保护实际未启用 | 只统计第一个 `--` 之前的 option 区段 | `test_git_side_effect_options_are_rejected` |
| 第五轮 | HIGH | `file -bC`、`file -bCm`、`--comp` 等短选项聚类可进入编译模式 | 改为短选项字符白名单与长选项精确白名单 | `test_file_compile_options_are_rejected` |
| 第五轮 | HIGH | 任意命名空间后缀 `.patch` 的未知工具被当作 patch 工具 | 收紧为精确工具名白名单 | `test_apply_patch_is_path_scoped_before_plan_approval` |
| 第六轮 | HIGH | Git 取值选项可吞掉紧随其后的 `--no-ext-diff`/`--no-textconv`，例如 `--word-diff-regex --no-ext-diff` | `_git_option_flags` 只接受明确无参 flag、自包含 `=` 选项或自包含短选项；未知取值选项拒绝 | `test_git_side_effect_options_are_rejected` |
| 第六轮 | HIGH | 未知写入工具可用安全 `file_path` 做诱饵，实际通过 `input` 写实现路径 | 精确工具名 + 显式路径字段白名单；未知写入工具 fail closed；move/rename 要求 source 与 destination 两类路径 | `test_unknown_write_tools_and_decoy_paths_fail_closed` |
| 第六轮 | 防御性加固 | `cp -R` 合并进既有目标目录时，嵌套目标 symlink 未被逐个检查 | 递归 `cp` 禁止合并进已存在目录；新目标路径仍允许 | `test_cp_and_mv_directory_destinations_follow_existing_symlinks` |

## 本机验证证据

以下命令均在仓库根目录执行并通过：

```bash
python3 -m unittest discover -s tests -v
python3 scripts/simulate.py
python3 scripts/doctor.py --json
python3 -m compileall -q scripts tests
openspec validate cross-platform-plugin-runtime --strict
git diff --check
```

关键结果：

- 单元测试：138 tests OK。
- 交付模拟：`Simulation passed`。
- 环境诊断：`ok: true`，退出码 0；源码目录模式下的 TTY 与 hook trust 为预期警告。
- OpenSpec 严格校验：`Change 'cross-platform-plugin-runtime' is valid`。
- 编译与空白检查：无错误。

审查代理额外只读探针确认：

- 攻击串 `git --no-pager diff --word-diff-regex --no-ext-diff --ignore-matching-lines --no-textconv -- README.md` 被拒绝。
- `git --no-pager diff --no-ext-diff --no-textconv --stat` 与 `git --no-pager log --no-ext-diff --no-textconv --no-show-signature --oneline -5` 仍允许。
- 未知工具 `mcp__evil__write` 携带安全 `file_path` 和恶意 patch `input` 时被拒绝；已知 `write_file` 的安全 artifact 路径仍允许。

## 非阻塞残余风险

- `_git_option_flags` 对所有 `--long=value` 仍主要依赖禁止前缀过滤，后续可继续收紧为允许选项的精确白名单。
- 已知工具如 `multi_edit` 只提取顶层路径字段；若宿主 schema 使用嵌套 `edits` 路径，需要补充 schema 级校验与测试。
- `record.py` 已阻止审批字段伪造，但 `review`、`command` 等证据仍由代理自报，证据真实性依赖代理自律与后续人工审计。
- PATH 中项目外同名可执行文件、PowerShell profile 别名或函数仍依赖宿主环境信任边界。
- `cp`/`mv` 的检查与命令执行之间存在 TOCTOU 窗口；当前守卫是尽力而为的静态路径防护，不是内核级沙箱。
- Windows junction 与重解析点的覆盖仍不完整，需要真实 Windows 环境验证。

## 尚未完成的外部验证

- Ubuntu/macOS/Windows GitHub Actions 矩阵尚未运行：当前工作区未提交、未推送。
- Windows Codex 桌面端安装与 Hooks 信任人工验收尚未执行。
- 因此本记录不声称三平台 CI 已通过，也不声称 Windows 宿主验收已完成。
