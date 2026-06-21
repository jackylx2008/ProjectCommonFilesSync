from __future__ import annotations

import difflib
import os
import site
import sys
from threading import Event
from datetime import datetime
from pathlib import Path
from typing import Any


def _prepare_pyside6_dll_path() -> None:
    candidates = [Path(path) / "PySide6" for path in site.getsitepackages()]
    user_site = site.getusersitepackages()
    if user_site:
        candidates.append(Path(user_site) / "PySide6")
    for candidate in candidates:
        if (candidate / "Qt6Core.dll").exists():
            os.add_dll_directory(str(candidate))
            os.environ["PATH"] = f"{candidate}{os.pathsep}{os.environ.get('PATH', '')}"
            return


_prepare_pyside6_dll_path()

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QCloseEvent,
    QFont,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
)
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from logging_config import get_logger, setup_logger
from common_sync.config import AppConfig, load_config
from common_sync.scanner import (
    FileVersion,
    ProjectFileState,
    ScanCancelled,
    ScanResult,
    VersionGroup,
    copy_version_to_projects,
    read_text_for_display,
    scan_projects,
)

CONFIG_PATH = Path(__file__).with_name("config.yaml")
CURRENT_PROJECT_DIR = Path(__file__).parent.resolve()
logger = get_logger(__name__)


