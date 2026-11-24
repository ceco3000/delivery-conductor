from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".venv")


def install(source: Path, target: Path) -> None:
    source = source.resolve()
    target = target.resolve()
    if target == source or source in target.parents:
        raise ValueError(f"目标目录不能位于源目录内: {target}")
    if target.exists() and not target.is_dir():
        raise FileExistsError(f"目标路径不是目录: {target}")
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"目标目录已存在且非空: {target}")
    if target.exists():
        target.rmdir()
    shutil.copytree(source, target, ignore=IGNORED, symlinks=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Install Delivery Conductor on macOS, Linux, or Windows.")
    parser.add_argument("--target", type=Path, default=Path.home() / "delivery-conductor")
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        print("安装失败: 需要 Python 3.10+。请安装新版 Python 后重试。", file=sys.stderr)
        return 1
    target = args.target.expanduser().resolve()
    try:
        install(PLUGIN_ROOT, target)
    except (OSError, ValueError) as error:
        print(f"安装失败: {error}", file=sys.stderr)
        return 1
    print(f"已复制编码总控插件到: {target}")
    doctor = target / "scripts" / "doctor.py"
    completed = subprocess.run([sys.executable, "-B", str(doctor)], cwd=str(target), check=False)
    if completed.returncode != 0:
        print(
            "安装失败: 安装副本的 doctor.py 环境检查未通过，请修复上方问题后重试。",
            file=sys.stderr,
        )
        return 1
    parent = target.parent
    print("下一步:")
    print(f"  cd {parent}")
    print(f"  codex plugin marketplace add ./{target.name}")
    print("  codex plugin add delivery-conductor@delivery-conductor")
    print("  重启 Codex，打开 Hooks > Review hooks，确认三个钩子后选择 Trust all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
