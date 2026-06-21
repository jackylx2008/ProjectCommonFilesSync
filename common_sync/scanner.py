from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from shutil import copy2
from time import perf_counter
from typing import Callable

from logging_config import get_logger

from .config import AppConfig


logger = get_logger(__name__)

MAX_SCAN_WORKERS = 8
STREAMING_THRESHOLD = 16 * 1024 * 1024
READ_CHUNK_SIZE = 4 * 1024 * 1024
BINARY_SUFFIXES = {".dll"}


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


class ScanCancelled(Exception):
    """Raised when a caller requests cancellation of an active scan."""


def scan_projects(
    config: AppConfig,
    current_project_dir: Path,
    progress_callback: Callable[[int, int], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> ScanResult:
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

    discovered_paths = list(
        _iter_target_files(
            config.scan_root,
            target_names,
            set(config.exclude_dirs),
            progress_callback,
            cancel_requested,
        )
    )
    file_paths = [
        path
        for path in discovered_paths
        if not _is_relative_to(path.parent.resolve(), current_project_dir)
    ]
    cpu_based_workers = max(1, (os.cpu_count() or 4) // 4)
    worker_count = min(MAX_SCAN_WORKERS, cpu_based_workers, max(1, len(file_paths)))
    logger.info(
        "Reading matched files: files=%d workers=%d",
        len(file_paths),
        worker_count,
    )
    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="project-scan",
    ) as executor:
        versions = executor.map(
            lambda path: _read_version(path, cancel_requested),
            file_paths,
        )
        for version in versions:
            file_path = version.path
            if cancel_requested and cancel_requested():
                raise ScanCancelled()
            project_dir = version.project_dir
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


def _iter_target_files(
    scan_root: Path,
    target_names: set[str],
    exclude_dirs: set[str],
    progress_callback: Callable[[int, int], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
):
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
        stack = [os.fspath(resolved_scan_root)]
        while stack:
            if cancel_requested and cancel_requested():
                raise ScanCancelled()
            directory = stack.pop()
            scanned_dirs += 1
            if progress_callback and scanned_dirs % 100 == 0:
                progress_callback(scanned_dirs, matched_files)
            try:
                entries = os.scandir(directory)
            except OSError as exc:
                unreadable_dirs += 1
                logger.warning("Skipping unreadable directory: %s (%s)", directory, exc)
                continue
            with entries:
                for entry in entries:
                    scanned_entries += 1
                    name = entry.name
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if name in exclude_dirs:
                                skipped_dirs += 1
                                logger.debug("Skipping excluded directory: %s", entry.path)
                                continue
                            stack.append(entry.path)
                        elif name in target_names and entry.is_file(
                            follow_symlinks=False
                        ):
                            matched_files += 1
                            yield Path(entry.path)
                    except OSError as exc:
                        logger.warning("Skipping unreadable entry: %s (%s)", entry.path, exc)
    finally:
        if progress_callback:
            progress_callback(scanned_dirs, matched_files)
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


def _read_version(
    path: Path,
    cancel_requested: Callable[[], bool] | None = None,
) -> FileVersion:
    stat = path.stat()
    is_binary = path.suffix.lower() in BINARY_SUFFIXES
    if stat.st_size <= STREAMING_THRESHOLD:
        data = path.read_bytes()
        digest = sha256(data).hexdigest()
        if is_binary:
            line_count = 0
        else:
            try:
                line_count = len(data.decode("utf-8-sig").splitlines())
            except UnicodeDecodeError:
                line_count = len(data.splitlines())
    else:
        digest, line_count = _stream_digest_and_line_count(
            path,
            cancel_requested,
            count_lines=not is_binary,
        )
    return FileVersion(
        project_dir=path.parent.resolve(),
        file_name=path.name,
        path=path.resolve(),
        digest=digest,
        size=stat.st_size,
        line_count=line_count,
        modified_ns=stat.st_mtime_ns,
    )


def _stream_digest_and_line_count(
    path: Path,
    cancel_requested: Callable[[], bool] | None,
    count_lines: bool,
) -> tuple[str, int]:
    digest = sha256()
    line_breaks = 0
    previous_was_cr = False
    last_byte = -1
    with path.open("rb") as stream:
        while chunk := stream.read(READ_CHUNK_SIZE):
            if cancel_requested and cancel_requested():
                raise ScanCancelled()
            digest.update(chunk)
            if count_lines:
                line_breaks += sum(
                    chunk.count(bytes((separator,)))
                    for separator in (10, 11, 12, 13, 28, 29, 30, 133)
                )
                line_breaks -= chunk.count(b"\r\n")
                if previous_was_cr and chunk[0] == 10:
                    line_breaks -= 1
                previous_was_cr = chunk[-1] == 13
                last_byte = chunk[-1]
    if not count_lines:
        return digest.hexdigest(), 0
    line_count = line_breaks
    if last_byte != -1:
        if last_byte not in (10, 11, 12, 13, 28, 29, 30, 133):
            line_count += 1
    return digest.hexdigest(), line_count


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
