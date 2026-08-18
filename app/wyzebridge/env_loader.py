"""Safely translate dotenv files into shell-quoted export statements."""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path
from typing import Sequence

from dotenv import dotenv_values

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def shell_exports(path: Path) -> str:
    """Return validated dotenv assignments as shell-quoted export commands.

    The input is treated strictly as dotenv data. Shell metacharacters in
    values are never evaluated while parsing, and non-assignment lines cause
    the whole file to be rejected before any output is emitted.
    """
    text = path.read_text(encoding="utf-8")
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        candidate = stripped[7:].lstrip() if stripped.startswith("export ") else stripped
        key, separator, _ = candidate.partition("=")
        if not separator or _ENV_NAME.fullmatch(key.strip()) is None:
            raise ValueError("invalid dotenv assignment")

    values = dotenv_values(path)
    exports: list[str] = []
    for key, value in values.items():
        if _ENV_NAME.fullmatch(key) is None:
            raise ValueError("invalid dotenv variable name")
        exports.append(f"export {key}={shlex.quote(value or '')}")
    return "\n".join(exports)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        return 2
    try:
        output = shell_exports(Path(args[0]))
    except (OSError, UnicodeError, ValueError):
        return 2
    if output:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
