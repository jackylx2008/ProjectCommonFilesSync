from __future__ import annotations

import difflib
import os
import site
import sys
from datetime import datetime
from pathlib import Path

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

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
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
    QVBoxLayout,
    QWidget,
)

from common_sync.config import load_config
from common_sync.scanner import (
    ProjectFileState,
    ScanResult,
    VersionGroup,
    copy_version_to_projects,
    read_text_for_display,
    scan_projects,
)


CONFIG_PATH = Path(__file__).with_name("config.yaml")
CURRENT_PROJECT_DIR = Path(__file__).parent.resolve()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Project Common Files Sync")
        self.resize(1500, 900)

        self.config = load_config(CONFIG_PATH)
        self.result: ScanResult | None = None
        self.current_file_name: str | None = None
        self.selected_group: VersionGroup | None = None

        self.file_table = self._make_table(["文件名", "项目数", "版本组", "缺失"])
        self.group_table = self._make_table(["版本", "项目数", "大小", "行数", "SHA256", "代表项目"])
        self.project_table = self._make_table(["覆盖", "项目", "状态", "版本", "大小", "行数", "修改时间"])
        self.project_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.project_table.itemChanged.connect(self._on_project_item_changed)

        self.left_diff = self._make_text_view()
        self.right_diff = self._make_text_view()
        self.status_label = QLabel()

        refresh_button = QPushButton("重新扫描")
        refresh_button.clicked.connect(self.refresh)
        copy_button = QPushButton("用选中版本覆盖勾选项目")
        copy_button.clicked.connect(self.copy_selected_version)
        diff_button = QPushButton("对比选中项目")
        diff_button.clicked.connect(self.show_selected_diff)

        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel(f"扫描目录: {self.config.scan_root}"))
        top_bar.addStretch()
        top_bar.addWidget(refresh_button)

        left_panel = QVBoxLayout()
        left_panel.addWidget(QLabel("目标文件"))
        left_panel.addWidget(self.file_table)
        left_panel.addWidget(QLabel("内容相同的版本组"))
        left_panel.addWidget(self.group_table)

        right_panel = QVBoxLayout()
        project_header = QHBoxLayout()
        project_header.addWidget(QLabel("项目状态"))
        project_header.addStretch()
        project_header.addWidget(diff_button)
        project_header.addWidget(copy_button)
        right_panel.addLayout(project_header)
        right_panel.addWidget(self.project_table)

        diff_splitter = QSplitter(Qt.Horizontal)
        diff_splitter.addWidget(self.left_diff)
        diff_splitter.addWidget(self.right_diff)
        diff_splitter.setSizes([1, 1])
        right_panel.addWidget(QLabel("左右并排差异"))
        right_panel.addWidget(diff_splitter)

        content_splitter = QSplitter(Qt.Horizontal)
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
        root.addWidget(self.status_label)
        central = QWidget()
        central.setLayout(root)
        self.setCentralWidget(central)

        self.file_table.itemSelectionChanged.connect(self._on_file_selected)
        self.group_table.itemSelectionChanged.connect(self._on_group_selected)
        self.project_table.itemSelectionChanged.connect(self._on_project_selection_changed)

        self.refresh()

    def refresh(self) -> None:
        try:
            self.config = load_config(CONFIG_PATH)
            self.result = scan_projects(self.config, CURRENT_PROJECT_DIR)
        except Exception as exc:
            QMessageBox.critical(self, "扫描失败", str(exc))
            return

        self.current_file_name = None
        self.selected_group = None
        self.group_table.setRowCount(0)
        self.project_table.setRowCount(0)
        self._set_diff("", "")
        self._fill_file_table()
        self.status_label.setText(
            f"已扫描 {len(self.result.project_dirs)} 个项目，目标文件 {len(self.result.target_files)} 个。"
        )

    def copy_selected_version(self) -> None:
        if not self.result or not self.selected_group:
            QMessageBox.information(self, "未选择版本", "请先在版本组列表中选择一个源版本。")
            return
        source = self.selected_group.representative
        targets = self._checked_projects()
        targets = [path for path in targets if path != source.project_dir]
        if not targets:
            QMessageBox.information(self, "未选择目标", "请勾选至少一个不同于源项目的目标项目。")
            return

        message = (
            f"将使用以下源文件覆盖 {len(targets)} 个项目中的 {source.file_name}：\n\n"
            f"{source.path}\n\n此操作会直接写入目标文件。"
        )
        if QMessageBox.question(self, "确认覆盖", message) != QMessageBox.Yes:
            return

        try:
            copied = copy_version_to_projects(source, targets)
        except Exception as exc:
            QMessageBox.critical(self, "覆盖失败", str(exc))
            return

        QMessageBox.information(self, "覆盖完成", f"已写入 {len(copied)} 个文件。")
        file_name = self.current_file_name
        self.refresh()
        if file_name:
            self._select_file_name(file_name)

    def show_selected_diff(self) -> None:
        states = self._states_for_compare()
        if len(states) < 2:
            QMessageBox.information(self, "选择不足", "请在项目状态表中选择两个项目，或勾选两个项目。")
            return
        self._show_diff_for_states(states[0], states[1])

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
            self._set_item(self.group_table, row, 4, group.digest[:12])
            self._set_item(self.group_table, row, 5, rep.project_dir.name)
        if groups:
            self.group_table.selectRow(0)

    def _fill_project_table(self, file_name: str) -> None:
        assert self.result is not None
        states = self.result.states_by_file[file_name]
        self.project_table.blockSignals(True)
        self.project_table.setRowCount(len(states))
        for row, state in enumerate(states):
            check_item = QTableWidgetItem("")
            check_item.setFlags(check_item.flags() | Qt.ItemIsUserCheckable)
            check_item.setCheckState(Qt.Unchecked)
            check_item.setData(Qt.UserRole, state)
            self.project_table.setItem(row, 0, check_item)

            version = state.version
            status = "存在" if version else "缺失"
            digest = version.digest[:12] if version else "-"
            size = str(version.size) if version else "-"
            lines = str(version.line_count) if version else "-"
            modified = _format_time(version.modified_ns) if version else "-"
            self._set_item(self.project_table, row, 1, _relative_or_absolute(state.project_dir, self.config.scan_root), state)
            self._set_item(self.project_table, row, 2, status)
            self._set_item(self.project_table, row, 3, digest)
            self._set_item(self.project_table, row, 4, size)
            self._set_item(self.project_table, row, 5, lines)
            self._set_item(self.project_table, row, 6, modified)
            if not version:
                self._shade_row(self.project_table, row, QColor("#fff4d6"))
        self.project_table.blockSignals(False)

    def _on_file_selected(self) -> None:
        selected = self.file_table.selectedItems()
        if not selected:
            return
        file_name = self.file_table.item(selected[0].row(), 0).data(Qt.UserRole)
        self.current_file_name = file_name
        self.selected_group = None
        self._fill_group_table(file_name)
        self._fill_project_table(file_name)
        self._set_diff("", "")

    def _on_group_selected(self) -> None:
        selected = self.group_table.selectedItems()
        if not selected:
            return
        self.selected_group = self.group_table.item(selected[0].row(), 0).data(Qt.UserRole)
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

    def _show_diff_for_states(self, left: ProjectFileState, right: ProjectFileState) -> None:
        if (
            left.version
            and right.version
            and left.version.digest == right.version.digest
        ):
            self._set_diff("", "")
            QMessageBox.information(
                self,
                "内容一致",
                f"两个项目中的 {left.file_name} 内容一致。\n\nSHA256: {left.version.digest}",
            )
            return
        left_text = read_text_for_display(left.version.path if left.version else None)
        right_text = read_text_for_display(right.version.path if right.version else None)
        left_title = str(left.version.path if left.version else left.project_dir / left.file_name)
        right_title = str(right.version.path if right.version else right.project_dir / right.file_name)
        left_view, right_view = _side_by_side_diff(left_text, right_text, left_title, right_title)
        self._set_diff(left_view, right_view)

    def _selected_project_states(self) -> list[ProjectFileState]:
        states: list[ProjectFileState] = []
        seen_rows = sorted({item.row() for item in self.project_table.selectedItems()})
        for row in seen_rows:
            state = self.project_table.item(row, 0).data(Qt.UserRole)
            if isinstance(state, ProjectFileState):
                states.append(state)
        return states

    def _checked_projects(self) -> list[Path]:
        projects: list[Path] = []
        for row in range(self.project_table.rowCount()):
            item = self.project_table.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                state = item.data(Qt.UserRole)
                if isinstance(state, ProjectFileState):
                    projects.append(state.project_dir)
        return projects

    def _checked_project_states(self) -> list[ProjectFileState]:
        states: list[ProjectFileState] = []
        for row in range(self.project_table.rowCount()):
            item = self.project_table.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                state = item.data(Qt.UserRole)
                if isinstance(state, ProjectFileState):
                    states.append(state)
        return states

    def _states_for_compare(self) -> list[ProjectFileState]:
        selected = self._selected_project_states()
        if len(selected) >= 2:
            return selected
        return self._checked_project_states()

    def _select_file_name(self, file_name: str) -> None:
        for row in range(self.file_table.rowCount()):
            if self.file_table.item(row, 0).data(Qt.UserRole) == file_name:
                self.file_table.selectRow(row)
                break

    def _set_diff(self, left: str, right: str) -> None:
        self.left_diff.setPlainText(left)
        self.right_diff.setPlainText(right)

    @staticmethod
    def _make_table(headers: list[str]) -> QTableWidget:
        table = QTableWidget()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        table.setAlternatingRowColors(True)
        table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        return table

    @staticmethod
    def _make_text_view() -> QPlainTextEdit:
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setLineWrapMode(QPlainTextEdit.NoWrap)
        text.setFont(QFont("Consolas", 10))
        return text

    @staticmethod
    def _set_item(table: QTableWidget, row: int, col: int, text: str, data=None) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        if data is not None:
            item.setData(Qt.UserRole, data)
        table.setItem(row, col, item)

    @staticmethod
    def _shade_row(table: QTableWidget, row: int, color: QColor) -> None:
        for col in range(table.columnCount()):
            item = table.item(row, col)
            if item:
                item.setBackground(color)


