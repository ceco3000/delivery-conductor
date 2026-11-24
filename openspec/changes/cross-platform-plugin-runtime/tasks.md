# Tasks

## 1. 基线与测试锁定

- [x] 1.1 为现有 macOS/Linux Hook、安装、路由和信任行为补充回归测试
- [x] 1.2 增加三平台可运行的单元测试入口，避免测试自身依赖 Bash
- [x] 1.3 记录 Codex 官方 `commandWindows`、声明哈希和内容哈希夹具

## 2. Hook 声明与单入口

- [x] 2.1 将 `UserPromptSubmit`、`PreToolUse`、`SessionStart` 改为直接 Python 启动
- [x] 2.2 为三个 Hook 增加调用同一 Python 文件的 `commandWindows`
- [x] 2.3 移除 `sh -c`、`/dev/null`、`|| true` 和 `${PLUGIN_ROOT:-.}`
- [x] 2.4 修正 Hook Python 入口的 stdin、输出和失败处理
- [x] 2.5 用 `sys.executable` 替换 Hook 内部的 `python3` 子进程

## 3. 平台适配层

- [x] 3.1 集中实现 Python 启动器探测与可执行文件发现
- [x] 3.2 集中实现跨平台命令展示和分词
- [x] 3.3 统一 `CODEX_HOME`、插件根和状态路径解析
- [x] 3.4 为 Windows 增加 PowerShell/cmd 只读命令分类

## 4. 安装与诊断

- [x] 4.1 新增 `scripts/install.py`
- [x] 4.2 删除 `scripts/install_linux.sh` 及其 Bash 测试
- [x] 4.3 更新 `doctor.py` 的平台、Python 启动器、Codex 和信任检查
- [x] 4.4 验证安装副本排除缓存且不改动用户配置

## 5. 信任校验

- [x] 5.1 让声明哈希按平台选择 `commandWindows`
- [x] 5.2 对齐 `${PLUGIN_ROOT}` 和插件环境变量替换顺序
- [x] 5.3 修正内容哈希的原生路径字节和符号链接语义
- [x] 5.4 增加 Windows、macOS、Linux 信任哈希回归测试

## 6. 文档与清单

- [x] 6.1 更新 README 的安装、运行和 Windows 前置条件
- [x] 6.2 更新技能中的命令示例和 Windows 解释器说明
- [x] 6.3 更新插件清单描述，移除仅 macOS/Linux 的表述
- [x] 6.4 增加最低 Codex 版本与升级后重新信任说明

## 7. 三系统验证

- [x] 7.1 扩展 GitHub Actions 为 Ubuntu/macOS/Windows 矩阵
- [x] 7.2 增加 Hook 冒烟测试、安装器测试和平台适配测试
- [x] 7.3 在 macOS 本机运行格式、单元测试和交付模拟
- [ ] 7.4 在 Linux 与 Windows CI 运行同等检查
- [ ] 7.5 完成 Windows Codex 桌面端人工安装与 Hook 信任验收

> 验证边界：7.4 需要提交并推送后触发 GitHub Actions；7.5 需要真实 Windows 宿主。两项均未执行，不得标记完成。
