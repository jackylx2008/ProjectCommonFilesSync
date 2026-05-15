from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from shutil import copy2
from time import perf_counter

from logging_config import get_logger

from .config import AppConfig


logger = get_logger(__name__)


@dataclass(frozen=True)
class FileVersion:
    project_dir: Path
    file_name: str
    path: Path
    digest: str
    size: int
    line_count: int
    modified_ns: int


@dataclass(frozen=True)
class ProjectFileState:
    project_dir: Path
    file_name: str
    version: FileVersion | None

    @property
    def exists(self) -> bool:
        return self.version is not None


@dataclass(frozen=True)
class VersionGroup:
    file_name: str
    digest: str
    versions: tuple[FileVersion, ...]

    @property
    def representative(self) -> FileVersion:
        return self.versions[0]


@dataclass(frozen=True)
class ScanResult:
    scan_root: Path
    target_files: tuple[str, ...]
    project_dirs: tuple[Path, ...]
    versions_by_file: dict[str, tuple[FileVersion, ...]]
    groups_by_file: dict[str, tuple[VersionGroup, ...]]
    states_by_file: dict[str, tuple[ProjectFileState, ...]]


def scan_projects(config: AppConfig, current_project_dir: Path) -> ScanResult:
    logger.info(
        "Scanning projects: scan_root=%s target_files=%d current_project_dir=%s",
        config.scan_root,
        len(config.target_files),
        current_project_dir,
    )
    target_names = set(config.target_files)
    versions_by_file: dict[str, list[FileVersion]] = {name: [] for name in config.target_files}
    project_dirs: set[Path] = set()
    current_project_dir = current_project_dir.resolve()

    for file_path in _iter_target_files(config.scan_root, target_names, set(config.exclude_dirs)):
        project_dir = file_path.parent.resolve()
        if _is_relative_to(project_dir, current_project_dir):
            logger.debug("Skipping current project file: %s", file_path)
            continue
        version = _read_version(file_path)
        versions_by_file[file_path.name].append(version)
        project_dirs.add(project_dir)

    sorted_projects = tuple(sorted(project_dirs, key=lambda path: str(path).lower()))
    frozen_versions = {
        name: tuple(sorted(items, key=lambda item: str(item.project_dir).lower()))
        for name, items in versions_by_file.items()
    }
    groups_by_file = {
        name: _group_versions(name, versions)
        for name, versions in frozen_versions.items()
    }
    states_by_file = {
        name: tuple(_state_for_project(project, name, frozen_versions[name]) for project in sorted_projects)
        for name in config.target_files
    }

    result = ScanResult(
        scan_root=config.scan_root,
        target_files=config.target_files,
        project_dirs=sorted_projects,
        versions_by_file=frozen_versions,
        groups_by_file=groups_by_file,
        states_by_file=states_by_file,
    )
    logger.info(
        "Project scan completed: projects=%d versions=%d",
        len(result.project_dirs),
        sum(len(items) for items in result.versions_by_file.values()),
    )
    return result


def copy_version_to_projects(source: FileVersion, target_projects: list[Path]) -> list[Path]:
    copied: list[Path] = []
    logger.info("Copying file version: source=%s targets=%d", source.path, len(target_projects))
    for project_dir in target_projects:
        target_path = project_dir / source.file_name
        if target_path.resolve() == source.path.resolve():
            logger.debug("Skipping copy to source path: %s", target_path)
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        copy2(source.path, target_path)
        copied.append(target_path)
        logger.info("File modified by copy: source=%s target=%s", source.path, target_path)
    logger.info("File version copy completed: copied=%d", len(copied))
    return copied


def read_text_for_display(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _iter_target_files(scan_root: Path, target_names: set[str], exclude_dirs: set[str]):
    start_time = datetime.now()
    started_at = perf_counter()
    scanned_dirs = 0
    scanned_entries = 0
    matched_files = 0
    skipped_dirs = 0
    unreadable_dirs = 0
    resolved_scan_root = scan_root.resolve()
    logger.info(
        "Directory traversal started: root=%s target_files=%d exclude_dirs=%d start_time=%s",
        resolved_scan_root,
        len(target_names),
        len(exclude_dirs),
        start_time.isoformat(timespec="seconds"),
    )
    try:
        stack = [resolved_scan_root]
        while stack:
            directory = stack.pop()
            scanned_dirs += 1
            try:
                entries = list(directory.iterdir())
            except OSError as exc:
                unreadable_dirs += 1
                logger.warning("Skipping unreadable directory: %s (%s)", directory, exc)
                continue
            scanned_entries += len(entries)
            for entry in entries:
                name = entry.name
                if entry.is_dir():
                    if name in exclude_dirs:
                        skipped_dirs += 1
                        logger.debug("Skipping excluded directory: %s", entry)
                        continue
                    stack.append(entry)
                elif entry.is_file() and name in target_names:
                    matched_files += 1
                    yield entry
    finally:
        end_time = datetime.now()
        logger.info(
            "Directory traversal finished: root=%s start_time=%s end_time=%s duration_seconds=%.3f "
            "directories=%d entries=%d matched_files=%d skipped_dirs=%d unreadable_dirs=%d",
            resolved_scan_root,
            start_time.isoformat(timespec="seconds"),
            end_time.isoformat(timespec="seconds"),
            perf_counter() - started_at,
            scanned_dirs,
            scanned_entries,
            matched_files,
            skipped_dirs,
            unreadable_dirs,
        )


def _read_version(path: Path) -> FileVersion:
    data = path.read_bytes()
    try:
        line_count = len(data.decode("utf-8-sig").splitlines())
    except UnicodeDecodeError:
        line_count = len(data.splitlines())
    stat = path.stat()
    return FileVersion(
        project_dir=path.parent.resolve(),
        file_name=path.name,
        path=path.resolve(),
        digest=sha256(data).hexdigest(),
        size=stat.st_size,
        line_count=line_count,
        modified_ns=stat.st_mtime_ns,
    )


def _group_versions(file_name: str, versions: tuple[FileVersion, ...]) -> tuple[VersionGroup, ...]:
    buckets: dict[str, list[FileVersion]] = {}
    for version in versions:
        buckets.setdefault(version.digest, []).append(version)
    groups = [
        VersionGroup(file_name=file_name, digest=digest, versions=tuple(items))
        for digest, items in buckets.items()
    ]
    return tuple(sorted(groups, key=lambda group: (-len(group.versions), group.digest)))


def _state_for_project(project: Path, file_name: str, versions: tuple[FileVersion, ...]) -> ProjectFileState:
    version = next((item for item in versions if item.project_dir == project), None)
    return ProjectFileState(project_dir=project, file_name=file_name, version=version)


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False