class ScanWorker(QObject):
    progress = Signal(int, int)
    succeeded = Signal(object, object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._cancel_requested = Event()

    def cancel(self) -> None:
        self._cancel_requested.set()

    @Slot()
    def run(self) -> None:
        try:
            config = load_config(CONFIG_PATH)
            result = scan_projects(
                config,
                CURRENT_PROJECT_DIR,
                progress_callback=self.progress.emit,
                cancel_requested=self._cancel_requested.is_set,
            )
        except ScanCancelled:
            logger.info("Background scan cancelled")
            self.cancelled.emit()
            return
        except Exception as exc:
            logger.exception("Background scan failed")
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(config, result)


class MainWindow(QMainWindow):
    def __init__(self, initial_config: AppConfig | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Project Common Files Sync")
        self.resize(1500, 900)

        self.config = initial_config or load_config(CONFIG_PATH)
        self.result: ScanResult | None = None
        self.current_file_name: str | None = None
        self.selected_group: VersionGroup | None = None
        self.current_diff_left: ProjectFileState | None = None
        self.current_diff_right: ProjectFileState | None = None
        self._syncing_diff_scroll = False
        self._scan_thread: QThread | None = None
        self._scan_worker: ScanWorker | None = None
        self._scan_reason = ""
        self._file_to_select_after_scan: str | None = None
        self._close_after_scan = False

        self.file_table = self._make_table(["文件名", "项目数", "版本组", "缺失"])
        self.group_table = self._make_table(
            ["版本", "项目数", "大小", "行数", "修改时间", "代表项目"]
        )
        self.group_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.project_table = self._make_table(
            ["覆盖", "项目", "状态", "版本", "大小", "行数", "修改时间"]
        )
        self.project_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.project_table.itemChanged.connect(self._on_project_item_changed)

        self.left_diff = self._make_text_view()
        self.right_diff = self._make_text_view()
        self._connect_diff_scroll_sync()
        self.left_diff_info = self._make_diff_info_label()
        self.right_diff_info = self._make_diff_info_label()
        self.copy_right_to_left_button = QPushButton("←")
        self.copy_right_to_left_button.setToolTip("用右侧文件覆盖左侧文件")
        self.copy_right_to_left_button.clicked.connect(self.copy_right_diff_to_left)
        self.copy_left_to_right_button = QPushButton("→")
        self.copy_left_to_right_button.setToolTip("用左侧文件覆盖右侧文件")
        self.copy_left_to_right_button.clicked.connect(self.copy_left_diff_to_right)
        self._set_diff_copy_buttons_enabled(False)
        self.status_label = QLabel()
        self.status_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

        self.refresh_button = QPushButton("重新扫描")
        self.refresh_button.clicked.connect(lambda: self.refresh(reason="manual"))
        copy_button = QPushButton("用选中版本覆盖勾选项目")
        copy_button.clicked.connect(self.copy_selected_version)
        copy_group_button = QPushButton("覆盖其他版本")
        copy_group_button.clicked.connect(self.copy_selected_group_to_other_versions)
        diff_button = QPushButton("对比选中项目")
        diff_button.clicked.connect(self.show_selected_diff)

        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel(f"扫描目录: {self.config.scan_root}"))
        top_bar.addStretch()
        top_bar.addWidget(self.refresh_button)

        left_panel = QVBoxLayout()
        left_panel.addWidget(QLabel("目标文件"))
        left_panel.addWidget(self.file_table)
        group_header = QHBoxLayout()
        group_header.addWidget(QLabel("内容相同的版本组"))
        group_header.addStretch()
        group_header.addWidget(copy_group_button)
        left_panel.addLayout(group_header)
        left_panel.addWidget(self.group_table)

        right_panel = QVBoxLayout()
        project_header = QHBoxLayout()
        project_header.addWidget(QLabel("项目状态"))
        project_header.addStretch()
        project_header.addWidget(diff_button)
        project_header.addWidget(copy_button)
        right_panel.addLayout(project_header)
        right_panel.addWidget(self.project_table)

        diff_splitter = QSplitter(Qt.Orientation.Horizontal)
        left_diff_panel = self._make_diff_panel(self.left_diff, self.left_diff_info)
        right_diff_panel = self._make_diff_panel(self.right_diff, self.right_diff_info)
        diff_splitter.addWidget(left_diff_panel)
        diff_actions = QVBoxLayout()
        diff_actions.addStretch()
        diff_actions.addWidget(self.copy_right_to_left_button)
        diff_actions.addWidget(self.copy_left_to_right_button)
        diff_actions.addStretch()
        diff_actions_widget = QWidget()
        diff_actions_widget.setLayout(diff_actions)
        diff_actions_widget.setMinimumWidth(44)
        diff_actions_widget.setMaximumWidth(56)
        diff_splitter.addWidget(diff_actions_widget)
        diff_splitter.addWidget(right_diff_panel)
        diff_splitter.setSizes([460, 48, 460])
        right_panel.addWidget(QLabel("左右并排差异"))
        right_panel.addWidget(diff_splitter)

        content_splitter = QSplitter(Qt.Orientation.Horizontal)
        left_widget = QWidget()
        left_widget.setLayout(left_panel)
        right_widget = QWidget()
        right_widget.setLayout(right_panel)
        content_splitter.addWidget(left_widget)
        content_splitter.addWidget(right_widget)
        content_splitter.setSizes([520, 980])

        root = QVBoxLayout()
        root.addLayout(top_bar)
        root.addWidget(content_splitter)
        central = QWidget()
        central.setLayout(root)
        self.setCentralWidget(central)
        self.statusBar().setMaximumHeight(28)
        self.statusBar().addWidget(self.status_label, 1)

        self.file_table.itemSelectionChanged.connect(self._on_file_selected)
        self.group_table.itemSelectionChanged.connect(self._on_group_selected)
        self.project_table.itemSelectionChanged.connect(
            self._on_project_selection_changed
        )

        self.refresh(reason="startup")

    def refresh(
        self, reason: str = "manual", select_file_name: str | None = None
    ) -> None:
        if self._scan_thread is not None:
            self.status_label.setText("扫描正在进行，请稍候……")
            return

        logger.info("Refresh started: reason=%s config=%s", reason, CONFIG_PATH)
        self._scan_reason = reason
        self._file_to_select_after_scan = select_file_name
        self.status_label.setText("正在扫描项目目录……")
        self.refresh_button.setEnabled(False)

        thread = QThread(self)
        worker = ScanWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_scan_progress)
        worker.succeeded.connect(self._on_scan_succeeded)
        worker.failed.connect(self._on_scan_failed)
        worker.cancelled.connect(thread.quit)
        worker.succeeded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(worker.deleteLater)
        worker.succeeded.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(self._on_scan_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._scan_thread = thread
        self._scan_worker = worker
        thread.start()

    @Slot(int, int)
    def _on_scan_progress(self, scanned_dirs: int, matched_files: int) -> None:
        self.status_label.setText(
            f"正在扫描……已检查 {scanned_dirs} 个目录，发现 {matched_files} 个目标文件。"
        )

    @Slot(object, object)
    def _on_scan_succeeded(self, config: AppConfig, result: ScanResult) -> None:
        self.config = config
        self.result = result
        self.current_file_name = None
        self.selected_group = None
        self.current_diff_left = None
        self.current_diff_right = None
        self._set_diff_copy_buttons_enabled(False)
        self.group_table.setRowCount(0)
        self.project_table.setRowCount(0)
        self._set_diff("", "", [], [])
        self._fill_file_table()
        if self._file_to_select_after_scan:
            self._select_file_name(self._file_to_select_after_scan)
        self.status_label.setText(
            f"已扫描 {len(self.result.project_dirs)} 个项目，目标文件 {len(self.result.target_files)} 个。"
        )
        logger.info(
            "Refresh finished: reason=%s projects=%d target_files=%d",
            self._scan_reason,
            len(self.result.project_dirs),
            len(self.result.target_files),
        )

    @Slot(str)
    def _on_scan_failed(self, message: str) -> None:
        logger.error("Refresh failed: reason=%s error=%s", self._scan_reason, message)
        self.status_label.setText(f"扫描失败：{message}")
        if not self._close_after_scan:
            QMessageBox.critical(self, "扫描失败", message)

    @Slot()
    def _on_scan_thread_finished(self) -> None:
        self._scan_thread = None
        self._scan_worker = None
        self._scan_reason = ""
        self._file_to_select_after_scan = None
        self.refresh_button.setEnabled(True)
        if self._close_after_scan:
            self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._scan_thread is not None and self._scan_thread.isRunning():
            self._close_after_scan = True
            self.status_label.setText("正在取消扫描并退出……")
            if self._scan_worker is not None:
                self._scan_worker.cancel()
            event.ignore()
            return
        super().closeEvent(event)

    def copy_selected_version(self) -> None:
        if not self.result or not self.selected_group:
            QMessageBox.information(
                self, "未选择版本", "请先在版本组列表中选择一个源版本。"
            )
            return
        source = self.selected_group.representative
        targets = self._checked_projects()
        targets = [path for path in targets if path != source.project_dir]
        if not targets:
            QMessageBox.information(
                self, "未选择目标", "请勾选至少一个不同于源项目的目标项目。"
            )
            return

        message = (
            f"将使用以下源文件覆盖 {len(targets)} 个项目中的 {source.file_name}：\n\n"
            f"{source.path}\n\n此操作会直接写入目标文件。"
        )
        if (
            QMessageBox.question(self, "确认覆盖", message)
            != QMessageBox.StandardButton.Yes
        ):
            logger.info(
                "Copy selected version canceled by user: source=%s targets=%d",
                source.path,
                len(targets),
            )
            return

        try:
            logger.info(
                "Copying selected version: source=%s targets=%d",
                source.path,
                len(targets),
            )
            copied = copy_version_to_projects(source, targets)
        except Exception as exc:
            logger.exception(
                "Copy selected version failed: source=%s targets=%d",
                source.path,
                len(targets),
            )
            QMessageBox.critical(self, "覆盖失败", str(exc))
            return

        logger.info("Copy selected version completed: copied=%d", len(copied))
        QMessageBox.information(self, "覆盖完成", f"已写入 {len(copied)} 个文件。")
        file_name = self.current_file_name
        logger.info(
            "Refreshing after file modification: operation=copy_selected_version copied=%d",
            len(copied),
        )
        self.refresh(
            reason="after_copy_selected_version", select_file_name=file_name
        )

    def copy_selected_group_to_other_versions(self) -> None:
        if not self.result or not self.current_file_name:
            QMessageBox.information(self, "未选择文件", "请先选择一个目标文件。")
            return

        groups = self._selected_groups()
        source_group = groups[0] if groups else self.selected_group
        if not source_group:
            QMessageBox.information(
                self, "未选择版本", "请先在版本组列表中选择一个源版本。"
            )
            return

        source = source_group.representative
        target_versions = [
            version
            for group in self.result.groups_by_file[self.current_file_name]
            if group.digest != source_group.digest
            for version in group.versions
        ]
        target_projects = [
            version.project_dir
            for version in target_versions
            if version.project_dir != source.project_dir
        ]
        if not target_projects:
            QMessageBox.information(self, "无需覆盖", "没有其它版本需要覆盖。")
            return

        preview = "\n".join(f"- {path}" for path in target_projects[:8])
        extra = (
            f"\n- ... 以及另外 {len(target_projects) - 8} 个项目"
            if len(target_projects) > 8
            else ""
        )
        message = (
            f"将使用以下源文件覆盖其它 {len(target_projects)} 个版本项目中的 {source.file_name}：\n\n"
            f"源文件：{source.path}\n\n"
            f"目标项目：\n{preview}{extra}\n\n"
            "此操作会直接写入目标文件。"
        )
        if (
            QMessageBox.question(self, "确认覆盖其他版本", message)
            != QMessageBox.StandardButton.Yes
        ):
            logger.info(
                "Copy group to other versions canceled by user: source=%s targets=%d",
                source.path,
                len(target_projects),
            )
            return

        try:
            logger.info(
                "Copying group to other versions: source=%s targets=%d",
                source.path,
                len(target_projects),
            )
            copied = copy_version_to_projects(source, target_projects)
        except Exception as exc:
            logger.exception(
                "Copy group to other versions failed: source=%s targets=%d",
                source.path,
                len(target_projects),
            )
            QMessageBox.critical(self, "覆盖失败", str(exc))
            return

        file_name = self.current_file_name
        logger.info("Copy group to other versions completed: copied=%d", len(copied))
        QMessageBox.information(self, "覆盖完成", f"已写入 {len(copied)} 个文件。")
        logger.info(
            "Refreshing after file modification: operation=copy_group_to_other_versions copied=%d",
            len(copied),
        )
        self.refresh(
            reason="after_copy_group_to_other_versions", select_file_name=file_name
        )

    def show_selected_diff(self) -> None:
        states = self._states_for_compare()
        if len(states) >= 2:
            self._show_diff_for_states(states[0], states[1])
            return

        groups = self._selected_groups()
        if len(groups) >= 2:
            self._show_diff_for_versions(
                groups[0].representative, groups[1].representative
            )
            return

        QMessageBox.information(
            self,
            "选择不足",
            "请在项目状态表中选择两个项目，或在版本组列表中选择两个版本。",
        )

    def copy_right_diff_to_left(self) -> None:
        self._copy_current_diff("right_to_left")

    def copy_left_diff_to_right(self) -> None:
        self._copy_current_diff("left_to_right")

    def _fill_file_table(self) -> None:
        assert self.result is not None
        self.file_table.setRowCount(len(self.result.target_files))
        for row, file_name in enumerate(self.result.target_files):
            states = self.result.states_by_file[file_name]
            existing = sum(1 for state in states if state.exists)
            missing = len(states) - existing
            groups = len(self.result.groups_by_file[file_name])
            self._set_item(self.file_table, row, 0, file_name, file_name)
            self._set_item(self.file_table, row, 1, str(existing))
            self._set_item(self.file_table, row, 2, str(groups))
            self._set_item(self.file_table, row, 3, str(missing))
        if self.result.target_files:
            self.file_table.selectRow(0)

    def _fill_group_table(self, file_name: str) -> None:
        assert self.result is not None
        groups = self.result.groups_by_file[file_name]
        self.group_table.setRowCount(len(groups))
        for row, group in enumerate(groups):
            rep = group.representative
            self._set_item(self.group_table, row, 0, f"版本 {row + 1}", group)
            self._set_item(self.group_table, row, 1, str(len(group.versions)))
            self._set_item(self.group_table, row, 2, str(rep.size))
            self._set_item(self.group_table, row, 3, str(rep.line_count))
            self._set_item(self.group_table, row, 4, _format_time(rep.modified_ns))
            self._set_item(self.group_table, row, 5, rep.project_dir.name)
        if groups:
            self.group_table.selectRow(0)

    def _fill_project_table(self, file_name: str) -> None:
        assert self.result is not None
        states = tuple(
            state for state in self.result.states_by_file[file_name] if state.version
        )
        self.project_table.blockSignals(True)
        self.project_table.setRowCount(len(states))
        for row, state in enumerate(states):
            check_item = QTableWidgetItem("")
            check_item.setFlags(
                check_item.flags() | Qt.ItemFlag.ItemIsUserCheckable
            )
            check_item.setCheckState(Qt.CheckState.Unchecked)
            check_item.setData(Qt.ItemDataRole.UserRole, state)
            self.project_table.setItem(row, 0, check_item)

            version = state.version
            assert version is not None
            status = "存在"
            digest = version.digest[:12]
            size = str(version.size)
            lines = str(version.line_count)
            modified = _format_time(version.modified_ns)
            self._set_item(
                self.project_table,
                row,
                1,
                _relative_or_absolute(state.project_dir, self.config.scan_root),
                state,
            )
            self._set_item(self.project_table, row, 2, status)
            self._set_item(self.project_table, row, 3, digest)
            self._set_item(self.project_table, row, 4, size)
            self._set_item(self.project_table, row, 5, lines)
            self._set_item(self.project_table, row, 6, modified)
        self.project_table.blockSignals(False)

    def _on_file_selected(self) -> None:
        selected = self.file_table.selectedItems()
        if not selected:
            return
        file_name = self.file_table.item(selected[0].row(), 0).data(
            Qt.ItemDataRole.UserRole
        )
        self.current_file_name = file_name
        self.selected_group = None
        self.current_diff_left = None
        self.current_diff_right = None
        self._set_diff_copy_buttons_enabled(False)
        self._fill_group_table(file_name)
        self._fill_project_table(file_name)
        self._set_diff("", "", [], [])

    def _on_group_selected(self) -> None:
        groups = self._selected_groups()
        if not groups:
            return
        self.selected_group = groups[0]
        if len(groups) >= 2:
            self._show_diff_for_versions(
                groups[0].representative, groups[1].representative
            )
            self.status_label.setText(
                f"已对比版本组: {groups[0].representative.path} ↔ {groups[1].representative.path}"
            )
            return
        self.status_label.setText(
            f"已选择源版本: {self.selected_group.representative.path}"
        )

    def _on_project_selection_changed(self) -> None:
        states = self._selected_project_states()
        if len(states) >= 2:
            self._show_diff_for_states(states[0], states[1])

    def _on_project_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            self.project_table.selectRow(item.row())

    def _show_diff_for_states(
        self, left: ProjectFileState, right: ProjectFileState
    ) -> None:
        self.current_diff_left = left
        self.current_diff_right = right
        self._set_diff_copy_buttons_enabled(bool(left.version and right.version))
        if (
            left.version
            and right.version
            and left.version.digest == right.version.digest
        ):
            self._set_diff_copy_buttons_enabled(False)
            self._set_diff("", "", [], [])
            QMessageBox.information(
                self,
                "内容一致",
                f"两个项目中的 {left.file_name} 内容一致。\n\nSHA256: {left.version.digest}",
            )
            return
        left_text = read_text_for_display(left.version.path if left.version else None)
        right_text = read_text_for_display(
            right.version.path if right.version else None
        )
        left_title = str(
            left.version.path if left.version else left.project_dir / left.file_name
        )
        right_title = str(
            right.version.path if right.version else right.project_dir / right.file_name
        )
        left_view, right_view, left_tags, right_tags = _side_by_side_diff(
            left_text, right_text, left_title, right_title
        )
        self._set_diff(left_view, right_view, left_tags, right_tags)

    def _show_diff_for_versions(self, left: FileVersion, right: FileVersion) -> None:
        self._show_diff_for_states(
            ProjectFileState(
                project_dir=left.project_dir, file_name=left.file_name, version=left
            ),
            ProjectFileState(
                project_dir=right.project_dir, file_name=right.file_name, version=right
            ),
        )

    def _copy_current_diff(self, direction: str) -> None:
        if not self.current_diff_left or not self.current_diff_right:
            QMessageBox.information(self, "未选择差异", "请先对比两个项目文件。")
            return

        if direction == "left_to_right":
            source_state = self.current_diff_left
            target_state = self.current_diff_right
        else:
            source_state = self.current_diff_right
            target_state = self.current_diff_left

        source = source_state.version
        if not source:
            QMessageBox.information(self, "源文件缺失", "源侧没有可用于覆盖的文件。")
            return

        target_path = target_state.project_dir / source.file_name
        message = (
            "将使用以下文件覆盖目标文件：\n\n"
            f"源文件：{source.path}\n\n"
            f"目标文件：{target_path}\n\n"
            "此操作会直接写入目标文件。"
        )
        if (
            QMessageBox.question(self, "确认覆盖", message)
            != QMessageBox.StandardButton.Yes
        ):
            logger.info(
                "Copy current diff canceled by user: source=%s target=%s",
                source.path,
                target_path,
            )
            return

        try:
            logger.info(
                "Copying current diff: source=%s target_project=%s",
                source.path,
                target_state.project_dir,
            )
            copied = copy_version_to_projects(source, [target_state.project_dir])
        except Exception as exc:
            logger.exception(
                "Copy current diff failed: source=%s target=%s",
                source.path,
                target_path,
            )
            QMessageBox.critical(self, "覆盖失败", str(exc))
            return

        file_name = self.current_file_name
        logger.info("Copy current diff completed: copied=%d", len(copied))
        QMessageBox.information(self, "覆盖完成", f"已写入 {len(copied)} 个文件。")
        logger.info(
            "Refreshing after file modification: operation=copy_current_diff copied=%d",
            len(copied),
        )
        self.refresh(reason="after_copy_current_diff", select_file_name=file_name)

    def _selected_project_states(self) -> list[ProjectFileState]:
        states: list[ProjectFileState] = []
        seen_rows = sorted({item.row() for item in self.project_table.selectedItems()})
        for row in seen_rows:
            state = self.project_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if isinstance(state, ProjectFileState):
                states.append(state)
        return states

    def _checked_projects(self) -> list[Path]:
        projects: list[Path] = []
        for row in range(self.project_table.rowCount()):
            item = self.project_table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                state = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(state, ProjectFileState):
                    projects.append(state.project_dir)
        return projects

    def _checked_project_states(self) -> list[ProjectFileState]:
        states: list[ProjectFileState] = []
        for row in range(self.project_table.rowCount()):
            item = self.project_table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                state = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(state, ProjectFileState):
                    states.append(state)
        return states

    def _selected_groups(self) -> list[VersionGroup]:
        groups: list[VersionGroup] = []
        seen_rows = sorted({item.row() for item in self.group_table.selectedItems()})
        for row in seen_rows:
            group = self.group_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if isinstance(group, VersionGroup):
                groups.append(group)
        return groups

    def _states_for_compare(self) -> list[ProjectFileState]:
        selected = self._selected_project_states()
        if len(selected) >= 2:
            return selected
        return self._checked_project_states()

    def _select_file_name(self, file_name: str) -> None:
        for row in range(self.file_table.rowCount()):
            if (
                self.file_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                == file_name
            ):
                self.file_table.selectRow(row)
                break

    def _set_diff(
        self, left: str, right: str, left_tags: list[str], right_tags: list[str]
    ) -> None:
        self.left_diff.setPlainText(left)
        self.right_diff.setPlainText(right)
        self._apply_diff_highlights(self.left_diff, left_tags)
        self._apply_diff_highlights(self.right_diff, right_tags)
        self._set_diff_info_labels()
        self.left_diff.verticalScrollBar().setValue(0)
        self.right_diff.verticalScrollBar().setValue(0)

    def _connect_diff_scroll_sync(self) -> None:
        self.left_diff.verticalScrollBar().valueChanged.connect(
            lambda value: self._sync_diff_scroll(self.left_diff, self.right_diff, value)
        )
        self.right_diff.verticalScrollBar().valueChanged.connect(
            lambda value: self._sync_diff_scroll(self.right_diff, self.left_diff, value)
        )

    def _sync_diff_scroll(
        self, source: QPlainTextEdit, target: QPlainTextEdit, value: int
    ) -> None:
        if self._syncing_diff_scroll:
            return
        self._syncing_diff_scroll = True
        try:
            target.verticalScrollBar().setValue(value)
        finally:
            self._syncing_diff_scroll = False

    def _set_diff_copy_buttons_enabled(self, enabled: bool) -> None:
        self.copy_right_to_left_button.setEnabled(enabled)
        self.copy_left_to_right_button.setEnabled(enabled)

    def _set_diff_info_labels(self) -> None:
        self.left_diff_info.setText(
            _format_state_version_info(self.current_diff_left, self.config.scan_root)
        )
        self.right_diff_info.setText(
            _format_state_version_info(self.current_diff_right, self.config.scan_root)
        )

    @staticmethod
    def _apply_diff_highlights(editor: QPlainTextEdit, tags: list[str]) -> None:
        colors = {
            "header": "#e8eef7",
            "delete": "#ffd7d5",
            "insert": "#ccffd8",
            "empty": "#f3f3f3",
        }
        selections: list[QTextEdit.ExtraSelection] = []
        for line_number, tag in enumerate(tags):
            color = colors.get(tag)
            if not color:
                continue
            block = editor.document().findBlockByLineNumber(line_number)
            if not block.isValid():
                continue
            selection: Any = QTextEdit.ExtraSelection()
            selection.cursor = QTextCursor(block)
            selection.format = QTextCharFormat()
            selection.format.setBackground(QColor(color))
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            selections.append(selection)
        editor.setExtraSelections(selections)

    @staticmethod
    def _make_table(headers: list[str]) -> QTableWidget:
        table = QTableWidget()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setStretchLastSection(True)
        table.setAlternatingRowColors(True)
        table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        table.setStyleSheet("""
            QTableWidget::item:selected {
                background-color: #b8d7ff;
                color: #000000;
            }
            QTableWidget::item:selected:!active {
                background-color: #d3e7ff;
                color: #000000;
            }
            """)
        return table

    @staticmethod
    def _make_text_view() -> QPlainTextEdit:
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        text.setFont(QFont("Consolas", 10))
        return text

    @staticmethod
    def _make_diff_info_label() -> QLabel:
        label = QLabel("未选择对比版本")
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        label.setStyleSheet("""
            QLabel {
                background-color: #f6f8fa;
                border-top: 1px solid #d8dee4;
                color: #24292f;
                padding: 4px 6px;
            }
            """)
        return label

    @staticmethod
    def _make_diff_panel(editor: QPlainTextEdit, info_label: QLabel) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(editor)
        layout.addWidget(info_label)
        panel.setLayout(layout)
        return panel

    @staticmethod
    def _set_item(
        table: QTableWidget, row: int, col: int, text: str, data=None
    ) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if data is not None:
            item.setData(Qt.ItemDataRole.UserRole, data)
        table.setItem(row, col, item)


def _side_by_side_diff(
    left_text: str,
    right_text: str,
    left_title: str,
    right_title: str,
) -> tuple[str, str, list[str], list[str]]:
    left_lines = left_text.splitlines()
    right_lines = right_text.splitlines()
    matcher = difflib.SequenceMatcher(a=left_lines, b=right_lines)
    left_out = [left_title, ""]
    right_out = [right_title, ""]
    left_tags = ["header", "equal"]
    right_tags = ["header", "equal"]

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        left_chunk = left_lines[i1:i2]
        right_chunk = right_lines[j1:j2]
        width = max(len(left_chunk), len(right_chunk))
        for index in range(width):
            left_line = left_chunk[index] if index < len(left_chunk) else ""
            right_line = right_chunk[index] if index < len(right_chunk) else ""
            if tag == "equal":
                left_prefix = right_prefix = "  "
                left_tag = right_tag = "equal"
            elif tag == "delete":
                left_prefix, right_prefix = "- ", "  "
                left_tag, right_tag = "delete", "empty"
            elif tag == "insert":
                left_prefix, right_prefix = "  ", "+ "
                left_tag, right_tag = "empty", "insert"
            else:
                left_prefix, right_prefix = "- ", "+ "
                left_tag = "delete" if index < len(left_chunk) else "empty"
                right_tag = "insert" if index < len(right_chunk) else "empty"
            left_out.append(f"{left_prefix}{left_line}")
            right_out.append(f"{right_prefix}{right_line}")
            left_tags.append(left_tag)
            right_tags.append(right_tag)

    return "\n".join(left_out), "\n".join(right_out), left_tags, right_tags


def _format_time(modified_ns: int) -> str:
    return datetime.fromtimestamp(modified_ns / 1_000_000_000).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _format_state_version_info(state: ProjectFileState | None, scan_root: Path) -> str:
    if not state or not state.version:
        return "未选择对比版本"
    version = state.version
    project = _relative_or_absolute(version.project_dir, scan_root)
    return (
        f"项目: {project} | 版本: {version.digest[:12]} | "
        f"大小: {version.size} | 行数: {version.line_count} | "
        f"修改时间: {_format_time(version.modified_ns)}\n"
        f"文件: {version.path}"
    )


def _relative_or_absolute(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def main() -> int:
    startup_config = load_config(CONFIG_PATH)
    setup_logger(log_level=startup_config.log_level)
    logger.info("Starting Project Common Files Sync desktop app")
    app = QApplication(sys.argv)
    window = MainWindow(startup_config)
    window.show()
    exit_code = app.exec()
    logger.info("Project Common Files Sync desktop app exited: code=%s", exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
