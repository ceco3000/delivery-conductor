from __future__ import annotations

import ntpath
import os
import re
import shlex
import subprocess
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
    return ["python3"]


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
    if any(
        token in command
        for token in (
            ";",
            "&",
            "|",
            ">",
            "<",
            "`",
            "$(",
            "\n",
            "\r",
            "(",
            ")",
        )
    ):
        return True
    return bool(CONTROL_PATTERN.search(command))


def relative_name_bytes(path: Path, *, platform: str | None = None) -> bytes:
    value = str(path).replace("/", "\\") if is_windows(platform) else path.as_posix()
    return os.fsencode(value)
