from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


DEFAULT_TARGET_FILES = [
    ".flake8",
    "LOCAL_AI_RUNTIME_SETUP.md",
    "COMMON_PROJECT_SKILLS.md",
]

DEFAULT_EXCLUDE_DIRS = [
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
]


@dataclass(frozen=True)
class AppConfig:
    scan_root: Path
    target_files: tuple[str, ...]
    exclude_dirs: tuple[str, ...]


def load_config(config_path: Path) -> AppConfig:
    base_dir = config_path.parent.resolve()
    raw: dict[str, Any] = {}
    if config_path.exists() and config_path.read_text(encoding="utf-8").strip():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            raw = loaded

    scan_root = Path(raw.get("scan_root", ".."))
    if not scan_root.is_absolute():
        scan_root = (base_dir / scan_root).resolve()

    target_files = raw.get("target_files", DEFAULT_TARGET_FILES)
    exclude_dirs = raw.get("exclude_dirs", DEFAULT_EXCLUDE_DIRS)
    return AppConfig(
        scan_root=scan_root,
        target_files=tuple(str(name) for name in target_files if str(name).strip()),
        exclude_dirs=tuple(str(name) for name in exclude_dirs if str(name).strip()),
    )