def _side_by_side_diff(left_text: str, right_text: str, left_title: str, right_title: str) -> tuple[str, str]:
    left_lines = left_text.splitlines()
    right_lines = right_text.splitlines()
    matcher = difflib.SequenceMatcher(a=left_lines, b=right_lines)
    left_out = [left_title, ""]
    right_out = [right_title, ""]

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        left_chunk = left_lines[i1:i2]
        right_chunk = right_lines[j1:j2]
        width = max(len(left_chunk), len(right_chunk))
        for index in range(width):
            left_line = left_chunk[index] if index < len(left_chunk) else ""
            right_line = right_chunk[index] if index < len(right_chunk) else ""
            if tag == "equal":
                left_prefix = right_prefix = "  "
            elif tag == "delete":
                left_prefix, right_prefix = "- ", "  "
            elif tag == "insert":
                left_prefix, right_prefix = "  ", "+ "
            else:
                left_prefix, right_prefix = "- ", "+ "
            left_out.append(f"{left_prefix}{left_line}")
            right_out.append(f"{right_prefix}{right_line}")

    return "\n".join(left_out), "\n".join(right_out)


def _format_time(modified_ns: int) -> str:
    return datetime.fromtimestamp(modified_ns / 1_000_000_000).strftime("%Y-%m-%d %H:%M:%S")


def _relative_or_absolute(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
