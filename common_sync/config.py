from __future__ import annotations

import os
import platform
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from logging_config import get_logger


logger = get_logger(__name__)


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
DEFAULT_LOG_LEVEL = "INFO"
ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
CLOUDSTATION_ROOT_MARKER = "CLOUDSTATION_ROOT"
PLATFORM_CLOUDSTATION_ENV = {
    "Windows": "CLOUDSTATION_ROOT_WINDOWS",
    "Darwin": "CLOUDSTATION_ROOT_MACOS",
    "Linux": "CLOUDSTATION_ROOT_LINUX",
}


@dataclass(frozen=True)
class AppConfig:
    scan_root: Path
    target_files: tuple[str, ...]
    exclude_dirs: tuple[str, ...]
    log_level: str


def load_config(config_path: Path) -> AppConfig:
    base_dir = config_path.parent.resolve()
    _load_common_env(base_dir / "common.env")
    raw: dict[str, Any] = {}
    if config_path.exists() and config_path.read_text(encoding="utf-8").strip():
        logger.info("Loading config: %s", config_path)
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            raw = loaded
        else:
            logger.warning("Config file does not contain a mapping; using defaults: %s", config_path)
    else:
        logger.info("Config file is empty or missing; using defaults: %s", config_path)

    scan_root = _resolve_config_path(str(raw.get("scan_root", "..")), base_dir)

    target_files = raw.get("target_files", DEFAULT_TARGET_FILES)
    exclude_dirs = raw.get("exclude_dirs", DEFAULT_EXCLUDE_DIRS)
    config = AppConfig(
        scan_root=scan_root,
        target_files=tuple(str(name) for name in target_files if str(name).strip()),
        exclude_dirs=tuple(str(name) for name in exclude_dirs if str(name).strip()),
        log_level=str(raw.get("log_level", DEFAULT_LOG_LEVEL)).upper(),
    )
    logger.info(
        "Config loaded: scan_root=%s target_files=%d exclude_dirs=%d log_level=%s",
        config.scan_root,
        len(config.target_files),
        len(config.exclude_dirs),
        config.log_level,
    )
    return config


def _load_common_env(env_path: Path) -> None:
    if not env_path.exists():
        logger.debug("Local environment file not found: %s", env_path)
        return
    logger.info("Loading local environment variables: %s", env_path)
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _resolve_config_path(raw_path: str, base_dir: Path) -> Path:
    expanded = _resolve_env_markers(raw_path, base_dir)
    path = Path(expanded).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _resolve_env_markers(value: str, base_dir: Path) -> str:
    cloudstation_root = _select_cloudstation_root(base_dir)

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        default = match.group(2)
        if name == CLOUDSTATION_ROOT_MARKER and cloudstation_root:
            return str(cloudstation_root)
        env_value = os.environ.get(name)
        if env_value is not None:
            return env_value
        if default is not None:
            return default
        raise ValueError(f"Missing environment variable for config path: {name}")

    return ENV_PATTERN.sub(replace, value)


def _select_cloudstation_root(base_dir: Path) -> Path | None:
    explicit = os.environ.get(CLOUDSTATION_ROOT_MARKER)
    if explicit:
        return Path(explicit).expanduser()

    platform_var = PLATFORM_CLOUDSTATION_ENV.get(platform.system())
    if platform_var:
        platform_value = os.environ.get(platform_var)
        if platform_value:
            return Path(platform_value).expanduser()

    inferred = _infer_cloudstation_root(base_dir)
    if inferred:
        logger.warning("CLOUDSTATION_ROOT is not configured; inferred from project path: %s", inferred)
    return inferred


def _infer_cloudstation_root(base_dir: Path) -> Path | None:
    for parent in (base_dir, *base_dir.parents):
        if parent.name.lower() == "cloudstation":
            return parent
    return None
