"""Путь к HAR: аргумент CLI или переменная TFS_HAR_PATH (без личных путей в репозитории)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def resolve_har_path() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1]).expanduser()
    env = (os.environ.get("TFS_HAR_PATH") or "").strip()
    if env:
        return Path(env).expanduser()
    print("Укажите HAR: TFS_HAR_PATH=... python script.py  или  python script.py path/to/file.har", file=sys.stderr)
    raise SystemExit(2)
