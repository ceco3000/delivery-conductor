#!/usr/bin/env bash
set -eu
export PYTHONDONTWRITEBYTECODE=1

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PLUGIN_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
TARGET="${HOME}/delivery-conductor"

usage() {
  printf '%s\n' "用法: bash scripts/install_linux.sh [--target <目录>]"
  printf '%s\n' "默认目标目录: ${HOME}/delivery-conductor"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --target)
      if [ "$#" -lt 2 ]; then
        printf '%s\n' "错误: --target 需要一个目录参数" >&2
        exit 2
      fi
      TARGET="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf '错误: 未知参数 %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "$TARGET" in
  "~"*) TARGET="${HOME}/${TARGET#\~/}" ;;
esac

if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' "错误: 未找到 python3。请先安装 Python 3.10+。" >&2
  exit 1
fi

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  printf '%s\n' "错误: 需要 Python 3.10+。当前版本见 python3 --version。" >&2
  exit 1
fi

if [ -e "$TARGET" ] && [ -n "$(find "$TARGET" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
  printf '目标目录已存在且非空: %s\n' "$TARGET" >&2
  printf '%s\n' "请先备份或删除该目录，或使用 --target 指定新目录。" >&2
  exit 1
fi

mkdir -p "$TARGET"
cp -a "$PLUGIN_ROOT/." "$TARGET/"
find "$TARGET" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$TARGET" -type f -name '*.pyc' -delete
chmod +x "$TARGET"/scripts/*.py "$TARGET"/scripts/install_linux.sh 2>/dev/null || true

printf '已复制编码总控插件到: %s\n' "$TARGET"
printf '\n环境诊断（仅供参考；安装并信任钩子后请用 --strict 复验）:\n'
if ! python3 "$TARGET/scripts/doctor.py"; then
  printf '\n诊断存在未通过项；安装后请按提示修复并重新运行。\n'
fi

if ! command -v codex >/dev/null 2>&1; then
  printf '\n警告: 未找到 codex 命令。请先安装 Codex，再执行下面的命令。\n'
fi

PARENT="$(dirname "$TARGET")"
BASE="$(basename "$TARGET")"
printf '\n下一步:\n'
printf '  cd %s\n' "$PARENT"
printf '  codex plugin marketplace add ./%s\n' "$BASE"
printf '  codex plugin add delivery-conductor@delivery-conductor\n'
printf '%s\n' "  重启 Codex，打开 Hooks > Review hooks，确认三个钩子后选择 Trust all"
printf '  python3 %s/scripts/host_trust.py\n' "$TARGET"
printf '  python3 %s/scripts/doctor.py --strict\n' "$TARGET"

